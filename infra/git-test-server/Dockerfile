# syntax=docker/dockerfile:1
#
# A hermetic git remote for the backend test suite (PLAN-PHASE-2-M3.md §7) — real git,
# real network, zero dependency on any real hosting provider or credential. Dev/test
# only: never part of compose.yml's default `up`, and unauthenticated by design (the
# askpass/credential mechanism gets its own unit tests independent of any server; the
# real, authenticated remote is only exercised in live verification).
FROM alpine:3.20
RUN apk add --no-cache git git-daemon
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 9418
ENTRYPOINT ["/entrypoint.sh"]
