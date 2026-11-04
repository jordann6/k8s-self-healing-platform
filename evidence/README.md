# Evidence

`make demo` writes `evidence/<run-id>/`: `report.md`, `report.json`, the raw
prober samples for every row window (`<row>/prober-<profile>-<kind>.json.gz`)
and the R1 evidence bundle (`R1/r1-hardened/`). Only curated reference runs are
committed; ad-hoc runs stay local (see .gitignore).
