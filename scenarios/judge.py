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
        # Row Q3 (kind only, Phase 3). Fixed before the first live run in
        # ADR-0011. The VPA is recommendation-only, so any workload mutation
        # is a miss and missing resource telemetry is an error.
        "Q3": {"recommend_s": 240, "min_samples": 6,
               "cpu_pressure_m": 100, "memory_pressure_mib": 64,
               "cpu_ratio_min": 0.70, "cpu_ratio_max": 2.00,
               "memory_ratio_min": 0.90, "memory_ratio_max": 2.00,
               "restarts": 0},
        # N1 (kind only, Phase 3). Fixed in ADR-0012 before live proof.
        "N1": {"min_requests": 20, "max_failures": 0, "p95_ms": 300,
               "min_denied": 5, "denied_successes": 0, "min_policy_drops": 1,
               "min_packets": 5, "interfaces": 1, "min_peers": 1},
        # P1 (kind only, Phase 3). Fixed in ADR-0013 before live proof.
        # Policy, signature-state, reason, metric, report, or digest evidence
        # missing from hardened is an error rather than a protected pass.
        "P1": {"admission_s": 20, "ready_s": 120, "recovery_s": 60,
               "min_pass_decisions": 2, "min_reject_decisions": 5},
        # D1 (kind only, Phase 3). Fixed in ADR-0014 before live proof.
        # Missing backup, restore, integrity, invalid-restore, or retention
        # telemetry is an error rather than a protected pass.
        "D1": {"backup_s": 120, "restore_s": 120, "rto_s": 180,
               "rpo_rows": 1, "rows": 100, "retention_days": 1},
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
    "Q3": "Under sustained CPU and memory pressure, the hardened VPA publishes a bounded recommendation within "
          "240s that is related to measured use, while Off mode leaves requests, the running pod, and restart "
          "count unchanged. The baseline has the same pressure and telemetry but no sizing recommendation.",
    "N1": "For Cilium-managed pods on different workers, hardened WireGuard replaces visible VXLAN underlay "
          "traffic with an established encrypted peer path while legitimate requests remain healthy and an "
          "unauthorized cross-node request remains attributable to policy enforcement.",
    "P1": "A digest-pinned artifact signed by the trusted local key is admitted and runs at the signed digest, "
          "while unsigned, invalid-signature, untrusted-signer, tag-only, and verifier-outage requests are "
          "denied with attributable telemetry and the verifier recovers within 60 seconds.",
    "D1": "A completed PostgreSQL backup restores exactly 100 deterministic rows into a fresh target within "
          "180 seconds, loses only the one intentional post-backup row, rejects a truncated archive atomically, "
          "and remains readable after identity and WORM deletion attempts.",
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


