#!/usr/bin/env bash
# Render G1 profiles and pin the role-split contract offline (ADR-0025).
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

kubectl kustomize "$root/gitops/g1/baseline" >"$tmp/baseline.yaml"
kubectl kustomize "$root/gitops/g1/hardened" >"$tmp/hardened.yaml"

python3 - "$tmp/baseline.yaml" "$tmp/hardened.yaml" <<'PY'
import pathlib
import sys
import yaml


def documents(path):
    return [doc for doc in yaml.safe_load_all(pathlib.Path(path).read_text()) if doc]


def one(docs, kind):
    found = [d for d in docs if d.get("kind") == kind]
    assert len(found) == 1, f"{kind}: expected one, found {len(found)}"
    return found[0]


baseline, hardened = map(documents, sys.argv[1:])
for docs in (baseline, hardened):
    gateway = one(docs, "Gateway")
    assert gateway["metadata"]["namespace"] == "selfheal-g1-platform"
    route = one(docs, "HTTPRoute")
    assert route["metadata"]["namespace"] == "selfheal-g1"
    assert route["spec"]["parentRefs"][0]["namespace"] == "selfheal-g1-platform"
    proxy = one(docs, "EnvoyProxy")
    assert proxy["spec"]["provider"]["kubernetes"]["envoyService"]["type"] == "ClusterIP"
    assert one(docs, "GatewayClass")["spec"]["controllerName"] == "gateway.envoyproxy.io/gatewayclass-controller"

base_listener = one(baseline, "Gateway")["spec"]["listeners"][0]
assert base_listener["allowedRoutes"] == {"namespaces": {"from": "All"}}
assert "hostname" not in base_listener

hard_listener = one(hardened, "Gateway")["spec"]["listeners"][0]
assert hard_listener["hostname"] == "app.g1.lab"
namespaces = hard_listener["allowedRoutes"]["namespaces"]
assert namespaces["from"] == "Selector"
assert namespaces["selector"]["matchLabels"] == {"selfheal.lab/g1-routes": "allowed"}
assert hard_listener["allowedRoutes"]["kinds"] == [
    {"group": "gateway.networking.k8s.io", "kind": "HTTPRoute"}]
assert "LoadBalancer" not in pathlib.Path(sys.argv[2]).read_text()
print("G1 profiles: baseline listener admits any namespace and host; hardened binds one host to labelled namespaces")
PY
