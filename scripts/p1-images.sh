#!/usr/bin/env bash
# Build one artifact, publish it under five repositories, and create the exact
# trusted, unsigned, invalid, and untrusted signature states P1 judges.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
registry_host="localhost:5001"
registry_cluster="selfheal-p1-registry:5000"
cosign_image="ghcr.io/sigstore/cosign/cosign:v2.6.3"
oras_image="ghcr.io/oras-project/oras:v1.3.2"
key_dir="${P1_KEY_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/k8s-self-healing-platform/p1}"
generated="$root/.p1"

umask 077
mkdir -p "$key_dir" "$generated"

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
	docker run --rm --network kind --env-file "$env_file" -v "$key_dir:/keys:ro" \
		"$cosign_image" "$@"
}

cosign_verify() {
	docker run --rm --network kind -v "$key_dir:/keys:ro" \
		"$cosign_image" verify --allow-http-registry --allow-insecure-registry \
		--insecure-ignore-tlog "$@"
}

generate_key trusted
generate_key untrusted

docker build -q -t selfheal-p1:artifact "$root/app/p1" >/dev/null
for repository in trusted unsigned invalid untrusted recovery; do
	docker tag selfheal-p1:artifact "$registry_host/p1/${repository}:v1"
	docker push "$registry_host/p1/${repository}:v1" >/dev/null
done

repo_digest="$(docker inspect "$registry_host/p1/trusted:v1" --format '{{index .RepoDigests 0}}')"
digest="${repo_digest##*@}"
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || {
	echo "p1-images: could not resolve the pushed artifact digest" >&2
	exit 1
}

sign() {
	local key="$1" repository="$2"
	cosign "$key_dir/${key}.env" sign --yes --key "/keys/${key}.key" \
		--tlog-upload=false --new-bundle-format=false --allow-http-registry \
		--allow-insecure-registry \
		"$registry_cluster/p1/${repository}@${digest}" >/dev/null
}

sign trusted trusted
sign trusted recovery
sign untrusted untrusted

# Copy the trusted legacy signature tag to the invalid repository, then flip
# one signature byte while preserving the OCI manifest and signed payload. This
# is a malformed cryptographic signature, not an absent or untrusted signature.
signature_tag="${digest/:/-}.sig"
docker run --rm --network kind "$oras_image" copy \
	--from-plain-http --to-plain-http \
	"$registry_cluster/p1/trusted:${signature_tag}" \
	"$registry_cluster/p1/invalid:${signature_tag}" >/dev/null

python3 - "$registry_host" "$signature_tag" <<'PY'
import base64
import json
import sys
import urllib.request

registry, tag = sys.argv[1:]
url = f"http://{registry}/v2/p1/invalid/manifests/{tag}"
request = urllib.request.Request(
    url,
    headers={"Accept": "application/vnd.oci.image.manifest.v1+json"},
)
with urllib.request.urlopen(request, timeout=10) as response:
    manifest = json.load(response)
signature = manifest["layers"][0]["annotations"]["dev.cosignproject.cosign/signature"]
raw = bytearray(base64.b64decode(signature, validate=True))
raw[-1] ^= 1
manifest["layers"][0]["annotations"]["dev.cosignproject.cosign/signature"] = base64.b64encode(raw).decode()
body = json.dumps(manifest, separators=(",", ":")).encode()
request = urllib.request.Request(
    url,
    data=body,
    method="PUT",
    headers={"Content-Type": "application/vnd.oci.image.manifest.v1+json"},
)
with urllib.request.urlopen(request, timeout=10) as response:
    if response.status not in (200, 201):
        raise SystemExit(f"p1-images: registry rejected invalid-signature fixture: {response.status}")
PY

python3 - "$generated/images.json" "$registry_cluster" "$digest" <<'PY'
import json
import sys

path, registry, digest = sys.argv[1:]
repositories = ("trusted", "unsigned", "invalid", "untrusted", "recovery")
document = {
    "digest": digest,
    "tag_only": f"{registry}/p1/trusted:v1",
    "images": {name: f"{registry}/p1/{name}@{digest}" for name in repositories},
}
with open(path, "w", encoding="utf-8") as stream:
    json.dump(document, stream, indent=2)
    stream.write("\n")
PY

verify_case() {
	local name="$1" key="$2" repository="$3" output rc
	set +e
	output="$(cosign_verify --key "/keys/${key}.pub" \
		"$registry_cluster/p1/${repository}@${digest}" 2>&1)"
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

echo "p1-images: published and verified one artifact digest across five signature cases"
