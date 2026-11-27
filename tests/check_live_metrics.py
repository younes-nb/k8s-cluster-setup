#!/usr/bin/env python3
"""Fail-closed Mimir acceptance test. Run during an active representative workload.
The expected service/edge list is a version-specific acceptance fixture, not inferred
from whatever happens to be visible. Missing, stale, NaN or infinite samples fail.
"""
import argparse
import json
import math
import pathlib
import sys
import urllib.parse
import urllib.request

HOSTS = {'master1','master2','master3','worker1','worker2','worker3','loadgen','mon1'}
NODES = HOSTS - {'loadgen','mon1'}
BACKENDS = {'mimir','loki','tempo','grafana','pyroscope'}


def finite_rows(rows):
    if not rows:
        raise ValueError('empty result')
    out = []
    for row in rows:
        val = float(row['value'][1])
        if not math.isfinite(val):
            raise ValueError('nonfinite result')
        out.append((row.get('metric', {}), val))
    return out


class Validation:
    def __init__(self, query):
        self.query = query
        self.failures = []
    def run(self, label, expr, predicate):
        try:
            rows = finite_rows(self.query(expr))
            if not predicate(rows):
                raise ValueError('unexpected value or identity: ' + repr(rows[:8]))
            print('PASS', label, f'({len(rows)} series)')
        except Exception as exc:
            self.failures.append(label)
            print('FAIL', label, str(exc))
    def values(self, label, expr, pred):
        self.run(label, expr, lambda rows: all(pred(v) for _,v in rows))
    def identities(self, label, expr, key, expected, pred=lambda v: True):
        self.run(label, expr, lambda rows: set(expected) <= {m.get(key) for m,v in rows if pred(v)} and all(pred(v) for m,v in rows if m.get(key) in expected))
    def exact_identities(self, label, expr, key, expected, pred=lambda v: True):
        self.run(label, expr, lambda rows: {m.get(key) for m,v in rows} == set(expected) and len(rows) == len(expected) and all(pred(v) for _,v in rows))
    def expected_edges(self, label, expr, expected, pred):
        self.run(label, expr, lambda rows: set(expected) <= {(m.get("client"), m.get("server")) for m,v in rows if pred(v)} and all(pred(v) for _,v in rows) and len(rows)==len({(m.get("client"),m.get("server")) for m,v in rows}))
    def no_unexpected_missing_edges(self, label, expr, exempt):
        # Empty is success only for this explicit absence assertion. Do not
        # infer protocol from the presence or absence of a latency histogram.
        try:
            raw = self.query(expr)
            rows = finite_rows(raw) if raw else []
            observed = {(m.get('client'), m.get('server')) for m,v in rows}
            if len(observed) != len(rows) or not observed <= set(exempt) or any(v <= 0 for _,v in rows):
                raise ValueError('unexpected missing latency: ' + repr(observed - set(exempt)))
            print('PASS', label, f'({len(rows)} explicitly exempt edges)')
        except Exception as exc:
            self.failures.append(label)
            print('FAIL', label, str(exc))
    def fresh(self, label, selector, max_age):
        self.values(label, f'time() - timestamp({selector})', lambda v: -2 <= v <= max_age)
    def present_or_unknown(self, label, selector):
        # Cumulative event counters (slow/failed/timeout) have no series at 0
        # events; that is unknown, not a measured zero and not a failure.
        try:
            rows = finite_rows(self.query(selector))
        except ValueError as exc:
            if str(exc) == 'empty result':
                print('PASS', label, '(unknown: 0 events)')
                return
            raise
        if not all(v >= 0 for _, v in rows):
            self.failures.append(label)
            print('FAIL', label, 'negative count')
        else:
            print('PASS', label, f'({len(rows)} series)')


def make_query(base):
    def query(expr):
        url = base.rstrip('/') + '/prometheus/api/v1/query?' + urllib.parse.urlencode({'query':expr})
        with urllib.request.urlopen(url, timeout=20) as r:
            obj = json.load(r)
        if obj.get('status') != 'success' or obj['data'].get('resultType') != 'vector':
            raise RuntimeError('invalid query response: '+str(obj.get('error',obj.get('status'))))
        if obj.get('warnings'):
            raise RuntimeError('query warnings: '+repr(obj['warnings']))
        return obj['data']['result']
    return query


