#!/usr/bin/env bash
# Copy to LeanProject/scripts/palomar_preflight.sh and set PROJECT_* below.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

find_toolkit() {
  local root="$1" d
  for d in \
    "${PALOMAR_PREFLIGHT_ROOT:-}" \
    "$(dirname "$root")/palomar-preflight" \
    "$root/palomar-preflight" \
    "$root/vendor/palomar-preflight"; do
    [[ -n "$d" && -f "$d/palomar_preflight.sh" ]] && {
      cd "$d" && pwd
      return 0
    }
  done
  echo "error: palomar-preflight not found; set PALOMAR_PREFLIGHT_ROOT or checkout toolkit" >&2
  return 1
}

# --- project-specific (edit when copying) ---
PROJECT_SORRY_PATHS="Solution.lean"
PROJECT_FORBIDDEN_PREFIXES=()
# --- end project-specific ---

TOOLKIT="$(find_toolkit "$ROOT")"
args=(--project-root "$ROOT" --sorry-paths "$PROJECT_SORRY_PATHS")
for prefix in "${PROJECT_FORBIDDEN_PREFIXES[@]}"; do
  args+=(--forbidden-prefix "$prefix")
done
exec bash "$TOOLKIT/palomar_preflight.sh" "${args[@]}" "$@"
