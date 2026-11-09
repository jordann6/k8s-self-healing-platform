#!/usr/bin/env python3
"""Pure CoreDNS rewrite used by the focused P1 registry lifecycle."""
from __future__ import annotations

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


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "patch-coredns":
        raise SystemExit("usage: p1_registry.py patch-coredns <IPv4>")
    document = json.load(sys.stdin)
    document["data"]["Corefile"] = patch_corefile(document["data"]["Corefile"], sys.argv[2])
    json.dump(document, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
