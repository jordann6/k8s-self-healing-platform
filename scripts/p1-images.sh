#!/usr/bin/env bash
# Build one artifact, publish it under five repositories, and create the exact
# trusted, unsigned, invalid, and untrusted signature states P1 judges.
#
#   scripts/p1-images.sh [kind|eks|aks]     (or P1_TARGET=...; default kind)
#
# kind: a local plain-HTTP registry (ADR-0013). eks and aks: ECR and ACR over
# TLS (ADR-0021). Cloud tags are immutable in ECR, so every step first asks
# whether its tag already exists and skips it, which makes a rerun inside a
# session safe. Registry tokens reach cosign and oras through a temporary Docker
# config in a 0700 directory, never a command line.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
target="${P1_TARGET:-${1:-kind}}"
cosign_image="ghcr.io/sigstore/cosign/cosign:v2.6.3"
oras_image="ghcr.io/oras-project/oras:v1.3.2"
key_dir="${P1_KEY_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/k8s-self-healing-platform/p1}"
cases=(trusted unsigned invalid untrusted recovery)

case "$target" in
kind) generated="$root/.p1" ;;
eks | aks) generated="$root/.p1/$target" ;;
*)
	echo "usage: $0 [kind|eks|aks]" >&2
	exit 2
	;;
esac

umask 077
mkdir -p "$key_dir" "$generated/work"
cfg="$(mktemp -d)"
trap 'rm -rf "$cfg"' EXIT
chmod 700 "$cfg"

# --- per-target registry layout ------------------------------------------------
docker_net=()
tls_flags=()      # cosign registry flags
oras_http=()      # oras flags for a plain-HTTP registry
oras_copy_http=()
case "$target" in
kind)
	docker_net=(--network kind)
	tls_flags=(--allow-http-registry --allow-insecure-registry)
	oras_http=(--plain-http)
	oras_copy_http=(--from-plain-http --to-plain-http)
	push_ref() { printf 'localhost:5001/p1/%s' "$1"; }
	cluster_ref() { printf 'selfheal-p1-registry:5000/p1/%s' "$1"; }
	printf '{}' >"$cfg/config.json"
	;;
eks)
	: "${MGMT_PROFILE:?set MGMT_PROFILE}" "${LAB_PROFILE:?set LAB_PROFILE}"
	repos="$(AWS_PROFILE="$MGMT_PROFILE" terraform -chdir="$root/infra/aws" output -json ecr_repositories)"
	region="$(AWS_PROFILE="$MGMT_PROFILE" terraform -chdir="$root/infra/aws" output -raw region)"
	push_ref() { jq -r --arg k "p1-$1" '.[$k]' <<<"$repos"; }
	cluster_ref() { push_ref "$1"; }
	registry_host="$(cluster_ref trusted)"
	registry_host="${registry_host%%/*}"
	reg_user="AWS"
	reg_token="$(AWS_PROFILE="$LAB_PROFILE" AWS_REGION="$region" aws ecr get-login-password)"
	;;
aks)
	registry_host="$(terraform -chdir="$root/infra/azure" output -raw acr_login_server)"
	[[ "$registry_host" == *.azurecr.io ]] || {
		echo "p1-images: unexpected registry '$registry_host'; has the apply run?" >&2
		exit 1
	}
	push_ref() { printf '%s/selfheal/p1/%s' "$registry_host" "$1"; }
	cluster_ref() { push_ref "$1"; }
	reg_user="00000000-0000-0000-0000-000000000000"
	reg_token="$(az acr login --name "${registry_host%%.*}" --expose-token --output tsv --query accessToken)"
	;;
esac

if [[ "$target" != kind ]]; then
	[[ -n "$reg_token" ]] || {
		echo "p1-images: no registry token" >&2
		exit 1
	}
	printf '%s' "$reg_token" | docker login --username "$reg_user" --password-stdin "$registry_host" >/dev/null
	printf '{"auths":{"%s":{"auth":"%s"}}}' "$registry_host" \
		"$(printf '%s:%s' "$reg_user" "$reg_token" | base64 | tr -d '\n')" >"$cfg/config.json"
	unset reg_token
