#!/usr/bin/env bash
# Install the English spaCy pipeline explicitly, preventing Kokoro/Misaki from
# attempting an implicit model download when the user presses Generate.
# (The work is done in bootstrap/spacy_model.py, shared with Windows.)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export PATH="$HOME/.local/bin:$PATH"
if [ ! -x .venv/bin/python ]; then
  echo 'ERROR: .venv is missing. Run ./start.sh to create it.' >&2
  exit 1
fi
exec .venv/bin/python -m bootstrap.spacy_model
