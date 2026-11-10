#!/usr/bin/env bash
# render-aks-profiles.sh: prove both AKS profiles render, without an Azure
# subscription or the network, and that each carries the placement it is
# judged on (row A4) and the Key Vault store (row S1). If `make aks-images`
# has not written the generated component yet, a placeholder stands in for
# the duration of the render.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
gen="$root/gitops/targets/aks/generated"
placeholder=0
if [[ ! -f "$gen/kustomization.yaml" ]]; then
	placeholder=1
	mkdir -p "$gen"
	cat >"$gen/kustomization.yaml" <<'YAML'
apiVersion: kustomize.config.k8s.io/v1alpha1
kind: Component
images:
  - {name: selfheal-app, newName: registry.invalid/selfheal/app, newTag: placeholder-v1}
  - {name: selfheal-qworker, newName: registry.invalid/selfheal/qworker, newTag: placeholder}
  - {name: selfheal-q2, newName: registry.invalid/selfheal/q2, newTag: placeholder}
patches:
  - target: {kind: SecretStore, name: backend}
    patch: |-
      - op: add
        path: /spec/provider/azurekv/vaultUrl
        value: https://vault.invalid/
YAML
fi
out="$(mktemp -d)"
trap '[[ $placeholder -eq 1 ]] && rm -rf "$gen"; rm -rf "$out"' EXIT
for p in baseline hardened; do
	kubectl kustomize "$root/gitops/targets/aks/$p" >"$out/$p.yaml"
	kubectl kustomize "$root/gitops/targets/aks/q1/$p" >"$out/q1-$p.yaml"
	image="$(awk '/image: / {print $2}' "$out/q1-$p.yaml" | grep '/qworker:' | head -1)"
	[[ "$image" == */qworker:* ]] || {
		echo "render-aks-profiles: $p Q1 worker was not rewritten to ACR: ${image:-missing}" >&2
		exit 1
	}
	kubectl kustomize "$root/gitops/targets/aks/q2/$p" >"$out/q2-$p.yaml"
	image="$(awk '/image: / {print $2}' "$out/q2-$p.yaml" | grep '/q2:' | head -1 || true)"
	[[ "$image" == */q2:* ]] || {
		echo "render-aks-profiles: $p Q2 workload was not rewritten to ACR: ${image:-missing}" >&2
		exit 1
	}
	python3 - "$out/q2-$p.yaml" "$p" <<'PYQ2'
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
prof = sys.argv[2]
dep = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "q2-target")
sig = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "q2-signal")
pod, spod = dep["spec"]["template"]["spec"], sig["spec"]["template"]["spec"]
bad = []
if pod.get("nodeSelector") != {"selfheal.lab/q2": "true"}:
    bad.append("q2-target is not pinned to the labelled workload nodes")
if not any(t.get("key") == "selfheal.lab/workload" for t in pod.get("tolerations", [])):
    bad.append("q2-target lacks the workload toleration")
if spod.get("nodeSelector") or spod.get("tolerations"):
    bad.append("q2-signal must stay unpinned on the system pool")
so = next(d for d in docs if d["kind"] == "ScaledObject")["spec"]
want = {"failureThreshold": 1, "replicas": 3, "behavior": "static"}
if (prof == "hardened") != (so.get("fallback") == want) or (prof == "baseline" and so.get("fallback")):
    bad.append(f"{prof} fallback is {so.get('fallback')}")
if bad:
    sys.exit("render-aks-profiles: Q2 " + prof + ": " + "; ".join(bad))
PYQ2
done

python3 - "$out" <<'PY'
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("render-aks-profiles: PyYAML is required for the assertions (pip install pyyaml)")

out = Path(sys.argv[1])
fail = []


def objs(profile):
    return [d for d in yaml.safe_load_all((out / f"{profile}.yaml").read_text()) if d]


def one(docs, kind, name):
    m = [d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name]
    if len(m) != 1:
        fail.append(f"expected one {kind}/{name}, found {len(m)}")
        return {}
    return m[0]


def check(profile, cond, what):
    if not cond:
        fail.append(f"{profile}: {what}")


