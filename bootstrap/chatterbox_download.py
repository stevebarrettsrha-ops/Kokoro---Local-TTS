"""Runs inside .venv-chatterbox (started by cloning_setup.py): downloads the Chatterbox model
and checks that the engine loads. Standalone on purpose; it can't import the studio's packages."""
import os
import threading

from huggingface_hub import hf_hub_download

FILES = ['ve.safetensors', 't3_cfg.safetensors', 's3gen.safetensors', 'tokenizer.json', 'conds.pt']

# The downloader draws no progress bar into a log file, so report the bytes on disk every 15 s.
# Newer huggingface_hub versions ("Xet" transfers) fetch pieces into HF_HOME/xet first and only
# write the finished file into the model folder at the end, so count whichever has grown more.
hf = os.environ['HF_HOME']
places = [os.path.join(hf, 'hub', 'models--ResembleAI--chatterbox', 'blobs'),  # real files (snapshots/ only links)
          os.environ.get('HF_XET_CACHE') or os.path.join(hf, 'xet')]


def size(path):
    total = 0
    for root, _, names in os.walk(path):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(root, n))
            except OSError:
                pass
    return total


start = [size(p) for p in places]


def on_disk():
    return max(max(0, size(p) - s0) for p, s0 in zip(places, start))


done = threading.Event()


def report():
    while not done.wait(15):
        print(f'   downloaded {on_disk() / 1e9:.2f} GB of about 3.2 GB so far', flush=True)


threading.Thread(target=report, daemon=True).start()
for f in FILES:
    print('  ', f, flush=True)
    hf_hub_download(repo_id='ResembleAI/chatterbox', filename=f)
done.set()
print('   model complete', flush=True)
import chatterbox  # noqa: E402,F401  (fails here, not in the studio, if the install is broken)
import perth  # noqa: E402
assert perth.PerthImplicitWatermarker, 'the Chatterbox watermarker could not load (setuptools/pkg_resources missing)'
import torch  # noqa: E402
gpu = 'none (CPU mode, slow)'
if torch.cuda.is_available():
    gpu = torch.cuda.get_device_name(0)
elif getattr(torch.backends, 'mps', None) and torch.backends.mps.is_available():
    gpu = 'Apple GPU available (CPU is used unless CHATTERBOX_DEVICE=mps)'
print('GPU:', gpu, flush=True)
