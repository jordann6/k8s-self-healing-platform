#!/usr/bin/env python3
"""Decide whether the D1 WORM store may be destroyed yet (ADR-0022 decision 3).

The runner writes `.d1/<target>-store.json` after the backup, naming when the retained
version's retention ends. This refuses until that time plus a margin has passed, and it
never trusts a missing, unreadable or incomplete record: the answer is then "not yet".

usage: d1_store_guard.py <record.json> [margin-seconds]   exit 0 = may proceed, 1 = refuse
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

DEFAULT_MARGIN_S = 600


def parse_time(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def decide(record_path: Path, now: dt.datetime, margin_s: int = DEFAULT_MARGIN_S) -> tuple[bool, str]:
    if not record_path.is_file():
        return False, f"no store record at {record_path}: the runner has not recorded a retention end, so waiting is the only safe answer"
    try:
        record = json.loads(record_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        return False, f"the store record is unreadable ({error})"
    until = parse_time(record.get("retain_until"))
    if until is None:
        return False, "the store record has no usable retain_until"
    allowed = until + dt.timedelta(seconds=margin_s)
    if now < allowed:
        remaining = int((allowed - now).total_seconds())
        return False, (f"retention runs until {until.isoformat()} (plus a {margin_s}s margin): "
                       f"{remaining}s remain, so the store cannot be emptied yet")
    return True, f"retention ended {until.isoformat()}; the store may be emptied and destroyed"


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 2
    margin = int(argv[2]) if len(argv) == 3 else DEFAULT_MARGIN_S
    ok, message = decide(Path(argv[1]), dt.datetime.now(dt.timezone.utc), margin)
    print(("d1-store-guard: " if ok else "d1-store-guard: REFUSED, ") + message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
