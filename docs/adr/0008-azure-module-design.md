# ADR-0008: The Azure module

- **Status:** accepted
- **Date:** 2026-11-05

## Context

The Azure module runs the kind core on AKS and adds three rows only a cloud
can host: a node stops (A4), the node firewall opens (S4), and a runtime
threat is answered by the cloud's own detector (R2). ADR-0007 records the
facts checked before any of it was written, row by row against the EKS
session's missteps, and the six decisions the operator took (D1 to D6). This
ADR records the design built on them, and what changed while building it.
Quotes are from Microsoft Learn and the Kubernetes docs, read on the date in
brackets.

## Decisions

### One subscription, one region, three resource groups

Terraform owns `rg-selfheal-lab`. Azure creates two more for the lab: the
AKS node group (`rg-selfheal-lab-nodes`, named explicitly) and the Azure
Monitor workspace's `MA_amw-selfheal_<region>_managed`. The subscription ID
lives in the gitignored `lab.auto.tfvars`; the provider registers no resource
provider itself (`resource_provider_registrations = "none"`), so an
unexpected registration can never hide in an apply. East US 2.

### Capacity: three system nodes, three to four workload nodes, 16 vCPUs (D1, revised)

ADR-0007 sized two system nodes. Adding up the requests of everything that
runs there (rendered pinned charts, AKS's own metrics-server and CoreDNS, the
Defender sensor at its 420m limit per node) gave 3020m of 3800m allocatable
on two 2-vCPU nodes (79%), too tight for a platform that must not
be the bottleneck. Three system nodes give 67%. The workload pool is tainted
and autoscaled from three (the hardened spread needs three domains) to four
(row A4's replacement). Peak: 14 of the 16 vCPUs decision D1 raises the Dsv6
family to. Revised before any apply: the regional limit is raised to 16, the
system pool runs `Standard_D2s_v3` (6 of the 10 DSv3 already approved) and the
workload pool `Standard_D2s_v6` (8 of 10 Dsv6); see Changes below. A stopped VM keeps its quota, which is why A4 needs that headroom.
The preflight fails closed below 16.

### Bring your own CNI, the same Cilium (D2)

`network_plugin = "none"`, pod CIDR 10.244.0.0/16 on both AKS and Cilium.
Rendering Cilium 1.20.4 found two traps the docs' one-liner
(`aksbyocni.enabled=true`) does not show: with the lab's base values the
chart writes no `ipam` key at all, and its default pool, 10.0.0.0/8, contains
the lab VNet. `cilium/values-aks.yaml` sets cluster-pool IPAM with the AKS pod
CIDR and VXLAN tunnelling; `make check` renders the chart and asserts both.
Nodes are NotReady until `make aks-platform` installs Cilium, so it runs
right after the apply.

### AKS's own metrics-server

AKS deploys and manages metrics-server itself. The platform step does not
install the chart the kind and EKS targets use (two servers would fight over
the `metrics.k8s.io` APIService) and instead fails unless that APIService is
`Available`, since row A3's HPA depends on it.

### Network rules for Key Vault and the evidence account

"IP network rules have no effect on requests that originate from the same
Azure region as the storage account"
([Storage network security limitations](https://learn.microsoft.com/en-us/azure/storage/common/storage-network-security-limitations), 2026-10-15).
The cluster's outbound IP therefore cannot be what admits it. The node subnet
has `Microsoft.KeyVault` and `Microsoft.Storage` service endpoints, Cilium
masquerades pod traffic to the node address, and both firewalls admit the
subnet plus the operator's address (Storage rejects /31 and /32 prefixes, so
the `/32` is stripped). The Logic App, which has no VNet, is admitted to the
evidence account by a resource instance rule (`Microsoft.Logic/workflows` is
on the trusted access list); there is no trusted-services bypass. Live check
L13: a service endpoint "can take up to 15 minutes" to apply.

### Identity

Every Azure caller in the cluster has its own user-assigned identity,
federated to exactly one service account (ESO, Prometheus, Grafana, the R2
worker), and every role assignment is direct and made in the apply (group
membership of managed identities "can take several hours", ADR-0007 row 13).

ESO uses the referenced service account form: the `SecretStore` names
`eso-backend`, and ESO exchanges that account's token itself. Argo CD's
`resource.inclusions` do not watch ServiceAccounts, so the account is created
by `make aks-bootstrap` with its client and tenant annotations (which keeps
the tenant ID out of the repo), as the kind reader is created outside GitOps.
The partner key is a plain Key Vault secret: the ESO provider page does not
document `property` for JSON values, so the AKS overlay removes it rather than
rely on undocumented behaviour. ESO reads the latest version (no `version`
field); `make aks-bootstrap` proves it by comparing the synced key with the
vault's (live checks L5, L13).

### Placement under test (row A4)

On EKS the baseline had no placement rule, so how many replicas a node kill
took depended on how many nodes existed, and A4 was inconclusive. On AKS:

- **Baseline:** a required pod affinity on a baseline-only label puts all
  three replicas on one node, every run. The label, not `app=web`, because
  right after a switch from the hardened profile the spread pods still exist
  and would attract new pods to every node. The first pod may go anywhere:
  "If the current Pod being scheduled is the first in a series that have
  affinity to themselves, it is allowed to be scheduled if it passes all other
  affinity checks"
  ([Assigning Pods to Nodes](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/), 2026-02-10).
- **Hardened:** hostname spread, `maxSkew` 1, `minDomains` 3 (the EKS lesson),
  `nodeTaintsPolicy: Honor` so a stopped node stops counting as a domain and
  the evicted replica goes Pending, which is what the cluster autoscaler
  reacts to. The pod tolerates `not-ready` and `unreachable` for 30s, not the
  default: Kubernetes "automatically adds a toleration for
  `node.kubernetes.io/not-ready` and `node.kubernetes.io/unreachable` with
  `tolerationSeconds=300`, unless you, or a controller, set those tolerations
  explicitly"
  ([Taints and Tolerations](https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/), 2026-07-27).

`scripts/render-aks-profiles.sh` renders both profiles offline and asserts
each of these properties (it is mutation-tested: removing one fails it).

### A4: one instance, by ID (decision D3 on the error clock)

The runner stops the instance hosting the most web pods with
`az vmss stop --instance-ids <id> --skip-shutdown`, chosen at T0, so no tag can
go stale, and starts it again after the row. Errors are judged from NotReady
(ADR-0003, decision D3). An `aks-auto-repair` event on that node inside the
window voids the row.

### S4: Azure Policy deployIfNotExists (decision D5)

A custom DINE definition on NSG security rules rewrites an inbound Allow from
the internet to Deny, `evaluationDelay: AfterProvisioningSuccess`. The
assignment's enforcement mode is the profile switch (ADR-0001 addendum). The
DINE docs say where the deployment runs but not what it is named
([deployIfNotExists](https://learn.microsoft.com/en-us/azure/governance/policy/concepts/effect-deploy-if-not-exists), updated 2025-12-01),
so the runner proves the revert by content instead of by a name: the rule
reads Deny with the description only the policy's template writes, and a
succeeded deployment after T0 carries that rule's name as its
`ruleFullName` parameter. Production puts a Deny policy first; this row
proves the detective layer behind it.

### R2: Defender alert to an in-cluster worker (decisions D4, D6)

Defender for Containers (subscription plan, only the `ContainerSensor`
extension) raises an alert; a workflow automation scoped to the lab group
posts it to a Consumption Logic App (the App Service quota is 0). The Logic
App:

1. trusts only ARM's copy of the alert: it lists the lab group's alerts and
   continues only with exactly one active alert whose `systemAlertId` matches
   the posted one and that names the cluster, polling up to 10 minutes (the
   AWS lesson: the query API can lag delivery). A forged callback cannot start
   a response;
2. enqueues an incident (`incidents` queue, TTL 900s);
3. waits up to 8 minutes for the in-cluster worker's `isolated.json`;
4. through ARM, with its own identity: an incremental snapshot of the node's
   OS disk into the lab group, a Run Command capture on the scale set
   instance (output capped at the last 4,096 bytes, documented), and
   `manifest.json`. Every outcome leaves a blob: `manifest.json`,
   `isolated.json` or `failed.json`.

The worker (`app/r2worker`, workload identity, Python stdlib, digest-pinned
base image) refuses anything not for the lab cluster or not a web pod in
`selfheal`, cordons the node FIRST and confirms it, then relabels the pod
into quarantine (the EKS race), and annotates the node
`cluster-autoscaler.kubernetes.io/scale-down-disabled`, the AKS-supported
stand-in for EC2 termination protection (a scale set instance protection
would be an unsupported IaaS change). A `CiliumNetworkPolicy` limits its
egress to the API server, DNS, Entra ID and the one storage account.

The worker exists because AKS `command invoke` is documented as "Not for
programmatic access", and a Consumption Logic App cannot be told to trust the
cluster CA. There is no supported per-node network quarantine (D4): node
NICs cannot carry their own NSG. The README lists the gap; production answers
are a quarantine node pool in its own subnet or Cilium host policies.

The Logic App's custom role is defined at the subscription ("a role
definition is a management group or subscription-level resource") and
assignable only in the lab and node groups. Defender pricing can also be set
per resource (one cluster); the lab uses the subscription plan, which destroy
resets to Free, and lists the per-resource plan as the production option.

The trigger is the request Microsoft's own simulation uses
(`mdc-eicar.alerts.security.azure.com`), sent by the web container's
`python3`, not a shell, so row R1's Falco rule stays out of it. Response
clocks run from the alert's own generation time; detection is Defender's
documented hour.

### Observability

In-cluster Prometheus (v3.15.1 in kube-prometheus-stack 92.17.1, above the
3.7.0 minimum for workload identity) evaluates the same Sloth rules and
remote-writes only the series the lab reads to an Azure Monitor workspace,
authenticated by workload identity against the workspace's data collection
rule. The role "takes about 30 minutes", so `make aks-platform` polls until
samples are sent and none fail (live check L9). Grafana 13 reads the
workspace through the `grafana-azureprometheus-datasource` plugin (Azure
auth on the core Prometheus type is deprecated); live check L14 confirms the
plugin ships in the image. No Azure Managed Grafana (ADR-0007 row 12).

### Teardown: three steps, fails closed

1. `make aks-predestroy`: Argo CD apps, the lab app and its PDB, evidence
   snapshots. Terraform deletes the workload node pool before the cluster, and
   a node pool delete honours PDBs unless told not to (`az aks nodepool delete
   --ignore-pdb`: "Delete an existing nodepool without considering Pod
   Disruption Budget"), so minAvailable 2 would stall the destroy. Any other
   PDB on a workload-pool pod that allows no disruption stops the script.
   Cilium stays: in overlay mode it owns no Azure resources.
2. The destroy plan, applied by the operator. The provider refuses to delete
   a group that still holds something Terraform did not create.
3. `make aks-verify-gone`, read-only: every check must say "gone", against the
   before-picture `make aks-preflight` records (policy assignments,
   `DefaultResourceGroup-*`, workflow automations, the Containers plan), so
   anything Defender added during the session is caught. A failed query
   prints LEFT, never "gone" (found while writing it: `az disk list` needs a
   group in CLI 2.83 and had silently returned nothing).

## Changes from ADR-0007, made while building

- Three system nodes, not two (capacity above).
- **Decision D1 revised (2026-11-05, before any apply): system nodes
  `Standard_D2s_v3`, workload nodes `Standard_D2s_v6`, regional limit 16.**
  The DSv3 increase returned `DeprecatedQuotaType`: "Additional quota isn't
  available for the requested family standardDSv3Family". The capacity
  restriction page: "Existing subscription requesting additional quota:
  Additional quota isn't approved", while "Existing subscription using
  already-approved quota: Can deploy or redeploy affected SKUs, subject to
  capacity availability"
  ([VM size series retirements and capacity growth restrictions](https://learn.microsoft.com/azure/virtual-machines/sizes/lifecycle/retirements-and-capacity-restrictions), updated 2026-09-25).
  `Standard_D2s_v5` is `NotAvailableForSubscription` in East US 2. A Dsv6
  increase to 16 returned `QuotaNotAvailableForResource`, which the Quota API
  documents as "Quota is currently unavailable in the location for the
  requested resource" ([Quota REST API](https://learn.microsoft.com/rest/api/quota/), troubleshooting table).
  An increase of the regional limit alone (`cores`) to 16 was approved. The
  lab therefore keeps the system pool on the DSv3 quota it already had (6 of
  10) and puts the workload pool, the one rows A4 and R2 act on, on Dsv6 (8
  of 10), the same 2 vCPU and 8 GiB shape at $0.101 an hour against $0.096.
  Dsv6 is Generation 2 only, has no temporary disk, supports no ephemeral OS
  disk (the lab already uses managed OS disks for R2's snapshot), and
  requires accelerated networking (MANA)
  ([Dsv6 series](https://learn.microsoft.com/azure/virtual-machines/sizes/general-purpose/dsv6-series), updated 2026-10-27).
  For AKS, "the platform supplies the image and already meets the
  Generation 2, NVMe, and MANA prerequisites", and mixed sizes belong in
  "separate node pools with labels and taints", which the lab already has
  ([v6 and v7 modernization FAQ](https://learn.microsoft.com/azure/virtual-machines/sizes/lifecycle/sizes-v6-v7-modernization-faq), updated 2026-09-25).
  `az vm list-skus` reports encryption at host supported on both. Nothing in
  the lab uses SCSI device paths. What no doc settles is the bring-your-own
  Cilium dataplane on a MANA NIC, and across two NIC types in one cluster:
  live check L20. The preflight checks each family and the region
  separately. If the landing zone is deployed again, its allowed-size list
  would need D2s_v6 added.
- Key Vault and the evidence account admit the node subnet, not the egress IP.
- `aks-images` runs before `aks-platform` (the platform step deploys the
  worker, which needs its image).
- The S4 revert is proven by content, not by a `PolicyDeployment_*` name.
- The workflow automation's switch is a `PATCH` of `isEnabled`: the trigger
  URI "will not be included in any response"
  ([Automations - Update](https://learn.microsoft.com/en-us/rest/api/defenderforcloud/automations/update), 2026-10-23),
  so a read-modify-write would drop it.
- **Found by the first apply (2026-11-05):** AKS rejected the cluster with
  "Tag name [environment] is duplicated. Tag name is case insensitive." The
  tag map carried `Environment` and `environment` (and `Owner` and `owner`)
  for the landing zone's tag rules; the resource group had quietly kept one
  of each, AKS refuses. ARM tag names are case-insensitive, so the lowercase
  copies were never needed and are gone; a precondition on the resource group
  now fails the plan if two tag names differ only in case. The apply stopped
  with 36 of 51 resources created, which exposed a preflight bug: it treated
  a session as in progress only when the cluster was in state, so the
  re-plan would have called the lab's own resources leftovers and could have
  overwritten the clean before-picture. Any resource in state now counts.
  The re-plan then stopped on a system-protected deny assignment, "Azure
  Monitor Workspace Managed Resource Group Deny-...", on the workspace's
  `MA_` group ("Prevents modification of the resources required for the
  Azure Monitor Workspace to function": DCR and DCE delete and write, group
  write and delete). "The resource group and its resources are automatically
  deleted when you delete the workspace"
  ([Manage an Azure Monitor workspace](https://learn.microsoft.com/azure/azure-monitor/metrics/azure-monitor-workspace-manage), updated 2026-08-07),
  so it cannot block the teardown; the preflight accepts exactly that one
  (system-protected, that name, the lab workspace's group) and still fails on
  any other deny assignment on a lab scope.
- checkov's HCL parser rejects a bare `if` key, so the policy rule quotes
  `"if"` and `"then"`. azurerm 5 needs `node_provisioning_profile`, and
  subnet service endpoints are blocks.

## Live checks (runbook section 2)

L1 to L10 are in ADR-0007. Added while building:

- **L11** The snapshot needs only `disks/read` on the source (no `beginGetAccess`).
- **L12** The Logic App's resource instance rule admits it through the storage firewall.
- **L13** The node subnet's service endpoints take effect (up to 15 minutes).
- **L14** The Grafana image carries `grafana-azureprometheus-datasource`.
- **L15** Cilium's `kube-apiserver` entity matches the AKS API server path from pods.
- **L16** `enableDefaultDeny` is accepted by the Cilium 1.20 CRD.
- **L17** A Logic App run on the success path is `Succeeded`, though earlier `Until` iterations had 404s.
- **L18** The DINE deployment's name and timing for a child `securityRules` write.
- **L19** A `PATCH` of `isEnabled` alone keeps the automation's Logic App action and trigger URI.
- **L20** Cilium 1.20.4 (BYO CNI, VXLAN) runs on the Dsv6 MANA NIC next to the DSv3 system nodes: agents ready, nodes Ready, pod traffic between the pools.

## Consequences

- AKS rows: A1 to A5, S1 to S4, R1, R2 (no S1b, no S5).
- Cost while up: about $0.85 to $1.00 an hour (three D2s_v3 and four D2s_v6 at peak, Defender
  per vCore, one public IP, ingestion), a 5 to 6 hour session about $5 to $6.
  R2 alone can take two hours (Defender's hour, twice). Infracost runs first;
  the cost gate fails closed.
- Microsoft does not support CNI issues on a bring-your-own-CNI cluster; this
  is a lab choice made for one dataplane across targets (ADR-0001).
