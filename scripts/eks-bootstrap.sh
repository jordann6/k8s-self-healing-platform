#!/usr/bin/env bash
# eks-bootstrap.sh: everything outside GitOps on EKS, after eks-platform and
# eks-images. The EKS counterpart of bootstrap.sh. Idempotent.
#
#   the lab CA and the prober's CA bundle      (same as kind)
#   the partner API key, now in Secrets Manager (row S1's backend)
#   the S1b database user, its secret and its rotation (eks-db-bootstrap.sh)
#   the in-cluster Git server from ECR, seeded from ./gitops
#   Argo CD's root app, pointed at targets/eks/apps/baseline
#
# No secret value touches a command line: generated values travel over stdin.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
: "${MGMT_PROFILE:?set MGMT_PROFILE}" "${LAB_PROFILE:?set LAB_PROFILE}" "${KUBECONFIG:?run through make}"
gen="$root/gitops/targets/eks/generated"
[[ -f "$gen/GITSERVER_IMAGE" ]] || { echo "eks-bootstrap: run make eks-images first" >&2; exit 1; }
region="$(AWS_PROFILE="$MGMT_PROFILE" terraform -chdir="$root/infra/aws" output -raw region)"
export AWS_PROFILE="$LAB_PROFILE" AWS_REGION="$region"

echo "eks-bootstrap: namespaces, CA, Git server"
kubectl kustomize "$root/bootstrap" | sed "s|image: selfheal-gitserver:dev|image: $(cat "$gen/GITSERVER_IMAGE")|" |
	kubectl apply -f - >/dev/null
"$root/scripts/q1-credentials.sh"
# The kind backend namespace and its reader are not used on EKS (Secrets
# Manager is the backend), but are harmless; the SecretStore no longer
# references them.
kubectl apply -f "$root/bootstrap/ca.yaml" >/dev/null
kubectl -n cert-manager wait --for=condition=Ready certificate/lab-ca --timeout=180s >/dev/null

echo "eks-bootstrap: partner API key in Secrets Manager"
secret="selfheal/partner-api-key"
if ! aws secretsmanager get-secret-value --secret-id "$secret" >/dev/null 2>&1; then
	python3 -I -c 'import json, secrets; print(json.dumps({"api-key": secrets.token_hex(24)}))' |
		aws secretsmanager put-secret-value --secret-id "$secret" --secret-string file:///dev/stdin >/dev/null
fi
key="$(aws secretsmanager get-secret-value --secret-id "$secret" --query SecretString --output text | jq -r '."api-key"')"
printf 'current=%s\nprevious=\n' "$key" |
	kubectl -n selfheal create secret generic partner-keys --from-env-file=/dev/stdin --dry-run=client -o yaml |
	kubectl apply -f - >/dev/null
unset key
kubectl -n cert-manager get secret lab-ca -o jsonpath='{.data.ca\.crt}' | base64 -d |
	kubectl -n selfheal create configmap lab-ca --from-file=ca.crt=/dev/stdin --dry-run=client -o yaml |
	kubectl apply -f - >/dev/null
kubectl -n selfheal get secret web-tls >/dev/null 2>&1 || "$root/scripts/static-cert.sh" 3600 >/dev/null

"$root/scripts/eks-db-bootstrap.sh"

echo "eks-bootstrap: O1 runtime objects (Secret, Lease, quarantine policy; Argo CD excludes them)"
kubectl apply -f "$root/bootstrap/o1-runtime.yaml" >/dev/null

echo "eks-bootstrap: P1 trust root (public key only)"
p1_keys="${P1_KEY_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/k8s-self-healing-platform/p1}"
if [[ -s "$p1_keys/trusted.pub" ]]; then
	kubectl -n kyverno create configmap p1-cosign-trust-root --from-file=cosign.pub="$p1_keys/trusted.pub" \
		--dry-run=client -o yaml | kubectl apply -f - >/dev/null
else
	echo "  no P1 key yet: run make eks-p1-images, then rerun make eks-bootstrap before row P1"
fi

echo "eks-bootstrap: Git"
kubectl -n gitops rollout status deploy/gitserver --timeout=180s >/dev/null
"$root/scripts/seed-git.sh"
sed 's|    path: apps/baseline|    path: targets/eks/apps/baseline|' "$root/bootstrap/root-app.yaml" | kubectl apply -f - >/dev/null
echo "eks-bootstrap: done (Argo CD root app -> targets/eks/apps/baseline)"
