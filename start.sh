#!/usr/bin/env bash
# Local Kokoro launcher. Kokoro 0.9.4 requires Python 3.10, 3.11 or 3.12.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export PATH="$HOME/.local/bin:$PATH"  # uv installed in the user binary directory

# OFFLINE=1 (or true/yes) uses only what is already downloaded.
case "${OFFLINE:-0}" in 1|true|yes|TRUE|YES) OFFLINE=1 ;; *) OFFLINE=0 ;; esac
export OFFLINE
export HF_HOME="$PWD/data/huggingface"
export HF_HUB_OFFLINE="$OFFLINE"
export HF_DATASETS_OFFLINE="$OFFLINE"
mkdir -p "$HF_HOME" outputs

# ---- first run: everything the studio needs is fetched now, not on first use ----
ask() {  # ask "question" -> yes unless the user answers n; no terminal -> no
  [ -t 0 ] || return 1
  local answer
  read -r -p "$1 [Y/n] " answer || return 1
  case "$answer" in [nN]*) return 1 ;; *) return 0 ;; esac
}

# System programs: espeak-ng (pronounces unusual words), ffmpeg (video, MP3, recordings), curl.
missing=()
for tool in espeak-ng ffmpeg curl; do
  command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done
if [ "${#missing[@]}" -gt 0 ]; then
  if [ "$OFFLINE" != '1' ] && command -v apt-get >/dev/null 2>&1 && \
     ask "Kokoro Studio needs ${missing[*]}. Install with sudo apt now?"; then
    sudo apt-get update && sudo apt-get install -y "${missing[@]}" || echo 'WARNING: installing system packages failed; continuing.' >&2
  else
    echo "NOTICE: missing ${missing[*]}. Install with: sudo apt install ${missing[*]}" >&2
  fi
fi

# uv fetches the right Python (3.12) and installs packages quickly; voice cloning needs it.
if ! command -v uv >/dev/null 2>&1 && [ "$OFFLINE" != '1' ] && command -v curl >/dev/null 2>&1; then
  if ask 'Install uv (the Python installer from astral.sh, into ~/.local/bin)?'; then
    curl -LsSf https://astral.sh/uv/install.sh | sh || echo 'WARNING: installing uv failed; continuing.' >&2
  fi
fi

compatible_python() {
  "$1" -c 'import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] < (3, 13) else 1)' >/dev/null 2>&1
}

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
    uv venv --python 3.12 .venv
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
bash ./repair-spacy.sh  # 'bash' so it runs even if the zip lost the executable bits

if [ "$OFFLINE" != '1' ]; then
  # The Kokoro model and all voices (about 350 MB once; a quick check after that).
  bash ./download-models.sh || echo 'WARNING: the model download did not finish; it is retried on the next start.' >&2
  # Cloned voices need ~9 GB more. That installs in the background so the studio can be
  # used right away; cloned voices start working when it finishes, without a restart.
  if [ "${SKIP_CLONING:-0}" != '1' ] && [ ! -f .venv-chatterbox/.ready ]; then
    if command -v uv >/dev/null 2>&1; then
      mkdir -p data/logs
      echo 'Setting up voice cloning in the background (large download). Progress: data/logs/setup-cloning.log'
      nohup bash ./setup-cloning.sh >> data/logs/setup-cloning.log 2>&1 &
    else
      echo 'NOTICE: voice cloning needs uv; install it and run ./setup-cloning.sh.' >&2
    fi
  fi
fi

echo "Python: $(.venv/bin/python --version 2>&1)"
PORT="${KOKORO_PORT:-8765}"
echo "Launching Kokoro Studio: http://127.0.0.1:${PORT}"
if command -v xdg-open >/dev/null 2>&1; then
  (sleep 3; xdg-open "http://127.0.0.1:${PORT}" >/dev/null 2>&1 || true) &
fi
exec .venv/bin/python app.py
