# ADR 0016: Q1 cloud parity contract

Status: accepted before the first Q1 cloud experiment on 2026-11-09.

## Context

Q1 proved on kind that a RabbitMQ backlog can activate a worker from zero,
survive a busy worker deletion without losing work, drain, and return to zero.
Phase 4 must run the same workload and verdict on EKS and AKS without weakening
the kind thresholds or substituting cloud service health for measured work.

AWS documents installing KEDA on EKS with its Helm chart in a dedicated
namespace. AKS supplies a managed KEDA add-on in `kube-system`; Microsoft states
that it must be the cluster's only external metrics server. The AKS module
already enables that add-on for row A3, so a second KEDA installation is not
allowed.

## Decision

Use one Q1 contract across all three targets:

- EKS installs the repository's pinned KEDA chart. AKS uses its managed add-on.
- The same RabbitMQ and qworker manifests run on all targets.
- The qworker image is built once per target session from the repository commit,
  pushed to ECR or ACR, and selected through a generated Kustomize component.
- RabbitMQ credentials are generated in cluster before Argo CD creates Q1. No
  credential value enters Git, Terraform state, a command result, or evidence.
- The cloud root applications own a separate `selfheal-q1` application. KEDA
  owns the hardened Deployment replica count, so Argo CD ignores only that
  field.
- A focused Q1 cloud run may skip unrelated web, monitoring, Falco, and responder
  readiness checks. It still requires the API, Cilium, Argo CD, Git server,
  target KEDA deployments, RabbitMQ, and qworker.

Keep the fixed kind thresholds on both clouds. The 240-message backlog must
drain within 120 seconds and the worker must return to zero within 120 seconds
after the drain. Missing KEDA readiness, queue telemetry, completion IDs, kill
attribution, or replica telemetry is an error.

## Cost and session boundary

The source change adds one encrypted ECR repository and image storage. It adds
no hourly AWS or Azure resource. Live proof reuses each lab's bounded EKS or AKS
session, so the existing cluster cost applies only while that session is up.
The operator reviews a fresh saved Terraform plan and projected cost before any
apply, then destroys and verifies the target through its existing runbook.

## References

- [Amazon EKS, autoscale AI inference with HPA and KEDA](https://docs.aws.amazon.com/eks/latest/userguide/ml-inference-autoscaling-hpa-keda.html)
- [Microsoft Learn, Kubernetes Event-Driven Autoscaling in AKS](https://learn.microsoft.com/azure/aks/keda)
