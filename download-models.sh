#!/usr/bin/env bash
# Fetches the Kokoro model and every English voice so the studio works offline.
# ./start.sh runs this by itself; it returns at once when everything is cached.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export HF_HOME="$PWD/data/huggingface"
export HF_HUB_OFFLINE=0
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import kokoro, huggingface_hub' >/dev/null 2>&1; then
  echo 'First run ./start.sh so it can finish installing the Python dependencies, then run this again.' >&2
  exit 1
fi
.venv/bin/python - <<'PY'
import sys
sys.path.insert(0, '.')
from huggingface_hub import hf_hub_download
from studio.engine import REPO_ID, VOICES

files = ['config.json', 'kokoro-v1_0.pth'] + [f'voices/{v}.pt' for v in VOICES]

def cached(f):
    try:
        hf_hub_download(repo_id=REPO_ID, filename=f, local_files_only=True)
        return True
    except Exception:  # noqa: BLE001 - not downloaded yet
        return False

missing = [f for f in files if not cached(f)]
if not missing:
    print(f'Kokoro model and {len(VOICES)} voices: ready')
    sys.exit(0)
print(f'Downloading the Kokoro model and voices ({len(missing)} files, about 350 MB the first time)...', flush=True)
try:
    for f in missing:
        hf_hub_download(repo_id=REPO_ID, filename=f)
except Exception as exc:  # noqa: BLE001 - a short message, not a traceback
    reason = str(exc).strip().splitlines()[0][:200] if str(exc).strip() else type(exc).__name__
    print(f'Could not download {f} from Hugging Face ({reason}). Check the internet connection; '
          'finished files are kept, so the next start carries on.', file=sys.stderr)
    sys.exit(1)
print('Kokoro model and voices downloaded. The studio also works offline now: OFFLINE=1 ./start.sh')
PY
# G2P uses the spaCy English model. Make it available for offline use.
bash ./repair-spacy.sh
