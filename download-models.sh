#!/usr/bin/env bash
# Fetches the Kokoro model and every English voice so the studio works offline.
# ./start.sh does this by itself; it returns at once when everything is cached.
#   --check   only report whether everything is downloaded (exit 1 if not); used offline
# (The work is done in bootstrap/models.py, shared with Windows.)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export PATH="$HOME/.local/bin:$PATH"
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import importlib.util as u, sys; sys.exit(0 if u.find_spec("kokoro") and u.find_spec("huggingface_hub") else 1)' >/dev/null 2>&1; then
  echo 'First run ./start.sh so it can finish installing the Python dependencies, then run this again.' >&2
  exit 1
fi
.venv/bin/python -m bootstrap.models "$@"
if [ "${1:-}" != '--check' ]; then
  .venv/bin/python -m bootstrap.spacy_model  # G2P uses the spaCy English model; make it available offline too
fi
