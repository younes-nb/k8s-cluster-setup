# Final telemetry acceptance

The 15 s online source-age ceiling is not a scrape timeout, a ruler evaluation interval, or a guarantee of algorithm execution time. Measure all three stages separately. No fabricated zeroes, timestamps, or latency estimates. This patch is an offline-tested candidate; the operator must exercise actual pinned binaries and runtime queries.

## k6

Use one writer for the canonical raw counters `k6_lab_{attempted,good,slow_success,failed,timeout}_total`: the k6 output, from JS `Counter('lab_attempted')` etc. Remove former Mimir alias rules writing the same names. A unique experiment ID prevents historical alias data from colliding. Check actual exported names for the installed build. Migrate research scripts which used `k6_lab_*_total` as JS names; don't add a prefix/suffix twice. Use the existing `/opt/loadgen/k6.env`, a unique experiment ID, built-in Prometheus remote write, and uncompressed JSON for controlled diagnosis. Keep the raw JSON locally protected; it may contain sensitive tags. The `check_k6_stream.py` test must run while k6 is still producing traffic, after warm-up, not after process exit. Stale markers can remove finished series from instant queries; preserve historical query windows and final raw data.

Example on loadgen (run from repo root with valid /opt/loadgen/k6.env):

```bash
source /opt/loadgen/k6.env
export EXPERIMENT_ID="telemetry-$(date -u +%Y%m%dT%H%M%S)-$(cat /proc/sys/kernel/random/uuid)"
export SMOKE_DURATION=120s
umask 077
mkdir -p acceptance/evidence/"$EXPERIMENT_ID"
EVIDENCE="$PWD/acceptance/evidence/$EXPERIMENT_ID"
k6 run -o experimental-prometheus-rw -o "json=$EVIDENCE/k6-points.json" /opt/loadgen/api-smoke.js > "$EVIDENCE/k6.log" 2>&1 &
K6_PID=$!
sleep 20
kill -0 "$K6_PID" || exit 1
python3 tests/check_k6_stream.py --mimir "${K6_PROMETHEUS_RW_SERVER_URL%/api/v1/push}" --experiment "$EXPERIMENT_ID" --scenario api --evidence "$EVIDENCE/counter-snapshots.jsonl" --duration 40
CHECK_RC=$?
wait "$K6_PID"
K6_RC=$?
test "$CHECK_RC" -eq 0 && test "$K6_RC" -eq 0
```

This proves the smoke output pipeline, not representative business workload success. Compare JSON event times to raw Mimir sample timestamps and account for each attempt/terminal outcome at run end. Require positive progress during the run, not just metric presence. Distinguish absent zero-valued series from an explicit zero. Keep k6 independent of controller input.

## Directed dependency latency

The rules aggregate by `(client, server, client_k8s_namespace_name, server_k8s_namespace_name)`. Verify actual Beyla labels and normalize at ingestion if needed (not by assuming an absent namespace). The acceptance fixture lists independently expected internal directed edges in the benchmark namespace. An idle edge may have no defined quantile; an active edge missing a finite client-side quantile is a failure unless an independently established non-HTTP/non-gRPC protocol exemption is recorded in `latency_exempt_edges` (client, server, protocol, reason). Never infer protocol from an absent histogram. Exemptions must not overlap required active edges. `autoscaler:edge_latency:missing` exposes missing coverage, not a healthy zero. Compare `+Inf` rate, histogram `_count` and all bucket boundaries per producer, and correlate counts with client request observations. Verify bucket monotonicity, exactly one producer identity and compatible histogram schemas. The filter does NOT repair genuinely incomplete histograms. Instrument supported client metrics or traces if necessary; do not substitute server P90 under a client-latency name. Maintain a single canonical owner and comparable instrumentation across algorithms. Validate raw source ages, SLO 200 ms and edge boundary 220 ms, known fast/slow cases, missing observations, and active/idle edge handling.

## Container statistics

Standalone cAdvisor is the canonical container-statistics owner, job `integrations/cadvisor`. Kubelet collection is for operational `/metrics` only (job `lab-kubelet`), not `/metrics/cadvisor`. Retain historical series in storage, but filter online resource rules and dashboards to the canonical job. Verify local-only listening and fixed housekeeping options against the pinned binary. Inspect `/metrics/cadvisor` raw explicit timestamps, kubelet `container_last_seen`, source collection cadence, `scrape_duration_seconds`, remote-write lag and host clocks on every node. The validator checks all currently Running Kubernetes containers (including benchmark and infrastructure), per-node coverage, and scrape health. Match current container IDs/Pod UIDs where stale prior instances can overlap; validate the stable identity and duplicate check on scale-out/in.

If source collection still exceeds 15 s, implement a real source-level remedy. Prefer supported `prometheus.exporter.cadvisor` within the existing Alloy stack with verified pinned-version support for fixed short housekeeping (e.g. 2–5 s and `allow_dynamic_housekeeping=false`); otherwise use a validated pinned cAdvisor exporter or supported kubelet configuration/build. A change of source requires verified containerd/cgroup v2 access, least privilege, labels (`node`, `namespace`, `pod`, `container`, container identity), CPU/memory/network/disk/throttling equivalence, and exactly one owner for each series. Stop old collection only after validating the replacement, update queries/validator consistently, preserve historical data. Do not use `honor_timestamps=false`, `timestamp` on freshly recorded aliases, or synthetic samples to mask stale measurements. Measure overhead and source age under load and scale churn; a faster scrape alone is not a solution.

## Acceptance

Run static and pinned quickstart tests, actual Alloy config validation, Mimir/PromQL rule checks with promtool or the pinned backend and fixtures, live validator during representative workload, and repeated source-age measurements covering multiple 15 s cycles. Test unavailable/NaN/stale signals, expected edge absent, and source interruption. Record exact commands, results, source versions, coverage and failures. A dashboard with no data is not success. Keep evidence outside Git or sanitized; no credentials in Git. See `docs/TELEMETRY.md` and `docs/METRICS_ACCEPTANCE.md` for remaining research acceptance requirements.