def judge_q3(o: dict, t: dict):
    if o.get("metrics_available") is not True:
        return False, "the resource Metrics API never reported the Q3 target", False, False
    usage = o.get("usage") or {}
    if (usage.get("samples") or 0) < t["min_samples"]:
        return False, f"resource telemetry was too thin ({usage.get('samples')} samples)", False, False
    if usage.get("cpu_p90_m") is None or usage.get("memory_p90_mib") is None:
        return False, "CPU or memory usage telemetry was missing", False, False
    if o.get("hpa_present") is True:
        return False, "an HPA targeted q3-target, so the recommendation cannot be isolated", False, False

    initial = o.get("initial_state") or {}
    final = o.get("final_state") or {}
    if initial.get("pod_count") != 1 or final.get("pod_count") != 1:
        return False, "the Q3 workload did not remain a single observable pod", False, False
    requests = initial.get("requests") or {}
    if requests.get("cpu_m") is None or requests.get("memory_mib") is None:
        return False, "the initial CPU or memory request was missing", False, False

    cpu_p90 = usage["cpu_p90_m"]
    memory_p90 = usage["memory_p90_mib"]
    pressure = cpu_p90 >= t["cpu_pressure_m"] and memory_p90 >= t["memory_pressure_mib"]
    stable = (o.get("requests_unchanged") is True and o.get("pod_uid_unchanged") is True
              and o.get("restart_delta") == t["restarts"])

    recommendation = o.get("recommendation")
    if not o.get("vpa_present"):
        if recommendation:
            return False, "a VPA recommendation appeared without the Q3 VPA object", False, False
        why = (f"{usage.get('samples')} samples, CPU p90 {cpu_p90}m, memory p90 {memory_p90}MiB; "
               f"no VPA recommendation; requests {requests.get('cpu')}/{requests.get('memory')}")
        return True, why, pressure, False

    if o.get("update_mode") != "Off":
        return False, f"VPA updateMode was {o.get('update_mode')!r}, expected 'Off'", False, False
    if not recommendation:
        return False, "the VPA status contained no target-container recommendation", False, False
    if o.get("recommendation_s") is None:
        return False, "the recommendation publication time was not measured", False, False

    for key in ("lowerBound", "target", "upperBound"):
        values = recommendation.get(key) or {}
        if not values.get("cpu_m") or not values.get("memory_mib"):
            return False, f"the VPA {key} CPU or memory value was missing", False, False
    lower = recommendation["lowerBound"]
    target = recommendation["target"]
    upper = recommendation["upperBound"]
    bounded = (lower["cpu_m"] <= target["cpu_m"] <= upper["cpu_m"]
               and lower["memory_mib"] <= target["memory_mib"] <= upper["memory_mib"])
    cpu_ratio = target["cpu_m"] / cpu_p90 if cpu_p90 else None
    memory_ratio = target["memory_mib"] / memory_p90 if memory_p90 else None
    related = (cpu_ratio is not None and t["cpu_ratio_min"] <= cpu_ratio <= t["cpu_ratio_max"]
               and memory_ratio is not None
               and t["memory_ratio_min"] <= memory_ratio <= t["memory_ratio_max"])
    above_requests = (target["cpu_m"] > requests["cpu_m"]
                      and target["memory_mib"] > requests["memory_mib"])
    healed = (pressure and le(o.get("recommendation_s"), t["recommend_s"])
              and bounded and related and above_requests and stable)
    why = (f"{usage.get('samples')} samples, CPU p90 {cpu_p90}m, memory p90 {memory_p90}MiB; "
           f"recommendation {target.get('cpu')}/{target.get('memory')} at {fmt_s(o.get('recommendation_s'))}, "
           f"ratios {cpu_ratio:.2f}/{memory_ratio:.2f}; bounded {bounded}, requests unchanged "
           f"{o.get('requests_unchanged')}, pod unchanged {o.get('pod_uid_unchanged')}, "
           f"restart delta {o.get('restart_delta')}")
    return True, why, pressure, healed


