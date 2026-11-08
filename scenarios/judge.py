"""Verdicts: pure functions, tested without a cluster (test_classify.py).

Every row is run twice, baseline then hardened, and only manifests and policy
differ between the runs (ADR-0001). A row's verdict comes from two questions:

  impact  did the fault hurt users or leave the system unhealed?
  healed  did every healing control act within its threshold?

Status taxonomy
  baseline   impact | no-impact | error
  hardened   healed | miss | inconclusive | error

Honesty rules (each one has a test):
  * A hardened row can only be "healed" if the same run's baseline showed
    "impact". A baseline that did not hurt proves nothing about the control.
    That row is "inconclusive", and inconclusive fails the run.
  * No data is never a pass. A window with too few prober samples, or a row
    whose trigger never fired, is "error".
  * A row aborted at the abort line is impact in the baseline and a miss in
    the hardened run, never silently dropped.
  * The run passes only if every hardened row is "healed". Nothing is skipped.
"""
from __future__ import annotations

import json

# Per-cloud thresholds. Repair clocks differ by design across clouds (ADR-0003),
# so each target gets its own numbers and the report prints them.
THRESHOLDS = {
    "kind": {
        "A1": {"recover_s": 60, "error_rate": 0.01},
        "A2": {"abort_s": 180, "error_rate": 0.01},
        "A3": {"p95_ms": 300, "error_rate": 0.01},
        "A5": {"recover_s": 60, "alert_s": 120, "error_rate": 0.01},
        "S1": {"failed": 0, "grace_s": 60},
        "S2": {"tls_errors": 0, "renew_s": 60, "serve_s": 150},
        "S3": {"revert_s": 60},
        "R1": {"isolate_s": 30, "evidence_s": 90, "error_rate": 0.01},
        # Row Q1 (kind only, Phase 3). drain_s: a 240-message backlog is drained
        # (work queue empty, nothing unacknowledged). zero_s: the worker is back
        # to zero replicas after the drain. The cloud ports are Phase 4.
        "Q1": {"drain_s": 120, "zero_s": 120},
        # Row Q2 (kind only, Phase 3). Fixed before the first live run in
        # ADR-0010. Missing metric, condition, request, or scheduler evidence
        # is an error rather than a protected pass.
        "Q2": {"detect_s": 30, "fallback_s": 30, "metric_recover_s": 45,
               "capacity_recover_s": 60, "scale_down_s": 90,
               "p95_ms": 300, "error_rate": 0.01, "fallback_replicas": 3,
               "max_replicas": 4, "min_replicas": 1, "min_request_samples": 20},
    },
    # Set before the first EKS run (ADR-0003). The rows shared with kind keep
    # the kind numbers: A3 in particular keeps 300 ms, the bar kind could not
    # reach on one shared node. The AWS-native rows carry service latency the
    # lab does not control (Config evaluation, GuardDuty finding delivery), so
    # their clocks are longer and say so in the report.
    "eks": {
        "A1": {"recover_s": 60, "error_rate": 0.01},
        "A2": {"abort_s": 180, "error_rate": 0.01},
        "A3": {"p95_ms": 300, "error_rate": 0.01},
        "A4": {"recover_s": 300, "error_rate": 0.01},
        "A5": {"recover_s": 60, "alert_s": 120, "error_rate": 0.01},
        "S1": {"failed": 0, "grace_s": 60},
        "S1b": {"failed": 0, "dropped": 0},
        "S2": {"tls_errors": 0, "renew_s": 60, "serve_s": 150},
        "S3": {"revert_s": 60},
        "S4": {"revert_s": 600},
        "S5": {"failed": 0, "replaced_s": 1200, "max_disrupted": 1},
        "R1": {"isolate_s": 30, "evidence_s": 90, "error_rate": 0.01},
        "R2": {"isolate_s": 600, "evidence_s": 900, "error_rate": 0.01},
    },
    # Set before the first AKS run (ADR-0003, "AKS thresholds"). Shared rows
    # keep the kind/EKS numbers. A4 judges errors from NotReady (decision D3);
    # R2's isolate and evidence clocks run from the Defender alert's own
    # generation time, and the alert itself must come within Defender's
    # documented hour.
    "aks": {
        "A1": {"recover_s": 60, "error_rate": 0.01},
        "A2": {"abort_s": 180, "error_rate": 0.01},
        "A3": {"p95_ms": 300, "error_rate": 0.01},
        "A4": {"recover_s": 600, "error_rate": 0.01},
        "A5": {"recover_s": 60, "alert_s": 120, "error_rate": 0.01},
        "S1": {"failed": 0, "grace_s": 60},
        "S2": {"tls_errors": 0, "renew_s": 60, "serve_s": 150},
        "S3": {"revert_s": 60},
        "S4": {"revert_s": 1200},
        "R1": {"isolate_s": 30, "evidence_s": 90, "error_rate": 0.01},
        "R2": {"alert_s": 3600, "isolate_s": 300, "evidence_s": 600, "error_rate": 0.01},
    },
}

