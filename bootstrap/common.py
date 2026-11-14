"""Paths and small helpers for the launcher steps (standard library only)."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from studio import system  # noqa: E402  (standard library only)

HF_HOME = ROOT / 'data' / 'huggingface'
LOGS = ROOT / 'data' / 'logs'
VENV = ROOT / '.venv'
CHATTERBOX_VENV = ROOT / '.venv-chatterbox'


def truthy(value):
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')


def offline():
    return truthy(os.environ.get('OFFLINE'))


def setup_env(hub_offline=None):
    """Keep every download inside this folder, and honour OFFLINE=1."""
    off = offline() if hub_offline is None else hub_offline
    os.environ['HF_HOME'] = str(HF_HOME)
    os.environ['HF_HUB_OFFLINE'] = '1' if off else '0'
    os.environ['HF_DATASETS_OFFLINE'] = '1' if off else '0'
    HF_HOME.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)


def find_uv():
    """uv on PATH, or where its installer puts it (~/.local/bin) when this terminal predates it."""
    found = shutil.which('uv')
    if found:
        return found
    for name in ('uv.exe', 'uv') if system.WINDOWS else ('uv',):
        candidate = Path.home() / '.local' / 'bin' / name
        if candidate.is_file():
            return str(candidate)
    return None


def say(*parts, err=False):
    print(*parts, file=sys.stderr if err else sys.stdout, flush=True)
