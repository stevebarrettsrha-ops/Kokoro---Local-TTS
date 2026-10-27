#!/usr/bin/env bash
# Bring an existing install into this folder so nothing has to download again.
#   ./move-old-setup.sh /path/to/old/kokoro/folder
# Moves (does not copy) the Python environments, downloaded models, takes,
# cloned voices and video creations. Anything that already exists here is left alone.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
old="${1:-}"
if [ -z "$old" ] || [ ! -d "$old" ]; then
  echo 'Usage: ./move-old-setup.sh /path/to/your/old/kokoro/folder' >&2
  exit 1
fi
old="$(cd "$old" && pwd)"
if [ "$old" = "$PWD" ]; then
  echo 'That is this folder already; nothing to move.'
  exit 0
fi
moved=0
for item in .venv .venv-chatterbox data outputs voices creations; do
  if [ -e "$old/$item" ]; then
    if [ -e "$item" ]; then
      echo "skip  $item (already here)"
    else
      mv "$old/$item" "./$item"
      echo "moved $item"
      moved=$((moved + 1))
    fi
  fi
done
chmod +x ./*.sh 2>/dev/null || true
echo "Done ($moved moved). Start the studio with: ./start.sh"
