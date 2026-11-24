# Kubernetes experimental lab

A single observability stack for a fixed eight-node CloudLab lab: three dedicated
control planes, three workers, one k6 generator and one external monitoring node.
Kubespray manages Kubernetes; Ansible manages the application and telemetry.
Gateway API + Envoy Gateway + MetalLB expose an application-only private VIP,
without NodePorts or an external API-server load balancer. Internal Kubespray
HAProxy remains. There is no Istio installation and no alternative monitoring
profile.

## Before deployment

1. Read [MIGRATION.md](docs/MIGRATION.md), [TELEMETRY.md](docs/TELEMETRY.md)
   and [VALIDATION.md](docs/VALIDATION.md). This is a statically checked
   candidate, not a live-tested release. **Do not re-bootstrap a working cluster**
   to update monitoring.
2. Copy `.env.example` to `.env`, fill the eight CloudLab hostnames/IPs and
   reserve a free private L2 `GATEWAY_PRIVATE_IP` (default suggestion
   `10.10.1.50`). Check `versions.env`, `lab-setup/inventory/group_vars/all/postcluster.yaml`
   and the monitoring/loadgen defaults against the live versions.
3. Ensure SSH aliases and verified known-host entries exist for all eight nodes.
   Set `GRAFANA_ADMIN_PASSWORD` (≥12 chars) for the monitoring role; rotate the
   old Grafana admin separately. Never commit a password or kubeconfig.
4. Supply `k6_sha256` — the official SHA256 of the pinned k6 archive. The role
   refuses to install without it.
5. Set `monitoring_disk_device` to a verified `/dev/disk/by-id/...` **only** if
   `/var/lib/monitoring` is not already a mountpoint. Formatting is opt-in and
   rejects existing signatures and non-empty mount directories.

## Use

- **Grafana:** `http://10.10.1.2:3000` (folder *Research Lab*, 7 dashboards,
  59 panels). Mimir/Loki/Tempo/Pyroscope are provisioned datasources.
- **Application:** `http://10.10.1.50/` over the experiment LAN (Envoy Gateway,
  no NodePort).
- **Load test:** from the loadgen, `source /opt/loadgen/k6.env; k6 run -o experimental-prometheus-rw /opt/loadgen/smoke.js`. That is a gateway smoke test, not the TrainTicket saga workload; supply `load_testing_src` to copy the research assets.
- **Native CPU HPA baseline:** `cd lab-setup && ansible-playbook playbook/native-hpa.yaml -b`, only after stopping every other controller. It fails if upstream CPU requests are missing; do not invent requests to force it.
- **Offline check:** `python3 tests/static_validate.py`
- **Live checks:** `python3 tests/check_live_metrics.py --mimir http://10.10.1.2:18080`

## Layout

```
docs/                 MIGRATION / TELEMETRY / VALIDATION / OFFLINE_VALIDATION / REMOVED_FILES
kubespray-overlay/    kubeadm/Kubespray inventory and host settings
lab-setup/            Ansible roles, inventory, playbooks
runner-init/          CloudLab runner bootstrap
tests/                offline + live acceptance checks
tools/                topology rendering and cluster IP generation
versions.env          pinned Kubespray ref and Ansible version
run-cluster-setup.sh  fresh-cluster wrapper (NOT for a live cluster)
```

Generated files, credentials and vendored collections are deliberately omitted.

## Fresh install vs migration

`run-cluster-setup.sh` is for a **clean** lab only. For an existing cluster use
the sequence in `docs/MIGRATION.md` — monitoring first, then the explicit
`migrate-legacy` play, then `postcluster`. The wrapper pins Kubespray and creates
isolated virtualenvs; it must not be re-run against a running cluster.