# Minimum share of the expected prober samples a window must hold to be judged.
MIN_SAMPLE_SHARE = 0.8
# Must match run.py: more than this share of failures in 10s stops an injection.
ABORT_ERROR_RATE = 0.5

HYPOTHESES = {
    "A1": "Error rate stays under 1% once a hung pod is detected, and the hung pod is restarted and serving "
          "within 60s, while one pod deadlocks and Chaos Mesh kills a second.",
    "A2": "A release that fails 30% of requests is stopped within 180s and only v1 serves afterwards, "
          "with the error rate back under 1%.",
    "A3": "Once a 30 rps spike that saturates three pods is sustained (T0+100s on), p95 latency stays "
          "under 300 ms and errors under 1%. The ramp before the HPA reacts is reported, not judged.",
    "A5": "A leaking container is killed at its limit, restarted and serving within 60s, a human is paged "
          "with a runbook within 120s, and no other pod restarts.",
    "S1": "Rotating the partner API key in the secret backend causes 0 failed requests.",
    "S2": "A web certificate reaching its renewal window causes 0 TLS errors.",
    "S3": "A deleted network policy and a hand-edited Deployment are reverted to Git within 60s.",
    "R1": "A shell in a web pod is isolated within 30s and evidence is preserved within 90s, "
          "with the pod kept for capture rather than killed.",
    "Q1": "A backlog of 240 messages scales the worker from zero, drains within 120s with no lost work "
          "even when a busy worker is killed mid-message, and the worker returns to zero, while the fixed "
          "baseline never scales to zero.",
    "Q2": "When the scaling metric fails, the failure is visible within 30s and the hardened fallback reaches "
          "three replicas within 30s while meeting the request SLO. Restored telemetry recovers within 45s, "
          "the maximum-replica and CPU scheduling ceilings are explicitly observed, restored capacity recovers "
          "within 60s, and withdrawn demand scales back to one within 90s.",
    "A4": "When the node hosting the most web pods is terminated, the error rate stays under 1% and every "
          "web replica is serving again, on a replacement node, within 300s.",
    "S1b": "Rotating the database password twice (both alternating users) causes 0 failed queries and "
           "0 dropped connections.",
    "S4": "A lab security group opened to 0.0.0.0/0 is closed again by the remediation automation "
          "within 600s.",
    "S5": "Replacing every workload node for a new AMI causes 0 failed requests and never takes more "
          "than one node out at a time.",
    "R2": "A runtime threat GuardDuty reports in a web pod is isolated at pod and node level within 600s "
          "and evidence is preserved within 900s, with the pod and node kept for investigation.",
}


# Rows whose mechanism differs on a target state their own hypothesis there.
HYPOTHESES_BY_TARGET = {
    "aks": {
        "A4": "When the scale set instance hosting the most web pods is stopped, the error rate stays under 1% "
              "from the moment its node is marked NotReady, and every web replica is serving again, off that "
              "node and with a new workload node, within 600s.",
        "S4": "An inbound rule opening the node NSG to 0.0.0.0/0 is closed again by the Azure Policy "
              "deployIfNotExists deployment within 1200s.",
        "R2": "A threat Defender for Containers reports in a web pod is isolated (node cordoned, then pod "
              "relabelled into quarantine) within 300s of the alert and evidence (OS-disk snapshot, Run Command "
              "capture, manifest) is preserved within 600s of the alert, with the pod and node kept.",
    },
}


def hypothesis(row: str, target: str = "kind") -> str:
    return HYPOTHESES_BY_TARGET.get(target, {}).get(row) or HYPOTHESES[row]


def enough(samples: int | None, expected: float) -> bool:
    return samples is not None and expected > 0 and samples >= MIN_SAMPLE_SHARE * expected


def classify(profile: str, measured_ok: bool, why: str, impact: bool, healed: bool,
             baseline_status: str | None) -> tuple[str, str]:
    if not measured_ok:
        return "error", why
    if profile == "baseline":
        return ("impact", why) if impact else ("no-impact", why)
    if baseline_status != "impact":
        return "inconclusive", (f"baseline was {baseline_status!r}; a heal can only be claimed "
                                f"against an impact observed in the same run")
    return ("healed", why) if healed else ("miss", why)


def le(v, limit) -> bool:
    return v is not None and v <= limit


# --------------------------------------------------------------------------- #
# per-row judges: obs -> (measured_ok, why, impact, healed)
# --------------------------------------------------------------------------- #

