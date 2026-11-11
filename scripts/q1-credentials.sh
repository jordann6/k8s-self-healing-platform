#!/usr/bin/env bash
# Create Q1's in-cluster RabbitMQ credential without committing or printing it.
# The same helper is used by kind, EKS, and AKS so the queue contract does not
# differ by target. It is idempotent and preserves an existing session value.
set -euo pipefail

if kubectl -n selfheal get secret rabbitmq >/dev/null 2>&1; then
	exit 0
fi

pass="$(openssl rand -hex 16)"
printf 'username=lab\npassword=%s\nhost=amqp://lab:%s@rabbitmq.selfheal.svc.cluster.local:5672/\n' \
	"$pass" "$pass" |
	kubectl -n selfheal create secret generic rabbitmq --from-env-file=/dev/stdin >/dev/null
unset pass
