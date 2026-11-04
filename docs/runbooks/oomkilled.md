# Runbook: WebContainerOOMKilled

**What fired:** a web container hit its memory limit, was OOMKilled by the
kernel and restarted by the kubelet. The limit contained the leak to one
container; the restart cleared it. Users saw at most the requests in flight on
that pod.

**Why you are paged anyway:** the restart treats the symptom. The code still
leaks, and a fast enough leak turns into a CrashLoopBackOff.

1. Confirm scope: `kubectl -n selfheal get pods -l app=web` (restarts on one pod
   or many?) and the alert's `pod` label.
2. Check the trend: `lab_leaked_bytes` per pod in Prometheus, or `kubectl -n
   selfheal top pods`. A sawtooth climbing to the limit is a leak; a flat line
   at the limit is an undersized limit. (The kind lab does not scrape cAdvisor;
   on the clouds use `container_memory_working_set_bytes`.)
3. If it is a release regression, roll back (Argo Rollouts: abort, or revert the
   image tag in Git).
4. Do not raise the limit as the fix. It only moves the cliff.
