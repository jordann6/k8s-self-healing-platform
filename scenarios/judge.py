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
        "A2": {"abort_s": 120, "error_rate": 0.01},
        "A3": {"p95_ms": 300, "error_rate": 0.01},
        "A5": {"recover_s": 60, "alert_s": 120, "error_rate": 0.01},
        "S1": {"failed": 0, "grace_s": 60},
        "S2": {"tls_errors": 0, "renew_s": 60, "serve_s": 150},
        "S3": {"revert_s": 60},
        "R1": {"isolate_s": 30, "evidence_s": 90, "error_rate": 0.01},
    }
}

# Minimum share of the expected prober samples a window must hold to be judged.
MIN_SAMPLE_SHARE = 0.8

HYPOTHESES = {
    "A1": "Error rate stays under 1% once a hung pod is detected, and the hung pod is restarted and serving "
          "within 60s, while one pod deadlocks and Chaos Mesh kills a second.",
    "A2": "A release that fails 30% of requests is stopped within 120s and only v1 serves afterwards, "
          "with the error rate back under 1%.",
    "A3": "Once a 60 rps spike that saturates three pods is sustained (T0+100s on), p95 latency stays "
          "under 300 ms and errors under 1%. The ramp before the HPA reacts is reported, not judged.",
    "A5": "A leaking container is killed at its limit, restarted and serving within 60s, a human is paged "
          "with a runbook within 120s, and no other pod restarts.",
    "S1": "Rotating the partner API key in the secret backend causes 0 failed requests.",
    "S2": "A web certificate reaching its renewal window causes 0 TLS errors.",
    "S3": "A deleted network policy and a hand-edited Deployment are reverted to Git within 60s.",
    "R1": "A shell in a web pod is isolated within 30s and evidence is preserved within 90s, "
          "with the pod kept for capture rather than killed.",
}


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


JUDGES = {"A1": judge_a1, "A2": judge_a2, "A3": judge_a3, "A5": judge_a5,
          "S1": judge_s1, "S2": judge_s2, "S3": judge_s3, "R1": judge_r1}


def verdict(row: str, profile: str, obs: dict, baseline_status: str | None, target: str = "kind") -> dict:
    t = THRESHOLDS[target][row]
    if obs.get("exception"):
        status, why = "error", f"runner exception: {obs['exception']}"
    else:
        ok, why, impact, healed = JUDGES[row](obs, t)
        status, why = classify(profile, ok, why, impact, healed, baseline_status)
    return {"status": status, "reason": why, "thresholds": t,
            "pass": status == "healed" if profile == "hardened" else status == "impact"}


def overall(results: dict) -> bool:
    """results[row][profile] -> verdict. The run passes only if every row has a
    hardened verdict and every one of them is healed."""
    return bool(results) and all(
        r.get("hardened", {}).get("status") == "healed" for r in results.values())
