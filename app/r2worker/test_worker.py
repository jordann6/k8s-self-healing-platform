"""The AKS R2 worker's refusal and ordering rules, tested without Azure or a
cluster.

The worker acts on production-shaped evidence, so its first job is to refuse:
a stale or malformed incident, an alert for another cluster or namespace, a
sample alert, an alert naming several pods, a pod that is not a live web pod,
or a node that is a system node or outside the lab node group must never be
cordoned or relabelled. When it does act, the cordon lands and is confirmed
before the relabel, so the pod's replacement cannot be scheduled onto the
node being quarantined (the EKS miss). Run: make test
"""
import base64
import json
import os
import time
import unittest

CLUSTER_ID = "/subscriptions/sub/resourceGroups/rg-selfheal-lab/providers/Microsoft.ContainerService/managedClusters/selfheal"
os.environ.update({
    "CLUSTER_NAME": "selfheal", "CLUSTER_ID": CLUSTER_ID, "LAB_NAMESPACE": "selfheal",
    "NODE_RESOURCE_GROUP": "rg-selfheal-lab-nodes", "STORAGE_ACCOUNT": "stexample",
})

import worker  # noqa: E402

PROVIDER = ("azure:///subscriptions/sub/resourceGroups/rg-selfheal-lab-nodes/providers/"
            "Microsoft.Compute/virtualMachineScaleSets/aks-work-12345678-vmss/virtualMachines/3")


def incident(run_id="08584abc", blob=None, age=10):
    received = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age))
    msg = {"run_id": run_id, "alert_blob": blob or f"r2/{run_id}/alert.json", "received_at": received}
    return base64.b64encode(json.dumps(msg).encode()).decode()


def alert(pods=(("selfheal", "web-7d9f-abcde"),), cluster="selfheal", display="Communication with suspicious domain",
          arm=True, cluster_id=CLUSTER_ID):
    ents = [{"$id": "1", "Type": "K8s-cluster", "Name": cluster, "ResourceId": cluster_id}]
    for i, (ns, name) in enumerate(pods):
        ents.append({"$id": f"ns{i}", "Type": "K8s-namespace", "Name": ns, "Cluster": {"$ref": "1"}})
        ents.append({"$id": f"p{i}", "Type": "K8s-pod", "Name": name, "Namespace": {"$ref": f"ns{i}"}})
    props = {"AlertDisplayName": display, "AlertType": "K8S_ThreatIntelDomain",
             "TimeGeneratedUtc": "2026-11-05T12:00:00Z", "Entities": ents, "CompromisedEntity": cluster}
    if arm:
        return {"id": f"{cluster_id}/providers/Microsoft.Security/alerts/a1", "properties": props}
    return dict(props, SystemAlertId="a1")


def pod(name="web-7d9f-abcde", app="web", node="aks-work-12345678-vmss000003", deleting=False, ns="selfheal"):
    md = {"name": name, "namespace": ns, "uid": "pod-uid-1", "labels": {"app": app, "lab/pool": "serving"}}
    if deleting:
        md["deletionTimestamp"] = "2026-11-05T12:00:00Z"
    return {"metadata": md, "spec": {"nodeName": node}}


def node(name="aks-work-12345678-vmss000003", pool="workload", provider=PROVIDER):
    return {"metadata": {"name": name, "labels": {"selfheal.lab/nodes": pool}},
            "spec": {"providerID": provider}}


class FakeApi:
    """Records every call in order; patches update the fake node."""

    def __init__(self, a=None, p=None, n=None, cordon_sticks=True):
        self.a, self.p, self.n = a or alert(), p or pod(), n or node()
        self.cordon_sticks = cordon_sticks
        self.calls = []

    def get_blob(self, name):
        self.calls.append(("get_blob", name))
        return self.a

    def get_pod(self, name):
        self.calls.append(("get_pod", name))
        return self.p

    def get_node(self, name):
        self.calls.append(("get_node", name))
        return self.n

    def patch_node(self, name, body):
        self.calls.append(("patch_node", name))
        if self.cordon_sticks:
            self.n = dict(self.n, spec=dict(self.n["spec"], unschedulable=True))

    def patch_pod(self, name, body):
        self.calls.append(("patch_pod", name, body))

    def patches(self):
        return [c for c in self.calls if c[0].startswith("patch")]


class Incident(unittest.TestCase):
    def test_fresh_incident_is_accepted(self):
        self.assertEqual(worker.parse_incident(incident())["run_id"], "08584abc")

    def test_stale_incident_is_refused(self):
        with self.assertRaisesRegex(worker.Refused, "stale"):
            worker.parse_incident(incident(age=worker.MAX_INCIDENT_AGE + 60))

    def test_alert_blob_outside_the_run_is_refused(self):
        with self.assertRaisesRegex(worker.Refused, "alert_blob"):
            worker.parse_incident(incident(blob="r2/other/alert.json"))

    def test_path_in_run_id_is_refused(self):
        with self.assertRaisesRegex(worker.Refused, "run_id"):
            worker.parse_incident(incident(run_id="../x"))

    def test_not_base64_json_is_refused(self):
        with self.assertRaises(worker.Refused):
            worker.parse_incident("not base64 at all!")


