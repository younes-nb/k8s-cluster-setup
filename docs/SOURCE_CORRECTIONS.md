# Source corrections applied during integration

The merged clean-install source was integrated verbatim (88 files) and the
deletions in `docs/REMOVED_FILES.txt` were applied. Two defects found while
reconciling the source against the actual pinned components were fixed in the
tracked source (not worked around at runtime):

1. **etcd metrics port** — `kubespray-overlay/inventory/lab/group_vars/all/etcd.yml`
   now sets `etcd_metrics_port: 2381`. Kubespray leaves this commented out by
   default, so etcd exposes metrics only on the TLS client port; the in-cluster
   Alloy node collector scrapes `127.0.0.1:2381` (see
   `roles/cluster_addons/templates/alloy-node.yaml.j2`) and would otherwise drop
   every etcd series.

2. **wipefs output flag** — `lab-setup/roles/monitoring/files/setup_monitoring_disk.sh`
   now uses `wipefs -n --noheadings -O TYPE` (capital `-O`). On util-linux ≥ 2.38
   (mon1 has 2.39.3) `-o` means *offset*, so the previous `-o TYPE` errored and
   produced empty output, silently bypassing the "device has no signatures"
   guard and weakening the no-blind-format protection.

3. **monitoring host address** — `lab-setup/inventory/host.yaml` sets mon1's
   `ansible_host` back to `{{ cloudlab_private_ips.monitoring }}` (10.10.1.2).
   The merged source used the literal `monitoring`, which does not resolve (the
   SSH alias is `mon1`), so the monitoring playbook could not connect.

4. **control-plane metrics bind address** —
   `kubespray-overlay/inventory/lab/group_vars/k8s_cluster/k8s-cluster.yml` now
   binds kube-controller-manager/kube-scheduler to `127.0.0.1`. These values are
   part of the shared kubeadm ClusterConfiguration; the merged `{{ ip }}` baked
   master1's IP into master2/3's static pods, which then crash-looped with
   `bind: cannot assign requested address` on 10.10.1.3:10257/10259. On an
   already-initialised cluster a Kubespray re-run updates the `kubeadm-config`
   ConfigMap but does not rewrite the existing `/etc/kubernetes/manifests/*`
   static pods, so the running manifests (bind-address AND probe host) were also
   corrected in place to `127.0.0.1` to match the fixed source.

5. **kubelet-serving CSR approver** — `kubelet_csr_approver_values` now sets
   `bypassDnsResolution: true`, `bypassHostnameCheck: true`,
   `providerRegex: ^(master|worker)[0-9]+$` and `providerIpPrefixes:
   [10.10.1.0/24]`. The kubelet CSR DNS SAN is the bare node name, which the
   chart's default DNS-resolution check cannot resolve, so every CSR was denied
   and cAdvisor HTTPS never got a cluster-CA-signed serving cert. The live
   approver deployment env was patched to match; new CSRs are Approved,Issued.

6. **frontend nginx upstream** — `dashboard-nginx.conf.j2` no longer proxies
   `/api/v1/avatar` to `ts-avatar-service:17001`. That Service is not part of
   Train Ticket 0.0.4, so nginx failed to start with `host not found in upstream
   "ts-avatar-service"` and `ts-ui-dashboard` crash-looped.

7. **rollout timeout** — `train_ticket.yaml` waits 1800s (was 900s) for the
   benchmark rollout. The 0.0.4 Spring Boot images take ~10-15 min cold to bind
   their ports, so 900s was too short on a first pull.

8. **idempotent Train Ticket clone** — the `ansible.builtin.git` task now sets
   `update: no`. Its update path crashes on an existing detached-HEAD tag clone
   (`'NoneType' object has no attribute 'startswith'`); a pinned tag never needs
   a git update, so reruns now clone only when the directory is absent.

