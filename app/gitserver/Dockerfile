# In-cluster Git server for Argo CD. The repo stays local (no GitHub in phase
# 1), so Argo CD pulls from git://gitserver.gitops.svc and the runner pushes to
# it through a port-forward. A bad release (row A2) is therefore a real commit,
# and a profile switch is a change Argo CD syncs, never a kubectl apply.
FROM alpine:3.22
RUN apk add --no-cache git git-daemon \
 && adduser -D -u 10001 git
USER 10001
EXPOSE 9418
ENTRYPOINT ["git", "daemon", "--reuseaddr", "--export-all", "--enable=receive-pack", \
            "--informative-errors", "--base-path=/srv/git", "/srv/git"]
