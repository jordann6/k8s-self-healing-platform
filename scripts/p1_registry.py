#!/usr/bin/env python3
"""Pure helpers for the P1 registry fixtures: the CoreDNS rewrite (kind) and the invalid-signature tamper."""
from __future__ import annotations

import base64
import json
import re
import sys


MARKER = "# selfheal-p1-registry"


def patch_corefile(corefile: str, address: str) -> str:
    block = (
        f"    {MARKER}\n"
        "    hosts {\n"
        f"        {address} selfheal-p1-registry\n"
        "        fallthrough\n"
        "    }\n"
    )
    pattern = re.compile(
        rf"    {re.escape(MARKER)}\n    hosts \{{\n.*?    \}}\n", re.DOTALL
    )
    if pattern.search(corefile):
        return pattern.sub(block, corefile, count=1)
    anchor = "    kubernetes cluster.local"
    if anchor not in corefile:
        raise ValueError("CoreDNS Corefile lacks the Kubernetes plugin anchor")
    return corefile.replace(anchor, block + anchor, 1)


SIGNATURE_ANNOTATION = "dev.cosignproject.cosign/signature"


def tamper_manifest(raw: str) -> str:
    """Flip the last signature byte of a Cosign legacy signature manifest.

    The OCI manifest and the signed payload stay intact, so the result is a
    malformed cryptographic signature, not an absent or untrusted one."""
    manifest = json.loads(raw)
    layers = manifest.get("layers") or []
    if len(layers) != 1:
        raise ValueError("a Cosign legacy signature manifest has exactly one layer")
    annotations = layers[0].get("annotations") or {}
    signature = annotations.get(SIGNATURE_ANNOTATION)
    if not signature:
        raise ValueError("the layer carries no signature annotation")
    data = bytearray(base64.b64decode(signature, validate=True))
    if not data:
        raise ValueError("the signature is empty")
    data[-1] ^= 1
    annotations[SIGNATURE_ANNOTATION] = base64.b64encode(bytes(data)).decode()
    return json.dumps(manifest, separators=(",", ":"))


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "tamper":
        sys.stdout.write(tamper_manifest(sys.stdin.read()))
        return 0
    if len(sys.argv) != 3 or sys.argv[1] != "patch-coredns":
        raise SystemExit("usage: p1_registry.py patch-coredns <IPv4> | tamper")
    document = json.load(sys.stdin)
    document["data"]["Corefile"] = patch_corefile(document["data"]["Corefile"], sys.argv[2])
    json.dump(document, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
