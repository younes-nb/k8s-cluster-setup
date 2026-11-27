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
    def fresh(self, label, selector, max_age):
        self.values(label, f'time() - timestamp({selector})', lambda v: -2 <= v <= max_age)


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


def validate(query, contract, namespace='train-ticket', max_age=15, experiment='smoke'):
    t=Validation(query)
    ns=json.dumps(namespace)
    exp=json.dumps(experiment)
    filt='{namespace='+ns+'}'
    # identity checks, not counts of arbitrary or duplicate series
    # Node metrics come from two owners: the 6 cluster nodes via the in-cluster
    # Alloy unix exporter (job="integrations/unix") and loadgen/mon1 via the
    # external gateway (job="lab-node"). Do not filter on one job label.
    t.identities('all eight host exporters', 'max by(instance)(node_memory_MemTotal_bytes)','instance',HOSTS,lambda v:v>0)
    t.identities('host sample freshness','max by(instance)(time()-timestamp(node_memory_MemTotal_bytes))','instance',HOSTS,lambda v:-2<=v<=max_age)
    t.identities('all six Kubernetes nodes Ready','kube_node_status_condition{condition="Ready",status="true"}','node',NODES,lambda v:v==1)
    t.identities('all six cAdvisor nodes','count by(node)(container_cpu_usage_seconds_total{container!="",container!="POD"})','node',NODES,lambda v:v>0)
    t.identities('cAdvisor freshness','max by(node)(time()-timestamp(container_cpu_usage_seconds_total{container!="",container!="POD"}))','node',NODES,lambda v:-2<=v<=max_age)
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
        t.identities(job+' healthy', 'up{job='+json.dumps(job)+'}', 'instance', {'master1','master2','master3'}, lambda v:v==1)
        t.fresh(job+' freshness', 'up{job='+json.dumps(job)+'}', max_age)
    t.identities('both MySQL exporters healthy', 'mysql_up{job="lab-mysql"}', 'instance', {'nacosdb-mysql','ts-mysql'}, lambda v:v==1)
    t.identities('required StatefulSets ready', 'kube_statefulset_status_replicas_ready'+filt, 'statefulset', {'nacosdb-mysql','ts-mysql','nacos'}, lambda v:v>=1)
    t.values('StatefulSet desired equals ready', 'kube_statefulset_replicas'+filt+' - on(namespace,statefulset) kube_statefulset_status_replicas_ready'+filt, lambda v:v==0)
    services=contract['active_services']
    service_filter='{k8s_namespace_name='+ns+'}'
    t.identities('expected active Beyla services','autoscaler:service_requests:rate1m'+service_filter,'service_name',services,lambda v:v>0)
    t.identities('expected service P90 finite','autoscaler:service_latency:p90_seconds'+service_filter,'service_name',services,lambda v:v>=0)
    t.identities('expected service raw sample freshness','autoscaler:service_telemetry_age:seconds'+service_filter,'service_name',services,lambda v:-2<=v<=max_age)
    edges={tuple(e) for e in contract['active_edges']}
    for suffix,pred in [('edge_requests:rate1m',lambda v:v>0),('edge_latency:p90_seconds',lambda v:v>=0),('edge_slo:violation',lambda v:v in (0,1))]:
        t.run(suffix,'autoscaler:'+suffix,lambda rows:edges <= {(m.get('client'),m.get('server')) for m,v in rows if pred(v)})
    t.fresh('raw edge counter freshness','traces_service_graph_request_total',max_age)
    t.values('frontend valid PBScaler SLO observation','autoscaler:frontend_slo:violation'+service_filter,lambda v:v in (0,1))
    for metric in ['k6_lab_attempted_total','k6_lab_good_total','k6_lab_slow_success_total','k6_lab_failed_total','k6_lab_timeout_total','k6_http_reqs_total']:
        sel=metric+'{experiment='+exp+'}'
        t.values('present '+metric,sel,lambda v:v>=0)
        t.fresh('fresh '+metric,sel,max_age)
    t.values('k6 native histogram count', 'histogram_count(k6_http_req_duration_seconds{experiment='+exp+'})', lambda v:v>=0)
    t.fresh('k6 histogram freshness', 'k6_http_req_duration_seconds{experiment='+exp+'}', max_age)
    t.values('k6 workload is active','sum(rate(k6_lab_attempted_total{experiment='+exp+'}[1m]))',lambda v:v>0)
    # Require the agent to enumerate deployment-specific feature metric names, not claim unknown signals available.
    for item in contract['extra_metrics']:
        t.values(item['name'],item['query'],lambda v:v>=0)
        if item.get('fresh_selector'):
            t.fresh(item['name']+' freshness',item['fresh_selector'],item.get('max_age',max_age))
    return t.failures


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mimir',required=True)
    p.add_argument('--contract',required=True,help='verified JSON with deployments, active_services, active_edges, extra_metrics')
    p.add_argument('--namespace',default='train-ticket')
    p.add_argument('--experiment',required=True)
    p.add_argument('--max-age',type=float,default=15)
    a=p.parse_args()
    if not 0<a.max_age<=15:
        p.error('online metric freshness must be at most 15 seconds')
    c=json.loads(pathlib.Path(a.contract).read_text())
    if any(not c.get(k) for k in ('deployments','active_services','active_edges','extra_metrics')):
        p.error('all contract groups must be nonempty; obtain independent expected identities before testing')
    failed=validate(make_query(a.mimir),c,a.namespace,a.max_age,a.experiment)
    print('FAILED: '+', '.join(failed) if failed else 'METRIC ACCEPTANCE PASSED (not full workflow or semantic acceptance)')
    return bool(failed)

if __name__=='__main__':
    sys.exit(main())

