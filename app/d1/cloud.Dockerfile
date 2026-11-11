# D1 tool image for EKS and AKS (ADR-0022): the same PostgreSQL client tools as
# the kind image, with the cloud store client in place of the MinIO client. The
# SDK versions are pinned. Kind keeps the Dockerfile next to this one untouched.
FROM postgres:17.11-alpine3.24
USER root
RUN set -eu; \
    apk add --no-cache python3 py3-pip; \
    python3 -m venv /opt/d1; \
    /opt/d1/bin/pip install --no-cache-dir boto3==1.44.0 azure-storage-blob==12.31.1 azure-identity==1.27.0; \
    /opt/d1/bin/pip uninstall -y pip setuptools >/dev/null 2>&1 || true
COPY backup.sh restore.sh d1store.py /usr/local/bin/
RUN chmod 0555 /usr/local/bin/backup.sh /usr/local/bin/restore.sh /usr/local/bin/d1store.py
USER 70:70
