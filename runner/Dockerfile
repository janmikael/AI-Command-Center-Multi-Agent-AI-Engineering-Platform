# syntax=docker/dockerfile:1
#
# The sandboxed agent-runner image (PLAN-PHASE-2-M2.md, extended by PLAN-PHASE-2-M3.md
# and PLAN-PHASE-2-M4.md). This image is never attacker-selectable:
# acc.orchestrator.launcher hardcodes the tag it launches.
#
# M4 (PLAN-PHASE-2-M4.md §1) is the first deliberate expansion of this image's software
# surface since M2: the Claude Agent SDK's Python package drives the Claude Code CLI (a
# Node.js program) as a subprocess, so both a pinned pip package and a pinned npm global
# package are added here. Every version below is pinned exactly, same discipline as the
# runner image tag and docker-socket-proxy's own tag — never "latest". `git`, `nodejs`
# and `npm` come from Debian trixie's own apt repository (no external apt/npm source
# added) — confirmed to produce a Node 20 LTS runtime, sufficient for the pinned CLI,
# before pinning this Dockerfile down (PLAN-PHASE-2-M4.md §1).
FROM python:3.13-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends git nodejs npm \
 && npm install -g @anthropic-ai/claude-code@2.1.283 \
 && rm -rf /var/lib/apt/lists/* /root/.npm
RUN groupadd --system --gid 10001 runner \
 && useradd --system --uid 10001 --gid runner --no-create-home runner
WORKDIR /workspace
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --no-compile -r /app/requirements.txt
COPY main.py git_ops.py git_askpass.py agent_sdk.py /app/
RUN chmod +x /app/git_askpass.py
USER runner
ENV HOME=/tmp \
    GIT_ASKPASS=/app/git_askpass.py \
    GIT_TERMINAL_PROMPT=0
ENTRYPOINT ["python", "/app/main.py"]
