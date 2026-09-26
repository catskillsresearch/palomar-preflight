#!/usr/bin/env bash
# Editorial audit wrapper: pinned Codex CLI, model gpt-6-sol.
set -euo pipefail

TOOLKIT_ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=palomar-lib.sh
source "$TOOLKIT_ROOT/palomar-lib.sh"
palomar_cd_project
export PYTHONPATH="$TOOLKIT_ROOT${PYTHONPATH:+:$PYTHONPATH}"

venv_ready() {
  local py="$1"
  [[ -n "$py" && -x "$py" ]] && "$py" -c "import yaml" 2>/dev/null
}

install_python_deps() {
  local py="$1"
  echo "editorial audit: installing Python dependencies into ${py%/bin/python}" >&2
  "$py" -m pip install \
    --index-url https://pypi.org/simple \
    -r "$TOOLKIT_ROOT/requirements-editorial.txt" >&2
}

# Prefer an interpreter that can import PyYAML. Search order:
# PALOMAR_EDITORIAL_PYTHON, VIRTUAL_ENV, this project's .venv-editorial /
# .venv-ocr, then sibling */.venv-editorial and */.venv-ocr.
pick_python() {
  local py parent
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
    if [[ -x "$py" ]]; then
      install_python_deps "$py"
      if venv_ready "$py"; then
        echo "editorial audit: using $py" >&2
        echo "$py"
        shopt -u nullglob
        return 0
      fi
    fi
  done
  shopt -u nullglob

  if [[ -L "$PALOMAR_PROJECT_ROOT/.venv-editorial" && ! -x "$PALOMAR_PROJECT_ROOT/.venv-editorial/bin/python" ]]; then
    echo "FAIL: $PALOMAR_PROJECT_ROOT/.venv-editorial is a broken symlink." >&2
    echo "Set PALOMAR_EDITORIAL_PYTHON to a python that has PyYAML." >&2
    return 1
  fi

  echo "editorial audit: creating $PALOMAR_PROJECT_ROOT/.venv-editorial" >&2
  python3 -m venv "$PALOMAR_PROJECT_ROOT/.venv-editorial"
  install_python_deps "$PALOMAR_PROJECT_ROOT/.venv-editorial/bin/python"
  echo "$PALOMAR_PROJECT_ROOT/.venv-editorial/bin/python"
}

load_openai_api_key() {
  if [[ -n "${OPENAI_API_KEY:-}" ]]; then
    return 0
  fi
  local keyfile
  for keyfile in \
    "$PALOMAR_PROJECT_ROOT/../openai_key.txt" \
    "$PALOMAR_PROJECT_ROOT/openai_key.txt" \
    "$TOOLKIT_ROOT/../openai_key.txt"; do
    if [[ -f "$keyfile" ]]; then
      OPENAI_API_KEY="$(tr -d '[:space:]' < "$keyfile")"
      if [[ -n "$OPENAI_API_KEY" ]]; then
        export OPENAI_API_KEY
        return 0
      fi
    fi
  done
  echo "FAIL: set OPENAI_API_KEY or put the key in ../openai_key.txt" >&2
  return 1
}

load_openai_api_key

CODEX_PREFIX="$TOOLKIT_ROOT/codex-runtime"
CODEX_BIN="$CODEX_PREFIX/node_modules/.bin/codex"
if [[ ! -x "$CODEX_BIN" ]] || [[ "$("$CODEX_BIN" --version 2>/dev/null || true)" != "codex-cli 0.147.0" ]]; then
  command -v npm >/dev/null 2>&1 || {
    echo "FAIL: npm is required to install the pinned Codex CLI runtime." >&2
    exit 1
  }
  echo "editorial audit: installing pinned Codex CLI 0.147.0" >&2
  npm ci --prefix "$CODEX_PREFIX" --ignore-scripts --no-audit --no-fund >&2
fi
export PALOMAR_CODEX="$CODEX_BIN"

exec "$(pick_python)" "$TOOLKIT_ROOT/palomar_editorial_audit.py" "$@"
