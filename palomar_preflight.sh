#!/usr/bin/env bash
# Palomar local preflight: mechanical Comparator checks + editorial LLM audit.
# Mechanical runs Palomar's pinned Comparator so local/CI rejection matches
# registry verification. Use --mechanical-only for CI without API calls.
# Use --editorial-only to rerun policy sync and the LLM audit without rebuilding.
set -euo pipefail

TOOLKIT_ROOT="$(cd "$(dirname "$0")" && pwd)"
export PALOMAR_TOOLKIT_ROOT="$TOOLKIT_ROOT"
# shellcheck source=palomar-lib.sh
source "$TOOLKIT_ROOT/palomar-lib.sh"
export PYTHONPATH="$TOOLKIT_ROOT${PYTHONPATH:+:$PYTHONPATH}"

MECHANICAL_ONLY=0
EDITORIAL_ONLY=0
ALLOW_DIRTY=0
NO_POLICY_SYNC=0
PALOMAR_FORBIDDEN_PREFIXES=()
PALOMAR_CLOSURE_PREFIXES=()
PALOMAR_EXTRA_PRINT_NAMES=()

palomar_preflight_usage() {
  cat <<'EOF'
Usage: palomar_preflight.sh [OPTIONS] [PROJECT_ROOT]

Run Palomar mechanical preflight (and optional editorial audit) for a Lean
project containing comparator.json and lean-toolchain.

Options:
  --project-root DIR       Lean project root (default: discover from cwd)
  --sorry-paths PATHS      Space-separated sorry scan paths (default: Solution.lean)
  --forbidden-prefix P     Extra forbidden Challenge import prefix (repeatable)
  --closure-prefix P       Extra namespace prefix for declaration-closure walk
  --extra-print-name N     Extra constant to #print in closure walk (repeatable)
  --mechanical-only        Skip policy sync and LLM editorial audit
  --editorial-only         Skip mechanical phases; run policy sync and LLM audit
  --allow-dirty            Pin HEAD even if Challenge/comparator files are dirty
  --no-policy-sync         Audit against committed vendor/palomar-policy only
  --report-out PATH        Write preflight-run.json (default: .cache/palomar-editorial/preflight-run.json)
  -h, --help               Show this help

Project wrappers typically exec this script with --project-root and --sorry-paths.
Optional scripts/palomar_preflight_local.sh runs extra mechanical checks.

Full preflight requires CURSOR_API_KEY (or ../tokens_ssto.yaml) and runs Cursor
editorial review: gpt-5.6-sol for substantive passes, composer-2.5 for lighter.
Use --editorial-only after a green mechanical run to retry the LLM audit only.

Every run writes a structured phase report (JSON) suitable for overview tables.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-root)
      [[ $# -ge 2 ]] || { echo "error: missing value for $1" >&2; exit 2; }
      export PALOMAR_PROJECT_ROOT="$(cd "$2" && pwd)"
      shift 2
      ;;
    --sorry-paths)
      [[ $# -ge 2 ]] || { echo "error: missing value for $1" >&2; exit 2; }
      export PALOMAR_SORRY_PATHS="$2"
      shift 2
      ;;
    --forbidden-prefix)
      [[ $# -ge 2 ]] || { echo "error: missing value for $1" >&2; exit 2; }
      PALOMAR_FORBIDDEN_PREFIXES+=("$2")
      shift 2
      ;;
    --closure-prefix)
      [[ $# -ge 2 ]] || { echo "error: missing value for $1" >&2; exit 2; }
      PALOMAR_CLOSURE_PREFIXES+=("$2")
      shift 2
      ;;
    --extra-print-name)
      [[ $# -ge 2 ]] || { echo "error: missing value for $1" >&2; exit 2; }
      PALOMAR_EXTRA_PRINT_NAMES+=("$2")
      shift 2
      ;;
    --mechanical-only) MECHANICAL_ONLY=1; shift ;;
    --editorial-only) EDITORIAL_ONLY=1; shift ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    --no-policy-sync) NO_POLICY_SYNC=1; shift ;;
    --report-out)
      [[ $# -ge 2 ]] || { echo "error: missing value for $1" >&2; exit 2; }
      export PALOMAR_REPORT_PATH="$2"
      shift 2
      ;;
    -h|--help) palomar_preflight_usage; exit 0 ;;
    --) shift; break ;;
    -*)
      echo "Unknown option: $1" >&2
      palomar_preflight_usage >&2
      exit 2
      ;;
    *)
      if [[ -z "${PALOMAR_PROJECT_ROOT:-}" && -f "$1/comparator.json" && -f "$1/lean-toolchain" ]]; then
        export PALOMAR_PROJECT_ROOT="$(cd "$1" && pwd)"
        shift
      else
        echo "Unknown argument: $1" >&2
        palomar_preflight_usage >&2
        exit 2
      fi
      ;;
  esac
