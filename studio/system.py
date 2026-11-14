"""What differs between Linux, macOS and Windows, kept in one place.

Standard library only: the launcher (bootstrap/) imports this before the
studio's own packages are installed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

WINDOWS = os.name == 'nt'
MAC = sys.platform == 'darwin'


def venv_python(venv):
    """The interpreter inside a virtual environment folder."""
    venv = Path(venv)
    return venv / 'Scripts' / 'python.exe' if WINDOWS else venv / 'bin' / 'python'


def start_command():
    """How the user starts the studio on this computer (for messages)."""
    return 'start.bat' if WINDOWS else './start.sh'


def setup_cloning_command():
    return 'setup-cloning.bat' if WINDOWS else './setup-cloning.sh'


def ffmpeg_install_hint():
    if WINDOWS:
        return 'winget install -e --id Gyan.FFmpeg (then start the studio again)'
    if MAC:
        return 'brew install ffmpeg'
    return 'sudo apt install ffmpeg'


def env_example(name, value):
    """`KOKORO_PORT=8899 ./start.sh`, or the Windows equivalent."""
    if WINDOWS:
        return f'set "{name}={value}" && start.bat'
    return f'{name}={value} ./start.sh'


# ---- a lock held for as long as a process runs -------------------------------------------
# The background cloning setup holds an OS lock on its lock file. Unlike a process id written
# into the file, an OS lock disappears by itself when the process ends (crash, reboot, Ctrl+C),
# so "is the setup still running?" never mistakes another program that got the same id.
_LOCK_OFFSET = 4096  # Windows locks byte ranges; lock one far past the text so readers aren't blocked


def try_lock(handle):
    """Take an exclusive lock on an open file without waiting. True if this process now holds it."""
    try:
        if WINDOWS:
            import msvcrt
            handle.seek(_LOCK_OFFSET)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def unlock(handle):
    try:
        if WINDOWS:
            import msvcrt
            handle.seek(_LOCK_OFFSET)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def lock_is_held(path):
    """True while some running process holds the lock on this file."""
    try:
        with open(path, 'a+b') as f:
            if try_lock(f):
                unlock(f)
                return False
            return True
    except OSError:
        return False
