#!/usr/bin/env python3
"""Redact account identifiers from EKS evidence before it is committed.

Replaces the lab and security account IDs (read from the gitignored
infra/aws/lab.auto.tfvars) with <lab-account> and <security-account>, and any
operator IPv4 address given with --ip with <operator-ip>, in every text file
and gzipped file under the given run directories. Idempotent; prints what it
changed and fails if anything is left afterwards.

    scripts/redact-evidence.py --ip 203.0.113.7 evidence/eks-run2 evidence/eks-run2-rows
"""
from __future__ import annotations

import argparse
import gzip
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def account_ids() -> dict[str, str]:
    tfvars = (ROOT / "infra" / "aws" / "lab.auto.tfvars").read_text()
    out = {}
    for key, label in (("lab_account_id", "<lab-account>"), ("security_account_id", "<security-account>")):
        m = re.search(rf'^{key}\s*=\s*"(\d{{12}})"', tfvars, re.M)
        if not m:
            sys.exit(f"redact-evidence: {key} not found in lab.auto.tfvars")
        out[m.group(1)] = label
    return out


def redact(text: str, subs: dict[str, str]) -> str:
    for old, new in subs.items():
        text = text.replace(old, new)
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", action="append", default=[], help="operator IPv4 address to redact (repeatable)")
    ap.add_argument("runs", nargs="+", type=Path)
    args = ap.parse_args()
    subs = account_ids()
    subs.update({ip: "<operator-ip>" for ip in args.ip})
    changed, left = 0, []
    for run in args.runs:
        for f in sorted(p for p in run.rglob("*") if p.is_file()):
            gz = f.suffix == ".gz"
            raw = gzip.decompress(f.read_bytes()) if gz else f.read_bytes()
            try:
                text = raw.decode()
            except UnicodeDecodeError:
                continue
            new = redact(text, subs)
            if new != text:
                f.write_bytes(gzip.compress(new.encode(), mtime=0) if gz else new.encode())
                changed += 1
                print(f"  redacted {f}")
            if any(s in new for s in subs):
                left.append(str(f))
    print(f"redact-evidence: {changed} file(s) changed")
    if left:
        print("redact-evidence: still present in:", *left, sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
