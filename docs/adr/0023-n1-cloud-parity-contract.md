# ADR 0023: N1 cloud parity contract

Status: ACCEPTED 2026-11-10 by Jordan (the same standing "do what's best, follow the initial
plan" instruction as ADR-0021 and ADR-0022). Source is built (unit tests and gates green);
nothing is applied to any cloud yet.

## Context

ADR-0012 fixed the N1 contract on a two-worker kind cluster. Cilium runs in VXLAN tunnel mode
there, the baseline shows VXLAN on UDP 8472, and the hardened profile (WireGuard plus beta node
encryption) replaces it with UDP 51871 and no plaintext marker. ADR-0018 decided the cloud
outline (AKS keeps the kind contract, EKS needs its own hypothesis and a different capture, and N1
runs last in a session because it restarts every Cilium agent). This ADR reads the kind
implementation against the cloud Cilium configuration and the vendor documentation (read
2026-11-10), lists what does not survive the move, and fixes the cloud contract before any code.
No threshold below comes from a run.

## Facts that change the kind contract

1. AKS runs the same datapath as kind. `cilium/values-aks.yaml` sets `routingMode: tunnel` with
   VXLAN, so the kind baseline and hardened observations transfer as written. Ubuntu 24.04 nodes
   run a 6.8 kernel, above the 5.6 WireGuard requirement, and the Azure NIC is `eth0` as on kind.
2. EKS runs ENI IPAM with `routingMode: native` (`cilium/values-eks.yaml`). There is no VXLAN, so
   the kind baseline check ("VXLAN UDP 8472 with decoded inner addresses") can never pass and the
   capture filter in `n1_row.capture`, which matches only UDP 8472 or 51871, would capture nothing
   in the baseline. Cross-node pod traffic leaves with the pod addresses on the VPC fabric.
3. The Cilium WireGuard page says that for peers "`allowed-ips` should contain a list of pod IP
   addresses running on the remote", that node public keys travel in the
   `network.cilium.io/wg-pub-key` annotation of the `CiliumNode`, and that the tunnel endpoint is
   UDP 51871 on each node. In tunnel mode (kind, AKS) ADR-0012 already observed the peer route to
   be the remote worker address. In native routing it should be the remote pod addresses, so the
   route check must name the pod IPs on EKS. The page mentions ENI IPAM only through the AWS VPC
   CNI chaining example, so WireGuard with ENI IPAM is not documented. The overview page lists no
   IPAM modes at all. This is the central unknown of the EKS half.
4. Cilium documents that WireGuard "might get fragmented" and that node-to-node encryption is beta
   and opts out nodes labelled control-plane. Managed control planes have no such nodes, so the
   exception does nothing here. It also lists "Packets may be dropped when configuring the
   WireGuard device leading to connectivity issues" as a known issue. Cilium needs UDP 51871
   reachable between all nodes. The EKS node security group already trusts itself on all
   protocols (ADR-0018), and AKS node traffic inside the VNet is allowed by the default NSG rules
   that already carry VXLAN.
5. A capture on `any` cannot prove the absence of plaintext. On the server's node the server pod's
   veth and `cilium_wg0` carry the traffic after decryption, so the marker is visible there in the
   hardened profile too. The plaintext check must read only the physical NICs the packets leave on.
   On the clouds the NIC name is not `eth0` everywhere: kind's `eth0` is a container veth, Azure's
   `eth0` is the NIC, and an ENA NIC on a Nitro instance is expected under another name and a
   secondary ENI carries pod traffic under ENI IPAM (live check L4, not assumed).
6. `apply_n1_profile` switches Cilium with `helm upgrade ... -f cilium/values.yaml -f
   cilium/n1-<profile>.yaml`, the kind values only. Run against EKS or AKS it would silently
   replace ENI IPAM or the AKS BYO CNI settings with kind's defaults and break the cluster. The
   platform scripts install Cilium with `values.yaml` plus `values-eks.yaml` or `values-aks.yaml`.
7. The N1 workload pins the client and server by node labels `selfheal.lab/n1-slot` that
   `kind/n1-cluster.yaml` puts on two workers, and the capture pod needs a privileged-labelled
   namespace that only the kind Makefile creates. The cloud nodes carry neither. Both clouds have
   at least two untainted system nodes (EKS `min_size = 2`, AKS three).
8. The N1 images are loaded into kind, not pushed. The capture image is pinned by digest and
   the workload image is the repository's own build.

## Decision

1. Contract. The ADR-0012 sequence, workloads, policy and every observation threshold stay as
   written on both clouds. Kind is untouched in behaviour.
2. AKS keeps the kind contract unchanged, with the capture on `eth0`.
3. EKS has its own hypothesis, because the mechanism differs. Native routing means the baseline
   proof is plaintext, not VXLAN.
   - Baseline. Cross-node pod traffic between the client and server pod IPs is visible as plaintext
     TCP on the physical NICs, carries the unique HTTP marker, and no WireGuard packet is seen
     between the two nodes.
   - Hardened. UDP 51871 is seen between the two node IPs, no TCP packet between the two pod IPs
     leaves on a physical NIC, the marker is not visible, and the WireGuard facts hold: exactly one
     interface per node, matching peer keys, a completed handshake on both nodes, and each peer's
     `allowed-ips` containing the remote pod IP (not the node IP as on kind).
   - Switching EKS to tunnel mode for N1 is rejected. It would change the datapath every other EKS
     row was proven on.
4. Capture. The capture lists the physical interfaces of the server node and reads every one.
   - Kind and AKS read `eth0`, as before. EKS discovers its interfaces from the capture pod (host
     network): every device under `/sys/class/net` whose resolved path is not under `virtual`,
     which excludes `lxc*`, `cilium_*`, `veth` and `lo`.
   - One `tcpdump` per interface runs for the same window and the facts are summed. The interface
     names are recorded in the evidence. An empty interface list is an error.
   - The filter is `( udp port 51871 and host <client node> and host <server node> ) or ( tcp and
     host <client pod> and host <server pod> )` on EKS, and the existing VXLAN and WireGuard filter
     on kind and AKS.
5. Facts. `capture_facts` gains `plain_packets`, the packets between the client and server pod
   addresses in either direction on the server port, and `wireguard_facts` takes the addresses the
   peer routes must contain: the node addresses on kind and AKS, the pod addresses on EKS.
6. Helm. The profile switch builds its value files per target: `values.yaml`, then
   `values-<target>.yaml` on a cloud, then `n1-<profile>.yaml`, so ENI IPAM, tunnel mode and BYO
   CNI survive the switch. Hardened keeps WireGuard plus beta node encryption on both clouds for
   parity with the kind contract. The hardened upgrade restarts every agent, so N1 stays last in a
   session, and nothing runs after it unless the runner switches back to baseline.
7. Placement. At row start the runner labels two distinct system nodes `selfheal.lab/n1-slot` as
   `client` and `server`, reusing existing labels so a rerun never moves the pins. Fewer than two
   usable nodes is an error. The capture pod is pinned to the server node and the row errors if
   any of the four pods lands elsewhere or if the client and server share a node.
8. Namespace and images. The privileged capture namespace moves to `bootstrap/n1-capture-namespace.yaml`
   and both cloud bootstraps apply it, the kind Makefile applies the same file. `eks-images.sh` and
   `aks-images.sh` push `n1` and `n1-capture` and extend the generated component. ECR gains two
   repositories. Overlays `gitops/targets/<cloud>/n1/{baseline,hardened}` wrap the kind profile
   and add the component, and each cloud's root apps gain `n1.yaml`.
9. Honest outcomes on EKS. If WireGuard does not encrypt pod traffic under ENI IPAM, the hardened
   result is a miss (the marker or pod TCP stays visible, or the peers carry no pod routes). If
   the agent cannot start with WireGuard, the rollout wait fails and the row is an error. Neither
   is relaxed. A miss would be recorded as a documented EKS limitation by a later amendment that
   names the run, as the AKS Defender result was for R2.
10. Versions. Session plans pass 1.36 as in ADR-0019 Decision 7.

## Thresholds

Identical to ADR-0012 on kind, EKS and AKS.

| Observation | kind | AKS | EKS |
|---|---:|---:|---:|
| Legitimate requests | at least 20 | same | same |
| Legitimate failures | 0 | 0 | 0 |
| Legitimate p95 | at most 300 ms | same | same |
| Denied attempts | at least 5 | same | same |
| Successful denied attempts | 0 | 0 | 0 |
| Attributable Hubble policy drops | at least 1 | same | same |
| Underlay packets that attribute the profile | at least 5 | at least 5 | at least 5 |
| Worker WireGuard interfaces | exactly 1 each | same | same |
| Worker peers | at least 1 each | same | same |

What counts as an underlay packet differs by datapath and is stated in the hypothesis. VXLAN on
kind and AKS, plaintext pod TCP in the EKS baseline, and UDP 51871 in every hardened profile.
Missing capture, missing interface list, missing Cilium status, missing peer, key, route or
handshake telemetry and missing Hubble telemetry stay errors.

## Verification plan

- Unit tests. Plaintext packet counting in both directions and ignoring unrelated traffic, the
  pod-IP route check next to the node-IP one, the EKS judge branch (baseline impact, hardened
  healed, marker still visible is a miss, no pod route is a miss, no interfaces is an error, VXLAN
  absence on EKS is not required), interface discovery parsing, per-target Helm value files, node
  slot labelling and thresholds. `make check` and `make test` stay green.
- Overlays render for both clouds and both profiles with the registry images and no kind image name.
- Local proof stays kind. Kind N1 is re-run after the change because the capture and the facts
  helpers are shared. The paid proof waits for the batched session (ADR-0018 Decision D).

## Live checks

| # | Check |
|---|---|
| L3 | WireGuard loads on the pinned AL2023 AMI and on AKS Ubuntu 24.04 (agent status shows WireGuard, no crash loop) |
| L4 | Which interface carries pod traffic under ENI IPAM, that the WireGuard endpoints are the node primary addresses, and that each peer's `allowed-ips` holds the remote pod IPs |
| L5 | MTU after WireGuard on 9001-byte ENIs and 1500-byte Azure NICs, with no fragmentation of the probe |
| L25 | Cross-node pod traffic is actually encrypted under ENI IPAM on EKS, the central unknown |
| L26 | Beta node encryption does not break node-to-node host traffic the cluster needs (Hubble relay, operator, kubelet probes) on either managed cloud |
| L27 | The discovered underlay interfaces are the NICs that carry pod and WireGuard traffic on EKS, and `eth0` on AKS |

## Cost and session boundary

No hourly resource. Two small ECR repositories and one tiny hostNetwork capture pod. The proof
reuses the session's bounded minutes and runs last, after which the cluster is destroyed and
verified gone. Never push before Phase 5.

## References

- [Cilium WireGuard](https://docs.cilium.io/en/stable/security/network/encryption-wireguard/) (read 2026-11-10)
- [Cilium transparent encryption](https://docs.cilium.io/en/stable/security/network/encryption/)
- ADR-0007, ADR-0012, ADR-0018, ADR-0019, ADR-0021, ADR-0022
