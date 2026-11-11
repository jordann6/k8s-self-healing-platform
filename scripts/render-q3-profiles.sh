#!/usr/bin/env bash
# render-q3-profiles.sh: pin Q3's recommendation-only profile difference.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT

for profile in baseline hardened; do
	kubectl kustomize "$root/gitops/q3/$profile" >"$out/$profile.yaml"
done

python3 - "$out" <<'PY'
import sys
from pathlib import Path

import yaml

root = Path(sys.argv[1])
fail = []


def objects(profile):
    return [doc for doc in yaml.safe_load_all((root / f"{profile}.yaml").read_text()) if doc]


def one(docs, kind, name):
    matches = [doc for doc in docs if doc["kind"] == kind and doc["metadata"]["name"] == name]
    if len(matches) != 1:
        fail.append(f"expected one {kind}/{name}, found {len(matches)}")
        return {}
    return matches[0]


baseline = objects("baseline")
hardened = objects("hardened")
for profile, docs in (("baseline", baseline), ("hardened", hardened)):
    target = one(docs, "Deployment", "q3-target")
    container = target.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [{}])[0]
    requests = container.get("resources", {}).get("requests", {})
    if requests != {"cpu": "50m", "memory": "32Mi"}:
        fail.append(f"{profile}: requests changed: {requests}")
    if any(doc.get("kind") == "HorizontalPodAutoscaler" for doc in docs):
        fail.append(f"{profile}: Q3 must not have an HPA")

baseline_vpa = [doc for doc in baseline if doc.get("kind") == "VerticalPodAutoscaler"]
if baseline_vpa:
    fail.append("baseline: VPA must be absent")
vpa = one(hardened, "VerticalPodAutoscaler", "q3-target").get("spec", {})
if vpa.get("updatePolicy", {}).get("updateMode") != "Off":
    fail.append("hardened: VPA updateMode must be Off")
policy = (vpa.get("resourcePolicy", {}).get("containerPolicies") or [{}])[0]
if policy.get("controlledValues") != "RequestsOnly" or set(policy.get("controlledResources", [])) != {"cpu", "memory"}:
    fail.append(f"hardened: unexpected resource policy: {policy}")

if fail:
    sys.exit("render-q3-profiles:\n  " + "\n  ".join(fail))
print("render-q3-profiles: baseline and hardened ok")
PY