def judge_a1(o: dict, t: dict):
    if not enough(o.get("samples"), o.get("expected", 0)):
        return False, f"prober window too thin ({o.get('samples')} of ~{o.get('expected')})", False, False
    if o.get("injected") is not True:
        return False, "the hang toggle did not answer", False, False
    err = o["error_rate_after"]
    impact = err >= t["error_rate"] or o.get("recovered_s") is None
    healed = le(o.get("recovered_s"), t["recover_s"]) and err < t["error_rate"] and not o.get("aborted")
    why = (f"error rate after detection {err:.2%}; hung pod "
           + (f"restarted and serving at {o['recovered_s']:.0f}s" if o.get("recovered_s") is not None
              else "never recovered"))
    return True, why, impact, healed


def judge_a2(o: dict, t: dict):
    if not enough(o.get("samples"), o.get("expected", 0)):
        return False, f"prober window too thin ({o.get('samples')} of ~{o.get('expected')})", False, False
    if not o.get("v2_seen"):
        return False, "the bad release never served a request, so nothing was tested", False, False
    err, rel = o["error_rate_final"], o.get("releases_final", [])
    if o.get("aborted_s") is not None:
        # The abort must come from a measured error rate. An analysis that
        # failed on empty results (no data yet) aborts any canary, good or bad,
        # and proves nothing about the bad release.
        measured = []
        for v in (o.get("analysis") or {}).get("values", []):
            try:
                measured += [float(x) for x in json.loads(v or "[]")]
            except (ValueError, TypeError):
                pass
        if not any(x >= 0.05 for x in measured):
            return False, (f"the canary was aborted, but no measurement showed an error rate >= 5% "
                           f"(values {(o.get('analysis') or {}).get('values')}): aborted for the wrong reason"), False, False
    impact = err >= t["error_rate"] or "v2" in rel
    healed = le(o.get("aborted_s"), t["abort_s"]) and rel == ["v1"] and err < t["error_rate"]
    why = (f"final error rate {err:.2%}, serving {','.join(rel) or 'nothing'}; "
           + (f"canary aborted at {o['aborted_s']:.0f}s" if o.get("aborted_s") is not None else "no abort"))
    return True, why, impact, healed


def judge_a3(o: dict, t: dict):
    if not enough(o.get("samples"), o.get("expected", 0)):
        return False, f"prober window too thin ({o.get('samples')} of ~{o.get('expected')})", False, False
    if not o.get("load_ran"):
        return False, "the load generator did not run", False, False
    p95, err = o.get("p95_ms"), o["error_rate"]
    # A window with no successful request has no p95: that is the worst case.
    impact = p95 is None or p95 > t["p95_ms"] or err >= t["error_rate"]
    healed = le(p95, t["p95_ms"]) and err < t["error_rate"] and o.get("max_replicas", 0) > o.get("min_replicas", 0)
    why = (f"sustain window p95 {p95} ms, errors {err:.2%}, replicas "
           f"{o.get('min_replicas')} -> {o.get('max_replicas')}")
    return True, why, impact, healed


def judge_a5(o: dict, t: dict):
    if not enough(o.get("samples"), o.get("expected", 0)):
        return False, f"prober window too thin ({o.get('samples')} of ~{o.get('expected')})", False, False
    if o.get("injected") is not True:
        return False, "the leak toggle did not answer", False, False
    impact = o.get("recovered_s") is None or o.get("alert_s") is None
    healed = (o.get("oom_s") is not None and le(o.get("recovered_s"), t["recover_s"])
              and le(o.get("alert_s"), t["alert_s"]) and bool(o.get("alert_runbook"))
              and o.get("other_restarts", 1) == 0 and o["error_rate"] < t["error_rate"])
    why = (f"OOMKilled {'at %.0fs' % o['oom_s'] if o.get('oom_s') is not None else 'never'}, "
           f"recovered {'at %.0fs' % o['recovered_s'] if o.get('recovered_s') is not None else 'never'}, "
           f"alert {'at %.0fs' % o['alert_s'] if o.get('alert_s') is not None else 'never fired'}, "
           f"leaked pod memory at end {o.get('memory_end_mb')} MiB, other restarts {o.get('other_restarts')}, "
           f"errors {o['error_rate']:.2%}")
    return True, why, impact, healed


def judge_s1(o: dict, t: dict):
    if not enough(o.get("samples"), o.get("expected", 0)):
        return False, f"prober window too thin ({o.get('samples')} of ~{o.get('expected')})", False, False
    if not o.get("revoked"):
        return False, "the old key was never revoked, so the rotation was not completed", False, False
    failed = o["failed"]
    impact = failed > t["failed"]
    healed = failed <= t["failed"] and le(o.get("rolled_s"), t["grace_s"])
    why = (f"{failed} failed of {o['samples']}; key synced "
           + (f"at {o['synced_s']:.0f}s" if o.get("synced_s") is not None else "never")
           + ", pods on the new key "
           + (f"at {o['rolled_s']:.0f}s" if o.get("rolled_s") is not None else "never"))
    return True, why, impact, healed


