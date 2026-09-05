#!/usr/bin/env bash
# Template for MizarCCL/scripts/palomar_preflight.sh (Mizar-style comparator flags).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

find_toolkit() {
  local root="$1" d
  for d in \
    "${PALOMAR_PREFLIGHT_ROOT:-}" \
    "$(dirname "$root")/palomar-preflight" \
    "$root/palomar-preflight"; do
    [[ -n "$d" && -f "$d/palomar_preflight.sh" ]] && {
      cd "$d" && pwd
      return 0
    }
  done
  echo "error: palomar-preflight not found" >&2
  return 1
}

TOOLKIT="$(find_toolkit "$ROOT")"
exec bash "$TOOLKIT/palomar_preflight.sh" \
  --project-root "$ROOT" \
  --sorry-paths "MizarCCL/HIDDEN.lean MizarCCL/TARSKI.lean Solution.lean" \
  --closure-prefix PreSet \
  --closure-prefix TarskiSet \
  --closure-prefix instMembership \
  "$@"
