# The AKS R2 worker (worker.py). Standard library only, no shell needed at
# run time, non-root, read-only root filesystem in the Deployment.
FROM python:3.12-alpine3.22
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN adduser -D -u 10001 worker
COPY worker.py /app/worker.py
USER 10001
ENTRYPOINT ["python3", "/app/worker.py"]