fi

generate_key() {
	local prefix="$1" password
	if [[ -s "$key_dir/${prefix}.key" && -s "$key_dir/${prefix}.pub" && -s "$key_dir/${prefix}.env" ]]; then
		return
	fi
	password="$(openssl rand -hex 24)"
	printf 'COSIGN_PASSWORD=%s\n' "$password" >"$key_dir/${prefix}.env"
	docker run --rm --user "$(id -u):$(id -g)" -v "$key_dir:/keys" -w /keys \
		--env-file "$key_dir/${prefix}.env" "$cosign_image" generate-key-pair \
		--output-key-prefix "$prefix" >/dev/null
}

cosign() {
	local env_file="$1"
	shift
	docker run --rm ${docker_net[@]+"${docker_net[@]}"} --env-file "$env_file" -e DOCKER_CONFIG=/dockercfg \
		-v "$cfg:/dockercfg:ro" -v "$key_dir:/keys:ro" "$cosign_image" "$@"
}

cosign_verify() {
	docker run --rm ${docker_net[@]+"${docker_net[@]}"} -e DOCKER_CONFIG=/dockercfg -v "$cfg:/dockercfg:ro" \
		-v "$key_dir:/keys:ro" "$cosign_image" verify ${tls_flags[@]+"${tls_flags[@]}"} --insecure-ignore-tlog "$@"
}

oras() {
	docker run --rm ${docker_net[@]+"${docker_net[@]}"} -e DOCKER_CONFIG=/dockercfg -v "$cfg:/dockercfg:ro" \
		-v "$generated/work:/work" "$oras_image" "$@"
}

# manifest_digest <reference>: the digest the registry holds for it, or nothing.
manifest_digest() {
	oras manifest fetch --descriptor ${oras_http[@]+"${oras_http[@]}"} "$1" 2>/dev/null | jq -r '.digest // empty'
}

generate_key trusted
generate_key untrusted

# --- one artifact, five repositories -------------------------------------------
if [[ "$target" == kind ]]; then
	docker build -q -t selfheal-p1:artifact "$root/app/p1" >/dev/null
	for c in "${cases[@]}"; do
		docker tag selfheal-p1:artifact "$(push_ref "$c"):v1"
		docker push "$(push_ref "$c"):v1" >/dev/null
	done
else
	# linux/amd64 for the cloud nodes. Build and push once, then copy the manifest
	# so every repository holds exactly the same digest.
	if [[ -z "$(manifest_digest "$(cluster_ref trusted):v1")" ]]; then
		docker buildx build --platform linux/amd64 --provenance=false -q \
			-t "$(cluster_ref trusted):v1" --push "$root/app/p1" >/dev/null
	else
		echo "  trusted:v1 exists"
	fi
	for c in "${cases[@]}"; do
		[[ "$c" == trusted ]] && continue
		if [[ -z "$(manifest_digest "$(cluster_ref "$c"):v1")" ]]; then
			oras copy "$(cluster_ref trusted):v1" "$(cluster_ref "$c"):v1" >/dev/null
		fi
	done
fi

digest="$(manifest_digest "$(cluster_ref trusted):v1")"
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || {
	echo "p1-images: could not resolve the pushed artifact digest" >&2
	exit 1
}
for c in "${cases[@]}"; do
	[[ "$(manifest_digest "$(cluster_ref "$c"):v1")" == "$digest" ]] || {
		echo "p1-images: repository $c does not hold $digest; empty the repositories and rerun" >&2
		exit 1
	}
done

signature_tag="${digest/:/-}.sig"

sign() {
	local key="$1" repository="$2"
	if [[ -n "$(manifest_digest "$(cluster_ref "$repository"):${signature_tag}")" ]]; then
		echo "  $repository signature exists"
		return
	fi
	cosign "$key_dir/${key}.env" sign --yes --key "/keys/${key}.key" \
		--tlog-upload=false --new-bundle-format=false ${tls_flags[@]+"${tls_flags[@]}"} \
		"$(cluster_ref "$repository")@${digest}" >/dev/null
}

