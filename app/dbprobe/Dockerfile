# Row S1b's database client (dbprobe.py). The PostgreSQL driver is the same
# hash-pinned wheel the rotation function uses, and TLS verifies against the
# RDS CA bundle, checked against a pinned digest at build time.
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1
ARG CA_URL=https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
ARG CA_SHA256=90a6f1a7978a1b1b096dca6c57511ca48d3b8cf052f8e704baf358f34ea2f21b
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements.txt \
 && python3 -c "import urllib.request, hashlib, sys; d = urllib.request.urlopen(sys.argv[1]).read(); \
assert hashlib.sha256(d).hexdigest() == sys.argv[2], 'RDS CA bundle changed upstream'; \
open('/tmp/ca.pem', 'wb').write(d)" "$CA_URL" "$CA_SHA256" \
 && mkdir -p /app && mv /tmp/ca.pem /app/rds-ca-bundle.pem \
 && useradd -u 10001 -M -s /usr/sbin/nologin lab
COPY dbprobe.py /app/dbprobe.py
USER 10001
EXPOSE 8080
ENTRYPOINT ["python3", "-u", "/app/dbprobe.py"]
