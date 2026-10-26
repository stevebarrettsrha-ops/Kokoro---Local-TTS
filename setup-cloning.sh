#!/usr/bin/env bash
# One-time setup for cloned voices (Chatterbox by Resemble AI, MIT licence).
# Chatterbox needs different torch/transformers versions from Kokoro, so it
# gets its own environment in .venv-chatterbox. Needs ~6 GB disk for the
# packages plus ~3 GB for model weights (downloaded here, once).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export PATH="$HOME/.local/bin:$PATH"
export HF_HOME="$PWD/data/huggingface"
mkdir -p "$HF_HOME" voices

if ! command -v uv >/dev/null 2>&1; then
  echo 'ERROR: uv is required (see README Quick start), then rerun ./setup-cloning.sh' >&2
  exit 1
fi
if [ ! -x .venv-chatterbox/bin/python ]; then
  echo 'Creating the voice-cloning environment (.venv-chatterbox, Python 3.11)...'
  uv venv --python 3.11 .venv-chatterbox
fi
echo 'Installing Chatterbox (large download: PyTorch + model code)...'
uv pip install --python .venv-chatterbox/bin/python "chatterbox-tts==0.1.7"

echo 'Downloading the Chatterbox model (about 3 GB; safe to rerun if interrupted)...'
.venv-chatterbox/bin/python - <<'PY'
from huggingface_hub import hf_hub_download
for f in ["ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors", "tokenizer.json", "conds.pt"]:
    print("  ", f, flush=True)
    hf_hub_download(repo_id="ResembleAI/chatterbox", filename=f)
import torch
print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none (CPU mode, slow)")
PY
echo
echo 'Voice cloning is ready. Start the studio with ./start.sh and use "Add a voice" in the Voice panel.'
