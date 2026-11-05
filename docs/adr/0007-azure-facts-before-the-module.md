# ADR-0007: Azure facts verified before the Azure module was written

- **Status:** accepted (2026-11-05; the module is built on it, ADR-0008)
- **Date:** 2026-11-04

## Context

The EKS session hit about fifteen live bugs, most of which a docs-first pass
would have caught (README, "On AWS"). For Azure, nothing is assumed from AWS
behaviour. Each row below takes one AWS misstep, finds the Azure mechanism,
quotes the official documentation (Microsoft Learn unless noted, read on
2026-11-04, page date in brackets), and records the decision it forces. What
the docs do not settle is a live check for the runbook (end of this ADR).

The subscription was also read (read-only `az` calls, 2026-11-04) so the
design starts from what is there, not from what the landing zone code would
create. Prices come from the Azure Retail Prices API
(`prices.azure.com/api/retail/prices`, East US 2, pay-as-you-go), because the
public pricing pages render their figures client-side.

## What the subscription holds today

| Check | Result | Consequence |
|---|---|---|
| Policy assignments visible at the subscription, inherited or below | One: `SecurityCenterBuiltIn` (the Defender for Cloud default initiative, audit effects) | Nothing denies creating or deleting a lab resource today |
| Deny assignments | None | Nothing blocks teardown the way the AWS SCP did |
| Management locks | One `CanNotDelete` lock, on the landing zone's Terraform state group | The lab uses its own groups and never touches that one |
| Management groups | Tenant root only (the landing zone hierarchy is torn down) | The landing zone Deny policies are not in force (next section) |
| Defender for Cloud plans | Foundational CSPM and Discovery on; Containers `Free`, never enabled | The lab turns Containers on and off; nothing else depends on it |
| Defender workflow automations, Logic Apps, Event Grid system topics, alert rules, action groups, security contacts | None | No existing responder can act on lab alerts (AWS row 15) |
| Resource providers | All needed ones registered except `Microsoft.Chaos` and `Microsoft.Quota` | Chaos Studio is not used (row 9); `Microsoft.Quota` is needed only for a CLI quota request |
| `EncryptionAtHost` feature | Registered | Node pools can encrypt at host |
| Regional vCPU quota, East US 2 | **10** total; DSv3, DSv4, Dsv6, Dav6, BS families 10 each; **DSv5 family 0** | The lab cannot fit a replacement node (row 8) without an increase |
| App Service quota | 0 (known) | No Functions; R2 uses Logic Apps Consumption |
| AKS versions in East US 2 | 1.31 to 1.37 offered, 1.36 is the default | Pin 1.34 to match EKS (supported, Cilium 1.18.12 minimum for managed Cilium) |

## If the landing zone is deployed again

The landing zone code (`azure-landing-zone/terraform`) assigns Deny policies
that would block this lab if they were live. They are not live today; the
preflight re-reads assignments and fails closed if any of these appear.

| Landing zone assignment (scope) | Effect on the lab | Design response |
|---|---|---|
| `deny-public-ip` (Workloads MG, only the hub group excluded) | Blocks the outbound public IP AKS creates in the node resource group | Preflight fails; production answer is a NAT gateway or firewall egress in the hub |
| `allowed-vm-sizes`: B2s, B2s_v2, D2s_v3, D2s_v5 (root MG) | Blocks any other size, VM scale sets included | Nodes use **Standard_D2s_v3**: on the list, and its family has quota (D2s_v5 is on the list but its family quota is 0) |
| `require-host-encryption`, Deny (root MG) | Blocks node pools without encryption at host | Node pools set `host_encryption_enabled = true` (feature registered) |
| `require-owner-tag`, `require-tag-*` on resource groups (Workloads MG) | Denies untagged groups, including the AKS node group and the Azure Monitor workspace's `MA_*_managed` group, unless excluded | Lab groups carry every tag; the two platform-created groups would need adding to `platform_managed_resource_groups` in the landing zone, never an exemption made by this lab |
| `allowed-locations` (Workloads MG: centralus, eastus2, westus2) | Region must be one of these | East US 2 |
| `approved-vm-images` (standalone VMs only, scale sets excluded) | None for AKS | None |

## Findings, row by row

### 1. CNI and what the module installs

**AWS:** module v21 set `bootstrap_self_managed_addons = false`; no CNI,
kube-proxy or CoreDNS; nodes NotReady; the node group failed after 30 minutes.

**Azure mechanism:** three options. Azure CNI Overlay; Azure CNI powered by
Cilium (managed); bring your own CNI (`network_plugin = "none"`).

