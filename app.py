#!/usr/bin/env python3
"""Kokoro Studio - local voice-over API and web UI (binds to 127.0.0.1 only)."""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

from studio.engine import Engine
from studio.engine import bounded as engine_bounded
from studio import video as vid

HERE = Path(__file__).resolve().parent
STATIC = HERE / 'static'
OUT = HERE / 'outputs'
ID_RE = re.compile(r'[0-9a-f]{32}')

engine = Engine(OUT, HERE / 'voices')
CREATIONS = HERE / 'creations'  # everything the video editor makes lives here
media = vid.MediaLibrary(CREATIONS / 'media')
projects = vid.ProjectStore(CREATIONS / 'projects')
exports = vid.ExportJobs(CREATIONS / 'exports')
app = Flask(__name__, static_folder=None)
app.json.sort_keys = False
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 ** 3  # video uploads can be large


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
    # Blocks cross-site requests: any web page can send a plain form POST to
    # 127.0.0.1 (e.g. uploading files to fill the disk), so writes must come
    # from the studio's own page.
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('Origin')
        if origin:  # 'null' (sandboxed frames, file pages) is cross-site too
            origin_host = urlsplit(origin).hostname or '' if origin != 'null' else ''
            if origin_host not in ('127.0.0.1', 'localhost', '::1'):
                abort(403)
        elif request.headers.get('Sec-Fetch-Site') == 'cross-site':
            abort(403)


@app.after_request
def private_headers(response):
    immutable = request.method == 'GET' and request.path.startswith('/api/media/')
    response.headers['Cache-Control'] = 'private, max-age=86400' if immutable else 'no-store'
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


def _json_object():
    """The request's JSON body if it is an object, else {} (a list or string never crashes a route)."""
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


@app.errorhandler(ValueError)
def bad_input(exc):
    """Validation errors (e.g. too many clips) reach the editor as a readable message."""
    return jsonify({'error': str(exc)[:600]}), 400


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


_take_cache = {}  # path -> (mtime, entry): re-reading thousands of take files on every call is slow
SUMMARY_KEYS = ('id', 'name', 'voice', 'style', 'seconds', 'rate', 'created', 'mp3', 'captions', 'lufs', 'true_peak')


def _all_takes():
    takes, seen = [], set()
    for filename in OUT.glob('*.json'):
        seen.add(filename)
        try:
            mtime = filename.stat().st_mtime
            cached = _take_cache.get(filename)
            if cached and cached[0] == mtime:
                entry = cached[1]
            else:
                entry = json.loads(filename.read_text(encoding='utf-8'))
                if not isinstance(entry, dict) or not ID_RE.fullmatch(entry.get('id', '')):
                    continue
                entry.setdefault('mp3', False)
                entry.setdefault('captions', 0)
                _take_cache[filename] = (mtime, entry)
            if (OUT / (entry['id'] + '.wav')).is_file():
                takes.append(entry)
        except (OSError, ValueError, TypeError, KeyError):
            continue
    for gone in set(_take_cache) - seen:
        _take_cache.pop(gone, None)
    takes.sort(key=lambda r: r.get('created', ''), reverse=True)
    return takes


@app.get('/api/recordings')
def recordings():
    """Takes, newest first. ?summary=1 lists every take without scripts (for the video editor);
    otherwise pages of full takes: ?offset=0&limit=50."""
    takes = _all_takes()
    if request.args.get('summary') == '1':
        def summary(t):
            segs = [{k: g.get(k) for k in ('start', 'end', 'style')} for g in t.get('segments') or [] if isinstance(g, dict)]
            return _links({**{k: t.get(k) for k in SUMMARY_KEYS}, 'segments': segs})
        return jsonify({'recordings': [summary(t) for t in takes], 'total': len(takes)})
    offset = int(engine_bounded(request.args.get('offset'), 0, 1e9, 0))
    limit = int(engine_bounded(request.args.get('limit'), 1, 500, 200))
    page = takes[offset:offset + limit]
    return jsonify({'recordings': [_links(dict(t)) for t in page], 'total': len(takes), 'offset': offset,
                    'more': offset + len(page) < len(takes)})