def validate(query, contract, namespace='train-ticket', max_age=15, experiment='smoke', scenario='load'):
    t=Validation(query)
    ns=json.dumps(namespace)
    exp=json.dumps(experiment)
    sc=json.dumps(scenario)
    filt='{namespace='+ns+'}'
    # identity checks, not counts of arbitrary or duplicate series
    # Node metrics come from two owners: the 6 cluster nodes via the in-cluster
    # Alloy unix exporter (job="integrations/unix") and loadgen/mon1 via the
    # external gateway (job="lab-node"). Do not filter on one job label.
    t.identities('all eight host exporters', 'max by(instance)(node_memory_MemTotal_bytes)','instance',HOSTS,lambda v:v>0)
    t.identities('host sample freshness','max by(instance)(time()-timestamp(node_memory_MemTotal_bytes))','instance',HOSTS,lambda v:-2<=v<=max_age)
    t.identities('all six Kubernetes nodes Ready','kube_node_status_condition{condition="Ready",status="true"}','node',NODES,lambda v:v==1)
    cadvisor = 'container_cpu_usage_seconds_total{job="integrations/cadvisor",container!="",container!="POD"}'
    t.identities('all six cAdvisor nodes', 'count by(node)('+cadvisor+')','node',NODES,lambda v:v>0)
    # Verify the current Running container identities, not stale series left by scaled-in Pods.
    # Freshness is source time, not the timestamp of a recording rule or HTTP scrape.
    running = 'max by(namespace,pod,container)(kube_pod_container_status_running == 1)'
    cpu_age = 'max by(namespace,pod,container)(time()-timestamp(container_cpu_usage_seconds_total{job="integrations/cadvisor",namespace!="",pod!="",container!="",container!="POD"}))'
    t.values('cAdvisor live Kubernetes container coverage', 'count('+running+' and on(namespace,pod,container) ('+cpu_age+')) / count('+running+')', lambda v:v==1)
    t.values('cAdvisor live Kubernetes container freshness', '('+cpu_age+') and on(namespace,pod,container) ('+running+')', lambda v:-2<=v<=max_age)
    # Restrict per-node freshness to currently Running containers, while still
    # requiring coverage on all six nodes; a fresh scrape alone is insufficient.
    active_cpu_age = '('+cpu_age+') and on(namespace,pod,container) ('+running+')'
    node_age = 'max by(node)(('+active_cpu_age+') * on(namespace,pod) group_left(node) max by(namespace,pod,node)(kube_pod_info{node!=""}))'
    t.identities('cAdvisor per-node source freshness',node_age,'node',NODES,lambda v:-2<=v<=max_age)
    t.exact_identities('cAdvisor scrape targets healthy','up{job="integrations/cadvisor"}','node',NODES,lambda v:v==1)
    t.values('no legacy kubelet cAdvisor scrape', 'absent(up{job="prometheus.scrape.kubelet"})', lambda v:v==1)
    t.exact_identities('kubelet operational metrics healthy','up{job="lab-kubelet"}','node',NODES,lambda v:v==1)
    t.identities('all expected benchmark deployments desired','kube_deployment_spec_replicas'+filt,'deployment',contract['deployments'],lambda v:v>=1)
    t.identities('all benchmark deployments available','kube_deployment_status_replicas_available'+filt,'deployment',contract['deployments'],lambda v:v>=1)
    t.values('no unavailable benchmark replicas','autoscaler:deployment_unavailable:replicas'+filt,lambda v:v==0)
    t.fresh('replica samples fresh','kube_deployment_status_replicas_ready'+filt,max_age)
    # count(empty) is empty: explicitly count with absent rather than pass absence as zero.
    t.values('no native HPA for no-controller acceptance','absent(kube_horizontalpodautoscaler_info'+filt+')',lambda v:v==1)
    t.identities('monitoring backends up','up{job="monitoring-backends"}','instance',BACKENDS,lambda v:v==1)
    t.fresh('backend scrape freshness','up{job="monitoring-backends"}',max(35,max_age))
    t.values('gateway reachable','probe_success',lambda v:v==1)
    t.values('gateway active workload','sum(rate(envoy_cluster_upstream_rq_total[1m]))',lambda v:v>0)
    t.fresh('gateway request metrics fresh','envoy_cluster_upstream_rq_total',max_age)
    for job in ('lab-etcd','lab-scheduler','lab-controller-manager'):
        t.exact_identities(job+' healthy', 'up{job='+json.dumps(job)+'}', 'instance', {'master1','master2','master3'}, lambda v:v==1)
        t.fresh(job+' freshness', 'up{job='+json.dumps(job)+'}', max_age)
    t.exact_identities('both MySQL exporters healthy', 'mysql_up{job="lab-mysql"}', 'instance', {'nacosdb-mysql','ts-mysql'}, lambda v:v==1)
    t.fresh('both MySQL exporters fresh', 'mysql_up{job="lab-mysql"}', 35)
    for job, expected in [('lab-nacos', {'nacos-0','nacos-1','nacos-2'}), ('lab-rabbitmq', set(contract['rabbitmq_pods']))]:
        selector = 'up{job='+json.dumps(job)+',namespace='+ns+'}'
        t.exact_identities(job+' healthy', selector, 'pod', expected, lambda v:v==1)
        t.exact_identities(job+' freshness', 'time()-timestamp('+selector+')', 'pod', expected, lambda v:-2<=v<=35)
    t.identities('required StatefulSets ready', 'kube_statefulset_status_replicas_ready'+filt, 'statefulset', {'nacosdb-mysql','ts-mysql','nacos'}, lambda v:v>=1)
    t.values('StatefulSet desired equals ready', 'kube_statefulset_replicas'+filt+' - on(namespace,statefulset) kube_statefulset_status_replicas_ready'+filt, lambda v:v==0)
    services=contract['active_services']
    service_filter='{k8s_namespace_name='+ns+'}'
    t.identities('expected active Beyla services','autoscaler:service_requests:rate1m'+service_filter,'service_name',services,lambda v:v>0)
    t.identities('expected service P90 finite','autoscaler:service_latency:p90_seconds'+service_filter,'service_name',services,lambda v:v>=0)
    t.identities('expected service raw sample freshness','autoscaler:service_telemetry_age:seconds'+service_filter,'service_name',services,lambda v:-2<=v<=max_age)
    edges={tuple(e) for e in contract['active_edges']}
    edge_filter='{client_k8s_namespace_name='+ns+',server_k8s_namespace_name='+ns+'}'
    # Do not fail because an unrelated or idle edge has NaN. Do fail if ANY
    # required active edge is missing, invalid or stale, or any active internal
    # edge is missing a valid latency. The fixture is independently determined.
    for suffix,pred in [('edge_requests:rate1m',lambda v:v>0),('edge_latency:p90_seconds',lambda v:v>=0),('edge_slo:violation',lambda v:v in (0,1))]:
        expr='autoscaler:'+suffix+edge_filter
        if suffix=='edge_requests:rate1m':
            expr+=' > 0'
        t.expected_edges(suffix,expr,edges,pred)
    keys='client,server,client_k8s_namespace_name,server_k8s_namespace_name'
    for label,metric in [('request','traces_service_graph_request_total'+edge_filter),('latency','traces_service_graph_request_client_seconds_bucket{client_k8s_namespace_name='+ns+',server_k8s_namespace_name='+ns+',le="+Inf"}')]:
        # Direct raw-source timestamps, not the timestamp of a ruler output.
        expr='max by('+keys+') ((time()-timestamp('+metric+')) and (rate('+metric+'[1m]) > 0))'
        t.expected_edges('raw '+label+' edge source freshness',expr,edges,lambda v:-2<=v<=max_age)
    # Non-HTTP exceptions must be independently established and declared in the
    # contract, not inferred from missing histograms. An unexpected active edge
    # with no observations is still a failure.
    exempt = {(e['client'], e['server']) for e in contract.get('latency_exempt_edges', [])}
    t.no_unexpected_missing_edges('no unexpected active internal edge missing latency',
        '(autoscaler:edge_requests:rate1m'+edge_filter+' > 0) unless on('+keys+') autoscaler:edge_latency:p90_seconds'+edge_filter, exempt)
    t.values('frontend valid PBScaler SLO observation','autoscaler:frontend_slo:violation'+service_filter,lambda v:v in (0,1))
    for metric in ['k6_lab_attempted_total','k6_lab_good_total','k6_lab_slow_success_total','k6_lab_failed_total','k6_lab_timeout_total','k6_http_reqs_total']:
        sel=metric+'{experiment='+exp+',scenario='+sc+'}'
        # The k6 setup() phase (broker login) leaves a stale ::setup series; the
        # measurement freshness is the load scenario only. The continuous counters
        # (attempted/good/http_reqs) prove stream liveness; slow/failed/timeout are
        # cumulative event counters: absent at 0 events = unknown, not measured zero.
        if metric in ('k6_lab_slow_success_total','k6_lab_failed_total','k6_lab_timeout_total'):
            t.present_or_unknown('present '+metric,sel)
            continue
        t.values('present '+metric,sel,lambda v:v>=0)
        t.fresh('fresh '+metric,metric+'{experiment='+exp+',scenario='+sc+'}',max_age)
    t.values('k6 native histogram count', 'histogram_count(k6_http_req_duration_seconds{experiment='+exp+',scenario='+sc+'})', lambda v:v>=0)
    t.values('k6 histogram has observations', 'sum(histogram_count(k6_http_req_duration_seconds{experiment='+exp+',scenario='+sc+'}))', lambda v:v>0)
    t.fresh('k6 histogram freshness', 'k6_http_req_duration_seconds{experiment='+exp+',scenario='+sc+'}', max_age)
    t.values('k6 workload is active','sum(rate(k6_lab_attempted_total{experiment='+exp+',scenario='+sc+'}[1m]))',lambda v:v>0)
    # Require the agent to enumerate deployment-specific feature metric names, not claim unknown signals available.
    for item in contract['extra_metrics']:
        if 'equals' in item:
            pred = lambda v: v == item['equals']
        else:
            pred = lambda v: item.get('min', 0) <= v <= item.get('max', math.inf)
        t.values(item['name'],item['query'],pred)
        if item.get('fresh_selector'):
            if item.get('fresh_max'):
                # Some sources (Beyla network flows) keep ended-flow series around;
                # requiring every historical flow to be fresh is wrong. Assert the
                # freshest series is within the 15 s gate instead (min age).
                t.values(item['name']+' freshness','min(time()-timestamp('+item['fresh_selector']+'))',lambda v: -2<=v<=item.get('max_age',max_age))
            else:
                t.fresh(item['name']+' freshness',item['fresh_selector'],item.get('max_age',max_age))
    return t.failures


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mimir',required=True)
    p.add_argument('--contract',required=True,help='verified JSON with deployments, active_services, active_edges, extra_metrics')
    p.add_argument('--namespace',default='train-ticket')
    p.add_argument('--experiment',required=True)
    p.add_argument('--scenario',default='load',help='k6 measurement scenario (load, api, smoke, etc.)')
    p.add_argument('--max-age',type=float,default=15)
    a=p.parse_args()
    if not 0<a.max_age<=15:
        p.error('online metric freshness must be at most 15 seconds')
    c=json.loads(pathlib.Path(a.contract).read_text())
    if any(not c.get(k) for k in ('deployments','active_services','active_edges','extra_metrics','rabbitmq_pods')):
        p.error('all contract groups must be nonempty; obtain independent expected identities before testing')
    exemptions = c.get('latency_exempt_edges', [])
    if not isinstance(exemptions, list) or any(not isinstance(e, dict) or not all(isinstance(e.get(k), str) and e[k].strip() for k in ('client','server','protocol','reason')) or e['protocol'].lower() in ('http','https','grpc','unknown') for e in exemptions):
        p.error('latency_exempt_edges must contain independently verified non-HTTP/non-gRPC pairs with protocol and reason')
    exempt_pairs = {(e['client'],e['server']) for e in exemptions}
    if len(exempt_pairs) != len(exemptions) or exempt_pairs & {tuple(e) for e in c['active_edges']}:
        p.error('duplicate exemptions or exemption overlaps required active edges')
    failed=validate(make_query(a.mimir),c,a.namespace,a.max_age,a.experiment,a.scenario)
    print('FAILED: '+', '.join(failed) if failed else 'METRIC ACCEPTANCE PASSED (not full workflow or semantic acceptance)')
    return bool(failed)

if __name__=='__main__':
    sys.exit(main())

