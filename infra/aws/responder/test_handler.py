"""The R2 responder's refusal rules, tested without AWS or a cluster.

The responder acts on production-shaped evidence, so its first job is to
refuse: a finding for another cluster or namespace, a synthetic event, or a
finding that maps to more than one pod must never move a node into
quarantine. Run: make test
"""
import os
import sys
import types
import unittest

# boto3 ships with the Lambda runtime; the pure functions tested here never
# call it, so a stub keeps the tests dependency-free.
sys.modules.setdefault("boto3", types.ModuleType("boto3"))
os.environ.update({"CLUSTER_NAME": "selfheal", "CLUSTER_ENDPOINT": "https://example.invalid",
                   "CLUSTER_CA": "", "LAB_NAMESPACE": "selfheal"})

import handler  # noqa: E402


def finding(**kw):
    d = {
        "id": "f1", "type": "Backdoor:Runtime/C&CActivity.B!DNS", "severity": 8,
        "service": {"eventFirstSeen": "2026-11-04T12:00:00Z"},
        "resource": {
            "resourceType": "EKSCluster",
            "eksClusterDetails": {"name": "selfheal"},
            "kubernetesDetails": {"kubernetesWorkloadDetails": {
                "name": "web-7d9f-abcde", "type": "pods", "uid": "pod-uid-1", "namespace": "selfheal",
                "containers": [{"name": "web"}]}},
        },
    }
    for k, v in kw.items():
        d["resource"][k] = v
    return d


def pod(name, uid, owner="rs-uid", app="web", deleting=False):
    md = {"name": name, "uid": uid, "labels": {"app": app}, "ownerReferences": [{"uid": owner}]}
    if deleting:
        md["deletionTimestamp"] = "2026-11-04T12:00:00Z"
    return {"metadata": md, "spec": {"containers": [{"name": "web"}], "nodeName": "n1"}}


class FindingTarget(unittest.TestCase):
    def test_lab_finding_is_accepted(self):
        t = handler.finding_target(finding())
        self.assertEqual(t["workload"]["uid"], "pod-uid-1")
        self.assertEqual(t["containers"], ["web"])

    def test_other_cluster_is_refused(self):
        with self.assertRaisesRegex(ValueError, "not 'selfheal'"):
            handler.finding_target(finding(eksClusterDetails={"name": "prod"}))

    def test_other_namespace_is_refused(self):
        f = finding()
        f["resource"]["kubernetesDetails"]["kubernetesWorkloadDetails"]["namespace"] = "kube-system"
        with self.assertRaisesRegex(ValueError, "namespace"):
            handler.finding_target(f)

    def test_non_eks_finding_is_refused(self):
        with self.assertRaisesRegex(ValueError, "not an EKS finding"):
            handler.finding_target(finding(resourceType="Instance"))


class MatchPods(unittest.TestCase):
    def setUp(self):
        self.t = handler.finding_target(finding())

    def test_pod_uid_matches_exactly_one(self):
        pods = [pod("a", "pod-uid-1"), pod("b", "pod-uid-2")]
        self.assertEqual([p["metadata"]["name"] for p in handler.match_pods(self.t, pods)], ["a"])

    def test_owner_uid_matching_several_pods_is_ambiguous(self):
        t = dict(self.t, workload=dict(self.t["workload"], uid="rs-uid"))
        self.assertEqual(len(handler.match_pods(t, [pod("a", "1"), pod("b", "2")])), 2)

    def test_non_app_and_terminating_pods_are_never_candidates(self):
        pods = [pod("a", "pod-uid-1", app="partner"), pod("b", "pod-uid-1", deleting=True)]
        self.assertEqual(handler.match_pods(self.t, pods), [])


