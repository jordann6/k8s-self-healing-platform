#!/usr/bin/env bash
# bootstrap.sh: everything outside GitOps, after the platform charts are up.
# Idempotent. FOCUSED_Q1=1 through FOCUSED_O1=1 creates only that
# capability's dependencies. The default path creates the complete lab.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
focused_q1="${FOCUSED_Q1:-0}"
focused_q2="${FOCUSED_Q2:-0}"
focused_q3="${FOCUSED_Q3:-0}"
focused_n1="${FOCUSED_N1:-0}"
focused_p1="${FOCUSED_P1:-0}"
focused_d1="${FOCUSED_D1:-0}"
focused_o1="${FOCUSED_O1:-0}"
focused_count=$((focused_q1 + focused_q2 + focused_q3 + focused_n1 + focused_p1 + focused_d1 + focused_o1))
if ((focused_count > 1)); then
	echo "bootstrap: choose one focused capability" >&2
	exit 2
fi
kubectl apply -f "$root/bootstrap/namespaces.yaml" >/dev/null
kubectl apply -f "$root/bootstrap/gitserver.yaml" >/dev/null

if ((focused_count == 0 || focused_o1 == 1)); then
	kubectl create namespace selfheal-o1 --dry-run=client -o yaml |
		kubectl label --local -f - pod-security.kubernetes.io/enforce=restricted -o yaml |
		kubectl apply -f - >/dev/null
	kubectl apply -f "$root/bootstrap/o1-runtime.yaml" >/dev/null
fi

if ((focused_count == 0 || focused_d1 == 1)); then
	for ns in selfheal-d1-source selfheal-d1-store selfheal-d1-restore; do
		kubectl create namespace "$ns" --dry-run=client -o yaml |
			kubectl label --local -f - pod-security.kubernetes.io/enforce=restricted -o yaml |
			kubectl apply -f - >/dev/null
	done
	if ! kubectl -n selfheal-d1-store get secret d1-minio-root >/dev/null 2>&1; then
		kubectl -n selfheal-d1-store create secret generic d1-minio-root \
			--from-literal=access-key=d1root \
			--from-literal=secret-key="$(openssl rand -hex 24)" >/dev/null
	fi
	if ! kubectl -n selfheal-d1-store get secret d1-backup-store >/dev/null 2>&1; then
		backup_secret="$(openssl rand -hex 24)"
		for ns in selfheal-d1-store selfheal-d1-source selfheal-d1-restore; do
			kubectl -n "$ns" create secret generic d1-backup-store \
				--from-literal=access-key=d1backup \
				--from-literal=secret-key="$backup_secret" >/dev/null
		done
	fi
	if ! kubectl -n selfheal-d1-source get secret d1-postgres >/dev/null 2>&1; then
		postgres_password="$(openssl rand -hex 24)"
		for ns in selfheal-d1-source selfheal-d1-restore; do
			kubectl -n "$ns" create secret generic d1-postgres \
				--from-literal=password="$postgres_password" >/dev/null
		done
	fi
fi

if [[ "$focused_p1" == "1" ]]; then
	key_dir="${P1_KEY_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/k8s-self-healing-platform/p1}"
	[[ -s "$key_dir/trusted.pub" ]] || {
		echo "bootstrap: P1 trusted public key is missing outside the repository" >&2
		exit 1
	}
	kubectl -n kyverno create configmap p1-cosign-trust-root \
		--from-file=cosign.pub="$key_dir/trusted.pub" --dry-run=client -o yaml |
		kubectl apply -f - >/dev/null
fi

if ((focused_count == 0)); then
	kubectl apply -f "$root/bootstrap/secret-backend.yaml" >/dev/null
	kubectl apply -f "$root/bootstrap/ca.yaml" >/dev/null
	kubectl -n cert-manager wait --for=condition=Ready certificate/lab-ca --timeout=120s >/dev/null

	if ! kubectl -n secret-backend get secret partner-api-key >/dev/null 2>&1; then
		kubectl -n secret-backend create secret generic partner-api-key \
			--from-literal=api-key="$(openssl rand -hex 24)" >/dev/null
	fi
	key="$(kubectl -n secret-backend get secret partner-api-key -o jsonpath='{.data.api-key}' | base64 -d)"
	kubectl -n selfheal create secret generic partner-keys --from-literal=current="$key" \
		--from-literal=previous= --dry-run=client -o yaml | kubectl apply -f - >/dev/null

	kubectl -n cert-manager get secret lab-ca -o jsonpath='{.data.ca\.crt}' | base64 -d |
		kubectl -n selfheal create configmap lab-ca --from-file=ca.crt=/dev/stdin --dry-run=client -o yaml |
		kubectl apply -f - >/dev/null

	kubectl -n selfheal get secret web-tls >/dev/null 2>&1 || "$root/scripts/static-cert.sh" 3600 >/dev/null
fi

# Row Q1's RabbitMQ broker credentials are generated and never committed. The
# fixed baseline workers declare the durable queues before the runner switches
# to the hardened profile, where KEDA can then scale the worker from zero.
if ((focused_count == 0 || focused_q1 == 1)); then
	"$root/scripts/q1-credentials.sh"
fi

"$root/scripts/seed-git.sh"
if [[ "$focused_q1" == "1" ]]; then
	kubectl apply -f "$root/gitops/apps/baseline/q1.yaml" >/dev/null
	echo "bootstrap: done (focused Q1 app -> q1/baseline)"
elif [[ "$focused_q2" == "1" ]]; then
	kubectl apply -f "$root/gitops/apps/baseline/q2.yaml" >/dev/null
	echo "bootstrap: done (focused Q2 app -> q2/baseline)"
elif [[ "$focused_q3" == "1" ]]; then
	kubectl apply -f "$root/gitops/apps/baseline/q3.yaml" >/dev/null
	echo "bootstrap: done (focused Q3 app -> q3/baseline)"
elif [[ "$focused_n1" == "1" ]]; then
	kubectl apply -f "$root/gitops/apps/baseline/n1.yaml" >/dev/null
	echo "bootstrap: done (focused N1 app -> n1/baseline)"
elif [[ "$focused_p1" == "1" ]]; then
	kubectl apply -f "$root/gitops/apps/baseline/p1.yaml" >/dev/null
	echo "bootstrap: done (focused P1 app -> p1/baseline)"
elif [[ "$focused_d1" == "1" ]]; then
	kubectl apply -f "$root/gitops/apps/baseline/d1.yaml" >/dev/null
	echo "bootstrap: done (focused D1 app -> d1/baseline)"
elif [[ "$focused_o1" == "1" ]]; then
	kubectl apply -f "$root/gitops/apps/baseline/o1.yaml" >/dev/null
	echo "bootstrap: done (focused O1 app -> o1/baseline)"
else
	kubectl apply -f "$root/bootstrap/root-app.yaml" >/dev/null
	echo "bootstrap: done (Argo CD root app -> apps/baseline)"
fi
