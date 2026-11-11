# Row Q1's queue worker (worker.py): a RabbitMQ consumer KEDA scales on queue
# depth. Non-root, read-only root filesystem in the Deployment. pika is the one
# non-stdlib dependency, installed from the hash-pinned pure-python wheel.
# Base pinned by digest (the multi-arch index of python:3.12-alpine3.22, read
# 2026-11-04) so a re-pushed tag cannot change what runs.
FROM python:3.12-alpine3.22@sha256:a190708a2dec1bd18b1decb539f8e8f5407abaa9bf39cacda583f7f8c11db322
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements.txt \
 && adduser -D -u 10001 worker
COPY worker.py /app/worker.py
USER 10001
ENTRYPOINT ["python3", "/app/worker.py"]