class Resolve(unittest.TestCase):
    def test_synthetic_source_is_not_acted_on(self):
        r = handler.resolve({"source": "selfheal.demo", "detail": finding()})
        self.assertFalse(r["act"])
        self.assertIn("not aws.guardduty", r["reason"])

    def test_ambiguous_finding_raises_instead_of_guessing(self):
        handler.k8s = lambda *a, **kw: {"items": [pod("a", "1"), pod("b", "2")]}
        ev = {"source": "aws.guardduty", "detail": finding()}
        ev["detail"]["resource"]["kubernetesDetails"]["kubernetesWorkloadDetails"]["uid"] = "rs-uid"
        with self.assertRaisesRegex(RuntimeError, "refusing to guess"):
            handler.resolve(ev)


class Preconditions(unittest.TestCase):
    """The mutations re-read the target and refuse a replacement, so a repeated
    or delayed response never quarantines an innocent pod or node."""

    def setUp(self):
        self.ctx = {"pod": "web-1", "pod_uid": "pod-uid-1", "node": "n1",
                    "node_uid": "node-uid-1", "finding": {"finding_id": "f1"}}

    def _stub(self, live_pod_uid="pod-uid-1", live_node_uid="node-uid-1"):
        calls = []

        def k8s(method, path, body=None, patch="merge"):
            calls.append((method, path, body))
            if method == "GET" and "/pods/" in path:
                return {"metadata": {"uid": live_pod_uid, "resourceVersion": "100"}}
            if method == "GET" and "/nodes/" in path:
                return {"metadata": {"uid": live_node_uid, "resourceVersion": "200"}}
            return {}
        handler.k8s = k8s
        return calls

    def test_isolate_rereads_then_patches_with_precondition(self):
        calls = self._stub()
        out = handler.isolate_pod(self.ctx)
        methods = [c[0] for c in calls]
        self.assertEqual(methods, ["GET", "PATCH"])  # re-read, then patch
        patch_body = calls[1][2]
        self.assertEqual(patch_body["metadata"]["uid"], "pod-uid-1")
        self.assertEqual(patch_body["metadata"]["resourceVersion"], "100")
        self.assertEqual(out["precondition"], {"pod_uid": "pod-uid-1", "resource_version": "100"})

    def test_isolate_refuses_replacement_pod(self):
        calls = self._stub(live_pod_uid="pod-uid-2")
        with self.assertRaisesRegex(RuntimeError, "refusing to act on a replacement pod"):
            handler.isolate_pod(self.ctx)
        self.assertEqual([c[0] for c in calls], ["GET"])  # no PATCH after the uid mismatch

    def test_cordon_refuses_replacement_node(self):
        calls = self._stub(live_node_uid="node-uid-2")
        with self.assertRaisesRegex(RuntimeError, "refusing to act on a replacement node"):
            handler.cordon(self.ctx)
        self.assertEqual([c[0] for c in calls], ["GET"])

    def test_isolate_is_idempotent_on_the_same_pod(self):
        # Two deliveries for the same live pod apply the same quarantine labels.
        self._stub()
        a = handler.isolate_pod(self.ctx)
        b = handler.isolate_pod(self.ctx)
        self.assertEqual(a["labels"], b["labels"])
        self.assertEqual(a["labels"], handler.QUARANTINE_LABELS)


class CaptureCommands(unittest.TestCase):
    def test_only_hex_container_ids_reach_the_shell(self):
        cmds = "\n".join(handler.capture_commands(["abc123def456", "x; rm -rf /", "ABCDEF123456"]))
        self.assertIn("abc123def456", cmds)
        self.assertNotIn("rm -rf", cmds)
        self.assertNotIn("ABCDEF", cmds)

    def test_instance_id_must_come_from_provider_id(self):
        with self.assertRaises(ValueError):
            handler.instance_of({"metadata": {"name": "n1"}, "spec": {"providerID": "kind://docker/x"}})
        self.assertEqual(handler.instance_of({"metadata": {"name": "n1"},
                                              "spec": {"providerID": "aws:///us-east-1a/i-0123456789abcdef0"}}),
                         "i-0123456789abcdef0")


if __name__ == "__main__":
    unittest.main()
