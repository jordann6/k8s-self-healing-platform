# Runbook: ExternalSecretSyncFailing

**What fired:** External Secrets could not read the backend for an
ExternalSecret. The Kubernetes Secret still holds the last value, so pods keep
working until the backend rotates or revokes it. A rotation during this window
causes an outage at revocation time.

1. `kubectl -n selfheal describe externalsecret <name>`: the status condition
   names the error (auth, not found, RBAC).
2. Check the store: `kubectl -n selfheal get secretstore backend -o yaml`
   (`status.conditions`).
3. If a rotation is in progress, ask the key owner to extend the grace window
   before anything else.
