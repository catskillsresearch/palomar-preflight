#!/usr/bin/env bash
# Replay the solution through the kernels PalomarSubmission registers.
#
# The registry verifier runs the project toolchain's `lake comparator` and
# writes a protected configuration whose `external_kernels` are absolute
# paths to that toolchain's `nanoda_bin` and `con-ron --jobs=2`. It drops the
# submitter's `enable_nanoda`. This script does the same in a temp file and
# does not edit the submitted comparator.json.
set -euo pipefail

TOOLKIT_ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=palomar-lib.sh
source "$TOOLKIT_ROOT/palomar-lib.sh"
palomar_cd_project
repository_root="$PALOMAR_PROJECT_ROOT"

cache_root=${PALOMAR_COMPARATOR_CACHE:-"$repository_root/.cache/palomar-comparator"}
# PalomarSubmission scripts/verify_submission.py CON_RON_JOBS. Two workers
# bound the memory of a large export on the registry runner.
CON_RON_JOBS=2

SKIP_KERNELS=0
for arg in "$@"; do
  case "$arg" in
    --skip-kernels|--skip-nanoda) SKIP_KERNELS=1 ;;
    -h|--help)
      cat <<'EOF'
Usage: verify-comparator.sh [--skip-kernels]

Runs `lake comparator` with a protected configuration. Palomar registers the
Lean toolchain's nanoda_bin and con-ron (--jobs=2) as external kernels.
--skip-kernels (and the old --skip-nanoda alias) skips both of those kernels.
EOF
      exit 0
      ;;
    *)
      echo "error: unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

mkdir -p "$cache_root"
lean_prefix="$(lake env lean --print-prefix)"
nanoda_bin="$lean_prefix/bin/nanoda_bin"
con_ron_bin="$lean_prefix/bin/con-ron"

if [[ "$SKIP_KERNELS" -eq 0 ]]; then
  for exe in "$nanoda_bin" "$con_ron_bin"; do
    if [[ -L "$exe" || ! -f "$exe" || ! -x "$exe" ]]; then
      echo "error: Lean toolchain is missing a real executable: $exe" >&2
      echo "Palomar requires nanoda_bin and con-ron beside lean in the toolchain bin/." >&2
      exit 1
    fi
  done
fi

protected_config="$(mktemp)"
trap 'rm -f "$protected_config"' EXIT
python3 - "$repository_root/comparator.json" "$protected_config" \
  "$SKIP_KERNELS" "$nanoda_bin" "$con_ron_bin" "$CON_RON_JOBS" <<'PY'
import json
import sys

source, dest, skip, nanoda, con_ron, jobs = sys.argv[1:]
with open(source, encoding="utf-8") as handle:
    config = json.load(handle)
config.pop("enable_nanoda", None)
config.pop("external_kernels", None)
if skip != "1":
    config["external_kernels"] = {
        "nanoda": [nanoda],
        "con-ron": [con_ron, f"--jobs={jobs}"],
    }
with open(dest, "w", encoding="utf-8") as handle:
    json.dump(config, handle)
    handle.write("\n")
if skip == "1":
    print(
        "WARNING: skipping nanoda and con-ron. Palomar runs both.",
        file=sys.stderr,
    )
else:
    print(
        "Protected comparator config registers toolchain kernels "
        f"nanoda and con-ron --jobs={jobs}.",
        file=sys.stderr,
    )
PY

cd "$repository_root"
lake exe cache get || true

# Palomar jails this step with bubblewrap. Ubuntu's AppArmor restriction on
# unprivileged user namespaces makes that jail fail before any kernel starts.
# The kernel verdict does not depend on the jail, so replay unsandboxed and
# say so. A machine where bubblewrap works keeps the sandboxed command.
sandbox_args=()
if ! bwrap --unshare-user --ro-bind / / --dev /dev true >/dev/null 2>&1; then
  echo "WARNING: bubblewrap cannot create a user namespace here." >&2
  echo "Palomar runs this step inside bubblewrap. Replaying with --inadvisably-no-sandbox." >&2
  echo "The nanoda and con-ron verdicts are the same; the jail around them is not." >&2
  sandbox_args+=(--inadvisably-no-sandbox)
fi

set +e
log_file="$cache_root/last-run.log"
lake comparator --config "$protected_config" "${sandbox_args[@]}" 2>&1 | tee "$log_file"
status=${PIPESTATUS[0]}
set -e

if [[ "$status" -ne 0 ]]; then
  echo ""
  echo "Comparator rejected the project (exit $status)"
  python3 - "$log_file" <<'PY'
import sys

markers = ("uncaught exception", "error:", "error]", "failed", "rejected", "declined")
path = sys.argv[1]
try:
    text = open(path, encoding="utf-8", errors="replace").read()
except OSError:
    raise SystemExit(0)
lines = [line.rstrip() for line in text.splitlines() if line.strip()]
marked = [
    line
    for line in lines
    if any(marker in line.lower() for marker in markers)
]
chosen = (marked or lines)[-12:]
print("\n".join(line[:400] for line in chosen))
PY
  echo ""
  echo "Next: Correct the Lean or Comparator failure quoted above before submitting."
  exit "$status"
fi

if [[ "$SKIP_KERNELS" -eq 1 ]]; then
  echo "OK: Comparator accepted Challenge vs Solution (external kernels skipped)."
else
  echo "OK: Comparator accepted Challenge vs Solution, including nanoda and con-ron."
fi
