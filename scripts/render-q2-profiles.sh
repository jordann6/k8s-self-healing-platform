#!/usr/bin/env bash
# render-q2-profiles.sh: pin the Q2 profile difference and scaling bounds.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT

for profile in baseline hardened; do
	kubectl kustomize "$root/gitops/q2/$profile" >"$out/$profile.yaml"
done

python3 - "$out" <<'PY'
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("render-q2-profiles: PyYAML is required")

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


for profile in ("baseline", "hardened"):
    docs = objects(profile)
    scaled = one(docs, "ScaledObject", "q2-target").get("spec", {})
    if (scaled.get("minReplicaCount"), scaled.get("maxReplicaCount")) != (1, 4):
        fail.append(f"{profile}: replica bounds are not 1 to 4")
    triggers = scaled.get("triggers", [])
    if len(triggers) != 1 or triggers[0].get("type") != "metrics-api":
        fail.append(f"{profile}: expected one Metrics API trigger")
    elif triggers[0].get("metricType") != "Value" or triggers[0].get("metadata", {}).get("targetValue") != "1":
        fail.append(f"{profile}: Metrics API trigger is not a Value target of 1")
    target = one(docs, "Deployment", "q2-target")
    request = target.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [{}])[0].get("resources", {}).get("requests", {}).get("cpu")
    if request != "250m":
        fail.append(f"{profile}: target CPU request is {request}, expected 250m")
    env = {item.get("name"): item.get("value") for item in target.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [{}])[0].get("env", [])}
    if env.get("ACQUIRE_TIMEOUT_MS") != "150":
        fail.append(f"{profile}: work-slot acquisition timeout is {env.get('ACQUIRE_TIMEOUT_MS')}, expected 150")
    fallback = scaled.get("fallback")
    if profile == "baseline" and fallback is not None:
        fail.append("baseline: fallback must be absent")
    if profile == "hardened" and fallback != {"failureThreshold": 1, "replicas": 3, "behavior": "static"}:
        fail.append(f"hardened: fallback is {fallback}")

if fail:
    sys.exit("render-q2-profiles:\n  " + "\n  ".join(fail))
print("render-q2-profiles: baseline and hardened ok")
PY
