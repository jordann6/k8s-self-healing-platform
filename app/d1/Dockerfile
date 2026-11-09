FROM alpine:3.22 AS minio_client
ARG TARGETARCH
RUN set -eu; \
    case "${TARGETARCH}" in \
      amd64) checksum=01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891 ;; \
      arm64) checksum=14c8c9616cfce4636add161304353244e8de383b2e2752c0e9dad01d4c27c12c ;; \
      *) echo "unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    wget -qO /mc "https://github.com/minio/mc/releases/download/RELEASE.2025-08-13T08-35-41Z/mc.linux-${TARGETARCH}.RELEASE.2025-08-13T08-35-41Z"; \
    echo "${checksum}  /mc" | sha256sum -c -; \
    chmod 0555 /mc

FROM postgres:17.11-alpine3.24
COPY --from=minio_client /mc /usr/local/bin/mc
COPY backup.sh restore.sh /usr/local/bin/
RUN chmod 0555 /usr/local/bin/backup.sh /usr/local/bin/restore.sh
USER 70:70
