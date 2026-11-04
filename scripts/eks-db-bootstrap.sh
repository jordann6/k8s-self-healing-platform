#!/usr/bin/env bash
# eks-db-bootstrap.sh: create the S1b database user, seed its secret, then turn
# rotation on. Called by eks-bootstrap.sh. The same order as the landing
# zone's rotation bootstrap, for the same reason: Secrets Manager tests the
# rotation function as soon as rotation is configured, and that test needs a
# real user and a real value, or it can strand AWSPENDING.
#
# The database is private, so the SQL runs as a short-lived Job in the
# cluster. The master password and the new one reach it over stdin into a
# Secret that is deleted with its namespace.
#
# The secret is seeded twice with the same value, so AWSPREVIOUS exists from
# the start: the hardened profile's ExternalSecret reads both stages, and a
# missing stage would fail its sync.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
: "${MGMT_PROFILE:?set MGMT_PROFILE}" "${LAB_PROFILE:?set LAB_PROFILE}" "${KUBECONFIG:?run through make}"
tfo() { AWS_PROFILE="$MGMT_PROFILE" terraform -chdir="$root/infra/aws" output -raw "$1"; }
db_host="$(tfo db_endpoint)"
master_arn="$(tfo db_master_secret_arn)"
secret_arn="$(tfo db_app_secret_arn)"
rotation_arn="$(tfo rotation_function_arn)"
region="$(tfo region)"
export AWS_PROFILE="$LAB_PROFILE" AWS_REGION="$region"
NS=db-bootstrap
POSTGRES_IMAGE="public.ecr.aws/docker/library/postgres:17-alpine"

kubectl -n selfheal create configmap db-endpoint --from-literal=DB_HOST="$db_host" \
	--from-literal=DB_PORT=5432 --from-literal=DB_NAME=selfheal --dry-run=client -o yaml | kubectl apply -f - >/dev/null

if [[ "$(aws secretsmanager describe-secret --secret-id "$secret_arn" --query 'RotationEnabled' --output text)" == "True" ]]; then
	echo "eks-db-bootstrap: rotation already enabled, nothing to do"
	exit 0
fi

echo "eks-db-bootstrap: creating selfheal_app"
password="$(aws secretsmanager get-random-password --password-length 32 \
	--exclude-characters ":/@\"'\\" --require-each-included-type --query RandomPassword --output text)"
master_password="$(aws secretsmanager get-secret-value --secret-id "$master_arn" \
	--query SecretString --output text | jq -r .password)"
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
trap 'kubectl delete namespace "$NS" --wait=false >/dev/null 2>&1 || true' EXIT
printf 'PGPASSWORD=%s\nAPP_PW=%s\n' "$master_password" "$password" |
	kubectl -n "$NS" create secret generic credentials --from-env-file=/dev/stdin --dry-run=client -o yaml |
	kubectl apply -f - >/dev/null
unset master_password
kubectl -n "$NS" create configmap sql --from-file="$root/infra/aws/rotation/app-role.sql" \
	--dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n "$NS" delete job app-role --ignore-not-found >/dev/null
kubectl apply -f - >/dev/null <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: app-role
  namespace: $NS
spec:
  backoffLimit: 1
  activeDeadlineSeconds: 300
  template:
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      nodeSelector: {selfheal.lab/nodes: system}
      securityContext:
        runAsNonRoot: true
        runAsUser: 70
        seccompProfile: {type: RuntimeDefault}
      containers:
        - name: psql
          image: $POSTGRES_IMAGE
          env:
            - {name: PGHOST, value: "$db_host"}
            - {name: PGUSER, value: selfheal_admin}
            - {name: PGDATABASE, value: selfheal}
            - {name: PGSSLMODE, value: require}
          envFrom:
            - secretRef: {name: credentials}
          command: [sh, -c]
          args: ["psql -v ON_ERROR_STOP=1 -f /sql/app-role.sql && echo selfheal_app ready"]
          securityContext:
            allowPrivilegeEscalation: false
            capabilities: {drop: [ALL]}
          volumeMounts:
            - {name: sql, mountPath: /sql}
      volumes:
        - name: sql
          configMap: {name: sql}
YAML
if ! kubectl -n "$NS" wait --for=condition=complete job/app-role --timeout=300s >/dev/null; then
	kubectl -n "$NS" logs job/app-role >&2 || true
	echo "eks-db-bootstrap: the role Job failed" >&2
	exit 1
fi

# The shape the alternating-users function expects. ssl=true: it never falls
# back to plaintext. masterarn is the RDS-managed master secret.
seed() {
	PASSWORD="$password" HOST="$db_host" MASTER="$master_arn" python3 -I -c '
import json, os
print(json.dumps({"engine": "postgres", "host": os.environ["HOST"], "port": 5432, "dbname": "selfheal",
                  "username": "selfheal_app", "password": os.environ["PASSWORD"],
                  "masterarn": os.environ["MASTER"], "ssl": True}))' |
		aws secretsmanager put-secret-value --secret-id "$secret_arn" --secret-string file:///dev/stdin \
			--query VersionId --output text
}
echo "eks-db-bootstrap: seeded $(seed) and $(seed) (AWSPREVIOUS exists from the start)"
unset password
aws secretsmanager rotate-secret --secret-id "$secret_arn" --rotation-lambda-arn "$rotation_arn" \
	--rotation-rules AutomaticallyAfterDays=30 --no-rotate-immediately >/dev/null
echo "eks-db-bootstrap: rotation enabled (row S1b triggers rotations on demand)"
