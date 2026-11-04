# Runbook: WebAvailabilityBudgetBurn

**What fired:** the web availability SLO (99.5%) is burning error budget faster
than the multiwindow, multi-burn-rate thresholds allow. Windows are the SRE
Workbook defaults divided by 12 for the lab (see slo/windows).

1. Is a release in flight? `kubectl -n selfheal get rollout web`. An aborted
   canary should already have stopped the burn.
2. Is the partner rejecting the key? Look for 502s and `partner_rejected` in
   `kubectl -n selfheal logs -l app=web`.
3. Note: this SLO is computed from the app's own metrics and cannot see
   requests that hang forever. The prober is the user's view.
