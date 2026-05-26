#!/usr/bin/env bash
set -euo pipefail

# Always run from this script's directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Use project-local config dir.
export OPENHARNESS_CONFIG_DIR="${SCRIPT_DIR}/records"
export OPENHARNESS_DATA_DIR="${OPENHARNESS_CONFIG_DIR}/data"
export OPENHARNESS_LOGS_DIR="${OPENHARNESS_CONFIG_DIR}/logs"

mkdir -p "${OPENHARNESS_CONFIG_DIR}"
mkdir -p "${OPENHARNESS_DATA_DIR}"
mkdir -p "${OPENHARNESS_LOGS_DIR}"

# OpenHarness reads settings.json from OPENHARNESS_CONFIG_DIR.
SETTINGS_SRC="${SCRIPT_DIR}/settings.json"
if [[ ! -f "${SETTINGS_SRC}" ]]; then
  echo "[ERROR] Missing \"${SETTINGS_SRC}\""
  echo "Please create it first or copy from template."
  exit 1
fi
cp -f "${SETTINGS_SRC}" "${OPENHARNESS_CONFIG_DIR}/settings.json"

# Launch OpenHarness.
if command -v oh >/dev/null 2>&1; then
  exec oh "$@"
else
  exec python -m openharness "$@"
fi
