"""Cloned voices: a local voice library plus a Chatterbox worker process.

Chatterbox pins its own torch/transformers versions, which clash with
Kokoro's, so it runs in a separate virtual environment (.venv-chatterbox,
created by ./setup-cloning.sh). This module talks to it over a pipe: one JSON
request per line in, one JSON reply per line out. The model stays loaded
between requests.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import threading
from collections import deque
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf

from . import audio as fx

SR = fx.MODEL_RATE
MIN_SECONDS = 6.0
MAX_SECONDS = 30.0
BEST_WINDOW = 10.0  # Chatterbox builds the voice's sound mostly from the first 10 s
CLONE_PREFIX = 'clone:'


def slugify(name):
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:40]
    return slug or 'voice'


# ---------------------------------------------------------------- library
class VoiceLibrary:
    def __init__(self, folder):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)

    def list(self):
        out = []
        for meta in sorted(self.dir.glob('*.json')):
            try:
                data = json.loads(meta.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            slug = meta.stem
            if not (self.dir / f'{slug}.wav').is_file():
                continue
            out.append({'id': CLONE_PREFIX + slug, 'slug': slug, 'name': data.get('name', slug),
                        'gender': data.get('gender', ''), 'seconds': data.get('seconds'),
                        'created': data.get('created'), 'engine': 'chatterbox'})
        return out

    def get(self, voice_id):
        if not isinstance(voice_id, str) or not voice_id.startswith(CLONE_PREFIX):
            return None
        slug = voice_id[len(CLONE_PREFIX):]
        if not re.fullmatch(r'[a-z0-9-]{1,48}', slug):
            return None
        return next((v for v in self.list() if v['slug'] == slug), None)

    def ref_path(self, voice_id):
        v = self.get(voice_id)
        return self.dir / f'{v["slug"]}.wav' if v else None

    def names(self):
        return {v['id']: v['name'] for v in self.list()}

    def add(self, data: bytes, filename: str, name: str, gender: str = ''):
        name = ' '.join(str(name or '').split())[:40]
        if not name:
            raise ValueError('Give the voice a name.')
        y = decode_audio(data, filename)
        ref, info = prepare_reference(y)
        slug = slugify(name)
        if (self.dir / f'{slug}.json').exists():
            slug = f'{slug}-{uuid.uuid4().hex[:4]}'
        sf.write(str(self.dir / f'{slug}.wav'), ref, SR, subtype='PCM_16')
        meta = {'name': name, 'gender': gender if gender in ('Female', 'Male') else '',
                'seconds': round(len(ref) / SR, 1), 'created': datetime.now(timezone.utc).isoformat(),
                'source_file': Path(filename or '').name[:80], **info}
        (self.dir / f'{slug}.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
        return self.get(CLONE_PREFIX + slug)

    def delete(self, voice_id):
        v = self.get(voice_id)
        if not v:
            return False
        for ext in ('wav', 'json'):
            p = self.dir / f'{v["slug"]}.{ext}'
            if p.exists():
                p.unlink()
        return True


def decode_audio(data: bytes, filename: str = ''):
    """Any common audio file -> mono float32 at 24 kHz."""
    if not data:
        raise ValueError('The file is empty.')
    try:
        y, sr = sf.read(io.BytesIO(data), dtype='float32', always_2d=True)
    except Exception:  # noqa: BLE001 - try ffmpeg for m4a/webm/aac etc.
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            raise ValueError('Could not read that file. Use WAV, FLAC, OGG or MP3, or install ffmpeg for other formats.')
        proc = subprocess.run([ffmpeg, '-loglevel', 'error', '-i', 'pipe:0', '-ac', '1', '-ar', str(SR), '-f', 'f32le', 'pipe:1'],
                              input=data, capture_output=True, timeout=120)
        if proc.returncode != 0 or not proc.stdout:
            raise ValueError('Could not read that audio file.')
        return np.frombuffer(proc.stdout, dtype=np.float32).copy()
    y = y.mean(axis=1)
    return fx.resample(y, int(sr), SR)


def prepare_reference(y):
    """Trim, level and order a reference so its cleanest 10 s come first."""
    y = np.asarray(y, dtype=np.float32)
    y, _ = fx.trim(y, threshold_db=-45, head_ms=60, tail_ms=120)
    seconds = len(y) / SR
    if seconds < MIN_SECONDS:
        raise ValueError(f'The recording needs at least {int(MIN_SECONDS)} seconds of speech (this one has {seconds:.1f}). '
                         '10 to 30 seconds works best.')
    # Score 0.5 s frames: speech present and not clipped.
    hop = SR // 2
    frames = len(y) // hop
    rms = np.array([np.sqrt(np.mean(y[i * hop:(i + 1) * hop] ** 2)) for i in range(frames)])
    clipped = np.array([np.mean(np.abs(y[i * hop:(i + 1) * hop]) > 0.985) for i in range(frames)])
    speech = (rms > rms.max() * 10 ** (-30 / 20)) & (clipped < 0.002)
    win = int(BEST_WINDOW * 2)
    start = 0
    if frames > win:
        scores = np.convolve(speech.astype(float), np.ones(win), 'valid')
        start = int(np.argmax(scores))
    a, b = start * hop, min(len(y), (start + win) * hop)
    ordered = np.concatenate([y[a:b], np.zeros(int(SR * 0.3), np.float32), y[:a], y[b:]])
    ordered = ordered[: int(MAX_SECONDS * SR)]
    level = fx.loudness(ordered, SR)
    if np.isfinite(level):
        ordered = ordered * fx.db_to_amp(-20 - level)
    ordered = np.clip(ordered, -0.98, 0.98).astype(np.float32)
    info = {'speech_ratio': round(float(speech.mean()) if frames else 0.0, 2),
            'clipping': bool(clipped.max() > 0.01) if frames else False,
            'best_window_start': round(start * 0.5, 1)}
    return ordered, info


# ---------------------------------------------------------------- worker
class CloneWorker:
    """Runs cloning/chatterbox_worker.py inside the Chatterbox environment."""

    def __init__(self, python=None, script=None, root=None):
        root = Path(root or Path(__file__).resolve().parents[1])
        self.python = Path(python or os.environ.get('CHATTERBOX_PYTHON') or root / '.venv-chatterbox' / 'bin' / 'python')
        self.script = Path(script or root / 'cloning' / 'chatterbox_worker.py')
        self.proc = None
        self.lock = threading.Lock()
        self.scratch = root / 'outputs' / '.clone-tmp'

    def available(self):
        return self.python.exists() and self.script.exists()

    def _start(self):
        if self.proc and self.proc.poll() is None:
            return
        if not self.available():
            raise RuntimeError('Voice cloning is not installed yet. Stop the studio and run ./setup-cloning.sh, '
                               'then start it again.')
        self.scratch.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, 'PYTHONUNBUFFERED': '1'}
        self.proc = subprocess.Popen([str(self.python), str(self.script)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=None, text=True, bufsize=1, env=env)

    def _call(self, request, timeout=900):
        with self.lock:
            self._start()
            try:
                self.proc.stdin.write(json.dumps(request) + '\n')
                self.proc.stdin.flush()
            except BrokenPipeError:
                self.proc = None
                raise RuntimeError('The cloning engine stopped unexpectedly. See the terminal for details.')
            result = {}

            def read():
                result['line'] = self.proc.stdout.readline()
            t = threading.Thread(target=read, daemon=True)
            t.start()
            t.join(timeout)
            if t.is_alive():
                self.proc.kill()
                self.proc = None
                raise RuntimeError('The cloning engine took too long and was restarted.')
            line = result.get('line') or ''
            if not line:
                self.proc = None
                raise RuntimeError('The cloning engine stopped unexpectedly. See the terminal for details '
                                   '(often: not enough GPU memory, or the model download was interrupted).')
            reply = json.loads(line)
            if not reply.get('ok'):
                raise RuntimeError(reply.get('error') or 'Cloning failed.')
            return reply

    def speak(self, text, ref_path, exaggeration=0.5, cfg_weight=0.5, temperature=0.8, seed=0):
        out = self.scratch / f'{uuid.uuid4().hex}.npy'
        try:
            self._call({'op': 'tts', 'text': text, 'ref': str(ref_path), 'out': str(out),
                        'exaggeration': float(exaggeration), 'cfg_weight': float(cfg_weight),
                        'temperature': float(temperature), 'seed': int(seed)})
            y = np.load(out).astype(np.float32).reshape(-1)
        finally:
            if out.exists():
                out.unlink()
        return y

    def status(self):
        return {'installed': self.available(), 'running': bool(self.proc and self.proc.poll() is None)}

    def close(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        self.proc = None


class Installer:
    """Runs setup-cloning.sh in the background so cloning can be installed from the browser."""

    def __init__(self, root=None):
        self.root = Path(root or Path(__file__).resolve().parents[1])
        self.job = None
        self.lock = threading.Lock()

    def start(self):
        with self.lock:
            if self.job and self.job['status'] == 'running':
                return self.status()
            script = self.root / 'setup-cloning.sh'
            if not script.exists():
                raise RuntimeError('setup-cloning.sh is missing from the app folder.')
            if not shutil.which('uv') and not (Path.home() / '.local' / 'bin' / 'uv').exists():
                raise RuntimeError('The installer needs uv. Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh')
            self.job = {'status': 'running', 'log': deque(maxlen=300), 'code': None}
            env = {**os.environ, 'PATH': f"{Path.home() / '.local' / 'bin'}:{os.environ.get('PATH', '')}",
                   'HF_HUB_OFFLINE': '0', 'NO_COLOR': '1', 'UV_NO_PROGRESS': '1'}
            proc = subprocess.Popen(['bash', str(script)], cwd=str(self.root), env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1)
            threading.Thread(target=self._pump, args=(proc, self.job), daemon=True).start()
        return self.status()

    @staticmethod
    def _pump(proc, job):
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                job['log'].append(line[-300:])
        job['code'] = proc.wait()
        job['status'] = 'done' if job['code'] == 0 else 'failed'

    def status(self):
        job = self.job
        if not job:
            return None
        return {'status': job['status'], 'log': list(job['log'])[-120:], 'code': job['code']}
