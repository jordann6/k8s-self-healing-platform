#!/usr/bin/env bash
# static-cert.sh <valid-seconds>: write Secret selfheal/web-tls with a
# certificate signed by the lab CA and valid for <valid-seconds> from now.
#
# This is the baseline's "someone ran openssl once" certificate for row S2:
# same CA and names as the cert-manager certificate, nobody renews it. The CA
# key is read into a temp directory for signing and deleted on exit.
set -euo pipefail

secs="${1:?usage: static-cert.sh <valid-seconds>}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

kubectl -n cert-manager get secret lab-ca -o jsonpath='{.data.tls\.crt}' | base64 -d >"$work/ca.crt"
kubectl -n cert-manager get secret lab-ca -o jsonpath='{.data.tls\.key}' | base64 -d >"$work/ca.key"

utc() { date -u -r "$1" +%Y%m%d%H%M%SZ 2>/dev/null || date -u -d "@$1" +%Y%m%d%H%M%SZ; }
now="$(date +%s)"
openssl ecparam -name prime256v1 -genkey -noout -out "$work/tls.key" 2>/dev/null
openssl req -new -key "$work/tls.key" -subj "/CN=web.selfheal.svc" -out "$work/tls.csr"
printf 'subjectAltName=DNS:web,DNS:web.selfheal,DNS:web.selfheal.svc,DNS:web.selfheal.svc.cluster.local\nextendedKeyUsage=serverAuth\n' >"$work/ext"
openssl x509 -req -in "$work/tls.csr" -CA "$work/ca.crt" -CAkey "$work/ca.key" -CAcreateserial \
	-not_before "$(utc $((now - 60)))" -not_after "$(utc $((now + secs)))" \
	-extfile "$work/ext" -out "$work/tls.crt" 2>/dev/null

kubectl -n selfheal create secret tls web-tls --cert="$work/tls.crt" --key="$work/tls.key" \
	--dry-run=client -o yaml |
	kubectl label --local -f - app.kubernetes.io/managed-by=static-cert.sh -o yaml |
	kubectl apply -f - >/dev/null
# ca.crt alongside, as cert-manager's secret carries it.
kubectl -n selfheal patch secret web-tls --type merge \
	-p "{\"data\":{\"ca.crt\":\"$(base64 <"$work/ca.crt" | tr -d '\n')\"}}" >/dev/null
openssl x509 -in "$work/tls.crt" -noout -serial -enddate
