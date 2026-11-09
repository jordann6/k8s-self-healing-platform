#!/usr/bin/env bash
# Lifecycle for P1's exact local registry container and kind connectivity.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
name="selfheal-p1-registry"
image="registry:3.1.2"
host_port="5001"

up() {
	if docker inspect "$name" >/dev/null 2>&1; then
		docker start "$name" >/dev/null
	else
		docker run -d --name "$name" --hostname "$name" --restart=no \
			-p "127.0.0.1:${host_port}:5000" "$image" >/dev/null
	fi
}

configure() {
	local address node
	docker network inspect kind >/dev/null
	if ! docker inspect "$name" --format '{{json .NetworkSettings.Networks}}' | grep -q '"kind"'; then
		docker network connect --alias "$name" kind "$name"
	fi
	address="$(docker inspect "$name" --format '{{(index .NetworkSettings.Networks "kind").IPAddress}}')"
	[[ "$address" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
		echo "p1-registry: registry did not receive a kind-network IPv4 address" >&2
		exit 1
	}
	for node in $(kind get nodes --name selfheal); do
		docker exec "$node" mkdir -p "/etc/containerd/certs.d/${name}:5000"
		printf 'server = "http://%s:5000"\n\n[host."http://%s:5000"]\n  capabilities = ["pull", "resolve"]\n' \
			"$name" "$name" | docker exec -i "$node" sh -c \
			"tee '/etc/containerd/certs.d/${name}:5000/hosts.toml' >/dev/null"
	done
	kubectl -n kube-system get configmap coredns -o json |
		python3 "$root/scripts/p1_registry.py" patch-coredns "$address" |
		kubectl apply -f - >/dev/null
	kubectl -n kube-system rollout restart deployment/coredns >/dev/null
	kubectl -n kube-system rollout status deployment/coredns --timeout=120s >/dev/null
}

down() {
	if docker inspect "$name" >/dev/null 2>&1; then
		docker rm -f "$name" >/dev/null
	fi
}

case "${1:-}" in
	up) up ;;
	configure) configure ;;
	down) down ;;
	*) echo "usage: $0 up|configure|down" >&2; exit 2 ;;
esac
