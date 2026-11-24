# Telemetry contract

One monitoring architecture, outside the cluster, plus in-cluster collectors that
must reach pod IPs.

| Layer | Where | Pushes to |
|---|---|---|
| Alloy (scraper) | in-cluster Deployment, pinned to a control-plane node | Mimir `18080`, Loki `3100` |
| Alloy node collector | in-cluster DaemonSet | Mimir (host + CRI metrics) |
| Alloy profile collector | in-cluster DaemonSet on workers | Pyroscope `4040` |
| Beyla | in-cluster DaemonSet | OTLP → **off-cluster** Alloy gateway `:4317` |
| Alloy gateway | mon1 | Mimir (metrics), Tempo `4317` (traces) |
| Mimir / Loki / Tempo / Grafana / Pyroscope | mon1, `/dev/sdb` | — |

5 s resolution. Retention 1440 h (60 days).

## Required signals

| Signal | Source |
|---|---|
| CPU request utilisation | cAdvisor + kube-state-metrics (`usage / request`) |
| CPU throttling | `container_cpu_cfs_throttled_periods_total / ..._periods_total` |
| Memory working set / limit | cAdvisor + kube-state-metrics |
| Pod / container CPU, network, disk, restarts, OOM | cAdvisor |
| Requests / limits, readiness, restarts, HPA state | kube-state-metrics |
| Node CPU/mem/net/disk | node_exporter (Alloy DaemonSet; loadgen + mon1 exporters) |
| Request rate, latency, errors, TCP | Beyla `http_server_request_duration_seconds`, `beyla_*` |
| **RED per service** | Envoy Gateway `envoy_cluster_upstream_rq_*` |
| Traces | Tempo (OTLP from Beyla / Envoy) |
| Logs | Loki (Alloy log tail) |
| Profiles | Pyroscope (eBPF CPU) |
| k6 client outcomes | k6 remote write → Mimir |

## Not available (documented, not fabricated)

| Missing | Consequence |
|---|---|
| **Destination-side Istio histograms** (`reporter="destination"`) | the mesh is retired; the old Istio-derived front-p90 SLO is gone. Use Envoy/Beyla or k6. `check_live_metrics.py` reports this explicitly. |
| `mongodb_*` internal counters | no scrape target for the exporter; container-level DB metrics only |
| JVM / heap / GC (`jvm_*`, `tomcat_*`, `http_server_requests_*`) | the images have no MDC filter and no tracing on the classpath |
| Spring request IDs / distributed tracing in-app | Istio's `X-Request-ID` was the correlation key; Beyla emits `traceparent` |
| Water: `istio_requests_total{reporter="destination"}` | 0 series after migration |

## Known gotchas

- `reject_old_samples_max_age: 9999h` in Loki — `0` is NOT "disabled" in Loki 3.x; it means "reject anything older than now".
- Loki caps a query at 5 000 entries; chunk accordingly.
- Mimir bucket series expire; query promptly.
- Two kube-state-metrics replicas were previously scraped twice (duplicate series). The KSM scrape is now via the ServiceMonitor only.
- `kube_*` series must be deduped with `max by (...)` if a second scrape ever appears.
- `node_exporter` metrics are per-instance; the Alloy unix exporter only covers its own host. For cluster-wide node capacity use cAdvisor or `kube_node_status_*`.

## Dashboards (Research Lab folder, provisioned)

| Dashboard | Coverage |
|---|---|
| 01 Lab overview | node coverage, CPU/mem, readiness, replicas, gateway probe, scrape failures, freshness |
| 02 Load generator | k6 rate, failures, latency, VUs, dropped iterations, outcomes, loadgen CPU/net |
| 03 Node resources | CPU, memory, disk, network, TCP retransmits, clock, FDs |
| 04 Kubernetes | replicas, pending, throttling, restarts, API, DNS, quota |
| 05 RED / dependency / SLO | service + edge rate/latency/errors, frontend p90, deadline, Envoy, Beyla |
| 06 Monitoring health | backend scrapes, Mimir series/ingestion, Loki bytes, Tempo spans, host disk/mem/CPU |
| 07 Logs | benchmark errors, all benchmark logs, collector logs |

Empty panels mean "the exporter/version/event is absent", not zero. Check the
collector before trusting a blank graph.