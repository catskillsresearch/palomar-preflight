# Palomar local preflight

Local/CI replica of Palomar registry mechanical verification, plus the
editorial LLM audit used before submission.

**Canonical layout:** keep this repository as a **sibling** of each Lean project
(`../palomar-preflight`) and invoke it through a thin project wrapper with
CLI flags. CI checks out this repo into `palomar-preflight/` inside each project.

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
    "$root/palomar-preflight"; do
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
| `--editorial-only` | Skip mechanical phases; run policy sync + LLM audit |
| `--no-policy-sync` | Use committed `vendor/palomar-policy` only |
| `--report-out PATH` | Structured run report (default: `.cache/palomar-editorial/preflight-run.json`) |

Environment overrides (optional):

| Variable | Meaning |
| --- | --- |
| `PALOMAR_PREFLIGHT_ROOT` | Explicit toolkit directory |
| `PALOMAR_SORRY_PATHS` | Default sorry scan paths if `--sorry-paths` omitted |
| `PALOMAR_CLOSURE_PREFIXES` | Extra Lean namespace prefixes for closure walk |
| `PALOMAR_EXTRA_PRINT_NAMES` | Extra constants to `#print` during closure walk |
| `PALOMAR_CHECK_DECL_KINDS` | Set to `0` if `theorem_names` includes defs (Mizar-style) |
| `PALOMAR_CHALLENGE_FORBIDDEN_PREFIXES` | Extra Challenge import bans |
| `PALOMAR_EDITORIAL_PYTHON` | Python with `cursor-sdk` for the LLM audit (skips venv create) |

The editorial audit looks for `cursor_sdk` in this order: `PALOMAR_EDITORIAL_PYTHON`,
`$VIRTUAL_ENV/bin/python`, `<project>/.venv-editorial`, `<project>/.venv-ocr`,
then sibling `*/.venv-editorial` and `*/.venv-ocr`. A project may symlink
`.venv-editorial` to a shared install (for example `../scott1964/.venv-editorial`).
A new venv is created only when none of those can `import cursor_sdk`.

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
bash scripts/palomar_preflight.sh --editorial-only    # retry LLM audit only
```

Mechanical includes Palomar-pinned Comparator (`verify-comparator.sh`):
declaration-closure Const matching, axiom checks, and Lean kernel replay.

## Run report (phase overview)

Every preflight run writes **`.cache/palomar-editorial/preflight-run.json`**
(schema `palomar-preflight-run-v1`). It records:

- Per-phase status (`pass` / `fail` / `skip` / `not_run`), summary line, exit code,
  duration, and tail of captured output
- Run options (`--mechanical-only`, `--editorial-only`, sorry paths, closure prefixes, …)
- Repository and toolkit commits, overall exit code and failed phase
- Paths to related artifacts (`comparator_last_run_log`, `review_draft`, …)
- Editorial synthesis outcome when the LLM audit runs

Render a markdown phase table from the last run:

```bash
python3 ../palomar-preflight/palomar_run_report.py print-table \
  --report .cache/palomar-editorial/preflight-run.json
```

Use `--report-out PATH` to override the default location.

## Sibling repo matrix

From the toolkit directory, summarize Palomar preflight status for every
**sibling repo** that has `scripts/palomar_preflight.sh` and **no Palomar
registry badge** in its README (registered entries are excluded):

```bash
./report.sh              # Canvas panel beside chat (in Cursor terminal)
./report.sh --print      # markdown to stdout
./report.sh --copy       # clipboard for chat paste
```

**Recommended in Cursor:** run `./report.sh` from the integrated terminal.
It writes `palomar-sibling-matrix.canvas.tsx` under your workspace
`.cursor/projects/.../canvases/` and opens it — click the **Canvas** tab
beside chat for the formatted table panel.

Reads each repo's `.cache/palomar-editorial/preflight-run.json`. Use
`--include-badged` or `--format json` as needed. Override canvas directory
with `PALOMAR_CANVAS_DIR` if auto-detection fails.

## Refresh policy pin

Policy still lives in each project's `vendor/palomar-policy/`. Preflight syncs
that tree on full runs via `palomar_policy_sync.py` in this toolkit.

## License

Apache-2.0.
