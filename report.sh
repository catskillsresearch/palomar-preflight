#!/usr/bin/env bash
# Palomar sibling status matrix for Cursor IDE.
#
# Default in an interactive terminal: open HTML matrix in browser.
# Use --print for markdown on stdout (pipes, CI, redirect).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PARENT="$(dirname "$ROOT")"
REPORT_DIR="$ROOT/reports"
DEFAULT_MD="$REPORT_DIR/sibling-matrix.md"
DEFAULT_HTML="$REPORT_DIR/sibling-matrix.html"
PLAN_NAME="palomar-sibling-status.plan.md"

COPY=0
OPEN=0
PANEL=0
CANVAS=0
PLAN_MODE=0
OPEN_HTML=0
PRINT=0
OUT=""
PYTHON_ARGS=()

usage() {
  cat <<EOF
Usage: report.sh [OPTIONS] [PALOMAR_SIBLING_MATRIX_ARGS...]

Palomar preflight status for sibling repos (no Palomar badge in README).

Options:
  -p, --panel      Open status report in Cursor panel (Canvas source file)
      --canvas     Force Canvas (.canvas.tsx)
      --plan       Force plan report (.plan.md)
      --html       Open standalone HTML table in browser (Chrome/xdg-open)
      --print      Print markdown to stdout (for redirect/CI)
  -o, --open       Write markdown to ${DEFAULT_MD} and open in plain editor
  -c, --copy       Copy markdown to the system clipboard
      --out PATH   Also write markdown to PATH
  -h, --help       Show this help

In Cursor, CLI cannot reliably force the rendered Canvas view; it opens the
.canvas.tsx source file. So default behavior opens a wide HTML table in browser.
Use --panel/--canvas only when you want the source file in Cursor.

Extra args go to palomar_sibling_matrix.py (e.g. --format json).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -c|--copy) COPY=1; shift ;;
    -o|--open) OPEN=1; shift ;;
    -p|--panel) PANEL=1; shift ;;
    --canvas) CANVAS=1; PLAN_MODE=0; PANEL=1; shift ;;
    --plan) PLAN_MODE=1; CANVAS=0; PANEL=1; shift ;;
    --html) OPEN_HTML=1; shift ;;
    --print) PRINT=1; shift ;;
    --out)
      [[ $# -ge 2 ]] || { echo "error: missing value for --out" >&2; exit 2; }
      OUT="$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) PYTHON_ARGS+=("$1"); shift ;;
  esac
done

if [[ "$PANEL" -eq 0 && "$PRINT" -eq 0 && "$OPEN" -eq 0 && "$COPY" -eq 0 && -z "$OUT" && "$OPEN_HTML" -eq 0 ]]; then
  OPEN_HTML=1
fi

resolve_plans_dir() {
  python3 - <<'PY'
import os
import sys
sys.path.insert(0, os.environ["PALOMAR_TOOLKIT"])
from palomar_sibling_matrix import resolve_cursor_plans_dir
plans = resolve_cursor_plans_dir()
if plans is None:
    raise SystemExit(1)
print(plans)
PY
}

resolve_canvas_dir() {
  python3 - <<'PY'
import os
import sys
sys.path.insert(0, os.environ["PALOMAR_TOOLKIT"])
from palomar_sibling_matrix import resolve_cursor_canvas_dir
canvas = resolve_cursor_canvas_dir()
if canvas is None:
    raise SystemExit(1)
print(canvas)
PY
}

open_in_cursor() {
  local path="$1"
  if command -v cursor >/dev/null 2>&1; then
    cursor -r "$path"
  elif command -v code >/dev/null 2>&1; then
    code -r "$path"
  fi
}

open_plan_panel() {
  export PALOMAR_TOOLKIT="$ROOT"
  local plans_dir plan_path
  if ! plans_dir="$(resolve_plans_dir)"; then
    echo "error: could not locate .cursor/plans for this workspace." >&2
    echo "Set PALOMAR_PLANS_DIR or run from a Cursor workspace root." >&2
    return 1
  fi
  plan_path="$plans_dir/$PLAN_NAME"
  python3 "$ROOT/palomar_sibling_matrix.py" \
    --write-plan "$plan_path" \
    --parent-dir "$PARENT" \
    "${PYTHON_ARGS[@]}"
  open_in_cursor "$plan_path"
  echo ""
  echo "Palomar plan report: $plan_path"
  echo "(Cursor Plan editor — formatted markdown, same renderer as agent plans.)"
}

open_canvas_panel() {
  export PALOMAR_TOOLKIT="$ROOT"
  local canvas_dir canvas_path
  if ! canvas_dir="$(resolve_canvas_dir)"; then
    echo "error: could not locate Cursor canvas directory." >&2
    return 1
  fi
  canvas_path="$canvas_dir/palomar-sibling-matrix.canvas.tsx"
  python3 "$ROOT/palomar_sibling_matrix.py" \
    --write-canvas "$canvas_path" \
    --parent-dir "$PARENT" \
    "${PYTHON_ARGS[@]}"
  open_in_cursor "$canvas_path"
  echo ""
  echo "Palomar canvas: $canvas_path (open the Canvas tab beside chat)"
}

open_panel() {
  if [[ "$PLAN_MODE" -eq 1 ]]; then
    open_plan_panel
  else
    open_canvas_panel
  fi
}

open_html_report() {
  local html_path="${1:-$DEFAULT_HTML}"
  python3 "$ROOT/palomar_sibling_matrix.py" \
    --write-html "$html_path" \
    --parent-dir "$PARENT" \
    "${PYTHON_ARGS[@]}"

  if command -v google-chrome >/dev/null 2>&1; then
    google-chrome "$html_path" >/dev/null 2>&1 &
  elif command -v chromium-browser >/dev/null 2>&1; then
    chromium-browser "$html_path" >/dev/null 2>&1 &
  elif command -v chromium >/dev/null 2>&1; then
    chromium "$html_path" >/dev/null 2>&1 &
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$html_path" >/dev/null 2>&1 &
  else
    echo "error: no browser opener found (google-chrome/chromium/xdg-open)." >&2
    return 1
  fi
  echo ""
  echo "Palomar HTML report: $html_path"
}

markdown_content() {
  python3 "$ROOT/palomar_sibling_matrix.py" --parent-dir "$PARENT" "${PYTHON_ARGS[@]}"
}

write_dest() {
  local dest="$1"
  local content
  content="$(markdown_content)"
  mkdir -p "$(dirname "$dest")"
  printf '%s' "$content" >"$dest"
  echo "Wrote $dest"
}

open_dest() {
  open_in_cursor "$1"
}

copy_to_clipboard() {
  local content
  content="$(markdown_content)"
  if command -v wl-copy >/dev/null 2>&1; then
    printf '%s' "$content" | wl-copy
  elif command -v xclip >/dev/null 2>&1; then
    printf '%s' "$content" | xclip -selection clipboard
  elif command -v pbcopy >/dev/null 2>&1; then
    printf '%s' "$content" | pbcopy
  else
    echo "error: no clipboard tool (try: sudo apt install wl-clipboard or xclip)" >&2
    return 1
  fi
  echo "Copied markdown to clipboard (paste with Ctrl+V)"
}

if [[ "$PANEL" -eq 1 ]]; then
  open_panel || {
    echo "warn: Cursor panel failed; opening HTML fallback..." >&2
    open_html_report || {
      echo "error: panel and HTML fallback both failed; use --print for markdown on stdout" >&2
      exit 1
    }
  }
fi

if [[ "$OPEN_HTML" -eq 1 ]]; then
  open_html_report
fi

if [[ -n "$OUT" ]]; then
  write_dest "$OUT"
fi

if [[ "$OPEN" -eq 1 ]]; then
  dest="${OUT:-$DEFAULT_MD}"
  if [[ -z "$OUT" ]]; then
    write_dest "$dest"
  fi
  open_dest "$dest"
fi

if [[ "$COPY" -eq 1 ]]; then
  copy_to_clipboard
fi

if [[ "$PRINT" -eq 1 ]]; then
  markdown_content
elif [[ "$PANEL" -eq 0 && "$OPEN" -eq 0 && "$COPY" -eq 0 && -z "$OUT" && "$OPEN_HTML" -eq 0 ]]; then
  markdown_content
fi