def judge_s2(o: dict, t: dict):
    if not enough(o.get("samples"), o.get("expected", 0)):
        return False, f"TLS prober window too thin ({o.get('samples')} of ~{o.get('expected')})", False, False
    errs = o["tls_errors"]
    impact = errs > t["tls_errors"]
    healed = errs <= t["tls_errors"] and le(o.get("renewed_s"), t["renew_s"]) and le(o.get("served_s"), t["serve_s"])
    why = (f"{errs} TLS errors of {o['samples']}; renewed "
           + (f"at {o['renewed_s']:.0f}s" if o.get("renewed_s") is not None else "never")
           + ", new certificate served "
           + (f"at {o['served_s']:.0f}s" if o.get("served_s") is not None else "never"))
    return True, why, impact, healed


def judge_s3(o: dict, t: dict):
    if not o.get("injected"):
        return False, "the drift was not injected", False, False
    p, e = o.get("policy_restored_s"), o.get("edit_reverted_s")
    impact = p is None or e is None
    healed = le(p, t["revert_s"]) and le(e, t["revert_s"])
    fmt = lambda v: f"{v:.0f}s" if v is not None else "never"  # noqa: E731
    return True, f"policy restored {fmt(p)}, hand edit reverted {fmt(e)}", impact, healed


def judge_r1(o: dict, t: dict):
    if not enough(o.get("samples"), o.get("expected", 0)):
        return False, f"prober window too thin ({o.get('samples')} of ~{o.get('expected')})", False, False
    if o.get("detected_s") is None:
        return False, "Falco never reported the shell, so the response trigger is unproven", False, False
    if not o.get("reachable_before"):
        return False, "the target was not reachable before the attack, so isolation proves nothing", False, False
    iso = o.get("isolated_s")
    # Isolated only counts with Hubble policy-drop evidence for the pod.
    if iso is not None and not o.get("hubble_policy_drops"):
        return False, "reachability failed but Hubble shows no policy drop: not proven to be the policy", False, False
    impact = iso is None
    healed = (le(iso, t["isolate_s"]) and le(o.get("evidence_s"), t["evidence_s"])
              and o.get("pod_kept_for_capture") is True and o["error_rate"] < t["error_rate"])
    fmt = lambda v: f"{v:.0f}s" if v is not None else "never"  # noqa: E731
    why = (f"detected {fmt(o.get('detected_s'))}, isolated {fmt(iso)} "
           f"({o.get('hubble_policy_drops', 0)} Hubble policy drops), evidence {fmt(o.get('evidence_s'))}, "
           f"errors {o['error_rate']:.2%}")
    return True, why, impact, healed


fmt_s = lambda v: f"{v:.0f}s" if v is not None else "never"  # noqa: E731


def judge_q1(o: dict, t: dict):
    pub, n = o.get("published"), o.get("n_target")
    # No data is never a pass: the backlog under test must have been enqueued.
    if n is None or pub != n:
        return False, f"the producer enqueued {pub} of {n}, so the backlog under test was not established", False, False
    dd = o.get("distinct_done")
    if dd is None:
        return False, "the done queue could not be read, so completion is unknown", False, False
    # Telemetry-failure honesty: if KEDA could not read the queue (hardened),
    # any scaling cannot be attributed to the control.
    if o.get("metrics_source_ok") is False:
        return False, "the ScaledObject was not Ready (KEDA could not read the queue), so scaling cannot be attributed", False, False
    start, mx, end = o.get("profile_start_replicas") or 0, o.get("max_replicas") or 0, o.get("end_replicas")
    no_lost_work = dd == pub and o.get("expected_done") == pub and o.get("unexpected_done") == 0
    scaled_out = o.get("scaled_up_s") is not None and mx > start
    # The kill only has teeth if a worker held an in-flight (unacknowledged)
    # message when it died; otherwise a drop could not have been tested.
    kill_had_teeth = (o.get("killed_pod") is not None and o.get("workers_at_kill") == 1
                      and (o.get("unacked_at_kill") or 0) >= 1)
    drained_ok = le(o.get("drained_s"), t["drain_s"])
    zero_after_drain = (o["scaled_to_zero_s"] - o["drained_s"]
                        if o.get("scaled_to_zero_s") is not None and o.get("drained_s") is not None else None)
    zero_ok = le(zero_after_drain, t["zero_s"]) and end == 0
    # Baseline shows impact by never idling to zero (fixed replicas); hardened
    # heals by scaling from zero, draining in time, losing no work across the
    # kill, and returning to zero.
    impact = (end or 0) > 0 or not drained_ok
    healed = no_lost_work and scaled_out and kill_had_teeth and drained_ok and zero_ok
    why = (f"published {pub}, distinct done {dd} ({o.get('duplicates')} duplicate completions), replicas "
           f"{start} to {mx} to {end}, drained {fmt_s(o.get('drained_s'))}, back to zero "
           f"{fmt_s(o.get('scaled_to_zero_s'))}, killed {o.get('killed_pod') or 'none'} "
           f"(unacked {o.get('unacked_at_kill')})")
    return True, why, impact, healed


