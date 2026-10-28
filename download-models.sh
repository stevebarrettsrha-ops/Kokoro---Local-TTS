#!/usr/bin/env bash
# Run once while online to fetch model weights and all English voices for offline use.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export HF_HOME="$PWD/data/huggingface"
export HF_HUB_OFFLINE=0
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import kokoro, huggingface_hub' >/dev/null 2>&1; then
  echo 'First run ./start.sh so it can finish installing the Python dependencies, then run this again.' >&2
  exit 1
fi
.venv/bin/python - <<'PY'
from huggingface_hub import snapshot_download
print('Downloading Kokoro base model + all American/British English voices to local cache...')
snapshot_download(repo_id='hexgrad/Kokoro-82M', allow_patterns=['config.json', 'kokoro-v1_0.pth', 'voices/af_*.pt', 'voices/am_*.pt', 'voices/bf_*.pt', 'voices/bm_*.pt'])
print('Model assets downloaded. You can now start the app with OFFLINE=1 ./start.sh')
PY
# G2P uses spaCy English model if installed. Make it available for air-gapped use.
bash ./repair-spacy.sh