def judge_n1(o: dict, t: dict):
    placement = o.get("placement") or {}
    if placement.get("cross_node") is not True:
        return False, "the client and server were not on different nodes", False, False
    capture = o.get("capture") or {}
    if not capture.get("bytes"):
        return False, "worker underlay capture was missing", False, False
    requests = capture.get("probe") or {}
    if (requests.get("samples") or 0) < t["min_requests"] or requests.get("p95_ms") is None:
        return False, f"legitimate request telemetry was too thin: {requests}", False, False
    denied = o.get("denied") or {}
    if (denied.get("samples") or 0) < t["min_denied"]:
        return False, f"denied-request telemetry was too thin: {denied}", False, False
    if o.get("hubble_available") is not True:
        return False, "Hubble policy telemetry was unavailable", False, False
    if o.get("policy_drops") is None:
        return False, "Hubble policy-drop count was missing", False, False

    service_ok = (requests.get("failures") == t["max_failures"]
                  and requests.get("p95_ms") <= t["p95_ms"])
    policy_ok = (denied.get("successes") == t["denied_successes"]
                 and o.get("policy_drops", 0) >= t["min_policy_drops"])
    config = str(o.get("wireguard_config") or "").lower()
    if config not in ("true", "false"):
        return False, f"Cilium WireGuard configuration telemetry was missing ({config!r})", False, False

    if config != "true":
        baseline_capture = (capture.get("vxlan_packets", 0) >= t["min_packets"]
                            and capture.get("wireguard_packets") == 0
                            and capture.get("marker_visible") is True)
        impact = baseline_capture and service_ok and policy_ok
        why = (f"VXLAN {capture.get('vxlan_packets')} attributed packets "
               f"({capture.get('vxlan_packets_total', capture.get('vxlan_packets'))} total), WireGuard "
               f"{capture.get('wireguard_packets')}, marker visible {capture.get('marker_visible')}; "
               f"requests {requests.get('samples')} with {requests.get('failures')} failures, "
               f"p95 {requests.get('p95_ms')} ms; denied successes {denied.get('successes')}, "
               f"policy drops {o.get('policy_drops')}")
        return True, why, impact, False

    wireguard = o.get("wireguard")
    if not wireguard:
        return False, "Cilium WireGuard status was missing", False, False
    peer_evidence = (wireguard.get("client_interfaces") == t["interfaces"]
                     and wireguard.get("server_interfaces") == t["interfaces"]
                     and wireguard.get("client_peer_count", 0) >= t["min_peers"]
                     and wireguard.get("server_peer_count", 0) >= t["min_peers"]
                     and wireguard.get("peer_keys_match") is True
                     and wireguard.get("client_routes_server") is True
                     and wireguard.get("server_routes_client") is True
                     and wireguard.get("client_handshake") is True
                     and wireguard.get("server_handshake") is True)
    encrypted_capture = (capture.get("wireguard_packets", 0) >= t["min_packets"]
                         and capture.get("vxlan_packets") == 0
                         and capture.get("marker_visible") is False)
    healed = service_ok and policy_ok and peer_evidence and encrypted_capture
    why = (f"WireGuard {capture.get('wireguard_packets')} packets, VXLAN "
           f"{capture.get('vxlan_packets')} attributed "
           f"({capture.get('vxlan_packets_total', capture.get('vxlan_packets'))} total), "
           f"marker visible {capture.get('marker_visible')}; "
           f"peers match {wireguard.get('peer_keys_match')}, routes "
           f"{wireguard.get('client_routes_server')}/{wireguard.get('server_routes_client')}, "
           f"handshakes {wireguard.get('client_handshake')}/{wireguard.get('server_handshake')}; "
           f"requests {requests.get('samples')} with {requests.get('failures')} failures, "
           f"p95 {requests.get('p95_ms')} ms; denied successes {denied.get('successes')}, "
           f"policy drops {o.get('policy_drops')}")
    return True, why, True, healed


