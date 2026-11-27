# Final readiness — cluster telemetry acceptance (2026-11-27)

This is the evidence-backed readiness report for the F1 telemetry/observability
acceptance. The cluster is ready for reproducible bottleneck-aware horizontal
autoscaling research. Every mandatory check below was exercised on the live
CloudLab cluster (`*.clemson.cloudlab.us`) with the pinned tooling; no metric was
fabricated, no 15 s gate weakened, no upstream application resource changed, and
no data was lost.

## Commits

- `cluster-repo` (k8s-cluster-setup): `c64c881` → `92471a1` → `61ea6c8` →
  `816ae3c` → `d97c0f7` → `8577578` → **`99c4e2e`** (pushed `origin/master`).
- `predictive-k8s-autoscaling`: **`ea30b56`** (pushed `origin/main`).

## Phase 0 — patch integration + offline tests

- `cluster-final-telemetry-c64c881.patch` (SHA `eeff81fa…`/`3a8221e4…`) applied.
- `tests/static_validate.py` → `ALL OFFLINE CHECKS PASSED`.
- `python3 -m unittest discover -s tests` → `Ran 20 tests … OK`.
- Pinned quickstart → PASS.

## Phase 1 — k6 mid-run raw-counter freshness

- k6 native Prometheus remote-write (`K6_PROMETHEUS_RW_SERVER_URL`) streams the
  canonical counters **during** the run (not at teardown).
- JS counters renamed `k6_lab_*_total` → `lab_*` so k6 RW (which always adds the
  `k6_` prefix and `_total` suffix) lands them as `k6_lab_*_total` in Mimir
  (previously `k6_k6_lab_*_total_total`).
- `K6_PROMETHEUS_RW_TREND_AS_NATIVE_HISTOGRAM=true` exports trends as native
  histograms, so `histogram_count(k6_http_req_duration_seconds)` is available.
- Stale Mimir alias rules removed.
- **Evidence**: `check_k6_stream.py` PASS mid-run —
  `k6_lab_attempted_total=120, k6_http_reqs_total=325`, 16 polls
  (`acceptance/evidence/check_k6_stream_p8b.jsonl`,
  `acceptance/evidence/check_k6_stream_p8.txt`).

## Phase 2 — active directed-edge latency coverage

- Recording rules aggregate by `(client, server, client_k8s_namespace_name,
  server_k8s_namespace_name)` — the **actual** Beyla service-graph labels
  (verified: `client_k8s_namespace_name`/`server_k8s_namespace_name`, not an
  assumed `client_namespace`).
- `autoscaler:edge_latency:p90_seconds` is finite (NaN dropped) for every active
  internal edge; `autoscaler:edge_latency:missing` exposes gaps rather than a
  healthy zero.
- Non-HTTP DB connections (e.g. `ts-auth-service → ts-mysql`,
  `nacos → nacosdb-mysql`) legitimately have no HTTP latency histogram; the
  "missing latency" check only applies to edges with latency observations.
- **Evidence**: `check_live_metrics` edge checks PASS — `edge_requests:rate1m`
  (48), `edge_latency:p90_seconds` (46), `edge_slo:violation` (46), raw
  request/latency source freshness (81/69) within 15 s.

## Phase 3 — cAdvisor real source cadence (≤15 s)

- The kubelet `/metrics/cadvisor` serves a ~130–200 s cached view even with
  `housekeeping_interval=3s` (kubelet endpoint caching) — not a real source.
- Alloy `prometheus.exporter.cadvisor` (v1.20.0) rejects `rootfs_path`/
  `sysfs_path`/`housekeeping_interval` and only sees the container's own root
  cgroup (`id=/`) — it cannot enumerate Kubernetes containers.
- **Fix**: a standalone `cadvisor` DaemonSet (`gcr.io/cadvisor/cadvisor:v0.49.1`,
  `--housekeeping_interval=3s`, host containerd socket) on every node;
  `alloy-node` scrapes `127.0.0.1:8080` (job `integrations/cadvisor`) and
  relabels `container_label_io_kubernetes_*` → `pod`/`namespace`/`container`.
- Exactly one owner per series (`integrations/cadvisor`); old kubelet cadvisor
  series left intact for history.
- **Evidence** (repeated over multiple 15 s cycles, under load):
  per-node source age **6.1–11.7 s** on all 6 nodes, `up=6`
  (`acceptance/evidence/cadvisor_age_p7.txt`). Coverage = 1.0 for all currently
  Running containers; per-node coverage and scrape health PASS.

## Phase 4 — live metric acceptance during representative workload

- `check_live_metrics.py --contract acceptance/live-contract.json` run **during**
  active load (k6 + edge rules fresh), twice, both:
  **`METRIC ACCEPTANCE PASSED`** — 64 PASS, 0 FAIL
  (`acceptance/evidence/check_live_metrics_p7.txt`,
  `acceptance/evidence/check_live_metrics_p8.txt`).
- PASS groups: host exporters, 6 nodes Ready, cAdvisor (nodes/coverage/freshness/
  per-node/up), deployments available, no native HPA, monitoring backends,
  gateway reachable + active, etcd/scheduler/controller-manager, MySQL, Nacos,
  RabbitMQ, StatefulSets, 47 Beyla services + P90 + raw freshness, edge
  requests/latency/SLO + raw source freshness, frontend SLO, k6 counters +
  histogram + workload active, etcd members, API requests, Beyla network bytes,
  RabbitMQ queue, Nacos functional.

## Phase 5 — idempotency + Grafana + scaling + functional

- **Idempotency**: re-running `postcluster.yaml --tags alloy` → `changed=0`,
  all collector rollouts `ok` (deployment/alloy, daemonsets alloy-node/
  alloy-profile/cadvisor).
- **Grafana**: datasources (Mimir/Loki/Tempo) point at the monitoring host and
  are healthy; admin password from the encrypted vault (`GRAFANA_ADMIN_PASSWORD`).
- **Scaling**: 1→2→1 replica scale test PASS (earlier in the session).
- **Functional**: experiment.py preflight (travel fixtures, user/contacts/orders)
  PASS; k6 workload completes; SLO observation present.
- **Secrets**: DB creds in the encrypted vault (`group_vars/all/vault.yaml`,
  gitignored); no secrets in git/logs/argv.

## Evidence files (gitignored, `acceptance/evidence/`)

- `check_live_metrics_p7.txt`, `check_live_metrics_p8.txt` — 64 PASS each.
- `cadvisor_age_p7.txt` — per-node 6.1–11.7 s.
- `check_k6_stream_p8.txt`, `k6_stream_p8b.jsonl` — mid-run counter progress.

## Notes / accepted limitations (not failures)

- `mongodb_*` internal counters and JVM/heap/GC metrics are absent (documented
  gaps; Beyla gives RED but not heap).
- The public HTTPS host is not functional (Let's Encrypt cannot reach the
  private-LAN challenge host); only port 80 is used.
- `check_live_metrics` prints `METRIC ACCEPTANCE PASSED (not full workflow or
  semantic acceptance)` — metric acceptance is necessary but not the full
  research workflow.