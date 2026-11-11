"""The EKS Argo CD inclusion list replaces the base list, so it must cover it.

A kind missing here is invisible to Argo CD on EKS: the object syncs but never
reports health (this stalled the Q1 ScaledObject in evidence/eks-20261109-211212).
Run: make test
"""
import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


def kinds(path):
    cfg = yaml.safe_load((ROOT / path).read_text())
    inc = yaml.safe_load(cfg["configs"]["cm"]["resource.inclusions"])
    return {(g, k) for e in inc for g in e["apiGroups"] for k in e["kinds"]}


class ArgoInclusions(unittest.TestCase):
    def test_eks_is_superset_of_base(self):
        missing = kinds("platform/argo-cd.yaml") - kinds("platform/eks/argo-cd.yaml")
        self.assertFalse(missing, f"EKS inclusion list is missing {sorted(missing)}")

    def test_eks_adds_nodepool(self):
        self.assertIn(("karpenter.sh", "NodePool"), kinds("platform/eks/argo-cd.yaml"))

    def test_aks_has_no_override(self):
        self.assertFalse((ROOT / "platform/aks/argo-cd.yaml").exists())


if __name__ == "__main__":
    unittest.main()
