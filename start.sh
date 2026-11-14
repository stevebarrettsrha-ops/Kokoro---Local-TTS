#!/usr/bin/env bash
# Kokoro Studio launcher for Linux and macOS (Windows: start.bat).
# Kokoro 0.9.4 requires Python 3.10, 3.11 or 3.12. This script makes the Python
# environment (.venv); the rest of the setup is bootstrap/launch.py, shared with Windows.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export PATH="$HOME/.local/bin:$PATH"  # uv installed in the user binary directory
case "$(uname -s)" in Darwin) MAC=1 ;; *) MAC=0 ;; esac
if [ "$MAC" = 1 ]; then  # Homebrew's folder is not on PATH when started from Finder (start.command)
  for brew_dir in /opt/homebrew/bin /usr/local/bin; do
    [ -d "$brew_dir" ] && PATH="$brew_dir:$PATH"
  done
fi

truthy() { case "${1:-}" in 1|true|yes|TRUE|YES|True|Yes) return 0 ;; *) return 1 ;; esac; }
# OFFLINE=1 (or true/yes) uses only what is already downloaded.
if truthy "${OFFLINE:-0}"; then OFFLINE=1; else OFFLINE=0; fi
export OFFLINE
export HF_HOME="$PWD/data/huggingface"
export HF_HUB_OFFLINE="$OFFLINE"
export HF_DATASETS_OFFLINE="$OFFLINE"
mkdir -p "$HF_HOME" outputs data/logs

# ---- first run: everything the studio needs is fetched now, not on first use ----
ask() {  # ask "question" -> yes unless the user answers n; no terminal (desktop launcher) -> no
  [ -t 0 ] || return 1
  local answer
  read -r -p "$1 [Y/n] " answer || { echo; return 1; }  # Ctrl+D
  case "$answer" in [nN]*) return 1 ;; *) return 0 ;; esac
}

# System programs. ffmpeg: video, MP3, recordings. (Kokoro brings its own espeak-ng.)
# curl or wget: only to install uv. A "no" is remembered, so it isn't asked on every start.
missing=()
command -v ffmpeg >/dev/null 2>&1 || missing+=(ffmpeg)
if ! command -v uv >/dev/null 2>&1 && ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
  missing+=(curl)
fi
declined=data/.declined-system-packages
if [ "$MAC" = 1 ]; then installer='brew'; elif command -v apt-get >/dev/null 2>&1; then installer='apt'; else installer=''; fi
if [ "${#missing[@]}" -gt 0 ]; then
  if [ "$OFFLINE" != '1' ] && [ -n "$installer" ] && [ "$(cat "$declined" 2>/dev/null)" != "${missing[*]}" ]; then
    if [ "$installer" = brew ] && ! command -v brew >/dev/null 2>&1; then
      echo "NOTICE: Kokoro Studio needs ${missing[*]}. Install Homebrew (https://brew.sh), then run: brew install ${missing[*]}" >&2
    elif [ "$installer" = brew ] && ask "Kokoro Studio needs ${missing[*]}. Install with Homebrew now?"; then
      brew install "${missing[@]}" ||
        echo "WARNING: installing ${missing[*]} failed; continuing. Install it yourself with: brew install ${missing[*]}" >&2
    elif [ "$installer" = apt ] && ask "Kokoro Studio needs ${missing[*]}. Install with sudo apt now?"; then
      # One broken software source (common with PPAs) must not stop the install.
      { sudo apt-get update || true; } && sudo apt-get install -y "${missing[@]}" ||
        echo "WARNING: installing ${missing[*]} failed; continuing. Install it yourself with: sudo apt install ${missing[*]}" >&2
    elif [ -t 0 ]; then
      echo "${missing[*]}" > "$declined"
    fi
  fi
  for tool in "${missing[@]}"; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      case "$installer" in
        brew) echo "NOTICE: missing ${missing[*]}. Install with: brew install ${missing[*]}" >&2 ;;
        apt) echo "NOTICE: missing ${missing[*]}. Install with: sudo apt install ${missing[*]}" >&2 ;;
        *) echo "NOTICE: missing ${missing[*]}. Install it with your system's package manager." >&2 ;;
      esac
      break
    fi
  done
fi

# uv fetches the right Python (3.12) and installs packages quickly; voice cloning needs it.
# (Its installer also adds ~/.local/bin to your shell's PATH for later terminals.)
if ! command -v uv >/dev/null 2>&1 && [ "$OFFLINE" != '1' ]; then
  if command -v curl >/dev/null 2>&1; then fetch='curl -LsSf'; elif command -v wget >/dev/null 2>&1; then fetch='wget -qO-'; else fetch=''; fi
  if [ -n "$fetch" ] && ask 'Install uv (the Python installer from astral.sh, into ~/.local/bin)?'; then
    $fetch https://astral.sh/uv/install.sh | sh || echo 'WARNING: installing uv failed; continuing.' >&2
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
(Ubuntu 26.04 and recent Homebrew default to Python 3.14, which Kokoro 0.9.4 does not support.)

Install uv (official installer) to manage Python 3.12 without changing the system Python:
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  uv python install 3.12

Then run ./start.sh again.
HELP
      exit 1
    fi
    "$python_candidate" -m venv .venv || {
      rm -rf .venv  # a half-made environment would make every later start fail
      echo "ERROR: Could not create a Python environment with $python_candidate." >&2
      echo "Install the venv module (sudo apt install ${python_candidate}-venv), or run ./start.sh again and answer yes to installing uv." >&2
      exit 1
    }
  fi
fi

if ! compatible_python .venv/bin/python; then
  echo 'ERROR: The virtual environment must use Python 3.10, 3.11, or 3.12.' >&2
  exit 1
fi

# Packages: (re)installed when missing or when requirements.txt changed (e.g. after an update).
# find_spec checks without importing, so a normal start doesn't load PyTorch just to look.
have_packages() {
  .venv/bin/python -c 'import importlib.util as u, sys; sys.exit(0 if all(u.find_spec(m) for m in ("flask", "kokoro", "misaki", "scipy", "soundfile", "huggingface_hub")) else 1)' >/dev/null 2>&1
}
req_hash="$(cksum < requirements.txt)"
if ! have_packages || { [ "$OFFLINE" != '1' ] && [ "$(cat .venv/.requirements 2>/dev/null)" != "$req_hash" ]; }; then
  if [ "$OFFLINE" = '1' ]; then
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
  .venv/bin/python -c 'import flask, kokoro, scipy, soundfile' || {
    echo 'ERROR: Kokoro dependencies are still unavailable. See pip/uv errors above.' >&2
    exit 1
  }
  echo "$req_hash" > .venv/.requirements
fi

# The rest (spaCy model, Kokoro model and voices, the background voice-cloning install,
# opening the browser, the studio itself) is shared with Windows.
exec .venv/bin/python -m bootstrap.launch
