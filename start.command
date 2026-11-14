#!/usr/bin/env bash
# macOS: double-click this file in Finder to start Kokoro Studio in a Terminal window.
cd "$(dirname "$0")" || exit 1
bash ./start.sh
status=$?
if [ "$status" -ne 0 ]; then
  echo
  read -r -p 'Kokoro Studio stopped with an error (see above). Press Return to close.' _
fi
exit "$status"
