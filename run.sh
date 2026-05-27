#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-venv}"
ENVIRONMENT="${1:-uat}"

if [[ "$ENVIRONMENT" != "uat" && "$ENVIRONMENT" != "dev" ]]; then
  echo "Invalid environment: '$ENVIRONMENT'"
  echo "Usage: ./run.sh [uat|dev]"
  exit 1
fi

if [[ ! -f "config.json" ]]; then
  echo "Missing required file: config.json"
  exit 1
fi

if [[ ! -f "network_list.csv" ]]; then
  echo "Missing required source file: network_list.csv"
  exit 1
fi

if [[ ! -f "requirements.txt" ]]; then
  echo "Missing required file: requirements.txt"
  exit 1
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python executable not found: $PYTHON_BIN"
  echo "Install Python 3 and retry."
  exit 1
fi

if [[ ! -d "$VENV_DIR" ]]; then
  echo "Creating virtual environment in '$VENV_DIR'..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

echo "Installing/updating dependencies..."
python -m pip install --upgrade pip
pip install -r requirements.txt

echo "Running reports for environment: $ENVIRONMENT"
if [[ "$ENVIRONMENT" == "uat" ]]; then
  python main.py --uat
else
  python main.py --dev
fi

echo "Run completed successfully."
