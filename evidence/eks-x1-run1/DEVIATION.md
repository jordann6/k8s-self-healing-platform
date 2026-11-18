# Deviation from the committed configuration

The report's `repo_commit` reads `a15a195-dirty`. The only working-tree change
that touched this cluster was the Karpenter NodePool CPU limit, cut from 24 to
12 in both `gitops/targets/eks/baseline/nodepool.yaml` and
`gitops/targets/eks/hardened/nodepool.yaml`. The account's on-demand vCPU
quota (L-1216C47A) was 16, with an increase to 32 still pending, so preflight
skipped the vCPU check and the limit was lowered to fit under the quota. The
edit is not committed and is reverted after teardown.

Neither row scales nodes, so the lower limit does not affect either verdict.
