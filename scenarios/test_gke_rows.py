"""The GKE row's evidence rules, tested without Google Cloud.

judge.py decides; these are the pieces of gke_rows.py that turn raw Google
Cloud and Kubernetes answers into the facts the judge reads, and the judge's
GKE A4 rules (ADR-0027). Run: make test
"""
import types
import unittest
from unittest import mock

import gke_rows as G
import judge
import run

PROJECT = "jn-selfheal-abc123"
RUNNER = {"project_id": PROJECT, "region": "us-central1", "zone": "us-central1-a", "cluster_name": "selfheal",
          "registry": "r", "workload_pool": "workload", "pod_cidr": "10.64.0.0/16"}


def h_with_node(provider_id):
    return types.SimpleNamespace(kj=lambda *a: {"spec": {"providerID": provider_id}})


class InstanceOf(unittest.TestCase):
    def setUp(self):
        G.outputs.cache_clear()
        self.p = mock.patch.object(G, "outputs", return_value=RUNNER)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_reads_zone_and_name_from_provider_id(self):
        h = h_with_node(f"gce://{PROJECT}/us-central1-a/gke-selfheal-workload-1a2b-xyz")
        self.assertEqual(G.instance_of(h, "n"), ("us-central1-a", "gke-selfheal-workload-1a2b-xyz"))

    def test_refuses_a_node_in_another_project(self):
        # A kubeconfig pointed at the wrong cluster must never stop that
        # project's VM.
        with self.assertRaises(RuntimeError):
            G.instance_of(h_with_node("gce://someone-else/us-central1-a/vm"), "n")

    def test_refuses_a_non_gce_provider_id(self):
        with self.assertRaises(RuntimeError):
            G.instance_of(h_with_node("kind://docker/selfheal/node"), "n")


def obs(**kw):
    o = {"stopped": True, "web_pods_on_node": 2, "not_ready_s": 45.0, "samples": 1000, "expected": 1000,
         "error_rate": 0.002, "recovered_s": 95.0, "zone": "us-central1-a", "instance": "vm1"}
    o.update(kw)
    return o


class JudgeA4(unittest.TestCase):
    t = judge.THRESHOLDS["gke"]["A4"]

    def test_heals_without_a_new_node(self):
        # The pool is fixed (D3): unlike AKS, no replacement node is required.
        ok, _, impact, healed = judge.judge_a4_gke(obs(), self.t)
        self.assertTrue(ok and healed and not impact)

    def test_no_not_ready_is_an_error_not_a_pass(self):
        ok, why, _, _ = judge.judge_a4_gke(obs(not_ready_s=None), self.t)
        self.assertFalse(ok)
        self.assertIn("NotReady", why)

    def test_unconfirmed_stop_is_an_error(self):
        ok, _, _, _ = judge.judge_a4_gke(obs(stopped=False), self.t)
        self.assertFalse(ok)

    def test_errors_over_bound_are_impact(self):
        _, _, impact, healed = judge.judge_a4_gke(obs(error_rate=0.05), self.t)
        self.assertTrue(impact)
        self.assertFalse(healed)

    def test_repair_inside_the_window_voids_the_row(self):
        v = judge.void_reason("A4", "gke", obs(repair_operations=["AUTO_REPAIR_NODES:DONE"]))
        self.assertIn("auto-repair", v)

    def test_a_vm_restart_is_reported_not_voided(self):
        self.assertIsNone(judge.void_reason("A4", "gke", obs(vm_restarted_s=300.0)))


class Target(unittest.TestCase):
    def test_every_gke_row_has_a_threshold(self):
        self.assertEqual(set(run.ROWS_BY_TARGET["gke"]), set(judge.THRESHOLDS["gke"]))

    def test_out_of_scope_rows_are_not_run(self):
        # ADR-0027 Scope: S1, S4 and R2 on GCP are the landing zone proofs.
        self.assertFalse({"S1", "S4", "R2"} & set(run.ROWS_BY_TARGET["gke"]))

    def test_shared_rows_keep_their_bounds(self):
        for r in ("A3", "R1"):
            self.assertEqual(judge.THRESHOLDS["gke"][r], judge.THRESHOLDS["kind"][r])

    def test_gke_preconditions_drop_the_metrics_server_deployment(self):
        with mock.patch.object(run, "TARGET", "gke"):
            res = run.precondition_resources(run.ROWS_BY_TARGET["gke"])
        self.assertNotIn(("kube-system", "deploy", "metrics-server"), res)
        self.assertIn(("keda", "deploy", "keda-operator"), res)


if __name__ == "__main__":
    unittest.main()
