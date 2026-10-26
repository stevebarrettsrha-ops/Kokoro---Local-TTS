#!/usr/bin/env python3
"""Kokoro Studio - local voice-over API and web UI (binds to 127.0.0.1 only)."""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

from studio.engine import Engine

HERE = Path(__file__).resolve().parent
STATIC = HERE / 'static'
OUT = HERE / 'outputs'
ID_RE = re.compile(r'[0-9a-f]{32}')

engine = Engine(OUT, HERE / 'voices')
app = Flask(__name__, static_folder=None)
app.json.sort_keys = False
app.config['MAX_CONTENT_LENGTH'] = 60 * 1024 * 1024  # voice uploads


def device_status():
    try:
        import torch
        if torch.cuda.is_available():
            return {'device': 'CUDA', 'gpu': torch.cuda.get_device_name(0), 'cuda': True}
        return {'device': 'CPU', 'gpu': None, 'cuda': False}
    except Exception:
        return {'device': 'CPU / unavailable', 'gpu': None, 'cuda': False}


@app.before_request
def local_only():
    # Blocks DNS-rebinding: a web page cannot reach this API under another host name.
    host = (request.host or '').split(':')[0].strip('[]')
    if host not in ('127.0.0.1', 'localhost', '::1'):
        abort(403)


@app.after_request
def private_headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.get('/')
def homepage():
    return send_from_directory(str(STATIC), 'index.html')


@app.get('/static/<path:name>')
def static_file(name):
    return send_from_directory(str(STATIC), name)


@app.get('/api/status')
def status():
    return jsonify({'ok': True, **device_status(), 'loaded': engine.model is not None,
                    'in_model_prosody': engine.patched, 'offline': os.environ.get('HF_HUB_OFFLINE', '0') == '1',
                    'ffmpeg': bool(shutil.which('ffmpeg')), 'recordings': len(list(OUT.glob('*.json'))),
                    'model': 'hexgrad/Kokoro-82M', 'server': 'local'})


@app.get('/api/voices')
def voices():
    return jsonify(engine.catalog())


def _payload():
    if not request.is_json:
        return None, (jsonify({'error': 'Send JSON to this endpoint.'}), 415)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, (jsonify({'error': 'Invalid request body.'}), 400)
    return payload, None


def _failure(exc):
    if isinstance(exc, ValueError):
        return jsonify({'error': str(exc)}), 400
    app.logger.exception('Voice generation failed')
    message = str(exc)
    if 'OfflineModeIsEnabled' in type(exc).__name__ or 'offline mode' in message.lower():
        message = 'A required model or voice is not cached. Run ./download-models.sh online first.'
    elif 'out of memory' in message.lower():
        message = 'The GPU ran out of memory. Try KOKORO_DEVICE=cpu ./start.sh, or close GPU-heavy apps.'
    return jsonify({'error': message[:600]}), 500


def _links(meta):
    rid = meta['id']
    return {**meta, 'audio_url': f'/api/audio/{rid}', 'download_url': f'/api/audio/{rid}?download=1',
            'mp3_url': f'/api/audio/{rid}?format=mp3&download=1' if meta.get('mp3') else None,
            'srt_url': f'/api/captions/{rid}.srt', 'vtt_url': f'/api/captions/{rid}.vtt'}


@app.post('/api/generate')
def generate():
    payload, error = _payload()
    if error:
        return error
    try:
        meta = engine.synthesize(payload, save=True)
    except Exception as exc:  # noqa: BLE001 - reported to the UI
        return _failure(exc)
    return jsonify({'ok': True, 'recording': _links(meta)})


@app.post('/api/phonemize')
def phonemize():
    """Show the phonemes Kokoro will read, to debug pronunciations."""
    payload, error = _payload()
    if error:
        return error
    try:
        return jsonify(engine.phonemize(payload))
    except Exception as exc:  # noqa: BLE001
        return _failure(exc)


