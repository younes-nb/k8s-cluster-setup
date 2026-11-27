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
  all collector rollouts `ok`. Re-running `monitoring.yaml` → `changed=1` (only
  the ingress-firewall re-apply, idempotent in effect); data-dir mode aligned to
  `0750` (the mode the images enforce) so the base-dir task no longer re-reports
  changed.
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
---

## Post-acceptance correction pass (patch a55fe52, 2026-11-27)

Tested revisions (report separately; this file is not its own SHA):
- `cluster-repo` HEAD **`0a34e4c`** (patch `a55fe52` integrated + fixes).
- `predictive-k8s-autoscaling` HEAD **`7d5d1f8`**.

### P0 — one canonical cAdvisor owner
- `alloy.yaml.j2`: kubelet scrape now `/metrics` (job `lab-kubelet`), **not**
  `/metrics/cadvisor`; the legacy `prometheus.scrape.kubelet` cadvisor job is gone.
- Standalone `cadvisor` DaemonSet (v0.49.1) flags verified against the image
  `--help`: `--listen_ip=127.0.0.1`, `--allow_dynamic_housekeeping=false`,
  `--max_housekeeping_interval=3s`, `--housekeeping_interval=3s`; loopback-only
  (`127.0.0.1:8080`), readiness `host: 127.0.0.1`.
- Live: only `integrations/cadvisor` emits `container_cpu_usage_seconds_total`
  (690 series, single owner); `up{job="integrations/cadvisor"}`=6;
  `absent(up{job="prometheus.scrape.kubelet"})`=1; `up{job="lab-kubelet"}`=6.
- Source age (all Running containers, canonical job): CPU/mem/throttle/fs/blkio
  **7–11 s** (≤15 s) on all 6 nodes; network is host-level (cadvisor) + per-flow
  (Beyla). CPU/throttling recording rules scoped to `job="integrations/cadvisor"`.

### P2 — independent HTTP/gRPC edge coverage (no circular exemptions)
- `latency_exempt_edges` = 41 client→MySQL pairs, each verified **SQL** from the
  server image (`mysql:5.7.34`) + port 3306 — not inferred from histogram absence.
  Required active edges are never exempted (overlap check).
- `check_live_metrics` `no_unexpected_missing_edges`: an active edge without p90
  fails unless independently classified; **67 PASS** during a representative run.

### P3 — k6 scenario + outcome accounting
- `--scenario` selects the measurement (default `load`; bundled smoke `api`).
- `check_k6_stream` PASS mid-run: `k6_lab_attempted_total=120`,
  `k6_http_reqs_total=316` (1 attempt ≈ 2.6 HTTP requests, same scope); event
  counters absent at 0 events = **unknown** (not measured zero), a present-but-old
  event sample is valid (rare events), a disappearing observed counter fails.

### P1 — readiness / controlled scaling / diagnostics
- Controlled idle **1→2→1** on `ts-travel-service` (no HPA, no other writer):
  decision 19:10:42Z, new pod Running 19:10:43Z, **Ready 19:17:59Z** (~7m16s
  Spring Boot+MySQL), discoverable (EndpointSlice 2 addresses), telemetry-visible
  (Beyla series), scale-in restored to 1 at 19:18:20Z. Root kubelet probe does not
  hit Envoy RED; Beyla RED rate is empty when idle (p90 from sparse probe).
- **Tempo**: correlated trace retrieved (`/api/traces/{id}` with k8s metadata).
- **Pyroscope**: query API is ConnectRPC `querier.v1.QuerierService` (not REST);
  `ProfileTypes`/`Series`/`SelectMergeStacktraces` with **millisecond** timestamps.
  Retrieved a real CPU profile (`train-ticket/k8snacos`, total 1.02e12 ns,
  JVM `JavaThread::thread_main_inner`/`JavaCalls::call_virtual`).
- **Dashboards**: 11 dashboards use `integrations/cadvisor` (no old kubelet);
  live queries during traffic return data (k8s CPU 125, mem 129, RED 47, SLO 1,
  k6 1 series).

### Reconciliation / idempotency
- `postcluster --tags alloy` re-run → `changed=0`; `monitoring.yaml` re-run →
  `changed=1` (firewall re-apply only, effect-idempotent).
