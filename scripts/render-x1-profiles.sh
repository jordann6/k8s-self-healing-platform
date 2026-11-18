#!/usr/bin/env bash
# Render X1 profiles and pin the operator contract offline (ADR-0026).
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

kubectl kustomize "$root/gitops/x1/baseline" >"$tmp/baseline.yaml"
kubectl kustomize "$root/gitops/x1/hardened" >"$tmp/hardened.yaml"

python3 - "$tmp/baseline.yaml" "$tmp/hardened.yaml" <<'PY'
import pathlib
import sys
import yaml


def documents(path):
    return [doc for doc in yaml.safe_load_all(pathlib.Path(path).read_text()) if doc]


baseline, hardened = map(documents, sys.argv[1:])
kinds = lambda docs: {d["kind"] for d in docs}
for kind in ("CustomResourceDefinition", "Role", "RoleBinding"):
    assert kind not in kinds(baseline), f"baseline must not carry {kind}"
assert not any(d["metadata"]["name"] == "x1-controller" for d in baseline)
policy = next(d for d in baseline if d["kind"] == "NetworkPolicy")
assert policy["spec"]["podSelector"]["matchLabels"] == {"selfheal.lab/quarantined": "true"}

crd = next(d for d in hardened if d["kind"] == "CustomResourceDefinition")
assert crd["spec"]["group"] == "selfhealing.lab"
version = crd["spec"]["versions"][0]
assert version["name"] == "v1alpha1" and version["subresources"] == {"status": {}}
assert version["schema"]["openAPIV3Schema"]["properties"]["spec"]["required"] == ["podName", "evidenceRef"]

role = next(d for d in hardened if d["kind"] == "Role")
for rule in role["rules"]:
    assert "delete" not in rule["verbs"] and "*" not in rule["verbs"], rule
    assert "secrets" not in rule["resources"], rule
pods = [r for r in role["rules"] if "pods" in r["resources"]]
assert len(pods) == 1 and sorted(pods[0]["verbs"]) == ["get", "list", "patch"]
assert not any(d["kind"] == "ClusterRole" for d in hardened)

controller = next(d for d in hardened if d["kind"] == "Deployment" and d["metadata"]["name"] == "x1-controller")
assert controller["spec"]["template"]["metadata"]["labels"]["selfheal.lab/protected"] == "true"
print("X1 profiles: baseline has no CRD or controller; hardened adds the CRD, a delete-free Role, and a protected controller")
PY