9. **frontend nginx duplicate/lost route** — the first avatar fix accidentally
   duplicated `location /api/v1/adminbasicservice/adminbasic/configs` and dropped
   `location /api/v1/travelservice/`. The template now has exactly one of each;
   nginx starts and `ts-ui-dashboard` serves.

10. **rollout progress deadline** — `inject_selectors.py` sets
    `spec.progressDeadlineSeconds: 1800` on rendered Deployments. The default
    600s deadline failed the rollout while the 0.0.4 JVMs were still starting
    (~526s for ts-route-service). This is rollout metadata, not a resource change.

11. **secret check semantics** — `static_validate.py` now checks `git ls-files`
    for the forbidden secret paths instead of on-disk existence. Kubespray
    legitimately generates a gitignored `kubeadm_certificate_key.creds`; the
    check's intent is "not tracked", and that file is not tracked.

12. **Gateway API CRD ownership** — `kubespray-overlay/.../addons.yml` sets
    `gateway_api_enabled: false`. Envoy Gateway's chart installs its own CRD
    bundle plus the `safe-upgrades.gateway.networking.k8s.io` VAP; Kubespray's
    v1.4.1 standard-channel CRDs made that install fail ("Installing CRDs with
    version before v1.5.0 is prohibited"). The chart now owns the CRDs.

13. **Alloy operator component args** — `alloy.yaml.j2` no longer passes
    `allow_arbitrary_file_access` / `scrape_interval` / `scrape_timeout` to
    `prometheus.operator.servicemonitors|podmonitors`. Alloy v1.20.0 rejects
    those top-level attributes (the scrape settings live in a `scrape` block as
    `default_scrape_interval`/`default_scrape_timeout`), which crash-looped the
    cluster collector. The ServiceMonitor/PodMonitor resources already set 5s.

14. **Alloy ServiceMonitor file access** — `allow_arbitrary_file_access = true`
    is set on `prometheus.operator.servicemonitors` only (podmonitors does not
    support it). The kube-prometheus-stack ServiceMonitors reference local CA
    files; without it Alloy silently rejects them and apiserver/coredns metrics
    never arrive.

15. **River comment syntax** — the added comment uses `//`, not `#`. Alloy River
    has no `#` comments; a `#` line is an illegal character and aborts the
    initial config load.

16. **Alloy ServiceMonitor scrape timeout** — the `servicemonitors` component
    now sets a `scrape { default_scrape_interval = "5s"; default_scrape_timeout
    = "4s" }` block. The kube-prometheus-stack ServiceMonitors use interval 5s
    with no timeout, so Alloy's default 10s timeout exceeded the interval and it
    dropped the whole ServiceMonitor ("scrape timeout greater than scrape
    interval"), losing apiserver/coredns/kube-proxy metrics.

No other source changes were required to make the offline checks pass. Runtime
verification of image/chart/CRD behaviour remains part of the live acceptance in
`docs/VALIDATION.md`.

## Verified external pins (2026-11-25)

- Kubespray checkout `9751ea6` == `v2.32.0`.
- k6 `1.6.0` linux-amd64 SHA256 `365591ae4039b51b2843b4a117e2dde96fa20cc0c5df800a330790380eebb697`.
- Train Ticket tag `0.0.4` -> commit `97fe13cc7423654a60250b200c8cea4296c4e073`.
- MetalLB `v0.15.2`, Envoy Gateway `v1.6.2`, Gateway API `v1.4.1` manifests resolve (HTTP 200).
- kube-prometheus-stack `79.8.2` (appVersion v0.86.2) exists.
- Image tags exist on Docker Hub: mimir 3.2.1, loki 3.7.8, tempo 3.0.3,
  grafana 13.3.0-distroless, pyroscope 1.18.1, alloy v1.20.0, beyla 3.37.0.
- Cluster topology: loadgen 10.10.1.1, mon1 .2, masters .3-.5, workers .6-.8;
  VIP 10.10.1.50 free (0 ARP replies); mon1 `/dev/sdb` and `/dev/sdc` empty.