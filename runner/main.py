"""M2 runner entrypoint: proves the sandbox round trip only. No SDK, no tools, no git —
see PLAN-PHASE-2-M2.md §0.2. Deliberately stdlib-only: no pip install, nothing besides
the base Python image, so there's as little as possible for a compromised process to
find useful even before any real capability exists (PLAN-PHASE-2-M2.md §2.7).

Talks to exactly one thing: the internal API, over plain HTTP inside the Docker network
(TLS is terminated at Caddy only, which this container never reaches — see the Caddyfile
and PLAN-PHASE-2-M2.md §2.6). Never touches Postgres, Redis, or any provider API.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

RUN_ID = os.environ["RUN_ID"]
RUN_TOKEN = os.environ["RUN_TOKEN"]
API_BASE_URL = os.environ["API_BASE_URL"]
HEARTBEAT_INTERVAL_S = float(os.environ.get("HEARTBEAT_INTERVAL_S", "5"))
WORK_DURATION_S = float(os.environ.get("WORK_DURATION_S", "20"))
REQUEST_TIMEOUT_S = 10


def _post(path: str, body: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(  # noqa: S310 - fixed internal URL, not user input
        f"{API_BASE_URL}{path}",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {RUN_TOKEN}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:  # noqa: S310
        raw = response.read()
        return json.loads(raw) if raw else {}


def main() -> int:
    elapsed = 0.0
    while elapsed < WORK_DURATION_S:
        try:
            reply = _post(f"/internal/runs/{RUN_ID}/heartbeat", {})
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"heartbeat failed: {exc}", file=sys.stderr)
        else:
            if reply.get("cancel_requested"):
                print("cancellation requested; exiting")
                return 0
        time.sleep(HEARTBEAT_INTERVAL_S)
        elapsed += HEARTBEAT_INTERVAL_S

    try:
        _post(f"/internal/runs/{RUN_ID}/complete", {"result_summary": "M2 smoke run OK"})
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"complete call failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