@app.post('/api/preview')
def preview():
    """Audition a line with the current direction. Nothing is saved."""
    payload, error = _payload()
    if error:
        return error
    text = payload.get('text', '')
    if isinstance(text, str) and len(text) > 600:
        payload = {**payload, 'text': text[:600]}
    payload = {**payload, 'target_seconds': None, 'mp3': False, 'rate': 24000}
    try:
        wav, info = engine.synthesize(payload, save=False)
    except Exception as exc:  # noqa: BLE001
        return _failure(exc)
    import io
    response = send_file(io.BytesIO(wav), mimetype='audio/wav')
    response.headers['X-Take-Info'] = json.dumps({k: info[k] for k in ('seconds', 'lufs', 'true_peak')})
    return response


def _take_path(rid, ext):
    if not ID_RE.fullmatch(rid):
        abort(404)
    path = OUT / f'{rid}.{ext}'
    if not path.is_file():
        abort(404)
    return path


def _download_name(rid, ext):
    try:
        name = json.loads((OUT / f'{rid}.json').read_text(encoding='utf-8')).get('name', '')
    except (OSError, ValueError):
        name = ''
    slug = re.sub(r'[^A-Za-z0-9]+', '-', name).strip('-').lower()[:40] or 'take'
    return f'{slug}-{rid[:6]}.{ext}'


@app.get('/api/audio/<rid>')
def audio(rid):
    ext = 'mp3' if request.args.get('format') == 'mp3' else 'wav'
    path = _take_path(rid, ext)
    return send_file(path, mimetype='audio/mpeg' if ext == 'mp3' else 'audio/wav',
                     as_attachment=request.args.get('download') == '1', download_name=_download_name(rid, ext))


@app.get('/api/captions/<rid>.<ext>')
def captions(rid, ext):
    if ext not in ('srt', 'vtt'):
        abort(404)
    path = _take_path(rid, ext)
    return send_file(path, mimetype='text/vtt' if ext == 'vtt' else 'application/x-subrip',
                     as_attachment=True, download_name=_download_name(rid, ext))


@app.get('/api/recordings')
def recordings():
    data = []
    for filename in OUT.glob('*.json'):
        try:
            entry = json.loads(filename.read_text(encoding='utf-8'))
            if not ID_RE.fullmatch(entry.get('id', '')) or not (OUT / (entry['id'] + '.wav')).is_file():
                continue
            entry.setdefault('mp3', False)
            entry.setdefault('captions', 0)
            data.append(_links(entry))
        except (OSError, ValueError, TypeError, KeyError):
            continue
    data.sort(key=lambda r: r.get('created', ''), reverse=True)
    return jsonify({'recordings': data[:200]})


@app.post('/api/voices/clone')
def add_cloned_voice():
    """Upload a recording to make a cloned voice. The uploader confirms consent."""
    upload = request.files.get('file')
    if upload is None:
        return jsonify({'error': 'Choose an audio file.'}), 400
    if request.form.get('consent') != 'yes':
        return jsonify({'error': 'Confirm you have the speaker\'s permission to use their voice.'}), 400
    try:
        voice = engine.library.add(upload.read(), upload.filename or '', request.form.get('name', ''),
                                   request.form.get('gender', ''))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'ok': True, 'voice': voice, 'cloning': engine.cloner.status()})


@app.get('/api/voices/clone/<slug>/sample')
def cloned_sample(slug):
    path = engine.library.ref_path('clone:' + slug)
    if path is None:
        abort(404)
    return send_file(path, mimetype='audio/wav')


@app.delete('/api/voices/clone/<slug>')
def delete_cloned_voice(slug):
    if not engine.library.delete('clone:' + slug):
        abort(404)
    return jsonify({'ok': True})


@app.delete('/api/recordings/<rid>')
def delete_recording(rid):
    if not ID_RE.fullmatch(rid):
        abort(404)
    removed = 0
    for ext in ('wav', 'mp3', 'srt', 'vtt', 'json'):
        path = OUT / f'{rid}.{ext}'
        if path.is_file():
            path.unlink()
            removed += 1
    if not removed:
        abort(404)
    return jsonify({'ok': True})


if __name__ == '__main__':
    from werkzeug.serving import run_simple
    port = int(os.environ.get('KOKORO_PORT', '8765'))
    print('\nKokoro Studio | http://127.0.0.1:%d\n' % port, flush=True)
    # Bind only to loopback: the app never exposes an API to the LAN.
    run_simple('127.0.0.1', port, app, threaded=True, use_reloader=False)
