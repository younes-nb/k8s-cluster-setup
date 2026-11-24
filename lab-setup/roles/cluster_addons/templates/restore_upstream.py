"""Restore resources/env from the rendered upstream manifests.

Used only with ``allow_live_migration=true``. It patches the live Deployments
back to the upstream resource declarations (including *absence* of resources)
and removes the legacy ``JAVA_TOOL_OPTIONS`` override, so a benchmark that was
tuned by hand can be returned to the pinned upstream values.

It never deletes a Deployment or a PVC, and it uses an optimistic-concurrency
test on resourceVersion so a simultaneous write cannot be clobbered.
"""
import json
import pathlib
import subprocess
import sys

import yaml

ns, kcfg, folder = sys.argv[1:4]
base = ["kubectl", "--kubeconfig", kcfg, "-n", ns]

for path in sorted(pathlib.Path(folder).glob("*.yml")):
    for d in yaml.safe_load_all(path.read_text()):
        if not d or d.get("kind") != "Deployment":
            continue
        name = d["metadata"]["name"]
        live = json.loads(subprocess.check_output(
            base + ["get", "deployment", name, "-o", "json"]))
        original = {c["name"]: c for c in d["spec"]["template"]["spec"]["containers"]}
        ops = []
        for i, c in enumerate(live["spec"]["template"]["spec"]["containers"]):
            if c["name"] not in original:
                continue
            wanted = original[c["name"]]
            prefix = f"/spec/template/spec/containers/{i}"
            if c.get("resources", {}) != wanted.get("resources", {}):
                ops.append({"op": "add", "path": prefix + "/resources",
                            "value": wanted.get("resources", {})})
            old_env = c.get("env", [])
            upstream_env = {e["name"]: e for e in wanted.get("env", [])}
            fixed = [e for e in old_env if e["name"] != "JAVA_TOOL_OPTIONS"]
            if "JAVA_TOOL_OPTIONS" in upstream_env:
                fixed.append(upstream_env["JAVA_TOOL_OPTIONS"])
            if old_env != fixed:
                ops.append({"op": "add", "path": prefix + "/env", "value": fixed})
            if (name in ("ts-user-service", "ts-ui-dashboard")
                    and "livenessProbe" in c and "livenessProbe" not in wanted):
                ops.append({"op": "remove", "path": prefix + "/livenessProbe"})
        if ops:
            ops.insert(0, {"op": "test", "path": "/metadata/resourceVersion",
                           "value": live["metadata"]["resourceVersion"]})
            subprocess.run(base + ["patch", "deployment", name,
                                   "--type=json", "-p", json.dumps(ops)], check=True)