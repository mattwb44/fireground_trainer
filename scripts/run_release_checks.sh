#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"

echo "Running unit tests..."
python3 -m unittest tests.test_submission_flow

echo
echo "Running Python syntax checks..."
python3 -m py_compile app.py models.py authz.py

echo
echo "Release checks passed."
