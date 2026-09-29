#!/bin/sh
# Seeds a bare test repository on first start (idempotent — the volume persists it
# across restarts), then serves it read/write over the unauthenticated git:// protocol.
# Dev/test only — see the Dockerfile for why unauthenticated is fine here.
set -eu

REPO_DIR=/repos/test.git
SEED_DIR=/tmp/seed

if [ ! -d "$REPO_DIR" ]; then
  git init --bare --initial-branch=main "$REPO_DIR"
  git clone "$REPO_DIR" "$SEED_DIR"
  cd "$SEED_DIR"
  git config user.name "Test Seed"
  git config user.email "seed@test.local"
  echo "hermetic test repository for the acc backend test suite" > README.md

  # A small existing module + its test (PLAN-PHASE-2-M4.3.md §2/§3): real content for a
  # real developer task to read and edit, not just create from nothing. Stdlib unittest,
  # not pytest — the runner sandbox has no test framework installed beyond Python's own
  # (PLAN-PHASE-2-M4.md §1's deliberately minimal image), and this way a real task's
  # `python3 -m unittest` needs nothing extra to actually run.
  cat > greetings.py <<'PYEOF'
def greet(name: str) -> str:
    """Return a friendly greeting for `name`."""
    return f"Hello, {name}!"
PYEOF
  cat > test_greetings.py <<'PYEOF'
import unittest

from greetings import greet


class TestGreet(unittest.TestCase):
    def test_greet_includes_the_name(self) -> None:
        self.assertEqual(greet("Ada"), "Hello, Ada!")


if __name__ == "__main__":
    unittest.main()
PYEOF

  git add README.md greetings.py test_greetings.py
  git commit -m "initial commit"
  git push origin main
  cd /
  rm -rf "$SEED_DIR"
fi

exec git daemon --reuseaddr --base-path=/repos --export-all --enable=receive-pack --verbose /repos
