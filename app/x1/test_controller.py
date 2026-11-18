import unittest

import controller as c

NOW = "2026-11-17T12:00:00.000000+00:00"


def cr(phase=None, request=None, handled=None, uid=None):
    status = {k: v for k, v in {"phase": phase, "targetUID": uid, "handledReleaseId": handled}.items() if v}
    spec = {"podName": "web-1", "evidenceRef": "ev", "release": {"requestId": request} if request else {}}
    return {"metadata": {"name": "q"}, "spec": spec, "status": status}


def pod(uid="u1", labels=None, restarts=0):
    return {"metadata": {"uid": uid, "labels": labels or {"app": "web", "lab/pool": "serving"}},
            "status": {"containerStatuses": [{"restartCount": restarts}]}}


def isolated_pod(uid="u1"):
    return pod(uid, {"selfheal.lab/quarantined": "true", "lab/pool": "quarantine"})


EVIDENCE = {"data": {"target_uid": "u1", "captured_at": "2026-11-17T11:59:00Z"}}


class Isolate(unittest.TestCase):
    def test_pending_isolates_without_touching_the_pod_otherwise(self):
        plan = c.reconcile(cr(), pod(restarts=0), None, NOW)
        self.assertEqual(plan["pod_labels"], {"selfheal.lab/quarantined": "true", "lab/pool": "quarantine"})
        self.assertEqual(plan["status"]["phase"], "Isolated")
        self.assertEqual(plan["status"]["targetUID"], "u1")
        self.assertEqual(plan["status"]["restartCountAtIsolation"], 0)

    def test_missing_pod_fails_and_changes_nothing(self):
        plan = c.reconcile(cr(), None, None, NOW)
        self.assertIsNone(plan["pod_labels"])
        self.assertEqual(plan["status"]["reason"], "PodNotFound")

    def test_protected_pod_is_refused_with_no_label_change(self):
        plan = c.reconcile(cr(), pod(labels={"selfheal.lab/protected": "true"}), None, NOW)
        self.assertIsNone(plan["pod_labels"])
        self.assertEqual((plan["status"]["phase"], plan["status"]["reason"]), ("Failed", "Protected"))

    def test_a_replacement_pod_with_the_same_name_is_never_acted_on(self):
        plan = c.reconcile(cr("Isolated", uid="u1"), pod(uid="u2"), None, NOW)
        self.assertIsNone(plan["pod_labels"])
        self.assertEqual(plan["status"]["reason"], "PodReplaced")


class Resume(unittest.TestCase):
    def test_a_second_controller_changes_nothing_when_the_state_is_already_reached(self):
        plan = c.reconcile(cr("Isolated", uid="u1"), isolated_pod(), None, NOW)
        self.assertEqual(plan, {"pod_labels": None, "status": None})

    def test_drifted_labels_are_put_back(self):
        plan = c.reconcile(cr("Isolated", uid="u1"), pod(), None, NOW)
        self.assertEqual(plan["pod_labels"], c.ISOLATE_LABELS)

    def test_terminal_phases_are_never_reopened(self):
        for phase in ("Released", "Failed"):
            self.assertEqual(c.reconcile(cr(phase), pod(), None, NOW), {"pod_labels": None, "status": None})


class Evidence(unittest.TestCase):
    def test_evidence_for_another_uid_is_ignored(self):
        stale = {"data": {"target_uid": "old", "captured_at": "x"}}
        self.assertIsNone(c.evidence_for(stale, "u1"))
        plan = c.reconcile(cr("Isolated", uid="u1"), isolated_pod(), stale, NOW)
        self.assertIsNone(plan["status"])

    def test_matching_evidence_moves_to_preserved(self):
        plan = c.reconcile(cr("Isolated", uid="u1"), isolated_pod(), EVIDENCE, NOW)
        self.assertEqual(plan["status"]["phase"], "Preserved")
        self.assertEqual(plan["status"]["evidenceCapturedAt"], "2026-11-17T11:59:00Z")


class Release(unittest.TestCase):
    def test_release_before_evidence_is_refused_and_the_pod_stays_isolated(self):
        plan = c.reconcile(cr("Isolated", request="r1", uid="u1"), isolated_pod(), None, NOW)
        self.assertIsNone(plan["pod_labels"])
        self.assertEqual(plan["status"]["handledReleaseId"], "r1")
        self.assertNotIn("phase", plan["status"])
        refused = [x for x in plan["status"]["conditions"] if x["type"] == "ReleaseRefused"]
        self.assertEqual(refused[0]["reason"], "NoEvidence")

    def test_a_refused_request_is_not_retried_into_a_release_once_evidence_arrives(self):
        plan = c.reconcile(cr("Isolated", request="r1", handled="r1", uid="u1"), isolated_pod(), EVIDENCE, NOW)
        self.assertEqual(plan["status"]["phase"], "Preserved")
        self.assertNotIn("releasedAt", plan["status"])

    def test_a_new_request_after_evidence_releases_in_a_later_pass(self):
        plan = c.reconcile(cr("Preserved", request="r2", handled="r1", uid="u1"), isolated_pod(), EVIDENCE, NOW)
        self.assertEqual(plan["pod_labels"], c.RESTORE_LABELS)
        self.assertEqual(plan["status"]["phase"], "Released")

    def test_release_in_the_same_pass_that_first_sees_evidence_is_refused(self):
        plan = c.reconcile(cr("Isolated", request="r2", uid="u1"), isolated_pod(), EVIDENCE, NOW)
        self.assertIsNone(plan["pod_labels"])
        self.assertEqual(plan["status"]["phase"], "Preserved")
        self.assertNotIn("releasedAt", plan["status"])


if __name__ == "__main__":
    unittest.main()
