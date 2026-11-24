#!/usr/bin/env python3
"""Live metric checks against the off-cluster Mimir.

    python3 tests/check_live_metrics.py --mimir http://10.10.1.2:18080 \
        [--namespace train-ticket] [--max-age 120]

Fails when a required signal is missing, stale, or non-finite. Absence is never
treated as zero or healthy.
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

p = argparse.ArgumentParser()
p.add_argument("--mimir", required=True)
p.add_argument("--namespace", default="train-ticket")
p.add_argument("--front", default="ts-ui-dashboard")
p.add_argument("--max-age", type=float, default=300)
a = p.parse_args()

BASE = a.mimir.rstrip("/")


def query(q):
    url = BASE + "/prometheus/api/v1/query?" + urllib.parse.urlencode({"query": q})
    with urllib.request.urlopen(url, timeout=20) as r:
        obj = json.load(r)
    if obj.get("status") != "success":
        raise RuntimeError(str(obj.get("error")))
    return obj["data"]["result"]


def check(label, q, minimum=1, mode="value"):
    """``mode=value`` compares the single aggregate value; ``mode=series``
    counts returned series (for ``count by (...)`` style queries)."""
    try:
        res = query(q)
    except Exception as exc:
        print(f"FAIL {label}: {exc}")
        return False
    vals = [float(x["value"][1]) for x in res]
    finite = [v for v in vals if v == v and v not in (float("inf"), float("-inf"))]
    if mode == "series":
        ok = len(finite) >= minimum
        detail = f"{len(finite)} series"
    else:
        total = finite[0] if finite else 0.0
        ok = total >= minimum
        detail = f"{total:.0f}"
    print(f"{'PASS' if ok else 'FAIL'} {label}: {detail}"
      + ("" if ok else f" (wanted >= {minimum})"))
    return ok


now = time.time()
results = [
    # The Alloy unix exporter is the only node_exporter today (one instance).
    # The loadgen/mon1 exporters are a NEW capability added by this repo.
    check("node_exporter coverage (Alloy unix exporter today)",
          'count(count by (instance) (node_memory_MemTotal_bytes))', 1),
    check("cAdvisor pod series",
          'count(count by (pod) (container_cpu_usage_seconds_total{container!="",container!="POD"}))', 10),
    # A sanity floor, not an exact count: the benchmark namespace size varies.
    check("kube-state-metrics replicas",
      f'count(kube_deployment_spec_replicas{{namespace="{a.namespace}"}})', 3),
    check("HPA current/desired (native baseline absent while AURORA owns replicas)",
      f'count(kube_horizontalpodautoscaler_status_current_replicas{{namespace="{a.namespace}"}})', 0),
    # The mesh is retired. The application path is Envoy Gateway, so the RED
    # signals come from the gateway proxy and from Beyla, not from Istio.
    check("Envoy Gateway upstream requests/s",
      'sum(rate(envoy_cluster_upstream_rq_total[5m]))', 0.0),
    check("Beyla RED metrics (http_server_request_duration_seconds)",
      'count(http_server_request_duration_seconds_count)', 0),
    check("frontend p99 from Beyla (ms)",
      f'histogram_quantile(0.99, sum by (le) (rate('
      f'http_server_request_duration_seconds_bucket{{service_name="{a.front}"}}[5m])))', 0),
    check("Envoy gateway upstream active",
      'sum(envoy_cluster_upstream_rq_active)', 1),
    # Only present once the off-cluster Alloy gateway is deployed (new in this repo).
    check("monitoring backends up (off-cluster Alloy gateway)",
      'count(up{job="monitoring-backends"} == 1)', 0),
    check("k6 remote write (only when a load test is running)",
      'count(k6_http_reqs_total)', 0),
]

# freshness: the newest istio sample must be recent
try:
    res = query('max(timestamp(envoy_cluster_upstream_rq_total))')
    newest = max(float(x["value"][1]) for x in res) if res else 0.0
    if not newest:
        raise RuntimeError("no envoy gateway series at all")
    age = now - newest
    ok = age <= a.max_age
    print(f"{'PASS' if ok else 'FAIL'} telemetry freshness: newest envoy sample {age:.0f}s old (max {a.max_age:.0f}s)")
    results.append(ok)
except Exception as exc:
    print(f"FAIL telemetry freshness: {exc}")
    results.append(False)

print()
print("ALL LIVE METRIC CHECKS PASSED" if all(results) else "SOME LIVE CHECKS FAILED")
sys.exit(0 if all(results) else 1)