def judge_q2(o: dict, t: dict):
    # The planned source outage is a fault. Missing evidence that it occurred
    # is not a successful fallback demonstration.
    if o.get("metric_source_available_before") is not True:
        return False, "the metric source was not readable before the fault", False, False
    if o.get("metric_outage_observed") is not True:
        return False, "the planned metric-source outage was not observed", False, False
    if o.get("conditions_present") is not True:
        return False, "ScaledObject or HPA conditions were missing before the fault", False, False
    error_state = o.get("metric_error_state") or {}
    if not error_state.get("scaledobject_conditions") or not error_state.get("hpa_conditions"):
        return False, "autoscaler conditions were missing during the metric failure", False, False

    detected = o.get("metric_failure_detected_s")
    recovered = o.get("metric_recovered_s")
    if detected is None:
        return False, "no autoscaler condition reported the metric failure", False, False
    if recovered is None:
        return False, "metric recovery was not measured", False, False

    ceiling = o.get("ceiling") or {}
    ceiling_state = ceiling.get("state") or {}
    limit_condition = (ceiling_state.get("hpa_conditions") or {}).get("ScalingLimited")
    if not limit_condition:
        return False, "the HPA ScalingLimited condition was missing at the replica ceiling", False, False
    ceiling_observed = (ceiling.get("demand", 0) > t["max_replicas"]
                        and ceiling_state.get("max_replicas") == t["max_replicas"]
                        and ceiling_state.get("actual_replicas") == t["max_replicas"]
                        and limit_condition.get("status") == "True")

    capacity = o.get("capacity") or {}
    pending_state = capacity.get("pending_state") or {}
    pending = pending_state.get("pending") or []
    if pending and any(not p.get("message") for p in pending):
        return False, "a Pending pod had no scheduler message", False, False
    if not pending:
        return False, "no Pending workload pod was observed at the node-capacity ceiling", False, False
    scheduler_attributed = any("Insufficient cpu" in (p.get("message") or "") for p in pending)
    if not scheduler_attributed:
        return False, f"Pending pods were not attributed to Insufficient cpu: {pending}", False, False

    outage = o.get("outage_requests") or {}
    recovery_requests = capacity.get("recovery_requests") or {}
    for label, stats in (("metric-outage", outage), ("capacity-recovery", recovery_requests)):
        if (stats.get("samples") or 0) < t["min_request_samples"]:
            return False, f"{label} request telemetry was too thin ({stats.get('samples')} samples)", False, False
        if stats.get("failure_rate") is None:
            return False, f"{label} request failures were missing", False, False
        if stats.get("p95_ms") is None and stats.get("failure_rate") < 1:
            return False, f"{label} request latency was missing", False, False

    outage_bad = (outage.get("p95_ms") is None or outage["p95_ms"] > t["p95_ms"]
                  or outage["failure_rate"] >= t["error_rate"])
    healthy_outage = (outage.get("p95_ms") is not None and outage["p95_ms"] <= t["p95_ms"]
                      and outage["failure_rate"] < t["error_rate"])
    healthy_recovery = (recovery_requests.get("p95_ms") is not None
                        and recovery_requests["p95_ms"] <= t["p95_ms"]
                        and recovery_requests["failure_rate"] < t["error_rate"])
    fallback_ok = (o.get("fallback_condition_observed") is True
                   and le(o.get("fallback_replicas_s"), t["fallback_s"]))
    capacity_ok = le(capacity.get("recovered_s"), t["capacity_recover_s"])
    scale_down_ok = (le(o.get("scale_down_s"), t["scale_down_s"])
                     and (o.get("end_state") or {}).get("ready_replicas") == t["min_replicas"])
    timing_ok = le(detected, t["detect_s"]) and le(recovered, t["metric_recover_s"])

    # Baseline impact comes from measured user impact during the signal loss.
    # Hardened healing additionally requires every negative mode and recovery.
    impact = outage_bad
    healed = (timing_ok and fallback_ok and healthy_outage and ceiling_observed
              and scheduler_attributed and capacity_ok and healthy_recovery and scale_down_ok)
    why = (f"metric error detected {fmt_s(detected)}, fallback {fmt_s(o.get('fallback_replicas_s'))}, "
           f"metric recovered {fmt_s(recovered)}; outage p95 {outage.get('p95_ms')} ms, "
           f"failures {outage.get('failure_rate')}; ceiling {ceiling_state.get('actual_replicas')} of "
           f"{ceiling_state.get('max_replicas')} (limited {limit_condition.get('status')}); "
           f"Pending {len(pending)} ({pending[0].get('message')}); capacity recovered "
           f"{fmt_s(capacity.get('recovered_s'))}, scale-down {fmt_s(o.get('scale_down_s'))}")
    return True, why, impact, healed


