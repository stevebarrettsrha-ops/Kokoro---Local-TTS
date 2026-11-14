#!/usr/bin/env bash
# Sets up cloned voices (Chatterbox by Resemble AI, MIT licence). ./start.sh runs
# this by itself in the background on the first run; you can also run it directly.
# Needs ~6 GB disk for the packages plus ~3 GB for model weights. Safe to rerun.
# (The work is done in bootstrap/cloning_setup.py, shared with Windows.)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export PATH="$HOME/.local/bin:$PATH"
if [ ! -x .venv/bin/python ]; then
  echo 'ERROR: .venv is missing. Run ./start.sh first.' >&2
  exit 1
fi
exec .venv/bin/python -m bootstrap.cloning_setup