@app.post('/api/voices/sources')
def open_voice_source():
    """Open a recording in the voice cutter: an uploaded file (audio or video, any length; the
    first 30 minutes are used), a microphone recording, or a file from the video uploads."""
    mid = request.form.get('media')
    try:
        if mid:
            meta, path = media.get(mid), media.path(mid)
            if not path or not meta or not meta.get('has_audio'):
                return jsonify({'error': 'That upload has no sound in it.'}), 400
            return jsonify({'source': engine.library.open_source(path, meta.get('name', ''))})
        upload = request.files.get('file')
        if upload is None:
            return jsonify({'error': 'Choose a recording.'}), 400
        tmp = engine.library.sources / f'.upload-{os.urandom(8).hex()}'
        try:
            upload.save(tmp)
            if not tmp.stat().st_size:
                return jsonify({'error': 'The file is empty.'}), 400
            return jsonify({'source': engine.library.open_source(tmp, upload.filename or '')})
        finally:
            tmp.unlink(missing_ok=True)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception:  # noqa: BLE001
        app.logger.exception('Opening a voice recording failed')
        return jsonify({'error': 'That recording could not be opened. Try another file, or a WAV or MP3.'}), 400


@app.get('/api/voices/sources/<sid>/audio')
def voice_source_audio(sid):
    path = engine.library.source_path(sid)
    if path is None:
        abort(404)
    return send_file(path, mimetype='audio/wav', conditional=True)


@app.delete('/api/voices/sources/<sid>')
def voice_source_delete(sid):
    return jsonify({'ok': engine.library.delete_source(sid)})


@app.post('/api/voices/clone')
def add_cloned_voice():
    """Make a cloned voice from a recording. The uploader confirms consent.

    Either source=<id>&start=&end= (a part chosen in the cutter) or file=<upload>."""
    if request.form.get('consent') != 'yes':
        return jsonify({'error': 'Confirm you have the speaker\'s permission to use their voice.'}), 400
    if request.form.get('source'):
        try:
            voice = engine.library.add_from_source(request.form['source'], request.form.get('start'), request.form.get('end'),
                                                   request.form.get('name', ''), request.form.get('gender', ''))
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        return jsonify({'ok': True, 'voice': voice, 'cloning': engine.cloner.status()})
    upload = request.files.get('file')
    if upload is None:
        return jsonify({'error': 'Choose an audio file.'}), 400
    data = upload.read(150 * 1024 * 1024 + 1)
    if len(data) > 150 * 1024 * 1024:
        return jsonify({'error': 'That recording is too large. 10-30 seconds of audio is all that is needed.'}), 400
    try:
        voice = engine.library.add(data, upload.filename or '', request.form.get('name', ''),
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


# ================================================================ video editor
def _take_exists(rid):
    return ID_RE.fullmatch(rid or '') is not None and (OUT / f'{rid}.wav').is_file()


def _resolve_media(mid):
    """Media id or take:<id> -> (path, has_audio)."""
    if isinstance(mid, str) and mid.startswith('take:'):
        rid = mid[5:]
        return ((OUT / f'{rid}.wav'), True) if _take_exists(rid) else (None, False)
    meta = media.get(mid)
    path = media.path(mid)
    return (path, bool(meta and meta.get('has_audio'))) if path else (None, False)


@app.get('/api/video/status')
def video_status():
    t = vid.tools()
    return jsonify({'ffmpeg': bool(t['ffmpeg']), 'ffprobe': bool(t['ffprobe']), 'formats': vid.FORMATS})


@app.get('/api/media')
def media_list():
    return jsonify({'media': media.list()})


@app.post('/api/media')
def media_upload():
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'Choose files to upload.'}), 400
    added, errors = [], []
    for f in files[:50]:
        tmp = media.dir / f'.upload-{os.urandom(8).hex()}'
        try:
            f.save(tmp)
            added.append(media.add_path(tmp, f.filename or ''))
        except (ValueError, RuntimeError) as exc:
            errors.append(str(exc))
        except Exception:  # noqa: BLE001 - one bad file must not fail the whole batch
            app.logger.exception('Media import failed')
            errors.append(f'"{f.filename}" could not be imported (it may be damaged or too slow to read).')
        finally:
            tmp.unlink(missing_ok=True)
    status = 200 if added or not errors else 400
    return jsonify({'added': added, 'errors': errors, **({'error': errors[0]} if not added and errors else {})}), status


@app.get('/api/media/<mid>/file')
def media_file(mid):
    path = media.path(mid, preview=request.args.get('preview') == '1')
    if path is None:
        abort(404)
    return send_file(path, conditional=True)


@app.get('/api/media/<mid>/thumb')
def media_thumb(mid):
    path = media.thumb(mid) if vid.ID_RE.fullmatch(mid) else None
    if path is None:
        abort(404)
    return send_file(path, mimetype='image/jpeg')


@app.patch('/api/media/<mid>')
def media_rename(mid):
    meta = media.rename(mid, _json_object().get('name', ''))
    return jsonify({'media': meta}) if meta else abort(404)


@app.delete('/api/media/<mid>')
def media_delete(mid):
    return jsonify({'ok': True}) if media.delete(mid) else abort(404)


