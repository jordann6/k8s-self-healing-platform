FROM python:3.13.9-alpine3.22

WORKDIR /app
COPY app.py /app/app.py

RUN apk upgrade --no-cache \
    && addgroup -S -g 10001 o1 \
    && adduser -S -D -H -u 10001 -G o1 o1
USER 10001:10001

ENTRYPOINT ["python3", "/app/app.py"]
