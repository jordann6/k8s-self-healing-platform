# Evidence

`make demo` writes `evidence/<run-id>/`: `report.md`, `report.json`, the raw
prober samples for every row window (`<row>/prober-<profile>-<kind>.json.gz`)
and the R1 evidence bundle (`R1/r1-hardened/`). Only curated reference runs are
committed; ad-hoc runs stay local (see .gitignore).

## Reference runs (kind, phase 1)

`kind-run5` and `kind-run7` ran the same commit on fresh clusters. Both runs:
every baseline row showed impact; hardened A1, A2, A5, S1, S2, S3 and R1 healed;
hardened A3 missed on latency only (p95 758 ms and 697 ms against 300 ms, errors
0.32% and 0.31%, HPA 3 -> 8 and 3 -> 10). Overall FAIL, by design: the runner
fails on any hardened miss. See the README "what broke" section for why A3's
latency bar is not met on one shared laptop node.
