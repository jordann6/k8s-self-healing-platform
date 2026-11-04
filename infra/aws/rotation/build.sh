#!/usr/bin/env bash
# Stage the rotation package into infra/aws/build/rotation, which archive_file zips
# at plan time. Run before any plan: make eks-plan does, and CI runs
# it through platform-guardrails' build_command (no cloud credentials needed).
#
# Reproducible on any runner: the driver is hash-pinned, no bytecode is written,
# and the RDS CA bundle is checked against a pinned digest. A build that differed
# between the plan runner and the post-apply re-plan would read as a perpetual diff.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
STAGE="$HERE/../build/rotation"
CA_URL="https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem"
CA_SHA256="90a6f1a7978a1b1b096dca6c57511ca48d3b8cf052f8e704baf358f34ea2f21b"

rm -rf "$STAGE"
mkdir -p "$STAGE"

# Linux x86_64 wheels for the Lambda runtime, whatever machine runs the build.
python3 -m pip install --quiet --disable-pip-version-check --no-compile \
  --require-hashes --only-binary=:all: \
  --platform manylinux2014_x86_64 --implementation cp --python-version 3.13 \
  --target "$STAGE" -r "$HERE/requirements.txt"

cp "$HERE/lambda_function.py" "$STAGE/"

curl -sSf -o "$STAGE/rds-ca-bundle.pem" "$CA_URL"
actual="$(python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$STAGE/rds-ca-bundle.pem")"
if [[ "$actual" != "$CA_SHA256" ]]; then
  echo "rotation build: the RDS CA bundle changed upstream ($actual). Review it, then update CA_SHA256." >&2
  exit 1
fi

echo "rotation build: staged $(find "$STAGE" -type f | wc -l | tr -d ' ') files in infra/aws/build/rotation"
