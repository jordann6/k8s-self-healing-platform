# ADR-0012: N1 WireGuard encryption contract

- **Status:** accepted
- **Date:** 2026-11-08

## Context

The existing Cilium controls prove policy enforcement and Hubble attribution,
but do not prove confidentiality for traffic crossing between nodes. N1 adds a
separate two-worker kind experiment. It does not change the existing one-node
kind proofs.

Primary references:

- <https://docs.cilium.io/en/stable/security/network/encryption-wireguard/>
- <https://docs.cilium.io/en/stable/security/network/encryption/>
- <https://docs.cilium.io/en/stable/operations/system_requirements/>

Cilium WireGuard creates one key pair per node and exposes its tunnel on UDP
51871. Same-node pod traffic is intentionally not encrypted. In tunnel mode,
cross-node pod traffic is first encapsulated by VXLAN and then protected by the
WireGuard tunnel.

## Decision

N1 uses a control plane and two workers. The legitimate and denied clients are
pinned to one worker and the server is pinned to the other. Both profiles use
the same workloads and CiliumNetworkPolicy.

Baseline keeps Cilium WireGuard disabled. Hardened enables WireGuard and beta
node encryption. The control-plane exception remains at Cilium's safe default.
The claim is limited to traffic between Cilium-managed endpoints on different
worker nodes.

The underlay capture is taken on the server worker's `eth0` and filtered to the
client and server worker InternalIPs. Baseline must show VXLAN UDP 8472 with
decoded inner addresses matching the N1 client and server pods, plus the unique
HTTP marker. Hardened must show WireGuard UDP 51871, no VXLAN packet attributed
to that inner N1 pod pair during the request window, and no plaintext marker.
The raw capture and report retain the total worker-pair VXLAN count so unrelated
background traffic remains visible. Hardened must also show matching WireGuard peer keys, remote
worker InternalIP routes, and a completed handshake on both workers. With
VXLAN tunneling, Cilium encrypts tunnel traffic between worker underlay
addresses, so the WireGuard peer route is the remote worker address rather
than the inner pod address.

## Fixed thresholds

| Observation | Threshold |
|---|---:|
| Legitimate requests | at least 20 |
| Legitimate failures | 0 |
| Legitimate p95 | at most 300 ms |
| Denied attempts | at least 5 |
| Successful denied attempts | 0 |
| Attributable Hubble policy drops | at least 1 |
| Expected underlay packets | at least 5 |
| Worker WireGuard interfaces | exactly 1 per worker |
| Worker peers | at least 1 per worker |

## Honesty rules

- Client and server must be on different worker nodes.
- Missing packet capture is an error.
- Missing Cilium encryption status is an error.
- Missing peer keys, routes, or handshake telemetry is an error.
- Missing Hubble telemetry is an error.
- A failed request does not prove encryption.
- A denied request without an observed policy drop does not prove policy.
- Desired Helm values alone never prove encryption.
- Same-node, control-plane, north-south, and non-Cilium traffic are outside the
  local claim.
- WireGuard node encryption is beta in Cilium 1.20.4 and is documented as such.

## Consequences

The experiment uses a dedicated multi-node cluster and a focused platform to
keep the proof attributable. Packet evidence records metadata and the synthetic
marker only. It never records credentials or user data.
