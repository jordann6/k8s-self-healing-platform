# One image for every role (see app.py). RELEASE is baked in, so the bad
# release for row A2 is a different image tag, exactly as in production: v1 is
# the good build, v2 carries the regression.
#
# curl stays in the image: the rotation driver and the evidence collector use it
# through kubectl exec. No shell is ever run by the app itself, which is what
# makes "a shell spawned in a web pod" a clean runtime signal for row R1.
FROM python:3.12-alpine3.22
ARG RELEASE=v1
ENV RELEASE=${RELEASE} PYTHONDONTWRITEBYTECODE=1
RUN apk add --no-cache curl \
 && adduser -D -u 10001 lab
COPY app.py /app/app.py
USER 10001
EXPOSE 8080 8443
ENTRYPOINT ["python3", "-u", "/app/app.py"]
