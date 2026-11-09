#!/usr/bin/env bash
# bootstrap.sh: everything outside GitOps, after the platform charts are up.
# Idempotent. FOCUSED_Q1=1, FOCUSED_Q2=1, FOCUSED_Q3=1, or FOCUSED_N1=1 creates only that
# capability's dependencies. The default path creates the complete lab.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
focused_q1="${FOCUSED_Q1:-0}"
focused_q2="${FOCUSED_Q2:-0}"
focused_q3="${FOCUSED_Q3:-0}"
focused_n1="${FOCUSED_N1:-0}"
focused_count=$((focused_q1 + focused_q2 + focused_q3 + focused_n1))
if ((focused_count > 1)); then
	echo "bootstrap: choose one focused capability" >&2
	exit 2
fi
kubectl apply -f "$root/bootstrap/namespaces.yaml" >/dev/null
kubectl apply -f "$root/bootstrap/gitserver.yaml" >/dev/null

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
if ((focused_count == 0 || focused_q1 == 1)) && ! kubectl -n selfheal get secret rabbitmq >/dev/null 2>&1; then
	pass="$(openssl rand -hex 16)"
	kubectl -n selfheal create secret generic rabbitmq \
		--from-literal=username=lab \
		--from-literal=password="$pass" \
		--from-literal=host="amqp://lab:${pass}@rabbitmq.selfheal.svc.cluster.local:5672/" >/dev/null
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
else
	kubectl apply -f "$root/bootstrap/root-app.yaml" >/dev/null
	echo "bootstrap: done (Argo CD root app -> apps/baseline)"
fi
