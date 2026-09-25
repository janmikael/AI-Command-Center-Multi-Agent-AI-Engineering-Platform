# syntax=docker/dockerfile:1
#
# The sandboxed agent-runner image (PLAN-PHASE-2-M2.md). No package manager left at
# runtime, no pip installs — main.py is stdlib-only. This image is never attacker-
# selectable: acc.orchestrator.launcher hardcodes the tag it launches.
FROM python:3.13-slim
RUN groupadd --system --gid 10001 runner \
 && useradd --system --uid 10001 --gid runner --no-create-home runner
WORKDIR /workspace
COPY main.py /app/main.py
USER runner
ENTRYPOINT ["python", "/app/main.py"]
