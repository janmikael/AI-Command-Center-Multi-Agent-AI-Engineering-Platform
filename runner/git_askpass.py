#!/usr/bin/env python3
"""GIT_ASKPASS target (PLAN-PHASE-2-M3.md §2.3). git invokes this with a prompt string
("Username for '...': " or "Password for '...': ") as argv[1] and reads our stdout as
the answer. The credential is read from an env var and never written to .git/config,
never embedded in the remote URL, never logged — this script's only job is to hand it
to git exactly once, in memory, for exactly this process.
"""

import os
import sys


def main() -> int:
    prompt = sys.argv[1] if len(sys.argv) > 1 else ""
    if "Username" in prompt:
        # GitHub (and most hosts) accept any non-empty username alongside a token
        # password/PAT over HTTPS; the token itself is what's actually checked.
        print("x-access-token")
    else:
        print(os.environ.get("GIT_CREDENTIAL", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