def judge_a4(o: dict, t: dict):
    if not enough(o.get("samples"), o.get("expected", 0)):
        return False, f"prober window too thin ({o.get('samples')} of ~{o.get('expected')})", False, False
    if not o.get("terminated"):
        return False, f"the FIS experiment did not terminate a node ({o.get('experiment_state')})", False, False
    # A kill that touched no web pod tests nothing about the web service.
    if not o.get("web_pods_on_node"):
        return False, "the terminated node ran no web pod, so nothing under test was hit", False, False
    err, rec = o["error_rate"], o.get("recovered_s")
    impact = err >= t["error_rate"] or rec is None
    healed = (le(rec, t["recover_s"]) and err < t["error_rate"] and bool(o.get("replacement_node"))
              and not o.get("stopped_by_alarm"))
    why = (f"killed {o.get('instance_id')} ({o.get('web_pods_on_node')} web pods), errors {err:.2%}, "
           f"all replicas serving {fmt_s(rec)}, replacement node {o.get('replacement_node') or 'none'}"
           + (", FIS stopped by the error-rate alarm" if o.get("stopped_by_alarm") else ""))
    return True, why, impact, healed


def judge_s1b(o: dict, t: dict):
    if not enough(o.get("samples"), o.get("expected", 0)):
        return False, f"db probe window too thin ({o.get('samples')} of ~{o.get('expected')})", False, False
    # The probe must have been logging in before the fault, or any "impact"
    # was already there (eks-run2 baseline: every login failing for 30
    # minutes before T0, after earlier runs had rotated the password).
    pre = o.get("pre_t0") or {}
    if not pre.get("samples") or pre.get("failed"):
        return False, (f"db probe not clean before T0 ({pre.get('failed')} of {pre.get('samples')} logins failed "
                       f"in the {pre.get('seconds')}s before), so the rotation's effect cannot be measured"), False, False
    if (o.get("rotations") or 0) < 2:
        return False, (f"only {o.get('rotations')} of 2 rotations completed; the original user's password "
                       f"was never changed"), False, False
    # Proof the test had teeth: the credential the run started with must have
    # stopped working. Otherwise 0 failures says nothing about rotation.
    if o.get("original_credential_rejected") is not True:
        return False, "the pre-rotation credential still logs in, so the rotation invalidated nothing", False, False
    failed, dropped = o["failed"], o["dropped"]
    impact = failed > t["failed"] or dropped > t["dropped"]
    healed = failed <= t["failed"] and dropped <= t["dropped"]
    why = (f"{failed} failed of {o['samples']} queries, {dropped} dropped connections, "
           f"{o['rotations']} rotations, users seen {','.join(o.get('users_seen') or []) or 'none'}")
    return True, why, impact, healed


def judge_s4(o: dict, t: dict):
    if not o.get("injected"):
        return False, "the public ingress rule was never confirmed on the group", False, False
    rev = o.get("reverted_s")
    # A revert only counts if the remediation automation did it. Anything else
    # (a cleanup, a person, another tool) is not the control under test.
    if rev is not None and not o.get("reverted_by_automation"):
        return False, (f"the rule disappeared at {rev:.0f}s but no remediation execution succeeded: "
                       f"reverted by something other than the control"), False, False
    impact = rev is None
    healed = le(rev, t["revert_s"])
    why = (f"non-compliant {fmt_s(o.get('noncompliant_s'))}, remediation {o.get('remediation_state') or 'none'}, "
           f"rule revoked {fmt_s(rev)}")
    return True, why, impact, healed


def judge_s5(o: dict, t: dict):
    if not enough(o.get("samples"), o.get("expected", 0)):
        return False, f"prober window too thin ({o.get('samples')} of ~{o.get('expected')})", False, False
    if not o.get("drifted"):
        return False, "Karpenter never marked a node as drifted, so no replacement was triggered", False, False
    failed, rep, conc = o["failed"], o.get("replaced_s"), o.get("max_disrupted")
    impact = failed > t["failed"] or rep is None
    healed = failed <= t["failed"] and le(rep, t["replaced_s"]) and le(conc, t["max_disrupted"])
    why = (f"{failed} failed of {o['samples']}; {o.get('nodes_before')} nodes replaced {fmt_s(rep)}, "
           f"at most {conc} disrupted at once, AMI {o.get('ami_before')} -> {o.get('ami_after')}")
    return True, why, impact, healed


