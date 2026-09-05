# Palomar local preflight

Local/CI replica of Palomar registry mechanical verification, plus the
editorial LLM audit used before submission.

**Canonical layout:** keep this repository as a **sibling** of each Lean project
(`../palomar-preflight`) and invoke it through a thin project wrapper with
CLI flags. Legacy `vendor/palomar-preflight/` copies still work as a fallback
but should not be edited in place.

Palomar rejects `.gitmodules`. Registry verification itself runs Comparator from
PalomarSubmission — not this repo — but `verify-comparator.sh` here uses the
same pins.

## Standard project wrapper

Each Lean project keeps `scripts/palomar_preflight.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
find_toolkit() {
  local root="$1" d
  for d in "${PALOMAR_PREFLIGHT_ROOT:-}" \
    "$(dirname "$root")/palomar-preflight" \
    "$root/palomar-preflight" \
    "$root/vendor/palomar-preflight"; do
    [[ -n "$d" && -f "$d/palomar_preflight.sh" ]] && { cd "$d" && pwd; return 0; }
  done
  echo "error: palomar-preflight not found" >&2; return 1
}
TOOLKIT="$(find_toolkit "$ROOT")"
exec bash "$TOOLKIT/palomar_preflight.sh" \
  --project-root "$ROOT" \
  --sorry-paths "YourLib Solution.lean" \
  "$@"
```

See `examples/project-wrapper.sh` and per-project copies under `examples/`.

### Registered projects

| Project | `--sorry-paths` | extra flags |
| --- | --- | --- |
| avg_case_mls | `AvgCaseMls/Palomar.lean Solution.lean` | — |
| qlambda | `QLambda Solution.lean` | `--forbidden-prefix QLambda` |
| scott1964 | `Scott1964 Solution.lean` | — |
| hybrid_logic_lean_revisited | `Hybrid Solution.lean` | — |
| MizarCCL | `MizarCCL/HIDDEN.lean MizarCCL/TARSKI.lean Solution.lean` | `--closure-prefix` ×3; set `PALOMAR_CHECK_DECL_KINDS=0` |

## Direct invocation

```bash
../palomar-preflight/palomar_preflight.sh \
  --project-root /path/to/lean-project \
  --sorry-paths "Lib Solution.lean" \
  --mechanical-only
```

Options:

| Flag | Meaning |
| --- | --- |
| `--project-root DIR` | Lean project root |
| `--sorry-paths PATHS` | Space-separated sorry/admit scan paths |
| `--forbidden-prefix P` | Extra forbidden Challenge import prefix (repeatable) |
| `--closure-prefix P` | Extra namespace prefix for declaration-closure walk |
| `--extra-print-name N` | Extra `#print` name for closure walk |
| `--mechanical-only` | Skip policy sync + LLM editorial audit |
| `--no-policy-sync` | Use committed `vendor/palomar-policy` only |

Environment overrides (optional):

| Variable | Meaning |
| --- | --- |
| `PALOMAR_PREFLIGHT_ROOT` | Explicit toolkit directory |
| `PALOMAR_SORRY_PATHS` | Default sorry scan paths if `--sorry-paths` omitted |
| `PALOMAR_CLOSURE_PREFIXES` | Extra Lean namespace prefixes for closure walk |
| `PALOMAR_EXTRA_PRINT_NAMES` | Extra constants to `#print` during closure walk |
| `PALOMAR_CHECK_DECL_KINDS` | Set to `0` if `theorem_names` includes defs (Mizar-style) |
| `PALOMAR_CHALLENGE_FORBIDDEN_PREFIXES` | Extra Challenge import bans |

Optional `scripts/palomar_preflight_local.sh` in the Lean project runs extra
mechanical checks (after submodule ban, before `lake build`).

## CI

Check out this repository beside or inside the Lean project, then run the
project wrapper:

```yaml
- uses: actions/checkout@v4
- uses: actions/checkout@v4
  with:
    repository: catskillsresearch/palomar-preflight
    ref: 8141cdcf7c13ae4aef6abe16a91bac3cfd46c5ac
    path: palomar-preflight
- run: bash scripts/palomar_preflight.sh --mechanical-only
```

Pin `ref` to the same commit recorded in `vendor/PALOMAR_PREFLIGHT_PIN` when
bumping the toolkit.

## Commands

```bash
bash scripts/palomar_preflight.sh --mechanical-only   # CI
bash scripts/palomar_preflight.sh                     # before Palomar submit
```

Mechanical includes Palomar-pinned Comparator (`verify-comparator.sh`):
declaration-closure Const matching, axiom checks, and Lean kernel replay.

## Refresh legacy vendor copies

```bash
python3 /path/to/palomar-preflight/palomar_preflight_sync.py --from-dir /path/to/palomar-preflight
# or, after this repo is on GitHub:
python3 vendor/palomar-preflight/palomar_preflight_sync.py
```

Pin file: `vendor/PALOMAR_PREFLIGHT_PIN`.

## License

Apache-2.0.