done

if [[ ${#PALOMAR_FORBIDDEN_PREFIXES[@]} -gt 0 ]]; then
  export PALOMAR_CHALLENGE_FORBIDDEN_PREFIXES="${PALOMAR_FORBIDDEN_PREFIXES[*]}"
fi
if [[ ${#PALOMAR_CLOSURE_PREFIXES[@]} -gt 0 ]]; then
  export PALOMAR_CLOSURE_PREFIXES="${PALOMAR_CLOSURE_PREFIXES[*]}"
fi
if [[ ${#PALOMAR_EXTRA_PRINT_NAMES[@]} -gt 0 ]]; then
  export PALOMAR_EXTRA_PRINT_NAMES="${PALOMAR_EXTRA_PRINT_NAMES[*]}"
fi

if [[ "$MECHANICAL_ONLY" -eq 1 && "$EDITORIAL_ONLY" -eq 1 ]]; then
  echo "error: --mechanical-only and --editorial-only are mutually exclusive" >&2
  exit 2
fi

export PALOMAR_MECHANICAL_ONLY="$MECHANICAL_ONLY"
export PALOMAR_EDITORIAL_ONLY="$EDITORIAL_ONLY"
export PALOMAR_ALLOW_DIRTY="$ALLOW_DIRTY"
export PALOMAR_NO_POLICY_SYNC="$NO_POLICY_SYNC"

palomar_cd_project
export PALOMAR_SORRY_PATHS="${PALOMAR_SORRY_PATHS:-Solution.lean}"

palomar_report_init
trap 'palomar_report_finalize' EXIT

MECHANICAL_PHASE_IDS=(
  comparator_config
  challenge_imports
  challenge_size
  lake_manifest
  no_submodules
  local_checks
  lake_build
  type_compare
  comparator
  sorry_scan
  axioms
  patch_format
)

if [[ "$EDITORIAL_ONLY" -eq 1 ]]; then
  for phase_id in "${MECHANICAL_PHASE_IDS[@]}"; do
    palomar_report_skip_phase "$phase_id" "editorial-only"
  done
else
palomar_run_phase comparator_config "Validate Comparator configuration" 1 python3 - <<'PY'
import json
import re

with open("comparator.json", encoding="utf-8") as f:
    cfg = json.load(f)

allowed_keys = {
    "challenge_module",
    "solution_module",
    "theorem_names",
    "definition_names",
    "permitted_axioms",
    "enable_nanoda",
}
unknown = sorted(set(cfg) - allowed_keys)
if unknown:
    raise SystemExit(f"Unknown comparator.json keys: {', '.join(unknown)}")

for key in ("challenge_module", "solution_module", "theorem_names", "definition_names", "permitted_axioms"):
    if key not in cfg:
        raise SystemExit(f"Missing comparator.json key: {key}")

challenge = cfg["challenge_module"]
solution = cfg["solution_module"]
theorems = cfg["theorem_names"]
definitions = cfg.get("definition_names", [])
axioms = cfg["permitted_axioms"]

if challenge == solution:
    raise SystemExit("challenge_module and solution_module must differ")

module_part = re.compile(r"[A-Za-z_][A-Za-z0-9_']*")
for key in ("challenge_module", "solution_module"):
    name = cfg[key]
    if not isinstance(name, str) or not name:
        raise SystemExit(f"{key} must be a nonempty string")
    if not all(module_part.fullmatch(part) for part in name.split(".")):
        raise SystemExit(f"{key} is not a safe dotted Lean module name: {name!r}")

if not isinstance(theorems, list) or not theorems or not all(
    isinstance(name, str) and name for name in theorems
):
    raise SystemExit("theorem_names must be a nonempty array of nonempty strings")

if not isinstance(definitions, list) or not all(
    isinstance(name, str) and name for name in definitions
):
    raise SystemExit("definition_names must be an array of nonempty strings")

allowed_axioms = {"propext", "Quot.sound", "Classical.choice"}
if not isinstance(axioms, list) or not all(isinstance(x, str) for x in axioms):
    raise SystemExit("permitted_axioms must be an array of strings")
extra = sorted(set(axioms) - allowed_axioms)
if extra:
    raise SystemExit(
        "permitted_axioms exceeds Palomar allowlist "
        f"(forbidden: {', '.join(extra)})"
    )

declarations = theorems + definitions
duplicates = sorted({name for name in declarations if declarations.count(name) > 1})
if duplicates:
    raise SystemExit(f"Duplicate Comparator names: {', '.join(duplicates)}")

print(
    f"OK: challenge_module={challenge}, solution_module={solution}, "
    f"{len(theorems)} theorems, {len(definitions)} definitions, "
    f"{len(declarations)} declarations."
)
PY

palomar_run_phase challenge_imports "Challenge import discipline (Mathlib only)" 1 python3 - <<'PY'
import json
import os
import re
from pathlib import Path

text = Path("Challenge.lean").read_text(encoding="utf-8")
imports = re.findall(r"^import\s+(\S+)", text, re.MULTILINE)
cfg = json.loads(Path("comparator.json").read_text(encoding="utf-8"))
forbidden = {"Solution"}
for name in cfg["theorem_names"] + cfg.get("definition_names", []):
    head = name.split(".", 1)[0]
    if head:
        forbidden.add(head)
for extra in os.environ.get("PALOMAR_CHALLENGE_FORBIDDEN_PREFIXES", "").split():
    forbidden.add(extra)
for imp in imports:
    head = imp.split(".", 1)[0]
    if head in forbidden or imp.startswith("Solution"):
        raise SystemExit(f"Forbidden Challenge import: {imp}")
    if not (imp.startswith("Init") or imp.startswith("Std")
            or imp.startswith("Lean") or imp.startswith("Mathlib")):
        raise SystemExit(
            f"Challenge import not allowlisted (Init/Mathlib/Std/Lean): {imp}"
        )
print(f"OK: Challenge has {len(imports)} explicit import(s).")
PY

palomar_run_phase challenge_size "Challenge surface size limits" 1 python3 - <<'PY'
from pathlib import Path

path = Path("Challenge.lean")
lines = path.read_text(encoding="utf-8").count("\n") + 1
size = path.stat().st_size
if lines >= 1000:
    raise SystemExit(f"Challenge.lean too long: {lines} lines (limit 1000)")
if size >= 100 * 1024:
    raise SystemExit(f"Challenge.lean too large: {size} bytes (limit 100 KiB)")
print(f"OK: Challenge.lean is {lines} lines, {size} bytes.")
PY

palomar_run_phase lake_manifest "Exactly one Lake manifest at repository root" 1 bash -c '
if [[ -f lakefile.toml && -f lakefile.lean ]]; then
  echo "FAIL: both lakefile.toml and lakefile.lean present."
  exit 1
fi
if [[ ! -f lakefile.toml && ! -f lakefile.lean ]]; then
  echo "FAIL: no lakefile.toml or lakefile.lean at repository root."
  exit 1
fi
if [[ ! -f lake-manifest.json ]]; then
  echo "FAIL: lake-manifest.json is missing."
  exit 1
fi
if [[ ! -f lean-toolchain ]]; then
  echo "FAIL: lean-toolchain is missing."
  exit 1
fi
echo "OK: Lake config, manifest, and toolchain present."
'

palomar_run_phase no_submodules "Reject git submodules (Palomar cannot preserve them)" 1 bash -c '
if [[ -e .gitmodules ]]; then
  echo "FAIL: .gitmodules is present; Palomar cannot preserve submodules."
  exit 1
fi
echo "OK: no .gitmodules."
'

if [[ -f scripts/palomar_preflight_local.sh ]]; then
  palomar_run_phase local_checks "Project-specific mechanical checks" 1 \
    bash scripts/palomar_preflight_local.sh
else
  palomar_report_skip_phase local_checks "no scripts/palomar_preflight_local.sh"
fi

palomar_run_phase lake_build "Build Lean project" 1 bash -c '
  log="$(mktemp)"
  trap "rm -f \"$log\"" EXIT
  lake build 2>&1 | grep -vE "LEAN_PATH|trace:" | tee "$log"
  ec=${PIPESTATUS[0]}
  tail -20 "$log"
  exit "$ec"
'

compare_status=0
palomar_run_phase type_compare \
  "Compare Challenge/Solution types and declaration-closure values" \
  0 env PALOMAR_QUIET=1 bash "$TOOLKIT_ROOT/compare_challenge_solution_types.sh" \
  || compare_status=$?

palomar_run_phase comparator "Run Palomar-pinned Comparator" 1 \
  bash "$TOOLKIT_ROOT/verify-comparator.sh"

if [[ "$compare_status" -ne 0 ]]; then
  echo "Pretty-print declaration-closure check also failed (exit ${compare_status})."
  palomar_report_observe type_compare_deferred_failure true
  export PALOMAR_REPORT_FAILED_PHASE="type_compare"
  export PALOMAR_REPORT_EXIT_CODE="$compare_status"
  export PALOMAR_REPORT_MESSAGE="Pretty-print declaration-closure check failed after Comparator passed."
  exit "$compare_status"
fi

palomar_run_phase sorry_scan "Reject proof holes in Solution sources" 1 python3 - <<'PY'
import os
import re
from pathlib import Path

pattern = re.compile(
    r"(^|:=|by)[[:space:]]+(?:sorry|admit)([[:space:];]|$)|^[[:space:]]*(?:sorry|admit)([[:space:];]|$)",
    re.MULTILINE,
)
raw = os.environ.get("PALOMAR_SORRY_PATHS", "Solution.lean").split()
if not raw:
    raw = ["Solution.lean"]
files = []
for item in raw:
    path = Path(item)
    if path.is_dir():
        files.extend(sorted(path.rglob("*.lean")))
    elif path.is_file():
        files.append(path)
    else:
        raise SystemExit(f"FAIL: PALOMAR_SORRY_PATHS entry missing: {item}")
hits = []
for path in files:
    text = path.read_text(encoding="utf-8")
    for match in pattern.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        hits.append(f"{path}:{line}")
if hits:
    print("FAIL: Solution proof sources contain sorry/admit:")
    print("\n".join(f"  {hit}" for hit in hits))
    raise SystemExit(1)
print(f"OK: scanned {len(files)} Solution proof file(s); no sorry/admit.")
PY

palomar_run_phase axioms "Check permitted theorem axioms" 1 \
  bash "$TOOLKIT_ROOT/palomar_axioms_check.sh"

palomar_run_phase patch_format "Check patch formatting" 1 git diff --check

fi

if [[ "$MECHANICAL_ONLY" -eq 1 ]]; then
  for phase_id in policy_sync editorial_prechecks mechanical_report editorial_audit; do
    palomar_report_skip_phase "$phase_id" "mechanical-only"
  done
  echo ""
  echo "OK: mechanical preflight passed (--mechanical-only; editorial audit skipped)."
  echo "NOTE: full Palomar preflight also runs vendored-policy sync and Cursor editorial audit (gpt-5.6-sol + composer-2.5)."
  export PALOMAR_REPORT_EXIT_CODE=0
  export PALOMAR_REPORT_MESSAGE="mechanical preflight passed (--mechanical-only)"
  exit 0
fi

SYNC_ARGS=(--root vendor/palomar-policy --pin vendor/PALOMAR_POLICY_PIN)
if [[ "$NO_POLICY_SYNC" -eq 1 ]]; then
  SYNC_ARGS+=(--no-sync)
fi

palomar_run_phase policy_sync "Sync PalomarPolicy to upstream latest" 1 \
  python3 "$TOOLKIT_ROOT/palomar_policy_sync.py" "${SYNC_ARGS[@]}"

palomar_run_phase editorial_prechecks "Palomar editorial pre-checks" 1 \
  python3 "$TOOLKIT_ROOT/palomar_editorial_checks.py"

MECH_REPORT_ARGS=(--out .cache/palomar-editorial/mechanical-report.json)
if [[ "$ALLOW_DIRTY" -eq 1 ]]; then
  MECH_REPORT_ARGS+=(--allow-dirty)
fi
palomar_run_phase mechanical_report "Build local mechanical report" 1 bash -c '
  mkdir -p .cache/palomar-editorial
  python3 "$0/palomar_mechanical_report.py" "$@"
' "$TOOLKIT_ROOT" "${MECH_REPORT_ARGS[@]}"

if [[ -z "${CURSOR_API_KEY:-}" ]]; then
  for TOKENS in ../tokens_ssto.yaml tokens_ssto.yaml; do
    if [[ -f "$TOKENS" ]]; then
      CURSOR_API_KEY="$(grep -E '^CURSOR_API_KEY:' "$TOKENS" | head -1 | sed -E 's/^CURSOR_API_KEY:[[:space:]]*//')"
      if [[ -n "$CURSOR_API_KEY" ]]; then
        export CURSOR_API_KEY
        break
      fi
    fi
  done
fi

palomar_run_phase editorial_audit "Palomar editorial audit (LLM, gpt-5.6-sol + composer-2.5)" 1 \
  bash "$TOOLKIT_ROOT/palomar_editorial_audit.sh" \
  --policy-dir vendor/palomar-policy \
  --policy-pin "$(tr -d '[:space:]' < vendor/PALOMAR_POLICY_PIN)" \
  --mechanical-report .cache/palomar-editorial/mechanical-report.json \
  --out .cache/palomar-editorial/review-draft.json

export PALOMAR_REPORT_EXIT_CODE=0
if [[ "$EDITORIAL_ONLY" -eq 1 ]]; then
  export PALOMAR_REPORT_MESSAGE="editorial preflight passed (--editorial-only; mechanical phases skipped)."
  echo ""
  echo "OK: editorial preflight passed (--editorial-only; mechanical phases skipped)."
else
  export PALOMAR_REPORT_MESSAGE="full Palomar preflight passed (mechanical + editorial neutral)."
  echo ""
  echo "OK: full Palomar preflight passed (mechanical + editorial neutral)."
fi
