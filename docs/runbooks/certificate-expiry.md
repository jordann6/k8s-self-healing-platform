# Runbook: CertificateNearExpiry

**What fired:** a cert-manager Certificate has under 15 minutes left (lab
threshold for 1h certificates). Renewal should have happened at
`status.renewalTime`; it did not.

1. `kubectl -n selfheal describe certificate <name>`: look for a failed
   CertificateRequest and its reason.
2. `kubectl -n cert-manager logs deploy/cert-manager --since=1h | grep <name>`.
3. Check the issuer: `kubectl get clusterissuer lab-ca -o yaml`.
