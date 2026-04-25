#!/usr/bin/env bash
# Avoid -u here: conda shell hook may reference unset variables.
set -eo pipefail

# Always run from this script's directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Conda env name (edit this line only when needed).
CONDA_ENV_NAME="py310"

# Use project-local config dir.
export OPENHARNESS_CONFIG_DIR="${SCRIPT_DIR}/build/wksp"
# Source settings file name under OPENHARNESS_CONFIG_DIR (copied to settings.json before launch).
OPENHARNESS_SETTINGS_SOURCE_FILE="settings_debug.json"
export OPENHARNESS_DATA_DIR="${OPENHARNESS_CONFIG_DIR}/data"
export OPENHARNESS_LOGS_DIR="${OPENHARNESS_CONFIG_DIR}/logs"

mkdir -p "${OPENHARNESS_CONFIG_DIR}"
mkdir -p "${OPENHARNESS_DATA_DIR}"
mkdir -p "${OPENHARNESS_LOGS_DIR}"

# OpenHarness reads settings.json from OPENHARNESS_CONFIG_DIR.
SETTINGS_SRC="${OPENHARNESS_CONFIG_DIR}/${OPENHARNESS_SETTINGS_SOURCE_FILE}"
if [[ ! -f "${SETTINGS_SRC}" ]]; then
  echo "[ERROR] Missing \"${SETTINGS_SRC}\""
  echo "Please create it first or copy from template."
  exit 1
fi
cp -f "${SETTINGS_SRC}" "${OPENHARNESS_CONFIG_DIR}/settings.json"

# Require conda. Exit immediately on failure.
if ! command -v conda >/dev/null 2>&1; then
  echo "[ERROR] conda is not available in PATH."
  exit 1
fi

# shellcheck disable=SC1091
eval "$(conda shell.bash hook)"
if ! conda activate "${CONDA_ENV_NAME}"; then
  echo "[ERROR] Failed to activate conda env \"${CONDA_ENV_NAME}\"."
  exit 1
fi

# Launch OpenHarness.
if command -v oh >/dev/null 2>&1; then
  exec oh "$@"
else
  exec python -m openharness "$@"
fi
