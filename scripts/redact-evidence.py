#!/usr/bin/env python3
"""Redact account identifiers from EKS and AKS evidence before it is committed.

Replaces the lab and security account IDs (read from the gitignored
infra/aws/lab.auto.tfvars) with <lab-account> and <security-account>, AWS
access key IDs and IAM unique IDs (AKIA, ASIA, AROA, AIDA and the other
prefixes; they decode offline to the account ID) with <aws-key-id> and
<aws-unique-id>, and any operator IPv4 address given with --ip with
<operator-ip>, in every text file and gzipped file under the given run
directories. Idempotent; prints what it changed and fails if anything is left
afterwards.

    scripts/redact-evidence.py --ip 203.0.113.7 evidence/eks-run2 evidence/eks-run2-rows

With --cloud azure (AKS runs) it instead replaces the subscription ID (from
the gitignored infra/azure/lab.auto.tfvars) with <azure-subscription>, the
tenant ID (from `az account show`, or --tenant) with <azure-tenant>, and then
every other GUID-shaped value (principal, object and client IDs, which
identify the tenant's identities) with <azure-guid>. Storage SAS signatures
(sig=...) and bearer tokens (JWTs) are redacted on every cloud. A GUID left
in an AKS run fails the run, like an account ID left in an EKS run.

    scripts/redact-evidence.py --cloud azure --ip 203.0.113.7 evidence/aks-run1
"""
from __future__ import annotations

import argparse
import gzip
import re
import subprocess
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


def azure_ids(tenant: str | None) -> dict[str, str]:
    tfvars = (ROOT / "infra" / "azure" / "lab.auto.tfvars").read_text()
    m = re.search(r'^subscription_id\s*=\s*"([0-9a-fA-F-]{36})"', tfvars, re.M)
    if not m:
        sys.exit("redact-evidence: subscription_id not found in infra/azure/lab.auto.tfvars")
    if not tenant:
        p = subprocess.run(["az", "account", "show", "--query", "tenantId", "-o", "tsv"],
                           capture_output=True, text=True, timeout=60)
        tenant = p.stdout.strip()
    if not GUID.fullmatch(tenant or ""):
        sys.exit("redact-evidence: no tenant ID (sign in with az login, or pass --tenant)")
    return {m.group(1).lower(): "<azure-subscription>", tenant.lower(): "<azure-tenant>"}


# EKS audit events carry the caller's session key ID and role unique ID.
KEY_ID = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
UNIQUE_ID = re.compile(r"\bA(?:ROA|IDA|GPA|NPA|NVA|PKA|CCA|3T)[A-Z0-9]{16,17}\b")
# Azure: identities are GUIDs. Credentials that could ever reach evidence:
# storage SAS signatures and bearer tokens.
GUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
SAS_SIG = re.compile(r"([?&]sig=)[A-Za-z0-9%/+=]+")
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")


def redact(text: str, subs: dict[str, str], azure: bool = False) -> str:
    for old, new in subs.items():
        if azure:
            text = re.sub(re.escape(old), new, text, flags=re.I)
        else:
            text = text.replace(old, new)
    text = KEY_ID.sub("<aws-key-id>", text)
    text = UNIQUE_ID.sub("<aws-unique-id>", text)
    text = SAS_SIG.sub(r"\1<sas-signature>", text)
    text = JWT.sub("<jwt>", text)
    if azure:
        text = GUID.sub("<azure-guid>", text)
    return text


def leftover(text: str, subs: dict[str, str], azure: bool = False) -> bool:
    low = text.lower()
    return (any(s.lower() in low for s in subs) or bool(KEY_ID.search(text) or UNIQUE_ID.search(text))
            or bool(SAS_SIG.search(text)) or bool(JWT.search(text))
            or (azure and bool(GUID.search(text))))


def path_subs(repo: Path | None = None, home: Path | None = None) -> dict[str, str]:
    """Runner tracebacks carry the operator's checkout path. The repo root
    goes first (dicts keep order), then the home directory for anything
    outside the repo."""
    repo = repo or Path(__file__).resolve().parent.parent
    home = home or Path.home()
    return {str(repo): "<repo>", str(home): "<home>"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", action="append", default=[], help="operator IPv4 address to redact (repeatable)")
    ap.add_argument("--cloud", choices=["aws", "azure"], default="aws")
    ap.add_argument("--tenant", help="Azure tenant ID (default: az account show)")
    ap.add_argument("runs", nargs="+", type=Path)
    args = ap.parse_args()
    azure = args.cloud == "azure"
    subs = azure_ids(args.tenant) if azure else account_ids()
    subs.update({ip: "<operator-ip>" for ip in args.ip})
    subs.update(path_subs())
    changed, left = 0, []
    for run in args.runs:
        for f in sorted(p for p in run.rglob("*") if p.is_file()):
            gz = f.suffix == ".gz"
            raw = gzip.decompress(f.read_bytes()) if gz else f.read_bytes()
            try:
                text = raw.decode()
            except UnicodeDecodeError:
                continue
            new = redact(text, subs, azure)
            if new != text:
                f.write_bytes(gzip.compress(new.encode(), mtime=0) if gz else new.encode())
                changed += 1
                print(f"  redacted {f}")
            if leftover(new, subs, azure):
                left.append(str(f))
    print(f"redact-evidence: {changed} file(s) changed")
    if left:
        print("redact-evidence: still present in:", *left, sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
