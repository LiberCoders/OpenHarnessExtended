#!/usr/bin/env bash
set -euo pipefail

UPSTREAM_URL="https://github.com/HKUDS/OpenHarness"

if ! git remote get-url upstream >/dev/null 2>&1; then
  echo "Upstream remote not found. Adding upstream: ${UPSTREAM_URL}"
  git remote add upstream "${UPSTREAM_URL}"
fi

echo "[1/2] Fetching latest changes from upstream..."
git fetch upstream

echo "[2/2] Merging upstream/main into current branch..."
git merge upstream/main

echo "Upstream sync completed successfully."