def judge_p1(o: dict, t: dict):
    digest = str(o.get("digest") or "")
    if not digest.startswith("sha256:") or len(digest) != 71:
        return False, f"expected artifact digest was missing or malformed ({digest!r})", False, False
    preparation = o.get("preparation") or {}
    required_preparation = (
        "one_digest", "trusted_valid", "unsigned_absent", "invalid_claim_rejected",
        "untrusted_rejected_by_trust_root", "untrusted_valid_with_own_key",
    )
    missing = [name for name in required_preparation if preparation.get(name) is not True]
    if missing:
        return False, f"signature preparation evidence missing or false: {', '.join(missing)}", False, False
    admissions = o.get("admissions") or {}

    def admitted(case: str) -> bool:
        item = admissions.get(case) or {}
        return (item.get("returncode") == 0 and item.get("pod_created") is True
                and le(item.get("elapsed_s"), t["admission_s"])
                and le(item.get("ready_s"), t["ready_s"])
                and str(item.get("image_id") or "").endswith("@" + digest))

    def rejected(case: str) -> bool:
        item = admissions.get(case) or {}
        reason = str(item.get("reason") or "")
        attributable = ("p1-trusted-signature" in reason or "P1 requires" in reason
                        or "digest" in reason.lower() or "image signature" in reason.lower())
        return (item.get("returncode") not in (None, 0) and item.get("pod_created") is False
                and le(item.get("elapsed_s"), t["admission_s"])
                and bool(reason) and attributable)

    if o.get("policy_present") is not True:
        cases = ("trusted", "unsigned", "invalid", "untrusted", "tag_only")
        impact = all(admitted(case) for case in cases)
        why = ("no P1 policy; admitted signed, unsigned, copied-invalid, untrusted-signer, and tag-only "
               f"references at {digest}")
        return True, why, impact, False

    if o.get("policy_ready") is not True:
        return False, "P1 policy readiness telemetry was missing", False, False
    if o.get("metrics_available") is not True:
        return False, "Kyverno admission metrics were missing", False, False
    if o.get("policy_report_pass") is not True:
        return False, "the trusted running Pod had no positive P1 PolicyReport", False, False
    metrics = o.get("metric_delta") or {}
    passed = metrics.get("pass", 0)
    rejected_decisions = metrics.get("fail", 0) + metrics.get("error", 0)
    if passed < t["min_pass_decisions"] or rejected_decisions < t["min_reject_decisions"]:
        return False, (f"P1 decision telemetry was incomplete (pass {passed}, "
                       f"fail or error {rejected_decisions})"), False, False
    negatives = ("unsigned", "invalid", "untrusted", "tag_only", "verifier_outage")
    healed = (admitted("trusted") and admitted("recovery")
              and all(rejected(case) for case in negatives)
              and le(admissions.get("recovery_s"), t["recovery_s"]))
    why = (f"trusted and recovered images ran at {digest}; denied "
           f"{', '.join(negatives)}; policy metrics pass {passed}, fail or error {rejected_decisions}; "
           f"verifier recovery {admissions.get('recovery_s')}s")
    return True, why, True, healed