def judge_r2(o: dict, t: dict):
    if not enough(o.get("samples"), o.get("expected", 0)):
        return False, f"prober window too thin ({o.get('samples')} of ~{o.get('expected')})", False, False
    f = o.get("finding") or {}
    # The row measures the real detector. A synthetic or sample finding would
    # prove only that the plumbing accepts an event.
    if f.get("source") != "aws.guardduty" or f.get("sample"):
        return False, f"no real GuardDuty finding for the attack (got {f or 'none'})", False, False
    if not o.get("reachable_before"):
        return False, "the target was not reachable before the attack, so isolation proves nothing", False, False
    iso, ev = o.get("isolated_s"), o.get("evidence_s")
    if iso is not None and o.get("reachable_after"):
        return False, "isolation was reported but the pod still answered the prober", False, False
    impact = iso is None
    healed = (le(iso, t["isolate_s"]) and le(ev, t["evidence_s"]) and o.get("pod_kept") is True
              and o.get("termination_protected") is True and (o.get("snapshots") or 0) >= 1
              and (o.get("capture_bytes") or 0) > 0 and o["error_rate"] < t["error_rate"])
    why = (f"{f.get('type')} seen {fmt_s(o.get('finding_s'))}, isolated {fmt_s(iso)} "
           f"(pod relabelled, node cordoned, quarantine group), evidence {fmt_s(ev)} "
           f"({o.get('snapshots', 0)} snapshots, {o.get('capture_bytes', 0)} B captured), "
           f"errors {o['error_rate']:.2%}")
    return True, why, impact, healed


# --------------------------------------------------------------------------- #
# AKS: the rows whose mechanism differs on Azure (ADR-0003, "AKS thresholds")
# --------------------------------------------------------------------------- #

def judge_a4_aks(o: dict, t: dict):
    if not o.get("stopped"):
        return False, f"the scale set instance was not confirmed stopped (power state {o.get('power_state')})", False, False
    if not o.get("web_pods_on_node"):
        return False, "the stopped node ran no web pod, so nothing under test was hit", False, False
    # Decision D3: the error clock starts at NotReady. Without it there is
    # no judged window at all.
    if o.get("not_ready_s") is None:
        return False, "the node was never marked NotReady in the window, so A4's error clock (D3) has no start", False, False
    if not enough(o.get("samples"), o.get("expected", 0)):
        return False, f"prober window too thin ({o.get('samples')} of ~{o.get('expected')})", False, False
    err, rec = o["error_rate"], o.get("recovered_s")
    impact = err >= t["error_rate"] or rec is None
    healed = le(rec, t["recover_s"]) and err < t["error_rate"] and bool(o.get("replacement_node"))
    gap = o.get("gap_error_rate")
    why = (f"stopped {o.get('vmss')}/{o.get('instance_id')} ({o.get('web_pods_on_node')} web pods), NotReady at "
           f"{fmt_s(o.get('not_ready_s'))}; errors from NotReady {err:.2%}"
           + (f" (before NotReady, reported not judged: {gap:.2%})" if gap is not None else "")
           + f"; all replicas serving {fmt_s(rec)}, new node {o.get('replacement_node') or 'none'}")
    return True, why, impact, healed


S4_MARKER = "Closed by policy"


def judge_s4_aks(o: dict, t: dict):
    if not o.get("injected"):
        return False, "the open rule was never confirmed on the node NSG", False, False
    rev = o.get("reverted_s")
    dep = o.get("deployment") or {}
    by_policy = (str(o.get("description_after") or "").startswith(S4_MARKER) and o.get("access_after") == "Deny"
                 and dep.get("state") == "Succeeded" and dep.get("after_t0") is True and dep.get("targets_rule") is True)
    # Only the policy's own deployment counts. A rule deleted, or closed by
    # anything else (a person, a script, a cleanup), is not the control.
    if rev is not None and not by_policy:
        return False, (f"the rule was closed or removed at {rev:.0f}s, but not by the policy deployment "
                       f"(access {o.get('access_after')}, deployment {dep or 'none'})"), False, False
    impact = rev is None
    healed = le(rev, t["revert_s"])
    why = (f"policy deployment {dep.get('name') or 'none'} ({dep.get('state') or '-'}), rule closed {fmt_s(rev)}, "
           f"non-compliant reported {fmt_s(o.get('noncompliant_s'))}")
    return True, why, impact, healed


