#!/usr/bin/env bash
# Thin shell entry that forwards to the Python CLI.
# Equivalent: python3 run.py <args>
# Per spec (A2): ./run.sh --rtl <dir> --top <name> --out <dir> --seed <n> --num-seq 5000

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/run.py" "$@"
