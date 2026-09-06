#!/usr/bin/env bash
# Editorial audit wrapper: cursor-sdk venv in the Lean project.
set -euo pipefail

TOOLKIT_ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=palomar-lib.sh
source "$TOOLKIT_ROOT/palomar-lib.sh"
palomar_cd_project
export PYTHONPATH="$TOOLKIT_ROOT${PYTHONPATH:+:$PYTHONPATH}"

venv_ready() {
  local py="$1"
  [[ -n "$py" && -x "$py" ]] && "$py" -c "import cursor_sdk" 2>/dev/null
}

# Prefer an existing cursor-sdk interpreter. Search order:
# PALOMAR_EDITORIAL_PYTHON, VIRTUAL_ENV, this project's .venv-editorial /
# .venv-ocr, then sibling */.venv-editorial and */.venv-ocr (shared installs
# and symlinks such as ../scott1964/.venv-editorial).
pick_python() {
  local py parent d
  parent="$(dirname "$PALOMAR_PROJECT_ROOT")"
  for py in \
    "${PALOMAR_EDITORIAL_PYTHON:-}" \
    "${VIRTUAL_ENV:+$VIRTUAL_ENV/bin/python}"; do
    if venv_ready "$py"; then
      echo "editorial audit: using $py" >&2
      echo "$py"
      return 0
    fi
  done
  shopt -s nullglob
  for py in \
    "$PALOMAR_PROJECT_ROOT/.venv-editorial/bin/python" \
    "$PALOMAR_PROJECT_ROOT/.venv-ocr/bin/python" \
    "$parent"/*/.venv-editorial/bin/python \
    "$parent"/*/.venv-ocr/bin/python; do
    if venv_ready "$py"; then
      echo "editorial audit: using $py" >&2
      echo "$py"
      shopt -u nullglob
      return 0
    fi
  done
  shopt -u nullglob

  if [[ -L "$PALOMAR_PROJECT_ROOT/.venv-editorial" ]]; then
    echo "FAIL: $PALOMAR_PROJECT_ROOT/.venv-editorial is a symlink, but" >&2
    echo "cursor_sdk is not importable there. Fix the link or set" >&2
    echo "PALOMAR_EDITORIAL_PYTHON to a python that has cursor-sdk." >&2
    return 1
  fi

  echo "editorial audit: creating $PALOMAR_PROJECT_ROOT/.venv-editorial" >&2
  python3 -m venv "$PALOMAR_PROJECT_ROOT/.venv-editorial"
  # `pick_python` is consumed by command substitution; keep pip chatter off stdout.
  # Use PyPI directly: global pip config may add broken extra indexes.
  "$PALOMAR_PROJECT_ROOT/.venv-editorial/bin/pip" install \
    --index-url https://pypi.org/simple \
    -r "$TOOLKIT_ROOT/requirements-editorial.txt" >&2
  echo "$PALOMAR_PROJECT_ROOT/.venv-editorial/bin/python"
}

load_cursor_api_key() {
  if [[ -n "${CURSOR_API_KEY:-}" ]]; then
    return 0
  fi
  local tokens
  for tokens in "$PALOMAR_PROJECT_ROOT/../tokens_ssto.yaml" "$PALOMAR_PROJECT_ROOT/tokens_ssto.yaml"; do
    if [[ -f "$tokens" ]]; then
      CURSOR_API_KEY="$(grep -E '^CURSOR_API_KEY:' "$tokens" | head -1 | sed -E 's/^CURSOR_API_KEY:[[:space:]]*//')"
      if [[ -n "$CURSOR_API_KEY" ]]; then
        export CURSOR_API_KEY
        return 0
      fi
    fi
  done
  echo "FAIL: set CURSOR_API_KEY or add it to ../tokens_ssto.yaml" >&2
  return 1
}

load_cursor_api_key
exec "$(pick_python)" "$TOOLKIT_ROOT/palomar_editorial_audit.py" "$@"
