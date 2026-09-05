#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$ROOT/palomar_sibling_matrix.py" --parent-dir "$(dirname "$ROOT")" "$@"
