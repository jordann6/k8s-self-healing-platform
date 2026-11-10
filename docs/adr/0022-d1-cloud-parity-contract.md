# ADR 0022: D1 cloud parity contract

Status: ACCEPTED 2026-11-10 by Jordan (the "do what's best, follow the initial
plan" instruction covers the build). Source is built (unit tests and gates
green); nothing is applied to any cloud yet.

## Context

ADR-0014 fixed the D1 contract on kind: PostgreSQL 17.11 logical backup, a MinIO
bucket with one-day COMPLIANCE retention, authorized source loss, restore into a
fresh target, a truncated-archive negative restore, and two deletion denials.
ADR-0018 decided the cloud outline (Decision B): S3 Object Lock COMPLIANCE and a
locked one-day version-level Blob policy, each in its own Terraform root with its
own resource group on Azure, destroyed in a second step 24 hours or more after the
session, plus an EBS CSI driver on EKS. This ADR reads the kind implementation
against the cloud modules and vendor documentation (read 2026-11-10), lists what
does not survive the move, and fixes the cloud contract before any code. No
threshold below comes from a run.

## Facts that change the kind contract

1. The kind store, tool and credentials are MinIO specific. The backup and restore
   scripts and the runner's deletion checks call the MinIO client `mc` with static
   keys held in Secrets (`d1-backup-store`). `mc` does not use EKS Pod Identity
   container credentials or AKS workload identity, and Azure Blob is not an S3
   API. The cloud store needs a client that speaks S3 with container credentials
   and Blob with federated credentials.
2. S3 Object Lock needs a bucket created with object lock (which turns versioning
   on). COMPLIANCE retention cannot be shortened or removed by any user including
   root, and the only way to delete before expiry is to delete the AWS account
   (ADR-0018 facts). A bucket holding a locked version cannot be emptied, so
   `terraform destroy` fails on it until retention expires. A delete without a
   version id only adds a delete marker, so the backup identity must simply hold
   no delete permission at all.
3. Azure version-level immutability support can be enabled on a storage account
   only when the account is created, and it cannot be disabled afterwards
   (Microsoft Learn, "Configure immutability policies for blob versions").
   `azurerm` 5.12 exposes it as the `immutability_policy` block (state, period in
   days, protected append writes) next to `blob_properties.versioning_enabled`.
   A locked policy cannot be deleted or shortened, only lengthened, and blobs can
   be deleted after the interval expires. With version-level immutability support
   on, the account can be deleted only after every container is deleted, and a
   container only when it holds no blobs, so a lab resource group holding the
   account would fail to delete (the same class as ResourceGroupDeletionBlocked
   on 2026-11-10). Policies can take up to 35 seconds to take effect.
4. Azure's built-in `Storage Blob Data Contributor` includes blob delete, so the
   backup identity needs a custom data-plane role with read and write but no
   delete. A role definition is a subscription-level resource (`infra/azure/r2.tf`
   already creates one for the responder).
5. EKS installs no EBS CSI driver (add-ons are vpc-cni, kube-proxy, coredns, the
   Pod Identity agent and the node monitoring agent), so D1's PostgreSQL PVCs would
   stay Pending. AKS ships the Azure Disk CSI driver with a default `managed-csi`
   class. Kind uses local-path storage, which provisions instantly. Cloud volume
   provisioning and attach inside the recovery window is new.
6. The kind manifests couple the MinIO store (`gitops/d1/base/store.yaml`) into the
   shared base that every profile includes, so a cloud overlay cannot reuse the
   base without also deploying MinIO.
7. The "administrator" deletion attempt is a Kubernetes Job using the MinIO root
   Secret. On the clouds the administrator is the operator identity running the
   runner (the lab profile on AWS, the signed-in Entra user on Azure), not a
   cluster secret.
