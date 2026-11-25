# CloudLab experimental Kubernetes platform

Clean-install revision of the final supplied cluster source (cc3bb8c462113e57c372f12d9c789616f9b27fe1). Target: three dedicated control-plane nodes, three benchmark workers, dedicated k6 and monitoring machines. No service mesh, Ingress NGINX, alternate monitoring profiles, application NodePort, or external API-server load balancer. Kubernetes Gateway API + Envoy Gateway + MetalLB uses a private application VIP. Kubespray's internal localhost HAProxy is retained.

This is an **offline-validated deployment candidate**. The new CloudLab allocation must be used to complete the acceptance work in `docs/VALIDATION.md`. Do not disable checks just to get a green deployment.

## Prepare the runner and private network

1. Review `versions.env`, the Kubespray overlay, `lab-setup/inventory/group_vars/all/postcluster.yaml`, and `lab-setup/roles/monitoring/defaults/main.yml`. Verify actual availability of every pinned image/chart and pinned upstream Train Ticket release. Capture image digests and exact Git SHAs. Use Python 3.12 on the runner. Use the existing `runner-init` playbook if needed; install SSH keys yourself and verify host fingerprints using CloudLab before enrollment. Never blindly accept a new key.
2. Clone Kubespray at the exact `KUBESPRAY_REF` from `versions.env` into `kubespray/` (ignored). Copy `.env.example` to `.env`, fill in new CloudLab hostnames and private IPs, and reserve an unused VIP on the shared private L2 network. Verify the SSH aliases and IPs independently on all eight hosts. `.env` contains topology only. Never source untrusted .env files.
3. Inspect `lsblk -f`, `lsblk -o NAME,TYPE,SIZE,MOUNTPOINTS,MODEL,SERIAL`, and `wipefs -n` on **mon1**. Export `MONITORING_DISK_DEVICE=/dev/disk/by-id/<verified-unused-device>`; do not guess a device. Set `MONITORING_ALLOW_FORMAT=true` only for a verified empty non-system disk authorized for formatting; otherwise leave false. Use a dedicated disk, not root or a shared NFS volume. Verify capacity for configured retention.
4. Export `GRAFANA_ADMIN_PASSWORD` (at least 16 URL-safe characters, not committed), `K6_SHA256` (official SHA-256 of the pinned Linux amd64 k6 archive), and `LAB_EXCLUSIVE_WRITER_CONFIRMED=true` only after verifying no other autoscalers are active. Keep secrets out of terminal history and evidence. Use encrypted `lab-setup/inventory/group_vars/all/zz-local.yaml` for secret database DSNs, probe overrides, and site-specific configuration; this file is ignored. Use Ansible Vault password file via `ANSIBLE_VAULT_PASSWORD_FILE` if needed. 
5. **Before Train Ticket installation:** fetch the exact `tt_repo_version` (currently 0.0.4), inspect its manifests and images, validate image compatibility, and fill `tt_probe_overrides` keyed `deployment/container` for containers lacking a verified upstream probe. The renderer fails closed on missing probes by default. Do not substitute `/` + accepted 404 or blindly disable the check. Set read-only database exporter DSNs in the local encrypted vars. Create monitoring DB accounts after DB initialization, before the `alloy` stage. No application resource or JVM overrides. If upstream mounts data via something other than PVC, inspect and implement a safe conversion in the renderer. Review the custom frontend config against the exact release.

## Installation

`./run-cluster-setup.sh --help` lists supported stages. The normal order is prepare, external monitoring, load generator, Kubespray, postcluster, kubectl, operator kubeconfig. For a new deployment, use these commands as an example of **staged installation**, not a blind all-in-one operation:

```bash
./run-cluster-setup.sh --start-from=preparing
# If postcluster stops at a required verification gate, correct source/local vars first.
# Resume a specific role after Kubespray is installed:
cd lab-setup
../.venv-lab/bin/ansible-playbook -i inventory/host.yaml playbook/postcluster.yaml -b --tags deps,control-plane,monitoring,resource-quota,train-ticket
# Initialize verified DB exporter users and credentials, then apply remaining roles.
../.venv-lab/bin/ansible-playbook -i inventory/host.yaml playbook/postcluster.yaml -b --tags deps,gateway,alloy,beyla,autoscaler-rbac
```

The initial all-in-one command may intentionally stop at a readiness, TLS, credentials, or validation gate. For controlled provisioning, use individual playbooks/tags and resume only after correcting the cause. Never re-run Kubespray or wipe a disk solely to fix an add-on. The wrapper enforces pinned Kubespray, prepares a local virtual environment and renders topology. The current repo uses alias-based SSH for remote access. Direct operator kubeconfig access or the SSH tunnel is administrative only, not an external API LB and not an autoscaler identity.

## Monitoring

Mimir, Loki, Tempo, Grafana, Pyroscope and the external Alloy gateway run on mon1. A node Alloy DaemonSet covers six Kubernetes nodes and tails local logs; a worker profiling DaemonSet collects CPU profiles. One in-cluster Alloy Deployment owns cluster metric discovery and Kubernetes events, plus read-only DB exporters. Beyla exports RED, dependency, process, runtime and TCP data to the external gateway every five seconds. Node Exporter on each external machine supplies host metrics. The Mimir rule contract is in `docs/TELEMETRY.md`. Grafana has 11 provisioned dashboards. Use SSH port forwarding to private Grafana/metrics endpoints if the runner cannot reach the LAN.

The acceptance workflow is `python3 tests/static_validate.py`, `python3 tests/test_live_validator.py`, then the component-specific validations and active workload check in `docs/VALIDATION.md`. The provided smoke.js is a telemetry connectivity test, **not** a scientific Train Ticket workload. Keep research load scripts separate and pin their revision; set `load_testing_src` explicitly if they are to be copied. No native HPA is installed automatically. `playbook/native-hpa.yaml` is a separate baseline and rejects missing CPU requests. The `autoscaling-research/replica-controller` ServiceAccount is intended for the research controller, not a running autoscaler. Only one replica writer per experiment.

