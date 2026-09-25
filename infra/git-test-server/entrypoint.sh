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
  git add README.md
  git commit -m "initial commit"
  git push origin main
  cd /
  rm -rf "$SEED_DIR"
fi

exec git daemon --reuseaddr --base-path=/repos --export-all --enable=receive-pack --verbose /repos
