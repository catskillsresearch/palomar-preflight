# Shared bash helpers for Palomar local preflight. Source from toolkit scripts.
# shellcheck shell=bash

palomar_toolkit_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")" && pwd
}

palomar_project_root() {
  if [[ -n "${PALOMAR_PROJECT_ROOT:-}" ]]; then
    printf '%s\n' "$PALOMAR_PROJECT_ROOT"
    return 0
  fi
  local dir="$PWD"
  while true; do
    if [[ -f "$dir/comparator.json" && -f "$dir/lean-toolchain" ]]; then
      printf '%s\n' "$dir"
      return 0
    fi
    [[ "$dir" == / ]] && break
    dir="$(dirname "$dir")"
  done
  echo "error: run from a Palomar Lean project (comparator.json + lean-toolchain)" >&2
  echo "Set PALOMAR_PROJECT_ROOT or invoke via the project's scripts/palomar_preflight.sh." >&2
  return 1
}

palomar_cd_project() {
  local root
  root="$(palomar_project_root)" || return 1
  export PALOMAR_PROJECT_ROOT="$root"
  cd "$root"
}

# Resolve a plain-file palomar-preflight checkout for a Lean project.
# Search order: PALOMAR_PREFLIGHT_ROOT, sibling ../palomar-preflight,
# in-repo palomar-preflight/ (CI checkout), legacy vendor/palomar-preflight/.
palomar_resolve_toolkit() {
  local project_root="${1:-}"
  local candidate=""
  if [[ -z "$project_root" ]]; then
    project_root="$(palomar_project_root)" || return 1
  fi
  project_root="$(cd "$project_root" && pwd)"
  for candidate in \
    "${PALOMAR_PREFLIGHT_ROOT:-}" \
    "$(dirname "$project_root")/palomar-preflight" \
    "$project_root/palomar-preflight" \
    "$project_root/vendor/palomar-preflight"; do
    [[ -z "$candidate" ]] && continue
    if [[ -f "$candidate/palomar_preflight.sh" ]]; then
      printf '%s\n' "$(cd "$candidate" && pwd)"
      return 0
    fi
  done
  cat >&2 <<EOF
error: palomar-preflight toolkit not found for $project_root
  tried: PALOMAR_PREFLIGHT_ROOT, ../palomar-preflight, palomar-preflight/, vendor/palomar-preflight/
  local dev: keep palomar-preflight as a sibling of the Lean project
  CI: checkout catskillsresearch/palomar-preflight into palomar-preflight/
EOF
  return 1
}
