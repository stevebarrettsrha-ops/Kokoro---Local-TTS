#!/usr/bin/env bash
# Local Kokoro launcher. Kokoro 0.9.4 requires Python 3.10, 3.11 or 3.12.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export PATH="$HOME/.local/bin:$PATH"  # uv installed in the user binary directory

export HF_HOME="$PWD/data/huggingface"
export HF_HUB_OFFLINE="${OFFLINE:-0}"
export HF_DATASETS_OFFLINE="${OFFLINE:-0}"
mkdir -p "$HF_HOME" outputs

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo 'NOTICE: ffmpeg is not installed. The Video tab needs it to upload and export.' >&2
  echo '        Install it with: sudo apt install ffmpeg' >&2
fi
if ! command -v espeak-ng >/dev/null 2>&1; then
  echo 'NOTICE: espeak-ng not found. Install with: sudo apt install espeak-ng libsndfile1' >&2
fi

compatible_python() {
  "$1" -c 'import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] < (3, 13) else 1)' >/dev/null 2>&1
}

# A .venv whose Python link is broken (e.g. uv replaced Python 3.12.x with a
# newer patch release) still has all its packages. Re-point it at an installed
# Python of the same version instead of reinstalling everything.
if [ -d .venv ] && [ ! -x .venv/bin/python ]; then
  echo 'The existing .venv has lost its link to Python; trying to repair it...'
  venv_minor="$(ls -d .venv/lib/python3.* 2>/dev/null | head -1 | sed 's#.*/python##')"
  new_python=''
  if [ -n "$venv_minor" ] && command -v uv >/dev/null 2>&1; then
    new_python="$(uv python find "$venv_minor" 2>/dev/null || true)"
  fi
  if [ -z "$new_python" ] && [ -n "$venv_minor" ]; then
    new_python="$(command -v "python$venv_minor" 2>/dev/null || true)"
  fi
  if [ -n "$new_python" ] && [ -x "$new_python" ]; then
    for link in python python3 "python$venv_minor"; do
      ln -sfn "$new_python" ".venv/bin/$link"
    done
    if [ -f .venv/pyvenv.cfg ]; then
      sed -i "s#^home = .*#home = $(dirname "$new_python")#" .venv/pyvenv.cfg
    fi
    if .venv/bin/python -c 'import flask, kokoro, scipy, soundfile' >/dev/null 2>&1; then
      echo "Repaired .venv using $new_python (no reinstall needed)."
    else
      echo 'Python link repaired; any missing packages will be installed next.'
    fi
  fi
  if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import sys' >/dev/null 2>&1; then
    backup=".venv-broken-$(date +%Y%m%d-%H%M%S)"
    echo "Could not repair it. Keeping it as $backup and building a fresh one."
    mv .venv "$backup"
  fi
fi

# Reusing a venv made by Ubuntu's default Python 3.14 would fail even after
# installing Python 3.12. Preserve the old environment instead of deleting it.
if [ -x .venv/bin/python ] && ! compatible_python .venv/bin/python; then
  old_version="$(.venv/bin/python --version 2>&1 || true)"
  backup=".venv-incompatible-$(date +%Y%m%d-%H%M%S)"
  if [ -e "$backup" ]; then
    backup="$backup-$$"
  fi
  echo "Existing .venv uses $old_version; Kokoro requires Python 3.10–3.12."
  echo "Preserving that environment as $backup"
  mv .venv "$backup"
fi

if [ ! -x .venv/bin/python ]; then
  if [ "${OFFLINE:-0}" = '1' ]; then
    echo 'ERROR: A compatible Python environment is missing. Run ./start.sh while online first.' >&2
    exit 1
  fi
  echo 'Creating Kokoro Python environment (3.12 preferred)...'
  # 'uv' downloads its own Python 3.12 without replacing /usr/bin/python3.
  if command -v uv >/dev/null 2>&1; then
    uv venv --clear --python 3.12 .venv
  else
    python_candidate=''
    for candidate in python3.12 python3.11 python3.10; do
      if command -v "$candidate" >/dev/null 2>&1 && compatible_python "$candidate"; then
        python_candidate="$candidate"
        break
      fi
    done
    if [ -z "$python_candidate" ]; then
      cat >&2 <<'HELP'
ERROR: No supported Python interpreter found (Kokoro needs Python 3.10–3.12).
Ubuntu 26.04 defaults to Python 3.14, which Kokoro 0.9.4 does not support.

Install uv (official installer) to manage Python 3.12 without changing Ubuntu:
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  uv python install 3.12

Then run ./start.sh again.
HELP
      exit 1
    fi
    "$python_candidate" -m venv .venv || {
      echo "ERROR: Unable to create venv using $python_candidate; try installing uv (instructions above)." >&2
      exit 1
    }
  fi
fi

if ! compatible_python .venv/bin/python; then
  echo 'ERROR: The virtual environment must use Python 3.10, 3.11, or 3.12.' >&2
  exit 1
fi

if ! .venv/bin/python -c 'import flask, kokoro, scipy, soundfile' >/dev/null 2>&1; then
  if [ "${OFFLINE:-0}" = '1' ]; then
    echo 'ERROR: Python packages are missing; run ./start.sh online first.' >&2
    exit 1
  fi
  echo 'Installing Kokoro and studio packages into .venv (first install downloads dependencies)...'
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python .venv/bin/python -r requirements.txt
  else
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
  fi
fi

.venv/bin/python -c 'import flask, kokoro, scipy, soundfile' || {
  echo 'ERROR: Kokoro dependencies are still unavailable. See pip/uv errors above.' >&2
  exit 1
}

# Verify the English G2P model before the server starts, not during the
# first synthesis request (which otherwise fails with a browser NetworkError).
./repair-spacy.sh

echo "Python: $(.venv/bin/python --version 2>&1)"
PORT="${KOKORO_PORT:-8765}"
echo "Launching Kokoro Studio: http://127.0.0.1:${PORT}"
if command -v xdg-open >/dev/null 2>&1; then
  (sleep 3; xdg-open "http://127.0.0.1:${PORT}" >/dev/null 2>&1 || true) &
fi
exec .venv/bin/python app.py
