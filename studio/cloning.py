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
SOURCE_MAX_SECONDS = 30 * 60   # longest recording the cutter opens (the first 30 min are used)
SUGGEST_SECONDS = 20.0          # length of the "best part" the cutter preselects
PEAKS = 1600                    # waveform resolution sent to the cutter
SOURCE_RE = re.compile(r'[0-9a-f]{32}')
_SAVE_LOCK = threading.Lock()  # two voices created at once must not pick the same file name


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
        name = _clean_name(name)
        return self._save(decode_audio(data, filename), name, gender, filename)

    def _save(self, y, name, gender, source_name, extra=None):
        ref, info = prepare_reference(y)
        meta = {'name': name, 'gender': gender if gender in ('Female', 'Male') else '',
                'seconds': round(len(ref) / SR, 1), 'created': datetime.now(timezone.utc).isoformat(),
                'source_file': Path(source_name or '').name[:80], **info, **(extra or {})}
        with _SAVE_LOCK:
            slug = slugify(name)
            if (self.dir / f'{slug}.json').exists() or (self.dir / f'{slug}.wav').exists():
                slug = f'{slug}-{uuid.uuid4().hex[:4]}'
            sf.write(str(self.dir / f'{slug}.wav'), ref, SR, subtype='PCM_16')
            (self.dir / f'{slug}.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
        return self.get(CLONE_PREFIX + slug)

    # ---- sources: a recording opened in the cutter, before the voice is made ----
    @property
    def sources(self):
        d = self.dir / '.sources'
        d.mkdir(exist_ok=True)
        return d

    def open_source(self, path, filename=''):
        """Decode any audio or video file into a source the cutter can show and play.

        Returns {id, name, seconds, peaks, suggest: [start, end]}.
        """
        self.prune_sources()
        y = decode_file(path, SOURCE_MAX_SECONDS)
        if len(y) < SR * 1:
            raise ValueError('That recording is shorter than a second.')
        sid = uuid.uuid4().hex
        sf.write(str(self.sources / f'{sid}.wav'), np.clip(y, -1, 1), SR, subtype='PCM_16')
        name = Path(filename or '').name[:80]
        (self.sources / f'{sid}.json').write_text(json.dumps({'name': name}), encoding='utf-8')
        return {'id': sid, 'name': name, 'seconds': round(len(y) / SR, 2), 'peaks': peaks(y),
                'suggest': list(best_part(y))}

    def source_path(self, sid):
        if not isinstance(sid, str) or not SOURCE_RE.fullmatch(sid):
            return None
        p = self.sources / f'{sid}.wav'
        return p if p.is_file() else None

    def delete_source(self, sid):
        p = self.source_path(sid)
        if not p:
            return False
        p.unlink(missing_ok=True)
        p.with_suffix('.json').unlink(missing_ok=True)
        return True

    def prune_sources(self, max_age_s=24 * 3600):
        now = datetime.now().timestamp()
        for f in self.sources.glob('*'):
            try:
                if now - f.stat().st_mtime > max_age_s:
                    f.unlink(missing_ok=True)
            except OSError:
                continue

    def clear_sources(self):
        """At start-up: recordings opened in a previous run (and half-finished uploads) are never used again."""
        for f in self.sources.glob('*'):
            f.unlink(missing_ok=True)

    def add_from_source(self, sid, start, end, name, gender=''):
        """Make a voice from the part [start, end] (seconds) of an opened source."""
        name = _clean_name(name)
        gone = ValueError('That recording is no longer open. Choose it again.')
        path = self.source_path(sid)
        if path is None:
            raise gone
        try:
            info = sf.info(str(path))
            sr, dur = info.samplerate, info.frames / info.samplerate
            try:
                a, b = max(0.0, float(start)), min(dur, float(end))
            except (TypeError, ValueError):
                a, b = 0.0, dur
            if not (b - a >= 1.0) or not np.isfinite(a + b):
                raise ValueError('Select at least a few seconds of the recording.')
            # Only the selected part is read: selecting all of a 30-minute file stays small.
            y, _ = sf.read(str(path), dtype='float32', start=int(a * sr), stop=int(b * sr))
        except (RuntimeError, OSError) as exc:  # removed while being read (e.g. another file was chosen)
            raise gone from exc
        try:
            meta = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            meta = {}
        voice = self._save(y, name, gender, meta.get('name', ''),
                           {'cut': [round(a, 2), round(b, 2)]})
        self.delete_source(sid)
        return voice

    def delete(self, voice_id):
        v = self.get(voice_id)
        if not v:
            return False
        for ext in ('wav', 'json'):
            p = self.dir / f'{v["slug"]}.{ext}'
            if p.exists():
                p.unlink()
        return True


def _clean_name(name):
    name = ' '.join(str(name or '').split())[:40]
    if not name:
        raise ValueError('Give the voice a name.')
    return name


def _finite(y):
    """Float files can hold NaN/inf samples; they would break loudness maths and JSON."""
    return np.nan_to_num(np.asarray(y, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)


def decode_file(path, max_seconds=SOURCE_MAX_SECONDS):
    """Audio track of any file ffmpeg (or soundfile) can read -> mono float32 at 24 kHz.

    Reads from disk and streams through ffmpeg, which converts to mono 24 kHz as it goes, so
    memory stays at the size of the result (~170 MB for 30 min) whatever the file's own format.
    soundfile is the fallback when ffmpeg is not installed. Stops after max_seconds.
    """
    path = Path(path)
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg:
        proc = subprocess.run([ffmpeg, '-nostdin', '-loglevel', 'error', '-i', str(path), '-vn', '-t', str(max_seconds),
                               '-ac', '1', '-ar', str(SR), '-f', 'f32le', 'pipe:1'],
                              capture_output=True, timeout=600, stdin=subprocess.DEVNULL)
        if proc.returncode == 0 and proc.stdout:
            return _finite(np.frombuffer(proc.stdout, dtype=np.float32))
    try:
        info = sf.info(str(path))
        frames = min(info.frames, int(max_seconds * info.samplerate))
        y, sr = sf.read(str(path), dtype='float32', always_2d=True, frames=frames)
        return _finite(fx.resample(y.mean(axis=1), int(sr), SR))
    except Exception:  # noqa: BLE001 - not a format soundfile knows (mp4, m4a, webm...)
        pass
    if not ffmpeg:
        raise ValueError('Could not read that file. Use WAV, FLAC, OGG or MP3, or install ffmpeg for other formats.')
    raise ValueError('Could not find any sound in that file.')


def _speech_frames(y, hop):
    """Per frame: sound that rises and falls like syllables, loud enough, and not clipped.

    The syllable test (a >10 dB spread between the loud and quiet 20 ms slices of the frame)
    keeps a steady music bed or noise from scoring higher than real speech, whose pauses would
    otherwise count against it.
    """
    frames = len(y) // hop
    if not frames:
        return np.zeros(0, bool)
    f = y[: frames * hop].reshape(frames, hop)
    rms = np.sqrt(np.einsum('ij,ij->i', f, f, dtype=np.float64) / hop)  # no float64 copy of the audio
    clipped = np.mean(np.abs(f) > 0.985, axis=1)
    slices = 25
    sub = f[:, : hop // slices * slices].reshape(frames, slices, hop // slices)
    sub_db = 10 * np.log10(np.einsum('ijk,ijk->ij', sub, sub, dtype=np.float64) / (hop // slices) + 1e-10)
    modulated = np.percentile(sub_db, 90, axis=1) - np.percentile(sub_db, 10, axis=1) > 10
    return (rms > rms.max() * 10 ** (-30 / 20)) & (clipped < 0.002) & modulated


def best_part(y, seconds=SUGGEST_SECONDS):
    """(start, end) in seconds of the window with the most clean speech."""
    total = len(y) / SR
    if total <= seconds:
        return 0.0, round(total, 2)
    hop = SR // 2
    speech = _speech_frames(y, hop).astype(float)
    win = int(seconds * 2)
    if len(speech) <= win:
        return 0.0, round(min(total, seconds), 2)
    start = int(np.argmax(np.convolve(speech, np.ones(win), 'valid'))) * 0.5
    return round(start, 2), round(min(total, start + seconds), 2)


def peaks(y, n=PEAKS):
    """Waveform outline for the cutter: n peak values in 0..1."""
    if not len(y):
        return []
    n = min(n, len(y))
    edges = np.linspace(0, len(y), n + 1).astype(int)
    out = np.maximum.reduceat(np.abs(y), edges[:-1])
    top = float(out.max()) or 1.0
    return [round(float(v) / top, 3) for v in out]


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
        return _finite(np.frombuffer(proc.stdout, dtype=np.float32))
    return _finite(fx.resample(y.mean(axis=1), int(sr), SR))


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
        custom = python or os.environ.get('CHATTERBOX_PYTHON')
        self.python = Path(custom or root / '.venv-chatterbox' / 'bin' / 'python')
        # setup-cloning.sh holds this file (with its process id) while it installs.
        self.setup_lock = None if custom else root / '.venv-chatterbox.installing'
        self.setup_log = root / 'data' / 'logs' / 'setup-cloning.log'
        self.script = Path(script or root / 'cloning' / 'chatterbox_worker.py')
        self.proc = None
        self.lock = threading.Lock()
        self.scratch = root / 'outputs' / '.clone-tmp'

    def setup_state(self):
        """'installing', 'interrupted' (setup stopped part-way) or None.

        The lock holds the setup's process id. That id only counts if it still is setup-cloning.sh:
        after a reboot the id may belong to another program, and a finished setup that nobody
        collected (a zombie) still answers kill(0) but has an empty command line.
        """
        if not self.setup_lock or not self.setup_lock.exists():
            return None
        try:
            pid = int(self.setup_lock.read_text().strip())
            cmdline = Path(f'/proc/{pid}/cmdline').read_bytes()
            return 'installing' if b'setup-cloning' in cmdline else 'interrupted'
        except (ValueError, OSError):
            return 'interrupted'

    def available(self):
        return self.python.exists() and self.script.exists() and self.setup_state() is None

    def _start(self):
        if self.proc and self.proc.poll() is None:
            return
        if not self.available():
            state = self.setup_state()
            if state == 'installing':
                raise RuntimeError('Voice cloning is still being installed in the background (a large download). '
                                   'Try again when it finishes; no restart is needed.')
            raise RuntimeError('Voice cloning is not installed yet. Run ./start.sh while online (it installs it), '
                               'or run ./setup-cloning.sh.')
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
            reply = self._call({'op': 'tts', 'text': text, 'ref': str(ref_path), 'out': str(out),
                                'exaggeration': float(exaggeration), 'cfg_weight': float(cfg_weight),
                                'temperature': float(temperature), 'seed': int(seed)})
            y = np.load(out).astype(np.float32).reshape(-1)
            sr = int(reply.get('sr') or SR)
            if sr != SR:  # the rest of the chain runs at Kokoro's rate
                y = fx.resample(y, sr, SR)
        finally:
            if out.exists():
                out.unlink()
        return y

    def status(self):
        return {'installed': self.available(), 'running': bool(self.proc and self.proc.poll() is None),
                'setup': self.setup_state()}

    def close(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        self.proc = None