def judge_d1(o: dict, t: dict):
    source = o.get("source") or {}
    source_ok = (source.get("available") is True and source.get("rows") == t["rows"]
                 and source.get("min_seq") == 1 and source.get("max_seq") == t["rows"]
                 and source.get("contiguous") is True and source.get("exact") is True
                 and bool(source.get("digest")))
    if not source_ok:
        return False, f"deterministic source data telemetry was missing or invalid ({source})", False, False

    loss = o.get("loss") or {}
    if o.get("backup") is None:
        impact = (o.get("cronjob_present") is False
                  and loss.get("original_data_absent") is True
                  and o.get("restore_available") is False)
        why = "no backup recovery point; source PVC replacement removed the deterministic data and no restore was available"
        return True, why, impact, False

    backup = o.get("backup") or {}
    restore = o.get("restore") or {}
    restored = o.get("restored") or {}
    invalid = o.get("invalid_restore") or {}
    resistance = o.get("deletion_resistance") or {}
    sentinel = o.get("post_backup_sentinel") or {}
    required = {
        "backup.job_name": backup.get("job_name"),
        "backup.job_uid": backup.get("job_uid"),
        "backup.completed": backup.get("completed"),
        "backup.completion_s": backup.get("completion_s"),
        "backup.backup_id": backup.get("backup_id"),
        "backup.archive_key": backup.get("archive_key"),
        "backup.archive_sha256": backup.get("archive_sha256"),
        "backup.manifest_sha256": backup.get("manifest_sha256"),
        "backup.source_max_seq": backup.get("source_max_seq"),
        "backup.created_at": backup.get("created_at"),
        "backup.object_version": backup.get("object_version"),
        "backup.object_size": backup.get("object_size"),
        "backup.retention_mode": backup.get("retention_mode"),
        "backup.retention_days": backup.get("retention_days"),
        "restore.job_name": restore.get("job_name"),
        "restore.job_uid": restore.get("job_uid"),
        "restore.completed": restore.get("completed"),
        "restore.completion_s": restore.get("completion_s"),
        "restore.target_namespace": restore.get("target_namespace"),
        "restore.target_database": restore.get("target_database"),
        "restore.archive_sha256": restore.get("archive_sha256"),
        "restored.digest": restored.get("digest"),
        "rpo_rows": o.get("rpo_rows"),
        "rpo_seconds": o.get("rpo_seconds"),
        "rto_s": o.get("rto_s"),
        "invalid.job_uid": invalid.get("job_uid"),
        "invalid.failed": invalid.get("failed"),
        "invalid.application_tables": invalid.get("application_tables"),
        "delete.writer": resistance.get("writer_delete_denied"),
        "delete.admin": resistance.get("admin_version_delete_denied"),
        "delete.archive_readable": resistance.get("archive_readable_after"),
        "delete.manifest_readable": resistance.get("manifest_readable_after"),
    }
    missing = [name for name, value in required.items() if value is None or value == ""]
    if missing:
        return False, f"D1 required telemetry missing: {', '.join(missing)}", False, False

    archive_sha = str(backup.get("archive_sha256"))
    manifest_sha = str(backup.get("manifest_sha256"))
    checksums_well_formed = (len(archive_sha) == 64 and len(manifest_sha) == 64)
    restored_ok = (restored.get("available") is True and restored.get("rows") == t["rows"]
                   and restored.get("min_seq") == 1 and restored.get("max_seq") == t["rows"]
                   and restored.get("contiguous") is True and restored.get("exact") is True
                   and restored.get("digest") == source.get("digest"))
    negative_ok = invalid.get("failed") is True and invalid.get("application_tables") == 0
    deletion_ok = (resistance.get("writer_delete_denied") is True
                   and resistance.get("admin_version_delete_denied") is True
                   and resistance.get("archive_readable_after") is True
                   and resistance.get("manifest_readable_after") is True)
    healed = (
        backup.get("completed") is True
        and le(backup.get("completion_s"), t["backup_s"])
        and backup.get("source_max_seq") == t["rows"]
        and backup.get("retention_mode") == "COMPLIANCE"
        and backup.get("retention_days") == t["retention_days"]
        and checksums_well_formed
        and sentinel.get("seq") == t["rows"] + 1
        and sentinel.get("acknowledged") is True
        and loss.get("original_data_absent") is True
        and restore.get("completed") is True
        and le(restore.get("completion_s"), t["restore_s"])
        and restore.get("target_namespace") == "selfheal-d1-restore"
        and restore.get("target_database") == "d1"
        and restore.get("archive_sha256") == archive_sha
        and restored_ok
        and o.get("rpo_rows") == t["rpo_rows"]
        and le(o.get("rto_s"), t["rto_s"])
        and negative_ok
        and deletion_ok
    )
    why = (f"backup {backup.get('backup_id')} version {backup.get('object_version')} completed in "
           f"{fmt_s(backup.get('completion_s'))}; restored {restored.get('rows')} exact rows into "
           f"{restore.get('target_namespace')}/{restore.get('target_database')} in "
           f"{fmt_s(o.get('rto_s'))}; RPO {o.get('rpo_rows')} row and {fmt_s(o.get('rpo_seconds'))}; "
           f"truncated restore failed atomically {negative_ok}; writer and WORM deletion denied {deletion_ok}")
    return True, why, True, healed


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
          "S5": judge_s5, "R1": judge_r1, "R2": judge_r2, "Q1": judge_q1, "Q2": judge_q2,
          "Q3": judge_q3, "N1": judge_n1, "P1": judge_p1, "D1": judge_d1}


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
