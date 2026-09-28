"""Inject spec.selector into upstream 0.0.4 manifests that omit it.

The FudanSELab k8s-deployment yamls predate mandatory selectors; modern
apiserver rejects them. Selector = pod template labels (all services use
`app: <name>`), which is exactly what the running cluster carries.
Pure function of the inputs: re-runs produce byte-identical output.
Usage: inject_selectors.py <src.yml>... <out_dir>
"""
import re
import sys
import os

import yaml

MONGO_PIN = os.environ.get("TT_MONGO_IMAGE", "mongo:4.4")


def main():
    *srcs, out_dir = sys.argv[1:]
    os.makedirs(out_dir, exist_ok=True)
    for src in srcs:
        with open(src) as f:
            docs = [d for d in yaml.safe_load_all(f) if d]
        n_fixed = 0
        for d in docs:
            if d.get("kind") == "Deployment":
                spec = d.setdefault("spec", {})
                if not spec.get("selector"):
                    labels = (spec.get("template", {})
                              .get("metadata", {}).get("labels"))
                    if not labels:
                        raise SystemExit(
                            "Deployment %s has no template labels to build a selector"
                            % d.get("metadata", {}).get("name"))
                    spec["selector"] = {"matchLabels": dict(labels)}
                    n_fixed += 1
                for c in (spec.get("template", {}).get("spec", {})
                          .get("containers", [])):
                    if c.get("image") == "mongo":
                        c["image"] = MONGO_PIN
                        n_fixed += 1
        dst = os.path.join(out_dir, os.path.basename(src))
        with open(dst, "w") as f:
            yaml.safe_dump_all(docs, f, default_flow_style=False)
        print("wrote %s (%d docs, %d selectors injected)"
              % (dst, len(docs), n_fixed), flush=True)


if __name__ == "__main__":
    main()
