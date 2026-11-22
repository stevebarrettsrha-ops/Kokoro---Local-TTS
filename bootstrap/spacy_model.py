"""Install the English spaCy pipeline explicitly, so Kokoro/Misaki never try an implicit
download when Generate is pressed.   python -m bootstrap.spacy_model
"""
from __future__ import annotations

import subprocess
import sys

from .common import find_uv, offline, say, system

MODEL_FOR_SPACY = {'3.8': '3.8.0', '3.7': '3.7.1'}  # model versions must match spaCy's major.minor


def loads(py):
    return subprocess.run([py, '-c', 'import spacy; spacy.load("en_core_web_sm")'],
                          capture_output=True, stdin=subprocess.DEVNULL).returncode == 0


def installed_here():
    """Quick check for this Python: the model package is installed and matches spaCy's major.minor.
    Reads package metadata only. Importing spaCy also imports PyTorch, which made every start
    wait several seconds (much longer on Windows) just to confirm the model is still there."""
    try:
        from importlib.metadata import version
        spacy_branch = '.'.join(version('spacy').split('.')[:2])
        model_branch = '.'.join(version('en_core_web_sm').split('.')[:2])
    except Exception:  # noqa: BLE001 - not installed
        return False
    return spacy_branch == model_branch and spacy_branch in MODEL_FOR_SPACY


def ensure(py=sys.executable):
    if (py == sys.executable and installed_here()) or loads(py):
        say('spaCy English model (en_core_web_sm): ready')
        return 0
    if offline():
        say('ERROR: spaCy English model is missing; install it once while online '
            f'(run {system.start_command()} online).', err=True)
        return 1
    try:
        from importlib.metadata import version
        branch = '.'.join(version('spacy').split('.')[:2])
    except Exception:  # noqa: BLE001
        say('ERROR: spaCy is missing. Reinstall the app requirements and retry.', err=True)
        return 1
    model = MODEL_FOR_SPACY.get(branch)
    if not model:
        say(f'ERROR: Unsupported spaCy {branch}. This step handles spaCy 3.7/3.8.', err=True)
        return 1
    wheel = (f'https://github.com/explosion/spacy-models/releases/download/'
             f'en_core_web_sm-{model}/en_core_web_sm-{model}-py3-none-any.whl')
    say(f'Installing spaCy English model {model} for spaCy {branch} (internet required)...')
    uv = find_uv()
    if uv:
        code = subprocess.run([uv, 'pip', 'install', '--python', py, wheel]).returncode
    else:
        # Environments created by 'uv venv' may not contain pip.
        if subprocess.run([py, '-m', 'pip', '--version'], capture_output=True).returncode != 0 and \
                subprocess.run([py, '-m', 'ensurepip', '--upgrade']).returncode != 0:
            say('ERROR: Neither uv nor pip is available. Install uv and retry.', err=True)
            return 1
        code = subprocess.run([py, '-m', 'pip', 'install', wheel]).returncode
    if code != 0 or not loads(py):
        say('ERROR: the spaCy English model could not be installed (see above).', err=True)
        return 1
    say('spaCy English model: verified.')
    return 0


if __name__ == '__main__':
    sys.exit(ensure())
