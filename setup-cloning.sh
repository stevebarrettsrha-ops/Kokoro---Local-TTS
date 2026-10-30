#!/usr/bin/env bash
# Sets up cloned voices (Chatterbox by Resemble AI, MIT licence). ./start.sh runs
# this by itself in the background on the first run; you can also run it directly.
# Chatterbox needs different torch/transformers versions from Kokoro, so it gets
# its own environment in .venv-chatterbox. Needs ~6 GB disk for the packages
# plus ~3 GB for model weights. Safe to rerun: finished steps are skipped.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export PATH="$HOME/.local/bin:$PATH"
export HF_HOME="$PWD/data/huggingface"
export HF_HUB_OFFLINE=0
mkdir -p "$HF_HOME" voices
lock=.venv-chatterbox.installing
ready=.venv-chatterbox/.ready
echo "=== Voice cloning setup, $(date) ==="
trap 'echo "Voice cloning setup FAILED (see above). It carries on the next time ./start.sh runs online, or run ./setup-cloning.sh." >&2' ERR
weights_cached() {
  .venv-chatterbox/bin/python - >/dev/null 2>&1 <<'PY'
from huggingface_hub import hf_hub_download
for f in ["ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors", "tokenizer.json", "conds.pt"]:
    hf_hub_download(repo_id="ResembleAI/chatterbox", filename=f, local_files_only=True)
PY
}

# Already done? (Older installs have no .ready marker: check them once and add it.)
if [ -x .venv-chatterbox/bin/python ] && { [ -f "$ready" ] || .venv-chatterbox/bin/python -c 'import chatterbox' >/dev/null 2>&1; } && weights_cached; then
  touch "$ready"; rm -f "$lock"
  echo 'Voice cloning: ready'
  exit 0
fi

# Only one setup at a time (start.sh may launch it while you run it by hand). The id in the
# lock only counts if that process really is this script: after a reboot it may be another program.
pid="$(cat "$lock" 2>/dev/null || true)"
if [ -n "$pid" ] && grep -qa setup-cloning "/proc/$pid/cmdline" 2>/dev/null; then
  echo "Voice cloning is already being set up (process $pid). Progress: data/logs/setup-cloning.log"
  exit 0
fi
if ! command -v uv >/dev/null 2>&1; then
  echo 'ERROR: uv is required (./start.sh installs it), then rerun ./setup-cloning.sh' >&2
  exit 1
fi
# The studio treats cloning as unavailable while this file exists, so a half-installed
# environment is never used. If setup stops part-way, the file stays and the next run resumes.
echo $$ > "$lock"
rm -f "$ready"
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
import chatterbox  # noqa: F401  (fails here, not in the studio, if the install is broken)
import torch
print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none (CPU mode, slow)")
PY
touch "$ready"
rm -f "$lock"
echo
echo 'Voice cloning is ready. Use "Add a voice from a recording" in the Voice panel (no restart needed).'