def judge_r2_aks(o: dict, t: dict):
    if not enough(o.get("samples"), o.get("expected", 0)):
        return False, f"prober window too thin ({o.get('samples')} of ~{o.get('expected')})", False, False
    a = o.get("alert") or {}
    if not a:
        return False, (f"no Defender alert naming the attacked pod within {t['alert_s']}s: the trigger never "
                       f"fired, so there was nothing to respond to"), False, False
    # Only the real detector counts: an alert generated after the attack,
    # naming the lab cluster and the attacked pod. A portal sample alert
    # names neither.
    if a.get("sample") or str(a.get("display_name") or "").startswith("[SAMPLE ALERT]"):
        return False, f"the alert is a sample alert ({a.get('display_name')})", False, False
    if not a.get("names_cluster") or not a.get("names_pod"):
        return False, (f"the alert does not name the lab cluster and the attacked pod "
                       f"(cluster {a.get('names_cluster')}, pod {a.get('names_pod')})"), False, False
    alert_s = a.get("generated_s")
    if alert_s is None or alert_s < 0:
        return False, f"the alert was generated before the attack ({alert_s}s)", False, False
    if alert_s > t["alert_s"]:
        return False, f"the alert came at {alert_s:.0f}s, past Defender's documented hour", False, False
    if not o.get("reachable_before"):
        return False, "the target was not reachable before the attack, so isolation proves nothing", False, False
    iso, ev = o.get("isolated_s"), o.get("evidence_s")
    if iso is not None and o.get("reachable_after"):
        return False, "isolation was reported but the pod still answered the prober", False, False
    rel = lambda v: None if v is None else v - alert_s  # noqa: E731
    iso_a, ev_a = rel(iso), rel(ev)
    # The automation is triggered by the alert, so nothing can act before
    # it was generated: a negative time is clock skew, not a fast heal.
    if (iso_a is not None and iso_a < 0) or (ev_a is not None and ev_a < 0):
        return False, (f"a response time is earlier than the alert (isolated {iso_a}, evidence {ev_a} s after it): "
                       f"clock skew, cannot be judged"), False, False
    impact = iso is None
    healed = (le(iso_a, t["isolate_s"]) and le(ev_a, t["evidence_s"]) and o.get("pod_kept") is True
              and o.get("scale_down_disabled") is True and o.get("snapshot_state") == "Succeeded"
              and (o.get("capture_bytes") or 0) > 0 and o.get("replacement_on_quarantined_node") is False
              and o["error_rate"] < t["error_rate"])
    why = (f"{a.get('display_name')} generated {fmt_s(alert_s)} after the attack; isolated {fmt_s(iso_a)} and "
           f"evidence {fmt_s(ev_a)} after the alert (snapshot {o.get('snapshot_state') or 'none'}, "
           f"{o.get('capture_bytes', 0)} B captured)"
           + ("; a replacement web pod landed on the quarantined node" if o.get("replacement_on_quarantined_node") else "")
           + (f"; Logic App: {o.get('failed_reason')}" if o.get("failed_reason") else "")
           + f"; errors {o['error_rate']:.2%}")
    return True, why, impact, healed


JUDGES_BY_TARGET = {"aks": {"A4": judge_a4_aks, "S4": judge_s4_aks, "R2": judge_r2_aks}}


def void_reason(row: str, target: str, o: dict) -> str | None:
    """Conditions that void a row as an error before anything else, the
    abort line included: the row did not test the control it names."""
    if o.get("void"):
        return str(o["void"])
    if target == "aks" and row == "A4" and o.get("repair_events"):
        return (f"AKS auto-repair acted on the stopped node inside the window ({', '.join(o['repair_events'])}): "
                f"the repair, not the lab's controls, would have healed it (ADR-0007 row 17)")
    return None


JUDGES = {"A1": judge_a1, "A2": judge_a2, "A3": judge_a3, "A4": judge_a4, "A5": judge_a5,
          "S1": judge_s1, "S1b": judge_s1b, "S2": judge_s2, "S3": judge_s3, "S4": judge_s4,
          "S5": judge_s5, "R1": judge_r1, "R2": judge_r2, "Q1": judge_q1, "Q2": judge_q2}


def verdict(row: str, profile: str, obs: dict, baseline_status: str | None, target: str = "kind") -> dict:
    t = THRESHOLDS[target][row]
    ab = obs.get("abort_evidence")
    void = void_reason(row, target, obs)
    if obs.get("exception"):
        status, why = "error", f"runner exception: {obs['exception']}"
    elif void:
        status, why = "error", void
    elif ab and ab.get("n", 0) >= 10 and (ab.get("error_rate") or 0) > ABORT_ERROR_RATE:
        # The abort line is evidence on its own: at least 10 samples in a 10s
        # window with more than half failing. Impact in the baseline, a miss
        # in the hardened run (still subject to the baseline-impact rule).
        why = (f"abort line hit at {ab.get('at_s')}s: {ab['error_rate']:.0%} of {ab['n']} {ab.get('kind', 'http')} "
               f"requests failed in 10s")
        status, why = classify(profile, True, why, True, False, baseline_status)
    else:
        judge = JUDGES_BY_TARGET.get(target, {}).get(row, JUDGES[row])
        ok, why, impact, healed = judge(obs, t)
        status, why = classify(profile, ok, why, impact, healed, baseline_status)
    return {"status": status, "reason": why, "thresholds": t,
            "pass": status == "healed" if profile == "hardened" else status == "impact"}


def overall(results: dict) -> bool:
    """results[row][profile] -> verdict. The run passes only if every row has a
    hardened verdict and every one of them is healed."""
    return bool(results) and all(
        r.get("hardened", {}).get("status") == "healed" for r in results.values())