@app.get('/api/recordings/<rid>/cues')
def take_cues(rid):
    """Caption cues of a voice take, so the editor can lay them out as on-screen text."""
    path = _take_path(rid, 'srt')
    cues = []
    for block in path.read_text(encoding='utf-8').strip().split('\n\n'):
        lines = block.strip().splitlines()
        if len(lines) < 3 or '-->' not in lines[1]:
            continue
        a, b = (x.strip() for x in lines[1].split('-->'))
        to_s = lambda ts: sum(float(x) * m for x, m in zip(ts.replace(',', '.').split(':'), (3600, 60, 1)))
        cues.append({'start': round(to_s(a), 3), 'end': round(to_s(b), 3), 'text': '\n'.join(lines[2:])})
    return jsonify({'cues': cues})


@app.get('/api/video/projects')
def project_list():
    return jsonify({'projects': projects.list()})


@app.post('/api/video/projects')
def project_create():
    body = _json_object()
    body.pop('id', None)
    project = vid.sanitize_project(body, media, _take_exists)
    return jsonify({'project': projects.save(project)})


@app.get('/api/video/projects/<pid>')
def project_get(pid):
    p = projects.get(pid)
    return jsonify({'project': p}) if p else abort(404)


@app.put('/api/video/projects/<pid>')
def project_save(pid):
    if not vid.ID_RE.fullmatch(pid):
        abort(404)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({'error': 'Invalid project.'}), 400
    old = projects.get(pid) or {}
    project = vid.sanitize_project({**body, 'id': pid, 'created': old.get('created')}, media, _take_exists)
    return jsonify({'project': projects.save(project)})


@app.delete('/api/video/projects/<pid>')
def project_delete(pid):
    return jsonify({'ok': True}) if projects.delete(pid) else abort(404)


@app.post('/api/video/projects/<pid>/export')
def project_export(pid):
    body = _json_object()
    if isinstance(body.get('project'), dict):
        old = projects.get(pid) or {}
        projects.save(vid.sanitize_project({**body['project'], 'id': pid, 'created': old.get('created')}, media, _take_exists))
    project = projects.get(pid)
    if not project:
        abort(404)
    lufs = body.get('lufs')
    lufs = None if lufs in (None, '', 'off') else vid._num(lufs, -30, -8, -14)
    quality = body.get('quality') if body.get('quality') in ('high', 'standard', 'small') else 'high'
    text_images = body.get('text_images')
    try:
        job = exports.start(project, _resolve_media, text_images if isinstance(text_images, dict) else {},
                            lufs=lufs, quality=quality)
    except (ValueError, RuntimeError) as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'job': job})


@app.get('/api/video/jobs')
def job_list():
    """Running exports, optionally for one project: the editor reattaches to them after a reload."""
    return jsonify({'jobs': exports.active(request.args.get('project') or None)})


@app.get('/api/video/jobs/<jid>')
def job_status(jid):
    job = exports.get(jid)
    return jsonify({'job': job}) if job else abort(404)


@app.post('/api/video/jobs/<jid>/cancel')
def job_cancel(jid):
    return jsonify({'ok': exports.cancel(jid)})


@app.get('/api/video/exports')
def export_list():
    return jsonify({'exports': exports.exports()})


@app.get('/api/video/exports/<name>')
def export_file(name):
    path = exports.export_path(name)
    if path is None:
        abort(404)
    return send_file(path, mimetype='video/mp4', conditional=True,
                     as_attachment=request.args.get('download') == '1', download_name=name)


@app.delete('/api/video/exports/<name>')
def export_delete(name):
    path = exports.export_path(name)
    if path is None:
        abort(404)
    path.unlink()
    return jsonify({'ok': True})


def recover():
    """Tidy up after the previous run and restart an export it didn't finish."""
    media.recover()
    for jid in exports.recover(_resolve_media):
        print(f'Restarting an interrupted video export ({jid[:8]}).', flush=True)


if __name__ == '__main__':
    from werkzeug.serving import make_server
    port = int(os.environ.get('KOKORO_PORT', '8765'))
    # Bind first: if the studio is already running, say so and leave its files alone.
    try:
        server = make_server('127.0.0.1', port, app, threaded=True)  # loopback only: never exposed to the LAN
    except (OSError, SystemExit) as exc:  # werkzeug reports a busy port as SystemExit
        print(f'\nPort {port} is already in use.\n'
              f'Kokoro Studio may already be running: open http://127.0.0.1:{port}\n'
              f'Or start it on another port: KOKORO_PORT={port + 1} ./start.sh\n', flush=True)
        raise SystemExit(1)
    recover()
    print('\nKokoro Studio | http://127.0.0.1:%d\n' % port, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
