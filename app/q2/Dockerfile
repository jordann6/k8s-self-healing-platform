# Q2's bounded HTTP workload, controllable metric source, and request generator.
# Reuse Q1's verified multi-architecture Python base pin.
FROM python:3.12-alpine3.22@sha256:a190708a2dec1bd18b1decb539f8e8f5407abaa9bf39cacda583f7f8c11db322

WORKDIR /app
COPY app.py /app/app.py

RUN addgroup -g 10001 app && adduser -D -u 10001 -G app app
USER 10001:10001

ENTRYPOINT ["python3", "/app/app.py"]
