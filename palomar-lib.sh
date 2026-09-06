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
# in-repo palomar-preflight/ (CI checkout).
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
    "$project_root/palomar-preflight"; do
    [[ -z "$candidate" ]] && continue
    if [[ -f "$candidate/palomar_preflight.sh" ]]; then
      printf '%s\n' "$(cd "$candidate" && pwd)"
      return 0
    fi
  done
  cat >&2 <<EOF
error: palomar-preflight toolkit not found for $project_root
  tried: PALOMAR_PREFLIGHT_ROOT, ../palomar-preflight, palomar-preflight/
  local dev: keep palomar-preflight as a sibling of the Lean project
  CI: checkout catskillsresearch/palomar-preflight into palomar-preflight/
EOF
  return 1
}

# --- Preflight run report (.cache/palomar-editorial/preflight-run.json) ---

palomar_report_path() {
  printf '%s\n' "${PALOMAR_REPORT_PATH:-.cache/palomar-editorial/preflight-run.json}"
}

palomar_report_py() {
  local toolkit="${PALOMAR_TOOLKIT_ROOT:-$(palomar_toolkit_root)}"
  python3 "$toolkit/palomar_run_report.py" --out "$(palomar_report_path)" "$@"
}

palomar_report_init() {
  local toolkit="${PALOMAR_TOOLKIT_ROOT:-$(palomar_toolkit_root)}"
  local options
  options="$(python3 - <<'PY'
import json, os
print(json.dumps({
    "mechanical_only": os.environ.get("PALOMAR_MECHANICAL_ONLY") == "1",
    "editorial_only": os.environ.get("PALOMAR_EDITORIAL_ONLY") == "1",
    "no_policy_sync": os.environ.get("PALOMAR_NO_POLICY_SYNC") == "1",
    "sorry_paths": os.environ.get("PALOMAR_SORRY_PATHS", "Solution.lean"),
    "forbidden_prefixes": os.environ.get("PALOMAR_CHALLENGE_FORBIDDEN_PREFIXES", "").split(),
    "closure_prefixes": os.environ.get("PALOMAR_CLOSURE_PREFIXES", "").split(),
    "extra_print_names": os.environ.get("PALOMAR_EXTRA_PRINT_NAMES", "").split(),
    "check_decl_kinds": os.environ.get("PALOMAR_CHECK_DECL_KINDS", "1"),
}))
PY
)"
  mkdir -p "$(dirname "$(palomar_report_path)")"
  palomar_report_py init \
    --project-root "$PALOMAR_PROJECT_ROOT" \
    --toolkit-root "$toolkit" \
    --options "$options"
  export PALOMAR_REPORT_FAILED_PHASE=""
  export PALOMAR_REPORT_EXIT_CODE=0
  export PALOMAR_REPORT_MESSAGE=""
}

palomar_report_finalize() {
  local ec="${PALOMAR_REPORT_EXIT_CODE:-$?}"
  if [[ -z "${PALOMAR_REPORT_EXIT_CODE:-}" ]]; then
    ec=$?
  fi
  palomar_report_py finalize \
    --exit-code "$ec" \
    --failed-phase "${PALOMAR_REPORT_FAILED_PHASE:-}" \
    --message "${PALOMAR_REPORT_MESSAGE:-}" \
    --print-path 2>/dev/null || true
}

palomar_report_observe() {
  local key="$1"
  local value="$2"
  palomar_report_py observe --key "$key" --value "$value"
}

palomar_report_skip_phase() {
  local id="$1"
  local reason="${2:-skipped}"
  palomar_report_py phase-skip --id="$id" --reason="$reason"
}

# Run one preflight phase: print title, capture output, record pass/fail.
# Usage: palomar_run_phase PHASE_ID "Title" [abort_on_fail=1] command...
# Set abort_on_fail=0 to record failure but continue (type_compare).
palomar_run_phase() {
  local phase_id="$1"
  local title="$2"
  local abort="${3:-1}"
  shift 3
  local out_file ec
  out_file="$(mktemp)"
  palomar_report_py phase-start --id "$phase_id" --title "$title"
  printf '\n== %s ==\n' "$title"
  set +e
  "$@" > >(tee "$out_file") 2>&1
  ec=$?
  set -e
  palomar_report_py phase-end --id "$phase_id" --exit-code "$ec" --output-file "$out_file"
  rm -f "$out_file"
  if [[ "$ec" -ne 0 ]]; then
    export PALOMAR_REPORT_FAILED_PHASE="$phase_id"
    export PALOMAR_REPORT_EXIT_CODE="$ec"
    if [[ "$abort" == "1" ]]; then
      exit "$ec"
    fi
  fi
  return "$ec"
}
