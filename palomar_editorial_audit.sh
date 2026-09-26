#!/usr/bin/env bash
# Editorial audit wrapper: OpenAI client, model gpt-6-sol.
set -euo pipefail

TOOLKIT_ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=palomar-lib.sh
source "$TOOLKIT_ROOT/palomar-lib.sh"
palomar_cd_project
export PYTHONPATH="$TOOLKIT_ROOT${PYTHONPATH:+:$PYTHONPATH}"

venv_ready() {
  local py="$1"
  [[ -n "$py" && -x "$py" ]] && "$py" -c "import openai" 2>/dev/null
}

install_openai() {
  local py="$1"
  echo "editorial audit: installing openai into ${py%/bin/python}" >&2
  "$py" -m pip install \
    --index-url https://pypi.org/simple \
    -r "$TOOLKIT_ROOT/requirements-editorial.txt" >&2
}

# Prefer an interpreter that can import openai. Search order:
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
      install_openai "$py"
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
    echo "Set PALOMAR_EDITORIAL_PYTHON to a python that has the openai package." >&2
    return 1
  fi

  echo "editorial audit: creating $PALOMAR_PROJECT_ROOT/.venv-editorial" >&2
  python3 -m venv "$PALOMAR_PROJECT_ROOT/.venv-editorial"
  install_openai "$PALOMAR_PROJECT_ROOT/.venv-editorial/bin/python"
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
exec "$(pick_python)" "$TOOLKIT_ROOT/palomar_editorial_audit.py" "$@"
