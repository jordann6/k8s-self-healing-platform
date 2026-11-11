# ADR 0014: D1 backup, restore, and deletion resistance contract

Status: accepted before the first D1 live experiment on 2026-11-09.

## Context

D1 must prove recoverable data, not merely a healthy backup controller or a
successful Job. The proof needs an identified recovery point, authorized data
loss, a declared restore target, exact integrity checks, recovery measurements,
an invalid restore, complete telemetry, and deletion resistance with a stated
boundary.

The Azure landing zone restore drill is the closest sibling prior art. It
records the recovery point, deletes only its stateful namespace, restores into
a declared target, checks the recovered sequence inside PostgreSQL, and reports
lost writes and elapsed time. D1 keeps those evidence rules while using a
smaller local design.

Primary documentation reviewed on 2026-11-09:

- PostgreSQL 17 `pg_dump` and `pg_restore` documentation for custom archives,
  `--single-transaction`, and `--exit-on-error`
- Kubernetes CronJob documentation for scheduled backup Jobs and Job completion
- Kubernetes persistent-volume documentation for the `Delete` and `Retain`
  boundaries
- MinIO object-locking, object-deletion, and identity-access documentation for
  versioning, WORM retention, default deny, and `s3:DeleteObject`
- Current PostgreSQL Docker Hub image records and official MinIO release
  binaries with published SHA-256 checksums

## Decision

Use PostgreSQL 17.11 and a custom-format logical archive. A CronJob defines the
scheduled backup mechanism. The runner creates one Job from that exact template
so the experiment controls its start time. The Job uploads the archive and a
manifest to an S3-compatible MinIO bucket in a separate namespace.

The source data is exactly rows 1 through 100. Each row contains a deterministic
payload and its expected SHA-256 value. Integrity is the ordered canonical CSV
and its SHA-256 digest, plus the row count, minimum sequence, maximum sequence,
and contiguity. After backup completion, row 101 is committed as an intentional
post-backup sentinel.

The authorized loss deletes only the D1 source StatefulSet, its pod, and its
PVC. The MinIO namespace and its PVC are outside that deletion boundary. The
restore target is a separate, empty PostgreSQL StatefulSet in
`selfheal-d1-restore`.

MinIO creates the bucket with object locking and one-day default COMPLIANCE
retention. Its backup identity can put, get, list, and inspect versions, but has
no delete action. D1 must observe both an AccessDenied delete from that identity
and a rejected administrator attempt to delete the retained archive version.

The invalid restore truncates a downloaded archive. `pg_restore` uses one
transaction and exits on the first error. The target database must contain zero
application tables after the expected failure.

## Fixed thresholds and verdict contract

These thresholds were fixed before the first live experiment:

- The backup Job completes within 120 seconds.
- The restore Job completes within 120 seconds.
- Recovery time is at most 180 seconds from source deletion start to verified
  restored data.
- The recovery point contains exactly rows 1 through 100.
- The measured recovery point objective is exactly one intentionally committed
  row: sentinel row 101 must not be restored.
- The restored canonical digest must equal the fixed source digest.
- The backup identity delete attempt must fail with AccessDenied.
- The administrator version-specific delete attempt must fail because the
  object is under COMPLIANCE retention.
- The retained archive remains readable with the original checksum after both
  deletion attempts.
- The truncated archive restore must fail and leave zero application tables.

The hardened result is an error if backup identity, Job completion, archive
key, object version, archive checksum, manifest checksum, source maximum
sequence, restore completion, restore target, integrity result, negative
restore result, or deletion-resistance telemetry is missing. Missing telemetry
never becomes a pass.

The baseline has the same source data and authorized deletion but no backup
CronJob or recovery point. Impact requires the original data to be absent after
the source PVC is replaced and no restore to be available.

## Boundaries and limitations

- The protected boundary is the MinIO S3 API, its policy engine, and WORM object
  retention. A host administrator can delete kind node storage or the whole
  cluster, which is required for lab teardown and is outside the claim.
- MinIO is one local replica with one PVC. The proof does not cover site loss,
  replication, erasure coding, hardware failure, or cross-region recovery.
- The local endpoint uses cluster-internal HTTP. Transport encryption and
  external identity federation are deferred to cloud proof.
- PostgreSQL credentials, MinIO credentials, Kubernetes control-plane state,
  cluster-scoped resources, and application binaries are not backed up.
- `pg_dump` is application-consistent for the database snapshot. This proof is
  not physical backup, point-in-time recovery, WAL archiving, or a backup of the
  PostgreSQL server configuration.
- COMPLIANCE retention is one day only. The lab teardown removes the whole local
  storage boundary after evidence review.