**Docs:**
- Managed Cilium: "Only label exclusion is supported for Azure CNI Powered by
  Cilium. ... Changes to other values in the ConfigMap are not supported."
  Hubble flows ("Container Network Observability (metrics and flow logs)")
  are "Not supported" without Advanced Container Networking Services (ACNS).
  L3/L4 `CiliumNetworkPolicy` is supported. "AKS clusters created with network
  data plane as Cilium don't use `kube-proxy`."
  [Configure Azure CNI Powered by Cilium](https://learn.microsoft.com/en-us/azure/aks/azure-cni-powered-by-cilium) (2026-08-05)
- ACNS is billed: `Advanced Container Networking Services, Standard Node,
  $0.025 per hour` (Retail Prices API).
- BYO CNI: "After AKS provisioning finishes, the cluster is online. But all
  the nodes are in a `NotReady` state." "You must specify a Pod CIDR."
  "Microsoft support can't assist with CNI-related issues in clusters that you
  deploy by bringing your own CNI plugin."
  [Bring your own CNI](https://learn.microsoft.com/en-us/azure/aks/use-byo-cni) (2026-04-16)
- Support policy: Microsoft manages "`kube-proxy` and cluster networking,
  except when BYOCNI is used"; BYOCNI is "Customer ⚠️ Unsupported".
  [AKS support policies](https://learn.microsoft.com/en-us/azure/aks/support-policies) (2026-08-27)

**Decision (proposed, see open decision D2):** BYO CNI with the same pinned
Cilium 1.20.4 the kind and EKS runs use, Hubble relay included. R1's evidence
is Hubble `POLICY_DENY` flows and its timing depends on the pre-allocated
quarantine identity (ADR-0002), and ADR-0001 needs the same Cilium in every
target. Managed Cilium would change the dataplane under the experiment, and
its flow data costs extra and is not the Hubble relay the runner reads. The
cost is honest: Microsoft does not support CNI issues on this cluster, and
the nodes are NotReady until `make aks-platform` installs Cilium, so the
platform step runs immediately after the apply. The pod CIDR is set
explicitly. Unlike EKS, nothing is silently missing: AKS still installs
CoreDNS and kube-proxy, which the Cilium values must match (live check L1).

### 2. Org guardrails that block create or delete

**AWS:** the org-root SCP `deny-disable-detective` denied deleting Config;
the destroy stopped and partially dismantled the delivery bucket.

**Azure mechanism:** Azure Policy Deny and DenyAction assignments at
management group, subscription or group scope; deny assignments; resource
locks; the AKS node resource group lockdown.

**Docs:**
- Node resource group lockdown: "ReadOnly: You can view node resource group
  resources, but a deny assignment blocks direct updates. Unrestricted: Direct
  updates to node resource group resources are allowed."
  [Node resource group lockdown](https://learn.microsoft.com/en-us/azure/aks/node-resource-group-lockdown) (2026-06-30)
- Deleting a cluster deletes "The node resource group and its resources".
  [Delete an AKS cluster](https://learn.microsoft.com/en-us/azure/aks/delete-cluster) (2024-04-16)
- Defender plans are changed by a subscription "Security admin role or Owner"
  ([Workflow automation](https://learn.microsoft.com/en-us/azure/defender-for-cloud/workflow-automations), 2026-07-03, prerequisites).

**Verified live:** no Deny or DenyAction policy, no deny assignment, no lock on
any group the lab will use (table above). The operator holds Owner on the
subscription.

**Decision:** node resource group restriction level stays `Unrestricted`
(R2 snapshots the node's OS disk there). The preflight lists policy
assignments (inherited and below), deny assignments and locks, and fails if
any Deny, DenyAction or lock touches a lab type or group. Teardown cannot be
blocked by anything that exists today.

### 3. Managed metrics: encryption keys, ingestion auth, propagation delay

**AWS:** AMP on a customer-managed key needed the caller's `kms:GenerateDataKey`;
AMP then served a cached denial for about four minutes.

**Azure mechanism:** Azure Monitor workspace; remote write to the data
collection rule (DCR) the workspace creates; Entra auth.

**Docs:**
- "Data is encrypted at rest using a Microsoft-managed key." Retention is a
  fixed 18 months; "Default ingestion limit: 1 million active time series".
  [Azure Monitor workspace overview](https://learn.microsoft.com/en-us/azure/azure-monitor/metrics/azure-monitor-workspace-overview) (2026-10-28)
- The workspace creates "a data collection rule and a data collection
  endpoint ... in a resource group in the form
  `MA_<azure-monitor-workspace-name>_<location>_managed`", and "The resource
  group and its resources are automatically deleted when you delete the
  workspace." Deletion has "no soft delete".
  [Manage an Azure Monitor workspace](https://learn.microsoft.com/en-us/azure/azure-monitor/metrics/azure-monitor-workspace-manage) (2026-08-07)
- Remote write: "Prometheus version 3.7.0 or greater is required for
  Microsoft Entra workload identity authentication"; the identity needs
  "Monitoring Metrics Publisher role on the workspace data collection rule";
  "It takes about 30 minutes for the assignment of the role to take effect.
  During this time, you may see an HTTP 403 error".
  [Connect self-managed Prometheus](https://learn.microsoft.com/en-us/azure/azure-monitor/metrics/prometheus-remote-write) (2025-09-16)
- The sidecar path is retired: "Effective March 31, 2027, Azure Monitor will
  not support the sidecar-based remote-write solution".
  [Remote write with managed identity](https://learn.microsoft.com/en-us/azure/azure-monitor/containers/prometheus-remote-write-managed-identity) (2024-04-18, updated 2026-07-17)
- Price: `Metrics ingestion, $0.16 per 10M samples`; queries `$0.001 per 10M`.

**Decision:** no customer-managed key exists to get wrong. The pinned
kube-prometheus-stack 92.17.1 ships Prometheus v3.15.1, so it remote-writes
directly (`azureAd.workloadIdentity`, no sidecar) only the series the lab
reads, as on EKS. The DCR role assignment is created in the infrastructure
apply, and the platform step polls the Prometheus log for 403s before the
demo starts (allow 30 minutes, per the docs). Verify-gone checks the
`MA_*_managed` group is gone.

### 4. Secret version semantics

**AWS:** `AWSPENDING` stayed on the new current version; the runner waited for
a label that never cleared.

**Azure mechanism:** Key Vault secret versions; ESO Azure Key Vault provider.

**Docs:**
- Key Vault has no built-in secret rotator; the documented pattern is an Event
  Grid `SecretNearExpiry` event ("Thirty days before the expiration date")
  calling a function.
  [Dual-credential rotation tutorial](https://learn.microsoft.com/en-us/azure/key-vault/secrets/tutorial-rotation-dual) (2026-07-07)
- ESO: "Workload Identity is the recommended authentication method for Azure
  Key Vault"; minimum role "Key Vault Secrets User". The provider page
  documents no `version` field and no "previous" alias.
  [ESO Azure Key Vault provider](https://external-secrets.io/latest/provider/azure-key-vault/) (read 2026-11-04)

**Decision:** S1 is the only secret row on Azure (S1b is AWS-only by plan). A
rotation is a new secret version; there are no labels to wait on, so the
runner treats a rotation as done when the new version's ID is the latest
enabled one and ESO's synced Secret carries its value. ESO reads the latest
version (no `version` set). The partner keeps accepting the old key for the
grace window exactly as on kind. Whether ESO reads latest without a version
field is confirmed live (L5).

### 5. Detector latency and the query API

**AWS:** `ListFindings` lagged EventBridge; the responder acted before the
runner could see the finding.

**Azure mechanism:** Defender for Containers alerts, delivered to a
Consumption Logic App by workflow automation (`Microsoft.Security/automations`);
alerts readable through the Security alerts API.

**Docs:**
- "Some alerts appear in near real-time; others can take up to an hour."
  ([Simulation tool README](https://github.com/microsoft/Defender-for-Cloud-Attack-Simulation); same words in
  [Kubernetes alerts](https://learn.microsoft.com/en-us/azure/defender-for-cloud/alerts-containers), 2026-05-18)
- "The workflow automation feature can trigger consumption logic apps on
  security alerts"; trigger "When a Defender for Cloud Alert is created or
  triggered". [Workflow automation](https://learn.microsoft.com/en-us/azure/defender-for-cloud/workflow-automations) (2026-07-03)
- "Security alerts are only triggered for actions and deployments that occur
  after you enable Defender for Containers on your subscription."
  [Introduction to Defender for Containers](https://learn.microsoft.com/en-us/azure/defender-for-cloud/defender-for-containers-introduction) (2026-08-10)
- The threat-intelligence test in Microsoft's own simulation tool is
  `curl -s -m 10 -X POST "http://mdc-eicar.alerts.security.azure.com?SendingData"`
  (`Images/attacker/src/ti-domain.sh`).

**Decision:** the runner times detection from the alert's own generation time
(read through the alerts API), waits for the alert itself, and never infers
detection from the Logic App run. The Defender plan is enabled in the
infrastructure apply, long before the row, and the trigger is the documented
test domain requested by the web container's own `python3` (not a shell, so
R1's Falco rule stays out of it). R2 gets two clocks: detection (documented
as up to an hour, reported against that bound) and response, measured from
the alert (the part the lab controls). Thresholds: see "Proposed AKS
thresholds".

### 6. Baseline broken before the fault

**AWS:** earlier rotations had invalidated the baseline's credential, so the
baseline was failing 30 minutes before S1b.

**Azure mechanism:** the same ESO refresh (10s hardened, 1h baseline).

**Docs:** ESO's examples use `refreshInterval: 1h0m0s`, "rate ESO pulls Azure
Key Vault" (provider page above).

**Decision:** the runner's pre-T0 clean window, already required for every
row since the EKS session, applies unchanged; S1 seeds a fresh key and forces
an ESO sync before T0 in both profiles. Key Vault soft delete matters for
re-runs, not for this row (row 11).

### 7. Responder race and stateful firewalls

**AWS:** the relabel made the Rollout create a replacement that the scheduler
put on the node being quarantined, 0.2s before the cordon; the stateful SG
swap kept the kubelet connection alive.

**Azure mechanism:** NSGs on subnets or NICs; Kubernetes cordon; the
responder's call path into the cluster.

**Docs:**
- "You can customize network security groups (NSGs) only on custom subnets
  ... Agent node NIC level: No." "Any modification made directly to the agent
  nodes through the IaaS APIs renders the cluster unsupportable."
  [AKS support policies](https://learn.microsoft.com/en-us/azure/aks/support-policies) (2026-08-27)
- `command invoke`: needs `Microsoft.ContainerService/managedClusters/runcommand/action`
  and `.../commandResults/read`; its pod requests "200m" CPU and "500Mi";
  "Azure Resource Manager (ARM) API timeout: 60 seconds: Maximum time for pod
  scheduling"; and under limitations, "**Not for programmatic access**: Use
  Bastion, VPN, or ExpressRoute for automated API calls."
  [Command invoke](https://learn.microsoft.com/en-us/azure/aks/access-private-cluster) (2025-06-10)
- Logic Apps HTTP actions follow the async pattern: a 202 with a `location`
  header is polled until it completes.
  [HTTP action](https://learn.microsoft.com/en-us/azure/connectors/connectors-native-http) (2026-07-10)

**Decision:** cordon first, then relabel, in one ordered command, so the
replacement can never land on the node. There is **no supported per-node
network quarantine on AKS** (no NIC-level NSG, and a subnet NSG would cut
every node), so the AWS security-group swap has no Azure equivalent here.
Node isolation is the cordon plus the pod's quarantine `CiliumNetworkPolicy`;
the README lists the gap and the production answer (a dedicated quarantine
node pool in its own subnet, or Cilium host policies). The responder reaches
the cluster through ARM, never the API server directly: AKS signs the API
server certificate with the cluster's own CA, which a Consumption Logic App
cannot be told to trust, and ARM needs no extra authorized IP range. The ARM
path is `command invoke`, which the docs say is not meant for automation;
that is open decision D6.

### 8. Topology spread, autoscaling, capacity

**AWS:** spread without `minDomains` spread nothing with one node; the
autoscaler only reacts to Pending pods.

**Azure mechanism:** AKS cluster autoscaler on VM scale set node pools (or
NAP, which is managed Karpenter); Kubernetes topology spread.

**Docs:**
- Cluster autoscaler "watches for pods ... that can't be scheduled"; "isn't
  zone-aware"; "If it determines that a pod cannot be scheduled on a new node
  due to restrictive topology spread constraints, it will not attempt to scale
  up." "Core quota exhaustion ... The cluster autoscaler enters an exponential
  backoff state"; backoff "might wait up to 30 minutes".
  [Cluster autoscaler overview](https://learn.microsoft.com/en-us/azure/aks/cluster-autoscaler-overview) (2026-09-04)
- NAP: "automatically deploys, configures, and manages Karpenter"; "You can't
  stop a cluster enabled with NAP".
  [Node auto-provisioning](https://learn.microsoft.com/en-us/azure/aks/node-autoprovision) (2026-02-15)
- `minDomains`: "When the number of eligible domains with match topology keys
  is less than minDomains, Pod topology spread treats global minimum as 0".
  `nodeTaintsPolicy`: "If this value is null, the behavior is equivalent to
  the Ignore policy" (tainted nodes still count as domains). Known limitation:
  "Pods matched on tainted nodes are respected."
  [Topology spread constraints](https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraints/) (Kubernetes v1.37 docs)
- Quota: "Quota is calculated based on the total number of cores in use both
  allocated and deallocated."
  [vCPU quotas](https://learn.microsoft.com/en-us/azure/virtual-machines/quotas) (2026-01-16)
  A family increase "usually happens within a few minutes" and raises the
  regional quota with it.
  [Increase VM-family vCPU quotas](https://learn.microsoft.com/en-us/azure/quotas/per-vm-quota-requests) (2025-01-22)

**Decision:** the cluster autoscaler on a dedicated, tainted workload pool
(not NAP: the plan rows need a fixed, explainable pool and NAP blocks cluster
stop). The hardened spread keeps `minDomains` (3 nodes; one zone-spread rule
only if the pool spans zones) and adds `nodeTaintsPolicy: Honor`, so a
stopped node with a NoSchedule taint stops counting as a domain and the
evicted replica goes Pending, which is what triggers the autoscaler. A
stopped VM keeps its quota, so the replacement needs headroom: 2 system + 3
workload D2s_v3 nodes use all 10 vCPUs. **Open decision D1:** raise the DSv3
family (and with it the region) to 16 vCPUs before the session.

### 9. Chaos target selection

**AWS:** a stale tag from an interrupted run made FIS pick a different node.

**Azure mechanism:** Chaos Studio, or a direct VM scale set instance stop by ID.

**Docs:**
- VM scale set Shutdown 2.0: the filter "Currently supports filtering on a
  list of zones ... If no filter is specified, this fault shuts down all
  instances in the virtual machine scale set." Version 1.0 takes "a delimited
  array of virtual machine scale set instance IDs". It "Restarts the VM at the
  end of the fault duration or if the experiment is canceled."
  [Fault library](https://learn.microsoft.com/en-us/azure/chaos-studio/chaos-studio-fault-library) (2026-09-05)
- "Experiments (classic) is being replaced by Chaos Studio Workspaces ...
  Microsoft no longer adds features to Experiments (classic)". Chaos Mesh
  faults need Chaos Mesh installed, and clusters with authorized IP ranges
  must allow the `ChaosStudio` service tag.
  [AKS Chaos Mesh faults](https://learn.microsoft.com/en-us/azure/chaos-studio/chaos-studio-tutorial-aks-cli) (2026-09-05)
- Price: `Azure Chaos Studio, Essential Action-Minute, $0.10`.

**Decision:** no Chaos Studio. The current shutdown fault targets zones, not
one instance, and the instance-ID version sits on the model being retired.
A4 stops exactly one instance by ID (`az vmss stop --instance-ids <id>
--skip-shutdown`), chosen at T0 as the node hosting the most web pods, and
the runner starts it again afterwards. No tags, so nothing can go stale. Pod
faults stay on the in-cluster Chaos Mesh, as on kind and EKS.

### 10. Long-lived connections through the managed API

**AWS:** port-forwards through the EKS API died over a two-hour run.

**Azure mechanism:** public API server with authorized IP ranges; the Azure
load balancer idle timeout on the node side.

**Docs:**
- Authorized ranges: "up to 200"; "The rules can take up to two minutes to
  propagate"; at create time "AKS adds the API server public IP and the
  outbound public IP of the Standard SKU load balancer ... by default".
  [API server authorized IP ranges](https://learn.microsoft.com/en-us/azure/aks/api-server-authorized-ip-ranges) (2026-08-03)
- The tunnel troubleshooting guide notes "a four-minute idle time-out to
  reclaim the SNAT ports from the idle flows" and lists port-forwarding among
  tunnel-dependent features.
  [Tunnel connectivity issues](https://learn.microsoft.com/troubleshoot/azure/azure-kubernetes/tunnel-connectivity-issues)

**Decision:** keep the EKS fix (every Hubble and Prometheus query reopens its
port-forward and a failed query raises). The API server allows the operator's
IPv4 /32 only, as on EKS. Port-forward survival over the session is live
check L8.

### 11. Leftovers the platform creates

**AWS:** a PDB stranded Karpenter nodes at teardown; cloud-created ENIs and
endpoints outlived Terraform.

**Azure mechanism:** the AKS node resource group (scale sets, NICs, managed
disks, load balancer, outbound public IP, kubelet identity), the
`MA_*_managed` group, Defender components, Key Vault soft delete, Log
Analytics.

**Docs:**
- Cluster delete removes "The node resource group and its resources".
  (Delete an AKS cluster, above.)
- Defender: "Turning off the Defender for Containers plan ... doesn't
  uninstall Defender components that are already deployed to clusters"; the
  profile is removed with `az aks update --disable-defender`; verify with
  `az security pricing show --name 'Containers'` showing `Free`.
  [Disable and remove Defender for Containers](https://learn.microsoft.com/en-us/azure/defender-for-cloud/defender-for-containers-remove) (2026-07-03)
- Key Vault: soft delete is on by default, retention "7 to 90 days", "You
  can't reuse the name of a key vault that was soft-deleted, until the
  retention period expires"; purge protection is "not enabled by default",
  and purge needs "Key Vault Purge Operator" or Owner.
  [Key Vault soft delete](https://learn.microsoft.com/en-us/azure/key-vault/general/soft-delete-overview) (2026-04-10)

**Decision:** teardown is ordered and fails closed: predestroy deletes the lab
app and its PDB, drains the workload pool and removes Cilium; the destroy plan
removes the cluster (and with it the node group), the Defender profile, the
Containers plan (back to `Free`), the workflow automation, the Logic App, the
policy assignment and definition, the Azure Monitor workspace, the vault
(7-day retention, no purge protection, then purged by the operator), and the
evidence snapshots. `aks-verify-gone` fails on any surviving lab-tagged
resource anywhere in the subscription, the node and `MA_` groups, any disk,
NIC, public IP or load balancer named for the cluster, a soft-deleted lab
vault, lab role assignments, a Containers plan other than `Free`, and any
Defender-created policy assignment or `DefaultResourceGroup-EUS2` that did not
exist before the session (the existing Central US default workspace is
recorded in the preflight and left alone).

### 12. Pricing traps

**Docs (Retail Prices API, East US 2, read 2026-11-04):**

| Item | Price | Session impact (4 to 5 hours) |
|---|---|---|
| AKS control plane, Free tier | free | No SLA; fine for a lab |
| AKS Standard tier (Uptime SLA) | $0.10 per cluster-hour | Not used |
| Standard_D2s_v3 | $0.096 per hour | 5 to 6 nodes: about $0.50 to $0.58 an hour |
| Defender for Containers | $0.00941 per vCore-hour | 10 to 12 vCores: about $0.11 an hour |
| ACNS | $0.025 per node-hour | Not used (row 1) |
| Azure Managed Grafana Standard | $0.034 per hour plus $6 per user per month | Not used; in-cluster Grafana as on EKS |
| Managed Prometheus ingestion | $0.16 per 10M samples | Cents with the remote-write allowlist |
| Logic Apps Consumption | $0.000025 per built-in action, $0.000125 per standard connector action | Cents |
| Standard static public IP | $0.005 per hour | One outbound IP |
| Premium SSD P10 (default 128 GiB managed OS disk) | $17.92 per month | Prorated cents; OS disks sized down to 64 GiB |
| Chaos Studio | $0.10 per action-minute | Not used |

**Decision:** about $0.75 to $0.90 an hour while up, roughly $4 to $5 for a
bounded session, before Log Analytics ingestion (capped with a daily quota).
Infracost runs before the session and the cost gate fails closed.

### 13. Identity propagation

**AWS:** IAM eventual consistency and cached denials.

**Docs:** "When you assign roles or remove role assignments, it can take up to
10 minutes for changes to take effect." For managed identities in groups,
"It can take several hours".
[Troubleshoot Azure RBAC](https://learn.microsoft.com/en-us/azure/role-based-access-control/troubleshooting) (2026-10-19)
Remote write role: "about 30 minutes" (row 3).

**Decision:** every role assignment is direct (no groups) and created in the
infrastructure apply. The platform step proves each one before the demo (ESO
reads the vault, Prometheus writes without 403, the Logic App identity can
run its first action against a no-op target) rather than discovering a
propagation delay inside a row.

### 14. Public access defaults on storage

**AWS:** the workload account had no account-level S3 public access block.

**Docs:** "By default, anonymous access to your blob data is always
prohibited. The default configuration for an Azure Resource Manager storage
account prohibits users from configuring anonymous access".
[Prevent anonymous read access](https://learn.microsoft.com/en-us/azure/storage/blobs/anonymous-read-access-prevent) (2026-07-15)

**Decision:** the evidence account still sets it explicitly
(`allow_nested_items_to_be_public = false`), disables shared key auth (Entra
only), requires TLS 1.2, and keeps public network access on only because a
Consumption Logic App writes the manifest from shared outbound IPs; checkov
and the preflight assert all of it.

### 15. Existing responders that could act on lab alerts

**AWS:** the security account's forensics pipeline reacted to every GuardDuty
finding and tried to act on a lab system node.

**Verified live:** no workflow automations, Logic Apps, Event Grid system
topics, alert rules, action groups, Sentinel workspace or security contacts.
Defender auto-provisioning is `Off`.

**Decision:** the lab's workflow automation is scoped to its own resource
group and alert filter, and the Logic App refuses any alert whose cluster is
not the lab cluster (unit tested, like the R2 responder on EKS).

### 16. Agent DaemonSets on tainted nodes

**AWS:** verified OK live (GuardDuty agent on tainted workload nodes).

**Docs:** the sensor runs as `microsoft-defender-collector-ds-*` and
`microsoft-defender-publisher-ds-*` DaemonSets in `kube-system`, limits
`memory: 296Mi cpu: 360m` and `200Mi / 60m`, egress `*.cloud.defender.microsoft.com`
on 443, "You can't configure resource limits". The docs say nothing about
tolerations.
[Container security architecture](https://learn.microsoft.com/en-us/azure/defender-for-cloud/defender-for-containers-architecture) (2026-05-18),
[Network access](https://learn.microsoft.com/en-us/azure/defender-for-cloud/defender-for-containers-network-access) (2026-04-16)

**Decision:** sizing counts the sensor's 420m CPU per node. Whether it lands on
the tainted workload nodes is live check L3; if it does not, R2 is recorded
as not runnable on this cluster (a miss with a reason), never skipped.

### 17. Node repair interplay

**AWS:** Karpenter repair waits 30 minutes, so it was configured, not judged.

**Docs:** "If AKS identifies an unhealthy node that stays unhealthy for at
least five minutes, AKS ... reboots ... reimages ... redeploys"; "can take up
to one hour"; it may not repair a node with the
`node.cloudprovider.kubernetes.io/shutdown` taint; events come from the
`aks-auto-repair` source (`NodeRebootStart`, ...).
[Node auto-repair](https://learn.microsoft.com/en-us/azure/aks/node-auto-repair) (2026-07-01)
Node OS channel `NodeImage`: "weekly cadence ... disruptive, following
maintenance windows and surge settings"; "use a maintenance window of four
hours or more". [Node OS auto-upgrade](https://learn.microsoft.com/en-us/azure/aks/auto-upgrade-node-os-image) (2026-06-26)
Node controller: `--node-monitor-grace-period` "Default: 50s".
[kube-controller-manager](https://kubernetes.io/docs/reference/command-line-tools-reference/kube-controller-manager/)

**Decision:** auto-repair is on (it cannot be turned off) and is recorded, not
judged, as on EKS. A4's stopped node carries the shutdown taint, so repair is
expected to stay out of A4; the runner records any `aks-auto-repair` event in
the row's window, and a repair inside A4 voids the row as an error. Node image
auto-upgrade is `NodeImage` with a 4-hour weekly window the preflight checks
is not during the session; the cluster version channel is `none` so the
control plane cannot upgrade mid-run. With BYO CNI, nodes are NotReady until
Cilium is installed; repair could reboot one if that takes over five minutes
(live check L1).

## Other facts the design rests on

- **No SSH.** Disabling the SSH service is preview ("`--ssh-access disabled`
  (preview)", feature flag `DisableSSHPreview`), and "By default, the SSH
  service on AKS cluster nodes is open to all users and pods running on the
  cluster." [Manage SSH access](https://learn.microsoft.com/en-us/azure/aks/manage-ssh-node-access) (2025-11-10).
  Decision: no preview features. No SSH key is kept (the key AKS requires is
  generated and discarded), nodes have no public IPs, the node subnet NSG
  denies 22 inbound, and the hardened Cilium policy denies pod egress to the
  host on 22. Node access is Run Command, as the plan says.
- **OS disks.** "When you don't explicitly request Azure managed disks for
  the OS, AKS defaults to ephemeral OS if possible". An ephemeral disk lives
  only on the host and cannot be snapshotted, so node pools set
  `os_disk_type = "Managed"` for R2's disk snapshot.
  [Storage concepts](https://learn.microsoft.com/en-us/azure/aks/concepts-storage) (2026-07-30)
- **Node capture.** AKS's own docs check node state with `az vmss run-command
  invoke ... --command-id RunShellScript --instance-id 0` (Manage SSH access,
  above). The managed Run Command page applies to "Linux VMs ✔️ Flexible
  scale sets", not the Uniform sets AKS uses, so R2 uses the action Run
  Command on the scale set instance.
- **Region.** East US 2: on the landing zone's allowed list, Azure Monitor
  workspace available, AKS 1.34 offered. Central US is avoided (the landing
  zone hit `AKSCapacityHeavyUsage` there six times).

## Open decisions for the operator

**Amendment (2026-11-05, before any apply):** D1 as written could not be
carried out. Azure refused the DSv3 increase (`DeprecatedQuotaType`; the
series is under capacity growth restrictions since July 2026) and
`Standard_D2s_v5` is not available to the subscription. The operator chose
`Standard_D2s_v6` for every node with the Dsv6 family raised to 16. ADR-0008
records the facts; the text below is kept as it was decided.

- **D1, quota.** Raise the DSv3 family in East US 2 from 10 to 16 vCPUs (the
  regional quota follows). CLI path, run by the operator:
  `az provider register -n Microsoft.Quota`, then
  `az quota update --resource-name standardDSv3Family --scope /subscriptions/<sub>/providers/Microsoft.Compute/locations/eastus2 --limit-object value=16 --resource-type dedicated` (the form in
  `az quota update --help`, quota extension 1.0.0).
  Without it, A4 cannot get a replacement node and node image upgrades have
  no surge room. Recommended.
- **D2, CNI.** BYO CNI with the pinned Cilium (recommended, row 1), or managed
  Cilium plus ACNS ($0.025 per node-hour, Microsoft-supported, but a different
  dataplane under the experiment and no Hubble relay for the runner).
- **D3, A4 error clock.** A stopped VM is not deleted the way a terminated EC2
  instance is: the Node stays, and its endpoints keep receiving traffic until
  the node controller marks it NotReady (50s grace). With three replicas,
  about a third of requests fail for that minute in **both** profiles, which
  alone breaks a 1% bar over the window. Proposal, set before any run: judge
  A4's error rate from the moment the node is marked NotReady (detection),
  report the gap before it, exactly as A1 judges after detection and A3
  reports its ramp; plus every replica serving within the bar. The
  alternative keeps 1% from T0 and A4 is expected to miss on AKS.
- **D4, node isolation gap (R2).** Accept that AKS has no supported per-node
  network quarantine, so R2 isolates by cordon plus the pod quarantine policy
  and lists the gap. Recommended.
- **D5, "remediation task" in S4.** The DINE assignment remediates a newly
  opened rule on its own when the write lands (that is the detective layer
  the row measures). A remediation task is how Azure Policy fixes resources
  that were already non-compliant; creating one by hand during the row would
  be a human action. Proposal: the hardened profile relies on the automatic
  DINE deployment; the runbook adds a remediation task only as the backstop
  step after teardown-time evaluation. If the event path does not fire for a
  child rule, S4 is a miss.
- **D6, R2 call path into the cluster.** (a) the Logic App calls AKS
  `command invoke` through ARM (one ordered `kubectl cordon && kubectl label`;
  the docs say it is "Not for programmatic access" and its pod must schedule
  within 60s), or (b) the Logic App enqueues the incident and a small
  in-cluster worker with workload identity performs cordon then relabel, while
  the Logic App does the Azure-side steps (Run Command, snapshot, manifest).
  Recommended: (b), because it follows the docs; (a) is fewer parts.

## Proposed AKS thresholds (to be written into ADR-0003 and `THRESHOLDS["aks"]` before the first run)

Rows shared with kind and EKS keep their numbers (A3 stays at 300 ms). The
Azure-native rows:

| Row | Threshold | Why |
|---|---|---|
| A4 | every web replica serving within 600s; errors under 1% from NotReady on (D3) | NotReady at about 50s, eviction after a 30s hardened toleration, an autoscaled D2s_v3 node in about 3 to 5 minutes, then pod start |
| S4 | rule closed by the policy's own deployment within 1200s | DINE runs after its `evaluationDelay` (set to `AfterProvisioningSuccess`); policy event data appears "around 15 minutes later" |
| R2 | alert within 3600s (documented bound, reported); pod isolated within 300s of the alert; evidence within 600s of the alert; errors under 1% | detection is Defender's clock ("up to an hour"); response is the lab's |
| R1 | unchanged, 30s | same Cilium, same pre-allocated identity |

## Live checks (runbook section 2; the docs do not settle these)

- **L1** BYO CNI: AKS create completes with NotReady nodes; Cilium values
  match the kube-proxy and CoreDNS AKS installs; whether auto-repair reboots a
  node before Cilium is up.
- **L2** Workflow automation delivers to a Logic App whose trigger is a plain
  HTTP Request (not the Defender connector), and the alert payload's field
  names for pod, namespace and node.
- **L3** Defender sensor pods run on the tainted workload nodes.
- **L4** Which alert the test-domain request raises from a `python3` process
  (DNS lookup alone, or the HTTP request), and its delay.
- **L5** ESO reads the latest Key Vault version with no `version` field.
- **L6** DINE fires for a child `securityRules` write made with
  `az network nsg rule create`, and how long it takes.
- **L7** A stopped scale set instance gets the
  `node.cloudprovider.kubernetes.io/shutdown` taint, and auto-repair leaves it
  alone.
- **L8** Hubble and Prometheus port-forwards across a multi-hour run.
- **L9** Prometheus remote write to the DCR without 403 after the role
  assignment settles.
- **L10** The Azure RBAC data actions the responder needs (patch node, patch
  pod) map to the documented `.../nodes/write` and `.../pods/write`.

## Sources (read 2026-11-04)

Microsoft Learn: Azure CNI Powered by Cilium; Bring your own CNI; AKS support
policies; Advanced Container Networking Services overview; Node auto-repair;
Cluster autoscaler overview; Node auto-provisioning; Node resource group
lockdown; Delete an AKS cluster; Command invoke; API server authorized IP
ranges; Manage SSH access; Storage concepts; Node OS auto-upgrade; vCPU
quotas; Increase VM-family vCPU quotas; Managed Run Command; Defender for
Containers introduction, architecture, network access, enable, remove,
Kubernetes alerts, alert validation, workflow automation, continuous export;
Azure Policy deployIfNotExists, modify, remediation, compliance data; Azure
Monitor workspace overview and management; Prometheus remote write (direct
and sidecar); Key Vault soft delete; Key Vault dual-credential rotation;
Troubleshoot Azure RBAC; Prevent anonymous blob access; Logic Apps HTTP
action; Chaos Studio fault library and AKS Chaos Mesh tutorial; AKS tunnel
connectivity troubleshooting. Also: Kubernetes topology spread constraints
and kube-controller-manager reference; External Secrets Operator Azure Key
Vault provider; Microsoft Defender for Cloud Attack Simulation repository;
Azure Retail Prices API.
