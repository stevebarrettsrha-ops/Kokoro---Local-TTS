"""Sets up cloned voices (Chatterbox by Resemble AI, MIT licence).

The launcher runs this by itself in the background on the first run; you can also run it
directly (./setup-cloning.sh, or setup-cloning.bat on Windows). Chatterbox needs different
torch/transformers versions from Kokoro, so it gets its own environment in .venv-chatterbox.
Needs ~6 GB disk for the packages plus ~3 GB for model weights. Safe to rerun: finished
steps are skipped.

While it runs it holds an OS lock on .venv-chatterbox.installing (see studio/system.py); the
studio treats cloning as unavailable while that file exists, so a half-installed environment
is never used. If setup stops part-way the file stays (unlocked) and the next run resumes.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime

from .common import CHATTERBOX_VENV, ROOT, find_uv, say, setup_env, system

LOCK = ROOT / '.venv-chatterbox.installing'
READY = CHATTERBOX_VENV / '.ready'
PY = system.venv_python(CHATTERBOX_VENV)
WEIGHTS_CACHED = '''
from huggingface_hub import hf_hub_download
for f in ["ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors", "tokenizer.json", "conds.pt"]:
    hf_hub_download(repo_id="ResembleAI/chatterbox", filename=f, local_files_only=True)
'''
# Chatterbox's watermarker (resemble-perth) needs pkg_resources from setuptools, which uv
# environments don't include (and setuptools 81+ is dropping). Without it the watermarker is
# silently None and every cloned read fails with "'NoneType' object is not callable".
ENGINE_LOADS = 'import perth, chatterbox, sys; sys.exit(0 if perth.PerthImplicitWatermarker else 1)'


def quiet_ok(*args):
    return subprocess.run([str(PY), *args], capture_output=True, stdin=subprocess.DEVNULL).returncode == 0


def ready_already():
    return PY.exists() and quiet_ok('-c', ENGINE_LOADS) and quiet_ok('-c', WEIGHTS_CACHED)


def has_pkg_resources():
    return quiet_ok('-c', 'import importlib.util as u, sys; sys.exit(0 if u.find_spec("pkg_resources") else 1)')


def run(cmd):
    code = subprocess.run([str(c) for c in cmd], cwd=ROOT, stdin=subprocess.DEVNULL).returncode
    if code != 0:
        raise RuntimeError(f'{os.path.basename(str(cmd[0]))} exited with code {code}')


def main():
    setup_env(hub_offline=False)
    (ROOT / 'voices').mkdir(exist_ok=True)
    say(f'=== Voice cloning setup, {datetime.now():%a %d %b %Y %H:%M:%S} ===')

    if ready_already() and not system.lock_is_held(LOCK):
        READY.touch()
        LOCK.unlink(missing_ok=True)
        say('Voice cloning: ready')
        return 0

    # Only one setup at a time (the launcher may start it while you run it by hand).
    handle = open(LOCK, 'a+b')
    if not system.try_lock(handle):
        handle.close()
        say(f'Voice cloning is already being set up. Progress: {os.path.join("data", "logs", "setup-cloning.log")}')
        return 0
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()).encode())
        handle.flush()
        uv = find_uv()
        if not uv:
            raise RuntimeError(f'uv is required ({system.start_command()} installs it), then run setup-cloning again')
        READY.unlink(missing_ok=True)
        if not PY.exists():
            say('Creating the voice-cloning environment (.venv-chatterbox, Python 3.11)...')
            run([uv, 'venv', '--python', '3.11', CHATTERBOX_VENV])
        say('Installing Chatterbox (large download: PyTorch + model code)...')
        run([uv, 'pip', 'install', '--python', PY, 'chatterbox-tts==0.1.7', 'setuptools<81'])
        say('Downloading the Chatterbox model (about 3 GB; safe to rerun if interrupted)...')
        run([PY, ROOT / 'bootstrap' / 'chatterbox_download.py'])
        READY.touch()
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
        say(f'{exc}', err=True)
        say('Voice cloning setup FAILED (see above). It carries on the next time the studio starts online, '
            f'or run {system.setup_cloning_command()}.', err=True)
        system.unlock(handle)
        handle.close()
        return 1
    system.unlock(handle)
    handle.close()
    LOCK.unlink(missing_ok=True)
    say()
    say('Voice cloning is ready. Use "Add a voice from a recording" in the Voice panel (no restart needed).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
