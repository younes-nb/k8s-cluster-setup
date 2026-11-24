# Acceptance checks (live)

Do not treat the offline check as a successful deployment. Run these on the
trusted lab runner in a maintenance window and fix failures before any
performance trial.

## Offline

```bash
python3 tests/static_validate.py
```

Then install the pinned collections and syntax-check every playbook:

```bash
cd lab-setup
ansible-galaxy collection install -r collections/requirements.yml -p collections
for pb in preparing loadgen monitoring postcluster migrate-legacy native-hpa; do
  ansible-playbook -i inventory/host.yaml playbook/$pb.yaml --syntax-check
done
```

Then validate the real Helm templates (`helm template`, `kubectl apply
--dry-run=server` after CRDs exist) and the rendered River files (`alloy
validate`), and `docker compose config --quiet` on mon1.

## Topology and gateway

```bash
kubectl get nodes -o wide
kubectl get pods -A -o wide
kubectl get gatewayclass,gateway,httproute -A
kubectl get svc -A
kubectl get hpa -A
kubectl -n train-ticket get deploy,pods,pvc
```

Expect: six nodes Ready, benchmark pods only on workers, **no `istio-proxy`
container in any pod**, no benchmark HPA except an explicit baseline, Gateway
`Programmed`, HTTPRoute `Accepted`/`ResolvedRefs`, Envoy Service type
`LoadBalancer` with `allocateLoadBalancerNodePorts: false`, and
`curl -f http://10.10.1.50/` answering 200 from the loadgen.

Also verify the readiness probes against the *actual* images, and that a
container failure is detected.

## Collectors and databases

```bash
kubectl -n monitoring get deploy,ds,pods -o wide
kubectl -n monitoring logs deploy/alloy --tail=100
kubectl -n monitoring logs ds/beyla --tail=100
```

On mon1:

```bash
findmnt --mountpoint /var/lib/monitoring
cd /opt/monitoring && sudo docker compose ps
sudo docker compose logs --tail=100 alloy-gateway
sudo iptables -S DOCKER-USER
ss -lntp
```

Confirm Mimir/Loki/Tempo/Pyroscope/Grafana are healthy, the data dirs are
writable by the image UIDs, and that the new `alloy-gateway` and `pyroscope`
services start without disturbing the existing 105 GB of data.

## Live metric checks

```bash
python3 tests/check_live_metrics.py --mimir http://10.10.1.2:18080
```

It fails for missing, stale or non-finite required signals. Note which checks
are *expected* to report zero because of the mesh retirement (destination-side
Istio histograms) — those are documented limitations, not regressions.

Then open all seven dashboards in Grafana (folder **Research Lab**) under known
load and confirm the plots are non-empty and the missing-data states look right.

## Research acceptance

Run controlled load, burst, timeout, intentional-rejection, downstream-DB
failure and unschedulable-replica cases. Compare against independent k6
measurements at identical timestamps and windows. Confirm labels survive pod
recreation, measure telemetry age and decision-cycle latency, and never run two
autoscalers against the same Deployment.