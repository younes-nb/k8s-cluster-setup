"""Render the pinned upstream manifests without changing resources.

Reads the upstream YAML, drops the Jaeger/Zipkin demo stack, converts NodePort /
LoadBalancer Services to ClusterIP, injects the worker nodeSelector and the
service selector that upstream 0.0.4 omits, and generates a readiness probe only
where upstream has none.

Resource requests/limits are NEVER invented: a container without them upstream
stays without them here. ``TT_PROBES`` may supply a verified per-container
override.
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
            labels = spec["template"]["metadata"]["labels"]
            if not spec.get("selector"):
                spec["selector"] = {"matchLabels": copy.deepcopy(labels)}
            pod = spec["template"]["spec"]
            pod.setdefault("nodeSelector", {})["lab.example.com/worker"] = "true"
            for c in pod["containers"]:
                key = f"{name}/{c['name']}"
                if key in overrides:
                    c["readinessProbe"] = overrides[key]["readinessProbe"]
                    if "startupProbe" in overrides[key]:
                        c["startupProbe"] = overrides[key]["startupProbe"]
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
                            "code=$(curl -sS --max-time 2 -o /dev/null "
                            f"-w '%{{http_code}}' http://127.0.0.1:{port}/); "
                            "case \"$code\" in 2??|3??|401|403|404|405) exit 0;; *) exit 1;; esac"]}}
                    c["readinessProbe"] = {**handler, "periodSeconds": 5,
                                           "timeoutSeconds": 3, "failureThreshold": 3}
                    c.setdefault("startupProbe", {**handler, "periodSeconds": 5,
                                                  "timeoutSeconds": 3,
                                                  "failureThreshold": 120})
        yield d


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
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