class AlertTarget(unittest.TestCase):
    def test_lab_alert_names_one_pod(self):
        self.assertEqual(worker.alert_target(alert())["pod"], "web-7d9f-abcde")

    def test_flat_connector_shape_is_read_too(self):
        t = worker.alert_target(alert(arm=False))
        self.assertEqual((t["pod"], t["alert_id"]), ("web-7d9f-abcde", "a1"))

    def test_other_cluster_is_refused(self):
        other = CLUSTER_ID.replace("selfheal", "prod")
        with self.assertRaisesRegex(worker.Refused, "lab cluster"):
            worker.alert_target(alert(cluster="prod", cluster_id=other))

    def test_cluster_entity_for_another_cluster_is_refused(self):
        with self.assertRaisesRegex(worker.Refused, "names cluster"):
            worker.alert_target(alert(cluster="prod"))

    def test_other_namespace_is_refused(self):
        with self.assertRaisesRegex(worker.Refused, "namespace"):
            worker.alert_target(alert(pods=(("kube-system", "coredns-abc"),)))

    def test_several_pods_is_refused(self):
        with self.assertRaisesRegex(worker.Refused, "refusing to guess"):
            worker.alert_target(alert(pods=(("selfheal", "web-a"), ("selfheal", "web-b"))))

    def test_no_pod_is_refused(self):
        with self.assertRaisesRegex(worker.Refused, "no pod"):
            worker.alert_target(alert(pods=()))

    def test_sample_alert_is_refused(self):
        with self.assertRaisesRegex(worker.Refused, "sample"):
            worker.alert_target(alert(display="[Sample alert] Communication with suspicious domain"))


class PodAndNode(unittest.TestCase):
    def test_non_web_pod_is_refused(self):
        with self.assertRaisesRegex(worker.Refused, "not a web pod"):
            worker.check_pod(pod(app="partner"))

    def test_terminating_pod_is_refused(self):
        with self.assertRaisesRegex(worker.Refused, "terminating"):
            worker.check_pod(pod(deleting=True))

    def test_system_node_is_never_quarantined(self):
        with self.assertRaisesRegex(worker.Refused, "not a workload node"):
            worker.node_target(node(pool="system"))

    def test_node_outside_the_lab_node_group_is_refused(self):
        with self.assertRaisesRegex(worker.Refused, "not the lab node group"):
            worker.node_target(node(provider=PROVIDER.replace("rg-selfheal-lab-nodes", "rg-prod-nodes")))

    def test_workload_node_resolves_to_scale_set_instance(self):
        self.assertEqual(worker.node_target(node()), {"vmss": "aks-work-12345678-vmss", "instance_id": "3"})


class Respond(unittest.TestCase):
    def test_cordon_is_confirmed_before_the_relabel(self):
        api = FakeApi()
        body = worker.respond(worker.parse_incident(incident()), api)
        names = [c[0] for c in api.calls]
        self.assertLess(names.index("patch_node"), names.index("patch_pod"))
        # the node is read back between the cordon and the relabel
        self.assertEqual(names[names.index("patch_node") + 1], "get_node")
        self.assertEqual(api.patches()[-1][2]["metadata"]["labels"], worker.QUARANTINE_LABELS)
        self.assertEqual((body["status"], body["vmss"], body["instance_id"]), ("isolated", "aks-work-12345678-vmss", "3"))

    def test_cordon_that_does_not_stick_never_relabels(self):
        api = FakeApi(cordon_sticks=False)
        with self.assertRaisesRegex(RuntimeError, "unschedulable"):
            worker.respond(worker.parse_incident(incident()), api)
        self.assertEqual([c[0] for c in api.patches()], ["patch_node"])

    def test_refusals_never_patch(self):
        cases = [FakeApi(a=alert(cluster="prod")), FakeApi(p=pod(app="partner")), FakeApi(n=node(pool="system"))]
        for api in cases:
            body, delete = worker.handle_message(incident(), 1, api)
            self.assertEqual((body["status"], delete), ("refused", True))
            self.assertEqual(api.patches(), [])


class ReportShape(unittest.TestCase):
    """The Logic App's ParseJson schema requires these keys; a refusal must
    carry them empty so no Azure-side step can target anything."""
    KEYS = {"status", "run_id", "node", "pod", "vmss", "instance_id"}

    def test_refusal_report_has_every_key_and_empty_targets(self):
        body, _ = worker.handle_message(incident(), 1, FakeApi(n=node(pool="system")))
        self.assertTrue(self.KEYS <= set(body))
        self.assertEqual({body[k] for k in ("node", "pod", "vmss", "instance_id")}, {""})

    def test_isolated_report_has_every_key_filled(self):
        body = worker.respond(worker.parse_incident(incident()), FakeApi())
        self.assertTrue(self.KEYS <= set(body))
        self.assertTrue(all(body[k] for k in self.KEYS))


class Retries(unittest.TestCase):
    class Broken(FakeApi):
        def get_pod(self, name):
            raise RuntimeError("HTTP 503")

    def test_transient_error_is_retried(self):
        self.assertEqual(worker.handle_message(incident(), 1, self.Broken()), (None, False))

    def test_gives_up_after_max_attempts_and_says_so(self):
        body, delete = worker.handle_message(incident(), worker.MAX_ATTEMPTS, self.Broken())
        self.assertTrue(delete)
        self.assertEqual(body["status"], "refused")
        self.assertIn("gave up", body["reason"])

    def test_malformed_message_is_dropped_without_a_report(self):
        self.assertEqual(worker.handle_message("garbage", 1, FakeApi()), (None, True))


if __name__ == "__main__":
    unittest.main()
