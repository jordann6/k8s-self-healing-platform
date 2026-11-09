FROM alpine:3.22 AS download
ARG TARGETARCH
RUN set -eu; \
    case "${TARGETARCH}" in \
      amd64) checksum=7c5bd8512c6e966455b1d198209358b2d191c77a83ab377c4073281065fb855f ;; \
      arm64) checksum=5c83cd2cf151717ba0243f73e1c7802ff36e272b67144bdd7f1f7d684fd6f03d ;; \
      *) echo "unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    wget -qO /minio "https://github.com/minio/minio/releases/download/RELEASE.2025-09-07T16-13-09Z/minio.linux-${TARGETARCH}.RELEASE.2025-09-07T16-13-09Z"; \
    echo "${checksum}  /minio" | sha256sum -c -; \
    chmod 0555 /minio

FROM alpine:3.22
RUN addgroup -g 1000 minio && adduser -D -H -u 1000 -G minio minio
COPY --from=download /minio /usr/local/bin/minio
USER 1000:1000
ENTRYPOINT ["minio"]
