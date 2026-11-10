#!/usr/bin/env bash
# Render P1 profiles and pin the fail-closed signature contract offline.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

kubectl kustomize "$root/gitops/p1/baseline" >"$tmp/baseline.yaml"
kubectl kustomize "$root/gitops/p1/hardened" >"$tmp/hardened.yaml"

python3 - "$tmp/baseline.yaml" "$tmp/hardened.yaml" <<'PY'
import pathlib
import sys
import yaml


def documents(path):
    return [doc for doc in yaml.safe_load_all(pathlib.Path(path).read_text()) if doc]


baseline, hardened = map(documents, sys.argv[1:])
assert not any(doc.get("kind") == "ImageValidatingPolicy" for doc in baseline)
policies = [doc for doc in hardened if doc.get("kind") == "ImageValidatingPolicy"]
assert len(policies) == 1
policy = policies[0]
spec = policy["spec"]
assert policy["apiVersion"] == "policies.kyverno.io/v1beta1"
assert spec["failurePolicy"] == "Fail"
assert spec["validationActions"] == ["Deny"]
assert spec["validationConfigurations"] == {
    "mutateDigest": False, "required": True, "verifyDigest": True}
assert spec["webhookConfiguration"]["timeoutSeconds"] == 15
assert spec["evaluation"]["background"]["enabled"] is True
assert spec["matchConstraints"]["namespaceSelector"]["matchLabels"]["selfheal.lab/p1"] == "true"
assert spec["matchImageReferences"] == [{"glob": "selfheal-p1-registry:5000/p1/*"}]
attestor = spec["attestors"][0]["cosign"]
assert "expression" in attestor["key"] and "data" not in attestor["key"]
assert attestor["ctlog"]["insecureIgnoreTlog"] is True
assert attestor["ctlog"]["insecureIgnoreSCT"] is True
assert len(spec["validations"]) == 1
print("P1 profiles: baseline has no image policy; hardened is digest-pinned and fail closed")
PY

# ADR-0021: the cloud overlays keep the contract and change only the registry
# match and the credential provider. No registry name, no insecure registry.
for cloud in eks aks; do
	kubectl kustomize "$root/gitops/targets/$cloud/p1/baseline" >"$tmp/$cloud-baseline.yaml"
	kubectl kustomize "$root/gitops/targets/$cloud/p1/hardened" >"$tmp/$cloud-hardened.yaml"
done
python3 - "$tmp" <<'PY'
import pathlib
import sys
import yaml

tmp = pathlib.Path(sys.argv[1])
expected = {"eks": ("*.dkr.ecr.*.amazonaws.com/selfheal/p1-*", "amazon"),
            "aks": ("*.azurecr.io/selfheal/p1/*", "azure")}
for cloud, (glob, provider) in expected.items():
    baseline = [d for d in yaml.safe_load_all((tmp / f"{cloud}-baseline.yaml").read_text()) if d]
    hardened = [d for d in yaml.safe_load_all((tmp / f"{cloud}-hardened.yaml").read_text()) if d]
    assert not any(d.get("kind") == "ImageValidatingPolicy" for d in baseline), cloud
    policy = next(d for d in hardened if d.get("kind") == "ImageValidatingPolicy")
    spec = policy["spec"]
    assert spec["matchImageReferences"] == [{"glob": glob}], cloud
    assert spec["credentials"] == {"providers": [provider]}, cloud
    assert "allowInsecureRegistry" not in str(spec), cloud
    assert "selfheal-p1-registry" not in str(spec), cloud
    assert spec["failurePolicy"] == "Fail" and spec["validationActions"] == ["Deny"], cloud
    assert spec["validationConfigurations"] == {
        "mutateDigest": False, "required": True, "verifyDigest": True}, cloud
print("P1 cloud overlays: baseline has no policy; hardened matches the cloud registry with its provider")
PY
