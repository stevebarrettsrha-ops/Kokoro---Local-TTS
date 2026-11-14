"""Everything the launcher does once .venv exists, the same on Linux, macOS and Windows:
spaCy model, Kokoro model and voices, the background voice-cloning install, then the studio.

    python -m bootstrap.launch      (start.sh / start.bat run this)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import webbrowser

from .common import LOGS, ROOT, find_uv, offline, say, setup_env, system, truthy

CLONING_NEEDS_GB = 12


def start_cloning_setup():
    """Cloned voices need ~9 GB more. That installs in the background so the studio can be used
    right away; cloned voices start working when it finishes, without a restart."""
    from . import cloning_setup as cs
    if os.environ.get('CHATTERBOX_PYTHON') or truthy(os.environ.get('SKIP_CLONING')):
        return
    # An install made before the setuptools fix is marked ready but can't load the watermarker:
    # clear the mark so the setup repairs it (a quick, small install).
    if cs.READY.exists() and not cs.has_pkg_resources():
        cs.READY.unlink(missing_ok=True)
    if cs.READY.exists() or system.lock_is_held(cs.LOCK):
        return
    free_gb = shutil.disk_usage(ROOT).free / 1024 ** 3
    if not find_uv():
        say(f'NOTICE: voice cloning needs uv; install it and run {system.setup_cloning_command()}.', err=True)
        return
    if free_gb < CLONING_NEEDS_GB:
        say(f'NOTICE: voice cloning needs about {CLONING_NEEDS_GB} GB free disk space '
            f'({free_gb:.0f} GB free); skipped for now.', err=True)
        return
    log_path = LOGS / 'setup-cloning.log'
    say(f'Setting up voice cloning in the background (large download). Progress: {log_path.relative_to(ROOT)}')
    kwargs = {}
    if system.WINDOWS:
        # Own hidden console and process group: Ctrl+C or closing this window doesn't stop it,
        # and the programs it runs (uv) don't pop up windows of their own.
        kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs['start_new_session'] = True  # survives Ctrl+C and a closed terminal
    with open(log_path, 'ab') as log:
        proc = subprocess.Popen([sys.executable, '-m', 'bootstrap.cloning_setup'], cwd=ROOT, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, close_fds=True, **kwargs)
    stop = f'taskkill /PID {proc.pid} /T /F' if system.WINDOWS else f'kill -- -{proc.pid}'
    say(f'  It keeps going if you stop the studio. To stop it: {stop}')


def open_browser(url):
    if truthy(os.environ.get('KOKORO_NO_BROWSER')):
        return
    # On Linux without a desktop, webbrowser may pick a text browser that takes over this terminal.
    if not (system.WINDOWS or system.MAC) and not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        return
    timer = threading.Timer(3, lambda: webbrowser.open(url))
    timer.daemon = True
    timer.start()


def main():
    setup_env()
    os.chdir(ROOT)
    (ROOT / 'outputs').mkdir(exist_ok=True)
    from . import models, spacy_model
    # Verify the English G2P model before the server starts, not during the first synthesis
    # request (which otherwise fails with a browser NetworkError).
    if spacy_model.ensure() != 0:
        return 1
    if offline():
        # Offline: just say so if the model was never downloaded (the first render would fail).
        if models.ensure(check_only=True) != 0:
            say(f'WARNING: the Kokoro model is not downloaded yet. Run {system.start_command()} once while online.', err=True)
    else:
        # The Kokoro model and all voices (about 350 MB once; a quick check after that).
        if models.ensure() != 0:
            say('WARNING: the model download did not finish; it is retried on the next start.', err=True)
        setup_env()  # models.ensure() may have switched the hub to offline for its check
        start_cloning_setup()

    say(f'Python: {sys.version.split()[0]}')
    port = os.environ.get('KOKORO_PORT', '8765').strip() or '8765'
    os.environ['KOKORO_PORT'] = port
    say(f'Launching Kokoro Studio: http://127.0.0.1:{port}')
    open_browser(f'http://127.0.0.1:{port}')
    import app
    app.main()
    return 0


if __name__ == '__main__':
    sys.exit(main())
