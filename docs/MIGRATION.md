# AURORA cluster migration — what this repo now deploys and what it deliberately drops

This is the target architecture after the merge. Nothing below has been applied
to the live cluster by simply re-running the full wrapper: the migration is a
documented, operator-approved sequence (see §5).

## 1. Target architecture

| | |
|---|---|
| Control plane | 3 dedicated nodes (`master1..3`), stacked etcd, untainted |
| Workers | 3 (`worker1..3`), labelled `lab.example.com/worker=true`, the only nodes that run benchmark pods |
| Load generator | 1 (`loadgen`), runs k6 from the repo, exports its own node metrics |
| Monitoring | 1 (`mon1`), Mimir + Loki + Tempo + Grafana + Pyroscope + an Alloy OTLP gateway, on `/dev/sdb` |
| Kubernetes | Kubespray v2.32.0, Kubernetes v1.36.4 |
| Application path | **Gateway API + Envoy Gateway + MetalLB**, private VIP `10.10.1.50`, **no NodePorts** |
| Ingress (removed) | Istio `istio-ingressgateway`, `istiod`, the in-namespace Gateway API proxy, ingress-nginx |
| Telemetry | Alloy (in-cluster scraper) → Mimir/Loki; Alloy node DaemonSet → node/host metrics; Beyla → OTLP → off-cluster Alloy gateway → Tempo/Mimir; Pyroscope for CPU profiles |

## 3. Deliberate removals

| Removed | Why |
|---|---|
| `istio.yaml` + the `istio-*` Helm releases | the mesh is retired; Gateway API + Envoy is the ingress |
| `ingress_gateway.yaml` (classic istio-ingressgateway) | replaced by `gateway.yaml` (MetalLB + Envoy Gateway) |
| `tasks/cert_manager.yaml`, `cert_issuer.yaml` are kept but the *ingress* they referenced is gone; the ACME solver targets the Envoy Gateway |
| `tasks/loki.yaml`, `mimir.yaml`, `tempo.yaml`, `monitoring_disk.yaml` | in-cluster stores retired; monitoring is off-cluster |
| `tasks/kubelet_reservations.yaml` **kept** (restored) | still required: allocatable headroom on every node |
| `roles/preparing_server/` (17 files) | broad sysctl/SSH/firewall overwrite role, never imported by `preparing.yaml`; OS prep is now four explicit tasks |
| `ts-avatar-service.yaml.j2`, `local-path-config-patch.json.j2`, `k6-dashboard.json.j2` | not in the pinned upstream, or superseded by the provisioned dashboards |
| `inventory/group_vars/all/vault.yaml`, `.vault.pass` | secrets must not be in git |
| `kubespray-overlay/.../kubeadm_certificate_key.creds` | same |
| `collections/ansible_collections/` | vendored tree removed; pinned `collections/requirements.yml` instead |

## 4. Preserved

- The 8-node topology and the `10.10.1.0/24` private addressing, including the `monitoring` alias (now a separate inventory group).
- Existing Mimir/Loki/Tempo/Grafana data on mon1 (`/var/lib/monitoring`, 105 GB, 1440 h retention). The role's chown is **non-recursive**, so existing files are never re-owned.
- TrainTicket's own resource requests/limits/JVM settings: the upstream manifests are rendered and applied unchanged (`inject_selectors.py`), and `restore_upstream.py` can restore them on a live cluster.
- `maxReplicas` semantics live in the explicit baseline playbook (`native-hpa.yaml`), never in the application role.
- Database PVCs are never deleted by any task.

## 5. Migration order (do not re-bootstrap)

```bash
# 0. stop k6 and every autoscaler; back up etcd, DB data, Grafana, monitoring config
export PATH="$HOME/.local/bin:$PATH"
export KUBECONFIG=$PWD/admin.conf

# 1. offline validation
python3 tests/static_validate.py

# 2. external monitoring first (additive: adds alloy-gateway + pyroscope)
cd lab-setup && ansible-playbook -i inventory/host.yaml playbook/monitoring.yaml -b

# 3. one-time disruptive migration (needs explicit approval)
ansible-playbook -i inventory/host.yaml playbook/migrate-legacy.yaml -b -e allow_live_migration=true

# 4. add-ons: MetalLB, Envoy Gateway, gateway, Alloy, Beyla, TrainTicket, quota
ansible-playbook -i inventory/host.yaml playbook/postcluster.yaml -b -e allow_live_migration=true

# 5. live validation
python3 ../tests/check_live_metrics.py --mimir http://10.10.1.2:18080
```

Kubespray changes (Gateway API CRDs, `kube_proxy_strict_arp`) must go through
Kubespray's own upgrade procedure, not a fresh `cluster.yml` run against a live
cluster.

## 6. Known limitations

- **No destination-side Istio histograms.** After the mesh retirement only `reporter="source"` series exist, so the old Istio-derived frontend p90 SLO is no longer computable. Latency objectives must come from the Envoy Gateway, Beyla (`http_server_request_duration_seconds`) or the client (k6). `tests/check_live_metrics.py` reports this as an explicit pass-with-zero rather than pretending data exists.
- **HTTPS never worked on this LAN.** Let's Encrypt cannot reach a private address, so the ACME certificate stays pending. Only port 80 is functional.
- **`mongodb_*` internal counters are absent** (no scrape target for the exporter).
- **No JVM/heap/GC metrics.** `jvm_*`, `tomcat_*`, `http_server_requests_*` are empty in this cluster.
- **The readiness probe the renderer generates is a connector check, not an application-semantic one.** Verify against the real images and supply `tt_probe_overrides` where the default cannot prove readiness.
- **Beyla's `application_*` metrics require kernel BTF/eBPF**; verify on the worker kernel before relying on them.
- **Monitoring is single-host, non-HA.**

## 7. Research workload scripts

The off-cluster Mimir/Loki ports are unchanged (`127.0.0.1:18080` / `:3100`), so
existing scripts keep working. Two changes are required:

1. `TARGET_URL` must point at the Envoy VIP (`http://10.10.1.50/`), not a NodePort.
2. Any query filtering `reporter="destination"` on Istio histograms must move to
the Envoy/Beyla equivalent; see `reports/stage7_28/exp13jul/` for the SLO
definition that the benchmark uses.