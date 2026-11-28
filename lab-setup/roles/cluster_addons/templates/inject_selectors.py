"""Render the pinned upstream manifests without changing resources.

Reads the upstream YAML, drops the Jaeger/Zipkin demo stack, converts NodePort /
LoadBalancer Services to ClusterIP, injects the worker nodeSelector and the
service selector that upstream 0.0.4 omits, and generates a readiness probe only
where upstream has none.

Resource requests/limits are NEVER invented: a container without them upstream
stays without them here. ``TT_PROBES`` may supply a verified per-container
override.

The ``mongo`` / ``mysql`` tags are pinned from ``TT_MONGO_IMAGE`` /
``TT_MYSQL_IMAGE`` because the floating tags now resolve to major versions that
cannot open the existing data files.

``out`` must be a DIRECTORY (the caller passes
``.../ts-deployment-part1.yml`` as the last SOURCE; the output directory is the
value of ``TT_OUT_DIR`` when set, otherwise the parent of that source).
"""
import argparse
import copy
import json
import os
import pathlib

import yaml


def render(docs, overrides):
    for d in docs:
        if not d or d.get("metadata", {}).get("name") in {
                "jaeger", "jaeger-query", "jaeger-collector", "zipkin"}:
            continue
        kind = d.get("kind")
        name = d["metadata"]["name"]

        if kind == "Service" and d.get("spec", {}).get("type") in ("NodePort", "LoadBalancer"):
            d["spec"]["type"] = "ClusterIP"
            for port in d["spec"].get("ports", []):
                port.pop("nodePort", None)
            for key in ("externalTrafficPolicy", "externalIPs",
                        "healthCheckNodePort", "loadBalancerIP",
                        "allocateLoadBalancerNodePorts"):
                d["spec"].pop(key, None)

        if kind == "Deployment":
            spec = d["spec"]
            # The 0.0.4 Spring Boot images can take ~10 min cold to bind their
            # ports; the default 600s progress deadline fails the rollout on a
            # first pull. This is a rollout-metadata field, not a resource change.
            spec.setdefault("progressDeadlineSeconds", 1800)
            labels = spec["template"]["metadata"]["labels"]
            # Upstream 0.0.4 floats the datastore images on ``mongo`` / ``mysql``.
            # Docker Hub's ``mongo`` tag now resolves to 9.x, which refuses to
            # open data files written by 4.4. Pin the major version from
            # ``TT_MONGO_IMAGE`` / ``TT_MYSQL_IMAGE`` (defaults 4.4 / 8.0).
            for c in spec["template"]["spec"]["containers"]:
                if c.get("image") == "mongo":
                    c["image"] = os.environ.get("TT_MONGO_IMAGE", "mongo:4.4")
                elif c.get("image") == "mysql":
                    c["image"] = os.environ.get("TT_MYSQL_IMAGE", "mysql:8.0")
            if not spec.get("selector"):
                spec["selector"] = {"matchLabels": copy.deepcopy(labels)}
            pod = spec["template"]["spec"]
            pod.setdefault("nodeSelector", {})["lab.example.com/worker"] = "true"
            if name.endswith(("-mongo", "-mysql")):
                spec["replicas"] = 1
                spec["strategy"] = {"type": "Recreate"}
                path = "/data/db" if name.endswith("-mongo") else "/var/lib/mysql"
                claim = name + "-data"
                db = pod["containers"][0]
                mounts = db.setdefault("volumeMounts", [])
                existing = next((m for m in mounts if m.get("mountPath") == path), None)
                if existing is None:
                    mounts.append({"name": "lab-db-data", "mountPath": path})
                    pod.setdefault("volumes", []).append({"name": "lab-db-data", "persistentVolumeClaim": {"claimName": claim}})
                    yield {"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": {"name": claim}, "spec": {"accessModes": ["ReadWriteOnce"], "storageClassName": os.environ.get("TT_STORAGE_CLASS", "local-path"), "resources": {"requests": {"storage": os.environ.get("TT_DB_PVC_SIZE", "2Gi")}}}}
                elif not any(v.get("name") == existing["name"] and "persistentVolumeClaim" in v for v in pod.get("volumes", [])):
                    raise ValueError(f"{name}: upstream data mount is not a PVC; inspect before changing")
            for c in pod["containers"]:
                key = f"{name}/{c['name']}"
                if key in overrides:
                    probe = overrides[key]["readinessProbe"]
                    if not isinstance(probe, dict) or not any(k in probe for k in ("httpGet", "exec", "grpc", "tcpSocket")):
                        raise ValueError(f"{key}: invalid readinessProbe")
                    c["readinessProbe"] = probe
                    if "startupProbe" in overrides[key]:
                        c["startupProbe"] = overrides[key]["startupProbe"]
                elif os.environ.get("TT_REQUIRE_VERIFIED_PROBES", "true").lower() == "true":
                    raise ValueError(f"{key}: verified readinessProbe required even when upstream supplies a TCP probe; inspect pinned image and supply TT_PROBES")
                elif "readinessProbe" not in c:
                    ports = c.get("ports") or []
                    if not ports:
                        raise ValueError(
                            f"{key}: no container port; supply an explicit probe override")
                    port = ports[0]["containerPort"]
                    if name == "ts-ui-dashboard":
                        handler = {"httpGet": {"path": "/", "port": port}}
                    elif name.endswith(("-mongo", "-mysql", "-redis", "-rabbitmq")):
                        handler = {"tcpSocket": {"port": port}}
                    else:
                        # Proves the HTTP connector answered (including 401/403).
                        # This is NOT an application-semantic readiness guarantee;
                        # verify the real images and supply TT_PROBES where needed.
                        handler = {"exec": {"command": [
                            "/bin/sh", "-ec",
                            "code=$(curl -sS --max-time 5 -o /dev/null "
                            f"-w '%{{http_code}}' http://127.0.0.1:{port}/); "
                            'case "$code" in 2??|3??|401|403|404|405) exit 0;; *) exit 1;; esac']}}
                    c["readinessProbe"] = {**handler, "periodSeconds": 5,
                                           "timeoutSeconds": 5, "failureThreshold": 5}
                    c.setdefault("startupProbe", {**handler, "periodSeconds": 5,
                                                  "timeoutSeconds": 3,
                                                  "failureThreshold": 120})
        yield d


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True,
                        help="output directory for the rendered manifests")
    parser.add_argument("sources", nargs="+")
    args = parser.parse_args()
    overrides = json.loads(os.environ.get("TT_PROBES", "{}"))
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for src in args.sources:
        path = pathlib.Path(src)
        docs = list(render(yaml.safe_load_all(path.read_text()), overrides))
        (out / path.name).write_text(yaml.safe_dump_all(docs, sort_keys=False))


if __name__ == "__main__":
    main()

