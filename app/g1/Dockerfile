FROM python:3.13.9-alpine3.22

WORKDIR /app
COPY app.py /app/app.py

RUN apk upgrade --no-cache \
    && addgroup -S -g 10001 g1 \
    && adduser -S -D -H -u 10001 -G g1 g1
USER 10001:10001

ENTRYPOINT ["python3", "/app/app.py"]