sign trusted trusted
sign trusted recovery
sign untrusted untrusted

# Copy the trusted legacy signature to the invalid repository under a temporary
# tag, then write the same manifest with one signature byte flipped to the real
# `.sig` tag. The `.sig` tag is written once, so ECR's immutable tags are fine.
# This is a malformed cryptographic signature, not an absent or untrusted one.
if [[ -z "$(manifest_digest "$(cluster_ref invalid):${signature_tag}")" ]]; then
	oras copy ${oras_copy_http[@]+"${oras_copy_http[@]}"} \
		"$(cluster_ref trusted):${signature_tag}" "$(cluster_ref invalid):copy-source" >/dev/null
	oras manifest fetch ${oras_http[@]+"${oras_http[@]}"} "$(cluster_ref invalid):copy-source" |
		python3 "$root/scripts/p1_registry.py" tamper >"$generated/work/tampered.json"
	oras manifest push ${oras_http[@]+"${oras_http[@]}"} --media-type application/vnd.oci.image.manifest.v1+json \
		"$(cluster_ref invalid):${signature_tag}" /work/tampered.json >/dev/null
else
	echo "  invalid signature exists"
fi

python3 - "$generated/images.json" "$digest" "$(cluster_ref trusted)" \
	"$(cluster_ref unsigned)" "$(cluster_ref invalid)" "$(cluster_ref untrusted)" \
	"$(cluster_ref recovery)" <<'PY'
import json
import sys

path, digest, trusted, unsigned, invalid, untrusted, recovery = sys.argv[1:]
refs = {"trusted": trusted, "unsigned": unsigned, "invalid": invalid,
        "untrusted": untrusted, "recovery": recovery}
document = {
    "digest": digest,
    "tag_only": f"{trusted}:v1",
    "images": {name: f"{ref}@{digest}" for name, ref in refs.items()},
}
with open(path, "w", encoding="utf-8") as stream:
    json.dump(document, stream, indent=2)
    stream.write("\n")
PY

verify_case() {
	local name="$1" key="$2" repository="$3" output rc
	set +e
	output="$(cosign_verify --key "/keys/${key}.pub" \
		"$(cluster_ref "$repository")@${digest}" 2>&1)"
	rc=$?
	set -e
	printf '%s\n' "$output" >"$generated/verify-${name}.txt"
	printf '%s' "$rc"
}

trusted_rc="$(verify_case trusted trusted trusted)"
unsigned_rc="$(verify_case unsigned trusted unsigned)"
invalid_rc="$(verify_case invalid trusted invalid)"
untrusted_trusted_rc="$(verify_case untrusted-trusted trusted untrusted)"
untrusted_own_rc="$(verify_case untrusted-own untrusted untrusted)"

python3 - "$generated/signatures.json" "$trusted_rc" "$unsigned_rc" \
	"$invalid_rc" "$untrusted_trusted_rc" "$untrusted_own_rc" <<'PY'
import json
import sys

path = sys.argv[1]
codes = [int(value) for value in sys.argv[2:]]
document = {
    "one_digest": True,
    "trusted_valid": codes[0] == 0,
    "unsigned_absent": codes[1] != 0,
    "invalid_claim_rejected": codes[2] != 0,
    "untrusted_rejected_by_trust_root": codes[3] != 0,
    "untrusted_valid_with_own_key": codes[4] == 0,
    "verification_returncodes": {
        "trusted_with_trusted_key": codes[0],
        "unsigned_with_trusted_key": codes[1],
        "invalid_with_trusted_key": codes[2],
        "untrusted_with_trusted_key": codes[3],
        "untrusted_with_own_key": codes[4],
    },
}
with open(path, "w", encoding="utf-8") as stream:
    json.dump(document, stream, indent=2)
    stream.write("\n")
if not all(document[name] for name in (
    "one_digest", "trusted_valid", "unsigned_absent", "invalid_claim_rejected",
    "untrusted_rejected_by_trust_root", "untrusted_valid_with_own_key",
)):
    raise SystemExit("p1-images: signature-state preflight failed")
PY

echo "p1-images ($target): published and verified one artifact digest across five signature cases"