TOL = {"key": "selfheal.lab/workload", "operator": "Exists", "effect": "NoSchedule"}
for profile in ("baseline", "hardened"):
    docs = objs(profile)
    store = one(docs, "SecretStore", "backend").get("spec", {}).get("provider", {})
    kv = store.get("azurekv", {})
    check(profile, list(store) == ["azurekv"], f"store provider is {list(store)}, want only azurekv")
    check(profile, kv.get("authType") == "WorkloadIdentity", "store authType is not WorkloadIdentity")
    check(profile, kv.get("serviceAccountRef", {}).get("name") == "eso-backend", "store does not reference SA eso-backend")
    check(profile, str(kv.get("vaultUrl", "")).startswith("https://"), "store has no https vaultUrl")
    es = one(docs, "ExternalSecret", "partner-api-key").get("spec", {})
    ref = (es.get("data") or [{}])[0].get("remoteRef", {})
    check(profile, ref == {"key": "partner-api-key"}, f"ExternalSecret remoteRef is {ref}, want the plain secret only")

    pod = one(docs, "Rollout", "web").get("spec", {}).get("template", {})
    spec = pod.get("spec", {})
    check(profile, spec.get("nodeSelector") == {"selfheal.lab/nodes": "workload"}, "web not on the workload pool")
    check(profile, TOL in spec.get("tolerations", []), "web does not tolerate the workload taint")
    for dep in ("prober", "partner"):
        sel = one(docs, "Deployment", dep).get("spec", {}).get("template", {}).get("spec", {}).get("nodeSelector")
        check(profile, sel == {"selfheal.lab/nodes": "system"}, f"{dep} not on the system pool")
    image = (spec.get("containers") or [{}])[0].get("image", "")
    check(profile, not image.startswith("selfheal-app"), f"web image {image!r} was not rewritten to the registry")

    affinity = spec.get("affinity", {}).get("podAffinity", {}).get("requiredDuringSchedulingIgnoredDuringExecution", [])
    spread = spec.get("topologySpreadConstraints", [])
    tols = {t["key"]: t for t in spec.get("tolerations", [])}
    if profile == "baseline":
        check(profile, pod.get("metadata", {}).get("labels", {}).get("lab/placement") == "colocated",
              "baseline pods lack the lab/placement=colocated label")
        check(profile, len(affinity) == 1 and affinity[0].get("topologyKey") == "kubernetes.io/hostname"
              and affinity[0].get("labelSelector", {}).get("matchLabels") == {"app": "web", "lab/placement": "colocated"},
              f"baseline podAffinity is {affinity}, want one required hostname term on app=web,lab/placement=colocated")
        check(profile, not spread, "baseline must not spread")
        check(profile, "node.kubernetes.io/unreachable" not in tols, "baseline must keep the default 300s tolerations")
    else:
        check(profile, not affinity, "hardened must not colocate")
        check(profile, "lab/placement" not in pod.get("metadata", {}).get("labels", {}), "hardened pods carry lab/placement")
        want = {"maxSkew": 1, "topologyKey": "kubernetes.io/hostname", "whenUnsatisfiable": "DoNotSchedule",
                "minDomains": 3, "nodeTaintsPolicy": "Honor",
                "labelSelector": {"matchLabels": {"app": "web", "lab/pool": "serving"}}}
        check(profile, spread == [want], f"hardened spread is {spread}, want [{want}]")
        for key in ("node.kubernetes.io/not-ready", "node.kubernetes.io/unreachable"):
            t = tols.get(key, {})
            check(profile, t.get("effect") == "NoExecute" and t.get("tolerationSeconds") == 30,
                  f"hardened toleration {key} is {t}, want NoExecute for 30s")

    # Row A3: on AKS KEDA owns the HPA (named web, as the runner expects), and
    # the shared HPA must be gone or the two would fight over the Rollout.
    hpas = [d for d in docs if d["kind"] == "HorizontalPodAutoscaler"]
    sos = [d for d in docs if d["kind"] == "ScaledObject"]
    check(profile, not hpas, f"AKS renders {len(hpas)} HPA(s); KEDA creates it")
    if profile == "baseline":
        check(profile, not sos, "baseline must not autoscale")
    else:
        so = one(docs, "ScaledObject", "web").get("spec", {})
        check(profile, so.get("scaleTargetRef", {}).get("name") == "web", "ScaledObject does not target Rollout web")
        check(profile, so.get("advanced", {}).get("horizontalPodAutoscalerConfig", {}).get("name") == "web",
              "KEDA's HPA is not named web")
        check(profile, (so.get("minReplicaCount"), so.get("maxReplicaCount")) == (3, 10), "ScaledObject bounds are not 3..10")
        trig = (so.get("triggers") or [{}])[0]
        check(profile, trig.get("type") == "prometheus" and trig.get("metadata", {}).get("threshold") == "100",
              "ScaledObject trigger is not Prometheus CPU at 100")

if fail:
    sys.exit("render-aks-profiles: FAIL\n  " + "\n  ".join(fail))
PY
for p in baseline hardened; do
	echo "render-aks-profiles: $p ok"
done
