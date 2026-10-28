#!/usr/bin/env bash
# Bring an existing install into this folder so nothing has to download again.
#   ./move-old-setup.sh /path/to/old/kokoro/folder
# Moves (does not copy) the Python environments, downloaded models, takes,
# cloned voices and video creations. Folders that exist on both sides are merged;
# a file that already exists here is never overwritten.
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
left=()
# Move what is missing here; when a folder exists on both sides, merge it item by item
# (e.g. ./start.sh already created an empty data/ or outputs/ here).
merge() {
  local src="$1" dst="$2" entry name
  for entry in "$src"/* "$src"/.[!.]* "$src"/..?*; do
    [ -e "$entry" ] || [ -L "$entry" ] || continue
    name="$(basename "$entry")"
    if [ ! -e "$dst/$name" ] && [ ! -L "$dst/$name" ]; then
      mv "$entry" "$dst/$name"
      moved=$((moved + 1))
    elif [ -d "$entry" ] && [ ! -L "$entry" ] && [ -d "$dst/$name" ]; then
      merge "$entry" "$dst/$name"
    else
      left+=("$entry")
    fi
  done
  rmdir "$src" 2>/dev/null || true  # only removes it if everything was moved
}
for item in .venv .venv-chatterbox data outputs voices creations; do
  [ -e "$old/$item" ] || continue
  if [ ! -e "$item" ]; then
    mv "$old/$item" "./$item"
    echo "moved  $item"
    moved=$((moved + 1))
  elif [ "$item" = .venv ] || [ "$item" = .venv-chatterbox ]; then
    echo "kept   $item (this folder already has one)"
  else
    merge "$old/$item" "./$item"
    echo "merged $item"
  fi
done
chmod +x ./*.sh 2>/dev/null || true
if [ "${#left[@]}" -gt 0 ]; then
  echo
  echo "These were NOT moved because a file with the same name is already here:"
  printf '  %s\n' "${left[@]}"
  echo "Your old copies are still in $old - do not delete that folder until you have checked them."
fi
echo "Done ($moved moved). Start the studio with: ./start.sh"
