"""Fetch the Kokoro model and every English voice so the studio works offline.

    python -m bootstrap.models           download whatever is missing (quick when all is cached)
    python -m bootstrap.models --check   only report whether everything is downloaded (exit 1 if not)
"""
from __future__ import annotations

import sys

from .common import setup_env, say, system


def ensure(check_only=False):
    setup_env(hub_offline=True if check_only else False)
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
        say(f'Kokoro model and {len(VOICES)} voices: ready')
        return 0
    if check_only:
        say(f'{len(missing)} of {len(files)} Kokoro model files are not downloaded yet.', err=True)
        return 1
    say(f'Downloading the Kokoro model and voices ({len(missing)} files, about 350 MB the first time)...')
    for f in missing:
        try:
            hf_hub_download(repo_id=REPO_ID, filename=f)
        except Exception as exc:  # noqa: BLE001 - a short message, not a traceback
            text = str(exc).strip()
            reason = text.splitlines()[0][:200] if text else type(exc).__name__
            say(f'Could not download {f} from Hugging Face ({reason}). Check the internet connection; '
                'finished files are kept, so the next start carries on.', err=True)
            return 1
    say('Kokoro model and voices downloaded. The studio also works offline now: '
        + system.env_example('OFFLINE', 1))
    return 0


if __name__ == '__main__':
    sys.exit(ensure(check_only=sys.argv[1:] == ['--check']))
