# ADR-0009: R2 responders re-check identity before acting, and dedup on the finding id

- **Status:** accepted
- **Date:** 2026-11-06

## Context

Both R2 responders turn one detector finding into one quarantined pod and node.
A review found two gaps in how they mutate:

1. **No identity precondition.** The AWS Lambda (`handler.py`) and the Azure
   worker (`worker.py`) patched the pod by name with a plain merge patch. Between
   `resolve` (which picks the pod) and the relabel there are several seconds of
   node operations. If the pod is replaced in that window — a crashed pod
   recreated with the same name and a new uid, or its node churned — the
   responder would quarantine the innocent replacement.

2. **No deduplication.** Nothing keyed the response on the finding. AWS
   EventBridge starts a new Step Functions execution per event, and the Azure
   Logic App's `run_id` is its own run name, not the Defender `SystemAlertId`. A
   re-delivered finding (GuardDuty re-emits on update; a queue message can be
   redelivered) would run the whole response again.

## Decision

1. **Re-read and refuse a replacement.** `resolve` now records the pod's
   `resourceVersion` and the node's `uid` alongside the pod uid it already kept.
   `isolate_pod` and `cordon` (AWS) and `respond` (Azure) re-read the live object
   immediately before patching and refuse if the uid no longer matches what
   `resolve` saw. The patch body carries the live `resourceVersion` (and the uid)
   so the API server enforces optimistic concurrency and rejects a patch raced by
   another writer. The precondition is recorded in the step output and the
   evidence manifest.

2. **Dedup on the finding / alert id.** The actions are already idempotent in
   effect (the same quarantine labels, the same session-revocation deny policy,
   and an evidence prefix keyed by finding id that a repeat simply overwrites), so
   a second AWS delivery re-applies the same state without compounding harm. The
   Azure worker adds an explicit guard: it keys a marker blob under
   `r2/by-alert/<alert id>` and, on a repeat of the same `SystemAlertId`, returns
   a `duplicate` result without patching anything. The dedup key is recorded in
   the isolation record.

## Alternatives rejected

| Option | Why not |
| ------ | ------- |
| Set the Step Functions execution name to the finding id from EventBridge | The EventBridge → Step Functions target cannot set the execution name from the event; dedup has to live in the workflow or the responder |
| Delete and recreate the pod instead of relabelling | The order is isolate → preserve → investigate; deleting destroys the evidence. Nothing in the responder deletes |
| Pin the patch to the resolve-time resourceVersion | Pods update status constantly, so that version is stale within seconds and every patch would 409. Re-reading just before the patch gives a current version and a fresh uid check |

## Consequences

A pod replaced mid-response now yields an explicit refusal in the evidence rather
than a mis-quarantine, which is the honest outcome and a good "what broke" story.
The Azure dedup adds one blob read and one write per real incident. The reports
now also carry `repo_commit` (`versions()` in `scenarios/run.py`), so every
result ties back to the exact code that produced it. New unit tests cover the
precondition and dedup paths in `test_handler.py` and `test_worker.py`.
