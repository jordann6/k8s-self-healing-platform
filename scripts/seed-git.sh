#!/usr/bin/env bash
# seed-git.sh: force-push ./gitops as the single commit on main of the
# in-cluster Git server, so every run starts from the same desired state.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
port="$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')"
kubectl -n gitops rollout status deploy/gitserver --timeout=120s >/dev/null
kubectl -n gitops port-forward svc/gitserver "$port:9418" >/dev/null 2>&1 &
pf=$!
trap 'kill $pf 2>/dev/null || true; rm -rf "$work"' EXIT
for _ in $(seq 1 40); do nc -z 127.0.0.1 "$port" 2>/dev/null && break; sleep 0.25; done

cp -R "$root/gitops/." "$work/"
git -C "$work" init -q -b main
git -C "$work" -c user.name=selfheal-lab -c user.email=lab@selfheal.invalid add -A
git -C "$work" -c user.name=selfheal-lab -c user.email=lab@selfheal.invalid commit -q -m "seed from ./gitops"
git -C "$work" push -q --force "git://127.0.0.1:$port/lab.git" main
echo "seed-git: main = $(git -C "$work" rev-parse --short HEAD)"
