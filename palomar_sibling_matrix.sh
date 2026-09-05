#!/usr/bin/env bash
# Report Palomar preflight status for sibling repos (no Palomar badge in README).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$ROOT/palomar_sibling_matrix.py" "$@"
