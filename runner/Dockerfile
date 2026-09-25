# syntax=docker/dockerfile:1
#
# The sandboxed agent-runner image (PLAN-PHASE-2-M2.md, extended by PLAN-PHASE-2-M3.md).
# `git` is the only thing this image installs beyond the base Python image and its own
# stdlib-only code — no pip installs, no other package manager left at runtime. This
# image is never attacker-selectable: acc.orchestrator.launcher hardcodes the tag it
# launches.
FROM python:3.13-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*
RUN groupadd --system --gid 10001 runner \
 && useradd --system --uid 10001 --gid runner --no-create-home runner
WORKDIR /workspace
COPY main.py git_ops.py git_askpass.py /app/
RUN chmod +x /app/git_askpass.py
USER runner
ENV HOME=/tmp \
    GIT_ASKPASS=/app/git_askpass.py \
    GIT_TERMINAL_PROMPT=0
ENTRYPOINT ["python", "/app/main.py"]
