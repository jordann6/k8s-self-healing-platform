FROM python:3.13.9-alpine3.22

WORKDIR /app
COPY controller.py /app/controller.py

RUN apk upgrade --no-cache \
    && addgroup -S -g 10001 x1 \
    && adduser -S -D -H -u 10001 -G x1 x1
USER 10001:10001

ENTRYPOINT ["python3", "/app/controller.py"]