8. Kind-only couplings in the runner: `d1_only()` is kind-only, the D1
   preconditions wait for the MinIO StatefulSet, the retention parser reads `mc`
   output (`Object locking 'COMPLIANCE' is configured for 1DAYS`), `is_access_denied`
   matches only S3's AccessDenied text, and the judge fixes `retention_mode ==
   "COMPLIANCE"`. The cloud bootstraps create none of the D1 namespaces or secrets.

## Decision

1. Contract. The ADR-0014 sequence, data, profiles and every observation threshold
   stay as written (backup 120s, restore 120s, RTO 180s, RPO exactly one row,
   exact digest, writer delete AccessDenied, administrator version delete denied
   by retention, archive readable after both, truncated restore leaves zero
   tables). Kind keeps MinIO, `mc` and its scripts untouched.
2. Stores, each in its own Terraform root with local state kept until the second
   destroy, never in `infra/aws` or `infra/azure`.
   - `infra/d1-store-aws`: one S3 bucket with object lock, versioning,
     COMPLIANCE default retention of 1 day, SSE-S3 encryption (no KMS key, so no
     key outlives the lab), public access blocked, TLS-only policy,
     `force_destroy = true` so the second destroy can remove expired versions.
   - `infra/d1-store-azure`: its own resource group, one StorageV2 account created
     with version-level immutability support and a Locked one-day default policy,
     versioning on, Entra-only auth (no shared keys), firewall default deny
     admitting the node subnet and the operator address, one container. The
     operator gets `Storage Blob Data Owner` on it so the administrator attempt is
     a real, authorized delete that the policy then refuses.
3. Second destroy. `scripts/d1-store-destroy.sh aws|azure` refuses to run until the
   newest retained version's retain-until time (recorded by the runner in
   `.d1/<target>-store.json`) has passed plus a 10 minute margin. Azure first
   deletes every blob version and the container, then runs `terraform destroy` on
   the root. The lab's `verify-gone` reports the store as kept by design until that
   time, the way it reports the Config remnants (ADR 0009 note), and never counts
   it as a leak.
4. Store client. A new cloud tool image adds `d1store.py` (boto3 for S3,
   `azure-storage-blob` and `azure-identity` for Blob, versions pinned) to the
   PostgreSQL tools. `backup.sh` and `restore.sh` branch on `D1_STORE`
   (`mc` default, `s3`, `azblob`) so the kind path is untouched and the markers the
   runner parses (`D1_BACKUP_COMPLETE`, `D1_MANIFEST_SHA256`, `D1_ARCHIVE_STAT`,
   `D1_RETENTION_INFO`) keep their shape. `d1store.py` prints retention as the
   store names it, and the judge compares the mode the target defines
   (`COMPLIANCE` on kind and EKS, `Locked` on AKS), not a hardcoded value.
5. Identity, direct and in Terraform, long before the row.
   - EKS. Pod Identity (the lab's own pattern, and boto3 and the AWS CLI both read
     container credentials), two roles bound to the backup service account in
     `selfheal-d1-source` and the restore service account in `selfheal-d1-restore`.
     Each may `PutObject`, `GetObject`, `GetObjectVersion`, `ListBucket`,
     `ListBucketVersions`, `GetBucketObjectLockConfiguration` and
     `GetObjectRetention` on the one bucket. There is no `DeleteObject`,
     `DeleteObjectVersion` or `BypassGovernanceRetention` anywhere.
   - AKS. Two user-assigned identities federated to the same two service accounts,
     each holding a custom `d1 backup writer` data role scoped to the store
     container with blob read and write and no delete.
   - The cluster roots take the store coordinates as variables (`d1_store_bucket`
     on AWS, `d1_store_account_id` on Azure, empty by default) and create nothing
     D1-related when they are empty, so a session without D1 changes nothing.
6. Cluster storage. EKS gains the `aws-ebs-csi-driver` add-on with its own Pod
   Identity role (`AmazonEBSCSIDriverPolicy`) and a `gp3` StorageClass
   (`WaitForFirstConsumer`, encrypted) applied by the platform script. The D1
   overlays set `storageClassName: gp3` on EKS and use `managed-csi` on AKS.
7. Manifests. The MinIO store moves out of the shared base into
   `gitops/d1/store-minio`, which only the kind profiles include. Cloud overlays
   `gitops/targets/<cloud>/d1/{baseline,hardened}` wrap the new base and add the
   cloud CronJob (service account, image, store environment). The restore Job and
   the store checks are built by the runner for the target.
8. Deletion evidence.
   - Writer attempt. An in-cluster Job with the backup identity deletes the archive.
     Denial is `AccessDenied` on S3 and `AuthorizationPermissionMismatch` (403) on
     Blob.
   - Administrator attempt. The runner, with the operator identity, deletes the
     retained version directly (`s3api delete-object --version-id`, or
     `az storage blob delete --version-id`). It must be refused by retention, not
     by a missing permission, so the runner first records that the operator holds the
     delete permission (AWS: `iam simulate-principal-policy` for
     `s3:DeleteObjectVersion` on the object, Azure: the operator's
     `Storage Blob Data Owner` assignment on the store). If that record is missing
     the row is an error, since a refusal could then be a permission gap.
   - Observed reasons are recorded verbatim. The recognised markers per cloud are
     live checks L9 and L10, and an unrecognised refusal is an error, not a pass.
9. Recovery clock honesty. `rto_s` still runs from source deletion start to verified
   restored data and still includes the new source database becoming Ready, which on
   the clouds includes provisioning a new EBS or Azure Disk volume. The runner now
   records that provisioning time as `source_reprovision_s` beside the total. If a
   run misses 180s because of volume provisioning, the miss is reported with that
   field and the contract is changed only by a later ADR amendment naming the run.
10. Session shape. D1 runs third in a session (after O1, Q3 and P1, before N1) per
    ADR-0018 Decision D. The store roots are applied by Jordan before the cluster
    session, each with a saved plan reviewed first, because the Azure policy lock
    and the S3 COMPLIANCE default cannot be undone.
11. Versions. Session plans pass 1.36 as in ADR-0019 Decision 7.

## Thresholds

Identical to ADR-0014 on kind, EKS and AKS.

| Observation | kind | EKS and AKS |
|---|---:|---:|
| Backup Job completes | 120s | 120s |
| Restore Job completes | 120s | 120s |
| Recovery time, loss start to verified data | 180s | 180s |
| Recovery point | rows 1 to 100 | same |
| RPO | exactly 1 row | exactly 1 row |
| Restored digest equals source digest | yes | yes |
| Retention days | 1 | 1 |
| Retention mode | COMPLIANCE | EKS COMPLIANCE, AKS Locked |
| Writer delete | denied | denied |
| Administrator version delete | denied by retention | denied by retention |
| Archive readable with original checksum after both | yes | yes |
| Truncated restore leaves zero application tables | yes | yes |

Missing telemetry stays an error. The Azure retention mode is Azure's own name for
the same one-day locked state, not a relaxation.

## Verification plan

- Unit tests. The store client output markers, the retention mode per target in the
  judge, the Azure and S3 denial recognisers (including an unrecognised refusal
  being an error), the cloud restore and check Jobs, the second-destroy guard
  (refuses before expiry, accepts after, never trusts a missing record), the
  provisioning-time field, per-target thresholds and fixture paths. `make check`
  and `make test` stay green.
- Overlays render for both clouds and both profiles with no MinIO object, no
  `mc`, the cloud image and the right storage class.
- Terraform validate, tflint, Checkov, trivy and conftest pass for both new roots
  and both cluster roots.
- Local proof stays kind, re-run after the base split. The paid proof waits for the
  batched session (ADR-0018 Decision D).

## Live checks

| # | Check |
|---|---|
| L9 | S3 refuses the backup role's delete with AccessDenied, and refuses the operator's version delete because of COMPLIANCE retention; the exact error shape |
| L10 | Blob refuses the backup identity (403 mismatch) and the operator's version delete (immutability error); a new version inherits the account policy as Locked for one day |
| L18 | EBS and Azure Disk provisioning time for the replaced source volume, against the 180s clock |
| L19 | boto3 and the AWS CLI read Pod Identity container credentials in the tool image |
| L20 | `azure-identity` workload identity reads and writes Blob from the tool image |
| L21 | The `azurerm` Locked policy can be created with the account, or needs an Unlocked create then a lock (plan review before apply) |
| L22 | The EBS CSI add-on and the gp3 class bind a PVC on EKS 1.36 |

## Cost and session boundary

No hourly resource. A one-object bucket and a LRS account for at least 24 hours
(cents), two Pod Identity roles, two Azure identities, one custom role, and EBS or
Azure Disk volumes for the PostgreSQL pods that live and die with the session. The
store survives the session by design and is destroyed in the second step, after
which `verify-gone` is run again. Never push before Phase 5.

## References

- [Azure immutable storage overview](https://learn.microsoft.com/en-us/azure/storage/blobs/immutable-storage-overview) (read 2026-11-10)
- [Configure immutability policies for blob versions](https://learn.microsoft.com/en-us/azure/storage/blobs/immutable-policy-configure-version-scope)
- [S3 Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html)
- ADR-0007, ADR-0014, ADR-0018, ADR-0019, ADR-0021
