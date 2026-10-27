"""Video editor backend: media library, projects, and ffmpeg export.

Everything the editor creates lives under one folder (``creations/``):
  media/     uploaded pictures, video and music (+ thumbnails, preview proxies)
  projects/  saved timelines (JSON)
  exports/   finished MP4 files

Voice takes rendered in the Script tab (``outputs/``) are usable on the
timeline directly as media ids of the form ``take:<id>``.
"""
from __future__ import annotations

import base64
import json
import math
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

ID_RE = re.compile(r'[0-9a-f]{32}')
COLOR_RE = re.compile(r'#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?')
IMAGE_EXT = {'png', 'jpg', 'jpeg', 'jfif', 'webp', 'bmp', 'tif', 'tiff', 'avif'}
BROWSER_IMAGE = {'png', 'jpg', 'jpeg', 'webp', 'bmp', 'avif'}
IMAGE_CODECS = {'mjpeg', 'png', 'webp', 'bmp', 'tiff', 'jpegls', 'jpeg2000', 'av1'}
VIDEO_EXT = {'mp4', 'mov', 'm4v', 'webm', 'mkv', 'avi', 'gif'}
AUDIO_EXT = {'mp3', 'wav', 'm4a', 'aac', 'ogg', 'oga', 'flac', 'opus'}
BROWSER_VIDEO = {'h264', 'vp8', 'vp9', 'av1'}
VISUAL_LANES = ('V1', 'V2', 'V3', 'T1')   # bottom -> top
AUDIO_LANES = ('A1', 'A2', 'A3')
KINDS = {'video', 'image', 'text', 'color', 'audio'}
FORMATS = {'16:9': (1920, 1080), '9:16': (1080, 1920), '1:1': (1080, 1080), '4:5': (1080, 1350)}
MAX_CLIPS = 600
ANIMS = ('none', 'fade', 'rise', 'drop', 'slide-left', 'slide-right', 'slide-up', 'slide-down', 'pop', 'zoom-out')
TRANSITIONS = ('none', 'crossfade', 'dip', 'slide-left', 'slide-right', 'slide-up', 'slide-down', 'zoom')
# Motion of an animation: (dx, dy) in frame widths/heights at the start, scale at the start, alpha share.
MOTION = {'fade': (0, 0, 1, 1), 'rise': (0, 0.06, 1, 1), 'drop': (0, -0.06, 1, 1), 'slide-left': (1, 0, 1, 0),
          'slide-right': (-1, 0, 1, 0), 'slide-up': (0, 1, 1, 0), 'slide-down': (0, -1, 1, 0), 'pop': (0, 0, 0.5, 0.5),
          'zoom-out': (0, 0, 1.3, 1)}
TRANSITION_ANIM = {'crossfade': 'fade', 'slide-left': 'slide-left', 'slide-right': 'slide-right', 'slide-up': 'slide-up',
                   'slide-down': 'slide-down', 'zoom': 'zoom-out'}
MAX_PROJECT_SECONDS = 60 * 60


def now():
    return datetime.now(timezone.utc).isoformat()


def tools():
    return {'ffmpeg': shutil.which('ffmpeg'), 'ffprobe': shutil.which('ffprobe')}


def _num(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, v)) if math.isfinite(v) else default


# ---------------------------------------------------------------- probing
def jpeg_orientation(path):
    """EXIF orientation (1-8) of a JPEG, or 1. Phones store photos sideways plus this flag."""
    try:
        with open(path, 'rb') as fh:
            data = fh.read(256 * 1024)
    except OSError:
        return 1
    if not data.startswith(b'\xff\xd8'):
        return 1
    i = 2
    while i + 4 < len(data):
        if data[i] != 0xFF:
            return 1
        marker, size = data[i + 1], int.from_bytes(data[i + 2:i + 4], 'big')
        if marker == 0xE1 and data[i + 4:i + 10] == b'Exif\x00\x00':
            t = i + 10
            order = 'little' if data[t:t + 2] == b'II' else 'big'
            ifd = t + int.from_bytes(data[t + 4:t + 8], order)
            if ifd + 2 > len(data):
                return 1
            for k in range(int.from_bytes(data[ifd:ifd + 2], order)):
                e = ifd + 2 + 12 * k
                if int.from_bytes(data[e:e + 2], order) == 0x0112:
                    v = int.from_bytes(data[e + 8:e + 10], order)
                    return v if 1 <= v <= 8 else 1
            return 1
        if marker in (0xDA, 0xD9):
            return 1
        i += 2 + size
    return 1


ORIENT_FILTERS = {2: 'hflip', 3: 'hflip,vflip', 4: 'vflip', 5: 'transpose=0', 6: 'transpose=1', 7: 'transpose=3',
                  8: 'transpose=2'}


def probe(path):
    """Duration, size and streams of a media file (ffprobe)."""
    exe = tools()['ffprobe']
    if not exe:
        raise RuntimeError('ffprobe (part of ffmpeg) is required for the video editor: sudo apt install ffmpeg')
    proc = subprocess.run([exe, '-v', 'error', '-print_format', 'json', '-show_streams', '-show_format', str(path)],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise ValueError(f'"{Path(path).name}" could not be read as audio, video or a picture. '
                         'It may be damaged, or a format ffmpeg on this computer cannot open.')
    data = json.loads(proc.stdout or '{}')
    streams = data.get('streams', [])
    video = next((s for s in streams if s.get('codec_type') == 'video'
                  and not s.get('disposition', {}).get('attached_pic')), None)
    audio = next((s for s in streams if s.get('codec_type') == 'audio'), None)
    duration = _num(data.get('format', {}).get('duration'), 0, 1e7, 0.0)
    info = {'duration': round(duration, 3), 'has_audio': audio is not None, 'has_video': video is not None,
            'format': data.get('format', {}).get('format_name', ''),
            'codec': video.get('codec_name') if video else None, 'width': 0, 'height': 0,
            'frames': int(video.get('nb_frames') or 0) if video and str(video.get('nb_frames', '')).isdigit() else 0}
    if video:
        w, h = int(video.get('width') or 0), int(video.get('height') or 0)
        rotation = 0
        for side in video.get('side_data_list', []) or []:
            if 'rotation' in side:
                rotation = int(float(side['rotation']))
        rotation = rotation or int(float(video.get('tags', {}).get('rotate', 0) or 0))
        if abs(rotation) % 180 == 90:
            w, h = h, w
        info.update(width=w, height=h)
    return info


def compute_peaks(path, per_second=40):
    """Loudness outline of any audio/video file for timeline waveforms (0-100 per step)."""
    exe = tools()['ffmpeg']
    if not exe:
        return {'per_second': per_second, 'peaks': []}
    proc = subprocess.run([exe, '-v', 'error', '-i', str(path), '-vn', '-ac', '1', '-ar', '8000', '-f', 's16le', 'pipe:1'],
                          capture_output=True, timeout=600)
    import numpy as np
    pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    step = max(1, 8000 // per_second)
    n = len(pcm) // step
    if not n:
        return {'per_second': per_second, 'peaks': []}
    blocks = np.abs(pcm[: n * step]).reshape(n, step).max(axis=1)
    top = float(blocks.max()) or 1.0
    return {'per_second': per_second, 'peaks': [int(round(100 * (b / top) ** 0.7)) for b in blocks]}


def cached_peaks(path, cache_path):
    path, cache_path = Path(path), Path(cache_path)
    try:
        if cache_path.is_file() and cache_path.stat().st_mtime >= path.stat().st_mtime:
            return json.loads(cache_path.read_text())
    except (OSError, ValueError):
        pass
    data = compute_peaks(path)
    try:
        cache_path.write_text(json.dumps(data))
    except OSError:
        pass
    return data


# ---------------------------------------------------------------- media
class MediaLibrary:
    def __init__(self, folder):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _meta_path(self, mid):
        return self.dir / f'{mid}.json'

    def get(self, mid):
        if not isinstance(mid, str) or not ID_RE.fullmatch(mid):
            return None
        try:
            return json.loads(self._meta_path(mid).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None

    def list(self):
        items = []
        for p in self.dir.glob('*.json'):
            try:
                m = json.loads(p.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if (self.dir / m.get('file', '')).is_file():
                items.append(m)
        items.sort(key=lambda m: m.get('created', ''), reverse=True)
        return items

    def path(self, mid, preview=False):
        m = self.get(mid)
        if not m:
            return None
        if preview and m.get('proxy') == 'ready' and (self.dir / f'{mid}.proxy.mp4').is_file():
            return self.dir / f'{mid}.proxy.mp4'
        p = self.dir / m['file']
        return p if p.is_file() else None

    def thumb(self, mid):
        p = self.dir / f'{mid}.thumb.jpg'
        return p if p.is_file() else None

    def add(self, data: bytes, filename: str):
        tmp = self.dir / f'.upload-{uuid.uuid4().hex}'
        tmp.write_bytes(data or b'')
        return self.add_path(tmp, filename)

    def add_path(self, src: Path, filename: str):
        """Import an uploaded file already saved at `src` (moved into the library)."""
        src = Path(src)
        try:
            return self._add(src, filename)
        finally:
            src.unlink(missing_ok=True)

    def _add(self, src: Path, filename: str):
        ext = Path(filename or '').suffix.lower().lstrip('.')
        if ext in ('heic', 'heif'):
            raise ValueError(f'"{filename}" is an iPhone HEIC photo, which can\'t be read here. On the phone choose '
                             '"Most Compatible" (Settings > Camera > Formats), or share it as JPG.')
        kind = 'image' if ext in IMAGE_EXT else 'video' if ext in VIDEO_EXT else 'audio' if ext in AUDIO_EXT else None
        if not tools()['ffprobe']:
            raise RuntimeError('ffmpeg is not installed, so pictures and videos can\'t be read. '
                               'Install it with:  sudo apt install ffmpeg  and restart the studio.')
        size = src.stat().st_size if src.exists() else 0
        if not size:
            raise ValueError(f'"{filename}" is empty.')
        mid = uuid.uuid4().hex
        safe_ext = ext if re.fullmatch(r'[a-z0-9]{1,5}', ext or '') else 'bin'
        stored = f'{mid}.{safe_ext}'
        path = self.dir / stored
        shutil.move(str(src), path)
        try:
            info = probe(path)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        if kind is None:  # unknown or missing extension: decide from the content
            is_still = info['has_video'] and (info['codec'] in IMAGE_CODECS or '_pipe' in info['format'] or
                                             info['format'].startswith('image2')) and info['frames'] <= 1
            kind = 'image' if is_still else 'video' if info['has_video'] else 'audio' if info['has_audio'] else None
            if kind is None:
                path.unlink(missing_ok=True)
                raise ValueError(f'"{filename}" is not a picture, video or audio file.')
        if kind == 'image':
            path, stored, info = self._normalise_image(path, mid, safe_ext, info)
        if kind in ('video', 'audio') and not info['duration']:
            # Browser recordings (MediaRecorder WebM) often carry no duration; a copy-remux adds it.
            fixed = path.with_name(f'{mid}.fixed.{safe_ext}')
            subprocess.run([tools()['ffmpeg'] or 'ffmpeg', '-y', '-loglevel', 'error', '-i', str(path), '-c', 'copy', str(fixed)],
                           capture_output=True, timeout=600)
            if fixed.is_file() and fixed.stat().st_size:
                fixed.replace(path)
                info = probe(path)
            fixed.unlink(missing_ok=True)
        if kind == 'image' and not info['has_video']:
            path.unlink(missing_ok=True)
            raise ValueError(f'"{filename}" could not be read as a picture.')
        if kind == 'video' and not info['has_video']:
            kind = 'audio' if info['has_audio'] else kind
        if kind == 'audio' and not info['has_audio']:
            path.unlink(missing_ok=True)
            raise ValueError(f'"{filename}" has no audio in it.')
        meta = {'id': mid, 'name': Path(filename).stem[:80] or 'Untitled', 'kind': kind, 'file': stored,
                'ext': Path(stored).suffix.lstrip('.'), 'created': now(), 'size': size, **info}
        if kind == 'image':
            meta['duration'] = None
        needs_proxy = kind == 'video' and (ext not in ('mp4', 'webm', 'm4v') or info['codec'] not in BROWSER_VIDEO)
        meta['proxy'] = 'pending' if needs_proxy else None
        self._meta_path(mid).write_text(json.dumps(meta, indent=2), encoding='utf-8')
        if kind in ('image', 'video'):
            self._make_thumb(meta)
        if needs_proxy:
            threading.Thread(target=self._make_proxy, args=(mid,), daemon=True).start()
        return self.get(mid)

    def _normalise_image(self, path, mid, ext, info):
        """Make a picture upright and browser-viewable, so preview and export agree."""
        orient = jpeg_orientation(path) if ext in ('jpg', 'jpeg', 'jfif', 'bin') else 1
        if orient == 1 and ext in BROWSER_IMAGE:
            return path, path.name, info
        exe = tools()['ffmpeg']
        out_ext = 'jpg' if info.get('codec') == 'mjpeg' else 'png'
        out = self.dir / f'{mid}.{out_ext}'
        tmp = self.dir / f'.{mid}.conv.{out_ext}'
        vf = ORIENT_FILTERS.get(orient)
        cmd = [exe, '-y', '-loglevel', 'error', '-noautorotate', '-i', str(path), '-frames:v', '1', '-map_metadata', '-1']
        if vf:
            cmd += ['-vf', vf]
        if out_ext == 'jpg':
            cmd += ['-q:v', '2']
        proc = subprocess.run(cmd + [str(tmp)], capture_output=True, timeout=120)
        if proc.returncode != 0 or not tmp.is_file():
            tmp.unlink(missing_ok=True)
            if ext in BROWSER_IMAGE:
                return path, path.name, info  # keep the original rather than fail
            path.unlink(missing_ok=True)
            raise ValueError('That picture format could not be converted. Save it as JPG or PNG and try again.')
        if path != out:
            path.unlink(missing_ok=True)
        tmp.replace(out)
        return out, out.name, {**probe(out), 'format': info.get('format', '')}

    def _make_thumb(self, meta):
        exe = tools()['ffmpeg']
        if not exe:
            return
        src = self.dir / meta['file']
        seek = ['-ss', f'{min(1.0, (meta.get("duration") or 0) / 3):.2f}'] if meta['kind'] == 'video' else []
        subprocess.run([exe, '-y', '-loglevel', 'error', *seek, '-i', str(src), '-frames:v', '1',
                        '-vf', 'scale=320:-2', str(self.dir / f'{meta["id"]}.thumb.jpg')], capture_output=True, timeout=60)

    def _make_proxy(self, mid):
        """Browser-playable preview copy (e.g. for iPhone HEVC .mov). Export still uses the original."""
        meta = self.get(mid)
        exe = tools()['ffmpeg']
        status = 'failed'
        if meta and exe:
            out = self.dir / f'{mid}.proxy.mp4'
            proc = subprocess.run([exe, '-y', '-loglevel', 'error', '-i', str(self.dir / meta['file']),
                                   '-vf', "scale=-2:'min(720,ih)'", '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '26',
                                   '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(out)],
                                  capture_output=True, timeout=3600)
            status = 'ready' if proc.returncode == 0 and out.is_file() else 'failed'
        meta = self.get(mid)
        if meta:
            meta['proxy'] = status
            self._meta_path(mid).write_text(json.dumps(meta, indent=2), encoding='utf-8')

    def peaks(self, mid):
        meta, path = self.get(mid), self.path(mid)
        if not meta or not path or meta['kind'] == 'image':
            return None
        return cached_peaks(path, self.dir / f'{mid}.peaks.json')

    def freeze_frame(self, mid, t):
        """Save the video frame at `t` seconds as a new picture in the library."""
        meta, path = self.get(mid), self.path(mid)
        if not meta or meta['kind'] != 'video' or not path:
            raise ValueError('Freeze frame works on video clips.')
        t = max(0.0, min(float(t), max(0.0, (meta.get('duration') or 0) - 0.05)))
        tmp = self.dir / f'.frame-{uuid.uuid4().hex}.png'
        proc = subprocess.run([tools()['ffmpeg'] or 'ffmpeg', '-y', '-loglevel', 'error', '-ss', f'{t:.3f}', '-i', str(path),
                               '-frames:v', '1', str(tmp)], capture_output=True, timeout=120)
        if proc.returncode != 0 or not tmp.is_file():
            tmp.unlink(missing_ok=True)
            raise ValueError('Could not grab that frame.')
        return self.add_path(tmp, f'{meta["name"][:60]} (frame {t:.1f}s).png')

    def rename(self, mid, name):
        meta = self.get(mid)
        if not meta:
            return None
        meta['name'] = ' '.join(str(name).split())[:80] or meta['name']
        self._meta_path(mid).write_text(json.dumps(meta, indent=2), encoding='utf-8')
        return meta

    def delete(self, mid):
        meta = self.get(mid)
        if not meta:
            return False
        for p in (self.dir / meta['file'], self.dir / f'{mid}.thumb.jpg', self.dir / f'{mid}.proxy.mp4', self._meta_path(mid)):
            p.unlink(missing_ok=True)
        return True


# ---------------------------------------------------------------- projects
def sanitize_text(t):
    t = t if isinstance(t, dict) else {}
    font = t.get('font') if t.get('font') in ('sans', 'serif', 'display', 'mono', 'rounded') else 'sans'
    bg = t.get('bg') if isinstance(t.get('bg'), str) and COLOR_RE.fullmatch(t.get('bg') or '') else ''
    return {
        'content': str(t.get('content', ''))[:2000], 'font': font,
        'size': _num(t.get('size'), 0.01, 0.4, 0.07), 'weight': int(_num(t.get('weight'), 300, 900, 700)),
        'color': t.get('color') if isinstance(t.get('color'), str) and COLOR_RE.fullmatch(t['color']) else '#ffffff',
        'bg': bg, 'align': t.get('align') if t.get('align') in ('left', 'center', 'right') else 'center',
        'italic': bool(t.get('italic')), 'shadow': bool(t.get('shadow', True)), 'upper': bool(t.get('upper')),
        'outline': bool(t.get('outline')),
        'effect': t.get('effect') if t.get('effect') in ('none', 'shadow', 'lift', 'hollow', 'splice', 'outline', 'echo',
                                                          'glitch', 'neon', 'background') else None,
        'fx_color': t.get('fx_color') if isinstance(t.get('fx_color'), str) and COLOR_RE.fullmatch(t['fx_color']) else '#000000',
    }


def sanitize_anim(a, allowed=ANIMS):
    a = a if isinstance(a, dict) else {}
    kind = a.get('type') if a.get('type') in allowed else 'none'
    return {'type': kind, 'dur': round(_num(a.get('dur'), 0.1, 5, 0.6), 3)}


def sanitize_project(p, media: MediaLibrary | None = None, take_exists=None):
    p = p if isinstance(p, dict) else {}
    fmt = p.get('format') if p.get('format') in FORMATS else '16:9'
    w, h = FORMATS[fmt]
    out = {
        'id': p.get('id') if isinstance(p.get('id'), str) and ID_RE.fullmatch(p.get('id')) else uuid.uuid4().hex,
        'name': ' '.join(str(p.get('name') or 'Untitled video').split())[:80],
        'format': fmt, 'width': w, 'height': h, 'fps': int(_num(p.get('fps'), 15, 60, 30)),
        'background': p.get('background') if isinstance(p.get('background'), str) and COLOR_RE.fullmatch(p['background'] or '') else '#000000',
        'clips': [], 'created': p.get('created') or now(), 'updated': now(),
    }
    seen = set()
    for c in (p.get('clips') or [])[:MAX_CLIPS]:
        if not isinstance(c, dict) or c.get('kind') not in KINDS:
            continue
        kind = c['kind']
        cid = str(c.get('id') or uuid.uuid4().hex[:10])[:24]
        if not re.fullmatch(r'[A-Za-z0-9_-]+', cid) or cid in seen:
            cid = uuid.uuid4().hex[:10]
        seen.add(cid)
        lane = c.get('lane')
        lanes = AUDIO_LANES if kind == 'audio' else VISUAL_LANES
        if lane not in lanes:
            lane = 'A1' if kind == 'audio' else ('T1' if kind == 'text' else 'V1')
        clip = {
            'id': cid, 'kind': kind, 'lane': lane, 'media': None, 'name': str(c.get('name', ''))[:80],
            'start': round(_num(c.get('start'), 0, MAX_PROJECT_SECONDS, 0), 3),
            'duration': round(_num(c.get('duration'), 0.1, MAX_PROJECT_SECONDS, 3), 3),
            'in': round(_num(c.get('in'), 0, 1e6, 0), 3),
            'x': _num(c.get('x'), -2, 2, 0), 'y': _num(c.get('y'), -2, 2, 0),
            'w': _num(c.get('w'), 0.01, 4, 1), 'h': _num(c.get('h'), 0.01, 4, 1),
            'fit': 'contain' if c.get('fit') == 'contain' else 'cover',
            'opacity': _num(c.get('opacity'), 0, 1, 1), 'fade_in': _num(c.get('fade_in'), 0, 10, 0),
            'fade_out': _num(c.get('fade_out'), 0, 10, 0), 'zoom': _num(c.get('zoom'), 0, 0.5, 0),
            'volume': _num(c.get('volume'), 0, 2, 1), 'muted': bool(c.get('muted')),
            'speed': round(_num(c.get('speed'), 0.25, 2, 1), 3), 'loop': bool(c.get('loop')),
        }
        if kind != 'audio':
            clip['anim_in'] = sanitize_anim(c.get('anim_in'))
            clip['anim_out'] = sanitize_anim(c.get('anim_out'))
            clip['transition'] = sanitize_anim(c.get('transition'), TRANSITIONS)
        if kind in ('video', 'image', 'audio'):
            mid = c.get('media')
            if isinstance(mid, str) and mid.startswith('take:') and ID_RE.fullmatch(mid[5:]):
                if kind != 'audio' or (take_exists and not take_exists(mid[5:])):
                    continue
            elif media is not None:
                meta = media.get(mid)
                if not meta:
                    continue
                if kind != meta['kind'] and not (kind == 'audio' and meta['kind'] == 'video' and meta.get('has_audio')):
                    continue
                if meta.get('duration') and kind in ('video', 'audio'):
                    clip['in'] = min(clip['in'], max(0.0, meta['duration'] - 0.1))
                    if kind == 'audio' and not clip['loop']:  # video may run past its end (last frame holds)
                        clip['duration'] = round(min(clip['duration'], (meta['duration'] - clip['in']) / clip['speed']), 3)
            elif not (isinstance(mid, str) and ID_RE.fullmatch(mid)):
                continue
            clip['media'] = mid
        if kind == 'text':
            clip['text'] = sanitize_text(c.get('text'))
        if kind == 'color':
            col = c.get('color')
            clip['color'] = col if isinstance(col, str) and COLOR_RE.fullmatch(col) else '#000000'
        out['clips'].append(clip)
    return out


class ProjectStore:
    def __init__(self, folder):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)

    def list(self):
        items = []
        for p in self.dir.glob('*.json'):
            try:
                d = json.loads(p.read_text(encoding='utf-8'))
                items.append({'id': d['id'], 'name': d['name'], 'format': d['format'], 'updated': d.get('updated'),
                              'clips': len(d.get('clips', [])), 'duration': project_duration(d)})
            except (OSError, ValueError, KeyError):
                continue
        items.sort(key=lambda d: d.get('updated') or '', reverse=True)
        return items

    def get(self, pid):
        if not isinstance(pid, str) or not ID_RE.fullmatch(pid):
            return None
        try:
            return json.loads((self.dir / f'{pid}.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None

    def save(self, project):
        (self.dir / f'{project["id"]}.json').write_text(json.dumps(project, indent=1), encoding='utf-8')
        return project

    def delete(self, pid):
        p = self.dir / f'{pid}.json'
        if ID_RE.fullmatch(pid or '') and p.is_file():
            p.unlink()
            return True
        return False


def project_duration(p):
    return round(max([c['start'] + c['duration'] for c in p.get('clips', [])] or [0.0]), 3)


# ---------------------------------------------------------------- motion plan
def motion_plan(project):
    """Per visual clip: extra tail (s), entrance and exit animation.

    A transition on clip B blends from the clip A that ends where B starts on
    the same track: A is held on screen under B for the transition's length
    while B animates in. "Dip" fades A out and B in through the background.
    static/video.js has the same logic for the preview.
    """
    visual = [c for c in project['clips'] if c['kind'] != 'audio']
    plan = {}
    for c in visual:
        ai, ao = c.get('anim_in') or {}, c.get('anim_out') or {}
        plan[c['id']] = {'ext': 0.0,
                         'in': (ai['type'], ai['dur']) if ai.get('type', 'none') != 'none' else None,
                         'out': (ao['type'], ao['dur']) if ao.get('type', 'none') != 'none' else None}
    for b in visual:
        tr = b.get('transition') or {}
        if tr.get('type', 'none') == 'none':
            continue
        prev = [a for a in visual if a is not b and a['lane'] == b['lane'] and abs(a['start'] + a['duration'] - b['start']) < 0.06]
        if not prev:
            continue
        a = max(prev, key=lambda x: x['start'])
        d = min(tr['dur'], b['duration'] * 0.9)
        if tr['type'] == 'dip':
            d = min(d, a['duration'] * 0.9)
            plan[a['id']]['out'] = ('fade', d / 2)
            plan[b['id']]['in'] = ('fade', d / 2)
        else:
            plan[a['id']]['ext'] = max(plan[a['id']]['ext'], d)
            plan[b['id']]['in'] = (TRANSITION_ANIM[tr['type']], d)
    return plan


def _motion_exprs(start, rdur, anim_in, anim_out):
    """ffmpeg expressions for (dx, dy, scale) given global time t (overlay) / local t (scale)."""
    dx, dy, sc_local = [], [], []
    if anim_in:
        mx, my, ms, _ = MOTION[anim_in[0]]
        rem = f'pow(1-clip((t-{start:.3f})/{anim_in[1]:.3f},0,1),3)'
        rem_local = f'pow(1-clip(t/{anim_in[1]:.3f},0,1),3)'
        if mx: dx.append(f'{mx}*{rem}')
        if my: dy.append(f'{my}*{rem}')
        if ms != 1: sc_local.append(f'(1+({ms - 1:.3f})*{rem_local})')
    if anim_out:
        mx, my, ms, _ = MOTION[anim_out[0]]
        rem = f'pow(1-clip(({start + rdur:.3f}-t)/{anim_out[1]:.3f},0,1),3)'
        rem_local = f'pow(1-clip(({rdur:.3f}-t)/{anim_out[1]:.3f},0,1),3)'
        if mx: dx.append(f'{-mx}*{rem}')
        if my: dy.append(f'{-my}*{rem}')
        if ms != 1: sc_local.append(f'(1+({ms - 1:.3f})*{rem_local})')
    return '+'.join(dx) or '0', '+'.join(dy) or '0', '*'.join(sc_local) or None


# ---------------------------------------------------------------- export
def _even(n):
    n = max(2, int(round(n)))
    return n + (n % 2)



def build_command(project, resolve, text_pngs, out_path, *, lufs=None, encoder='libx264', quality='high'):
    """Return (ffmpeg args, total seconds, warnings).

    resolve(media_id) -> (path, has_audio) for video/image/audio media.
    text_pngs: clip id -> path of a full-frame transparent PNG for text clips.
    """
    W, H, fps = project['width'], project['height'], project['fps']
    total = max(0.1, project_duration(project))
    args, fc, warnings = ['-y', '-hide_banner', '-loglevel', 'error', '-nostats', '-progress', 'pipe:1'], [], []
    n_in = 0
    fc.append(f'color=c=0x{project["background"].lstrip("#")[:6]}:s={W}x{H}:r={fps}:d={total:.3f},format=rgba[base0]')
    base = 'base0'
    audio_labels = []
    order = {lane: i for i, lane in enumerate(VISUAL_LANES)}
    visual = sorted([c for c in project['clips'] if c['kind'] != 'audio'], key=lambda c: (order[c['lane']], c['start']))
    plan = motion_plan(project)

    def add_input(*a):
        nonlocal n_in
        args.extend(a)
        n_in += 1
        return n_in - 1

    for k, c in enumerate(visual):
        mp = plan.get(c['id'], {'ext': 0.0, 'in': None, 'out': None})
        start, ext = c['start'], mp['ext']
        dur = c['duration'] + ext  # held longer when the next clip transitions in over it
        needs_scale = any(a and MOTION[a[0]][2] != 1 for a in (mp['in'], mp['out']))
        text_box = None
        bw, bh = _even(c['w'] * W), _even(c['h'] * H)
        bx, by = int(round(c['x'] * W)), int(round(c['y'] * H))
        chain = []
        opacity = c['opacity']
        if c['kind'] == 'color':
            col = c['color'].lstrip('#')
            opacity *= int(col[6:8], 16) / 255 if len(col) == 8 else 1.0  # lavfi colour has no alpha
            idx = add_input('-f', 'lavfi', '-t', f'{dur:.3f}', '-i', f'color=c=0x{col[:6]}:s={bw}x{bh}:r={fps}')
            chain.append('format=rgba')
        elif c['kind'] == 'text':
            png = text_pngs.get(c['id'])
            if not png:
                warnings.append(f'Text "{c["text"]["content"][:30]}" was skipped (no rendered image).')
                continue
            idx = add_input('-loop', '1', '-framerate', str(fps), '-t', f'{dur:.3f}', '-i', str(png))
            bx, by = 0, 0  # text images are full frame with the text already placed
            chain.append('format=rgba')
            if needs_scale:  # crop to the text box (plus room for glows) so it scales around itself
                m = int(c['text']['size'] * min(W, H) + 0.25 * bh)
                x0, y0 = max(0, int(c['x'] * W) - m), max(0, int(c['y'] * H) - m)
                x1, y1 = min(W, int((c['x'] + c['w']) * W) + m), min(H, int((c['y'] + c['h']) * H) + m)
                cw, ch = max(2, (x1 - x0) // 2 * 2), max(2, (y1 - y0) // 2 * 2)
                chain.append(f'crop={cw}:{ch}:{x0}:{y0}')
                bx, by, text_box = x0, y0, (cw, ch)
        else:
            path, has_audio = resolve(c['media'])
            if path is None:
                warnings.append(f'Missing media for "{c.get("name") or c["kind"]}" was skipped.')
                continue
            if c['kind'] == 'image' and c['zoom'] > 0:
                idx = add_input('-i', str(path))
                frames = max(1, int(round(dur * fps)))
                chain.append(f'scale={bw * 2}:{bh * 2}:force_original_aspect_ratio=increase,crop={bw * 2}:{bh * 2},setsar=1')
                chain.append(f"zoompan=z='1+{c['zoom']:.4f}*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                             f':d={frames}:s={bw}x{bh}:fps={fps}')
            else:
                if c['kind'] == 'image':
                    idx = add_input('-loop', '1', '-framerate', str(fps), '-t', f'{dur:.3f}', '-i', str(path))
                else:
                    sp = c.get('speed', 1.0)
                    loop = ['-stream_loop', '-1'] if c.get('loop') else []
                    idx = add_input('-ss', f'{c["in"]:.3f}', *loop, '-t', f'{dur * sp + 0.05:.3f}', '-i', str(path))
                    if abs(sp - 1) > 1e-3:
                        chain.append(f'setpts=(PTS-STARTPTS)/{sp:.4f}')
                    # Past the end of the source (dragged longer, or held under a transition): freeze the last frame.
                    chain.append(f'tpad=stop_mode=clone:stop_duration={dur:.3f},trim=duration={dur:.3f}')
                if c['fit'] == 'contain':
                    chain.append(f'scale={bw}:{bh}:force_original_aspect_ratio=decrease,format=rgba,'
                                 f'pad={bw}:{bh}:(ow-iw)/2:(oh-ih)/2:color=black@0')
                else:
                    chain.append(f'scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh}')
                chain.append('setsar=1')
            chain.append(f'fps={fps},format=rgba')
            if c['kind'] == 'video' and has_audio and not c['muted'] and c['volume'] > 0:
                audio_labels.append(_audio_chain(fc, f'{idx}:a', c, len(audio_labels)))
        if opacity < 0.999:
            chain.append(f'colorchannelmixer=aa={opacity:.3f}')
        if c['fade_in'] > 0:
            chain.append(f'fade=t=in:st=0:d={min(c["fade_in"], dur):.3f}:alpha=1')
        if c['fade_out'] > 0:
            fo = min(c['fade_out'], c['duration'])
            chain.append(f'fade=t=out:st={c["duration"] - fo:.3f}:d={fo:.3f}:alpha=1')
        for which, anim in (('in', mp['in']), ('out', mp['out'])):
            if anim and MOTION[anim[0]][3] > 0:
                d = min(anim[1] * MOTION[anim[0]][3], dur)
                st_ = 0 if which == 'in' else dur - d
                chain.append(f'fade=t={which}:st={st_:.3f}:d={d:.3f}:alpha=1')
        dx, dy, scale = _motion_exprs(start, dur, mp['in'], mp['out'])
        ow, oh = text_box or ((bw, bh) if c['kind'] != 'text' else (W, H))
        if scale:
            pw, ph = _even(ow * 1.32), _even(oh * 1.32)
            chain.append(f"scale=w='trunc(max(2,{ow}*{scale})/2)*2':h='trunc(max(2,{oh}*{scale})/2)*2':eval=frame")
            chain.append(f'pad=w={pw}:h={ph}:x=(ow-iw)/2:y=(oh-ih)/2:color=black@0:eval=frame')
        chain.append(f'setpts=PTS-STARTPTS+{start:.3f}/TB')
        fc.append(f'[{idx}:v]' + ','.join(chain) + f'[v{k}]')
        nxt = f'base{k + 1}'
        x = f"{bx}+{W}*({dx})-(w-{ow})/2" if (scale or dx != '0') else str(bx)
        y = f"{by}+{H}*({dy})-(h-{oh})/2" if (scale or dy != '0') else str(by)
        fc.append(f"[{base}][v{k}]overlay=x='{x}':y='{y}':eof_action=pass:format=auto:"
                  f"enable='between(t,{start:.3f},{start + dur:.3f})'[{nxt}]")
        base = nxt
    fc.append(f'[{base}]format=yuv420p[vout]')

    for c in [c for c in project['clips'] if c['kind'] == 'audio']:
        if c['muted'] or c['volume'] <= 0:
            continue
        path, has_audio = resolve(c['media'])
        if path is None or not has_audio:
            warnings.append(f'Missing audio for "{c.get("name") or "audio"}" was skipped.')
            continue
        loop = ['-stream_loop', '-1'] if c.get('loop') else []
        idx = add_input('-ss', f'{c["in"]:.3f}', *loop, '-t', f'{c["duration"] * c.get("speed", 1.0) + 0.05:.3f}', '-i', str(path))
        audio_labels.append(_audio_chain(fc, f'{idx}:a', c, len(audio_labels)))

    if audio_labels:
        mix = ''.join(f'[{a}]' for a in audio_labels)
        tail = f'amix=inputs={len(audio_labels)}:normalize=0:dropout_transition=0' if len(audio_labels) > 1 else 'anull'
        post = f',loudnorm=I={lufs}:TP=-1.5:LRA=11,aresample=48000' if lufs is not None else ''
        fc.append(f'{mix}{tail},apad,atrim=0:{total:.3f}{post}[aout]')
    else:
        idx = add_input('-f', 'lavfi', '-t', f'{total:.3f}', '-i', 'anullsrc=r=48000:cl=stereo')
        fc.append(f'[{idx}:a]anull[aout]')

    crf = {'high': '18', 'standard': '21', 'small': '25'}.get(quality, '20')
    venc = ['-c:v', encoder]
    venc += ['-preset', 'medium', '-crf', crf] if encoder == 'libx264' else ['-q:v', '3']
    args += ['-filter_complex', ';'.join(fc), '-map', '[vout]', '-map', '[aout]', '-r', str(fps), *venc,
             '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-movflags', '+faststart',
             '-t', f'{total:.3f}', str(out_path)]
    return args, total, warnings


def _audio_chain(fc, src, c, n):
    dur = c['duration']
    sp = c.get('speed', 1.0)
    tempo = []
    if abs(sp - 1) > 1e-3:  # atempo works in 0.5-2x steps
        tempo = [f'atempo={sp:.4f}'] if sp >= 0.5 else ['atempo=0.5', f'atempo={sp / 0.5:.4f}']
    parts = ['asetpts=PTS-STARTPTS', *tempo, f'atrim=0:{dur:.3f}', 'asetpts=PTS-STARTPTS', 'aresample=48000',
             'aformat=sample_fmts=fltp:channel_layouts=stereo', f'volume={c["volume"]:.3f}']
    if c['fade_in'] > 0:
        parts.append(f'afade=t=in:st=0:d={min(c["fade_in"], dur):.3f}')
    if c['fade_out'] > 0:
        fo = min(c['fade_out'], dur)
        parts.append(f'afade=t=out:st={dur - fo:.3f}:d={fo:.3f}')
    ms = int(round(c['start'] * 1000))
    if ms > 0:
        parts.append(f'adelay={ms}:all=1')
    label = f'a{n}'
    fc.append(f'[{src}]' + ','.join(parts) + f'[{label}]')
    return label


def decode_png(data_url, max_bytes=20 * 1024 * 1024):
    if not isinstance(data_url, str) or not data_url.startswith('data:image/png;base64,'):
        return None
    raw = base64.b64decode(data_url.split(',', 1)[1], validate=False)
    if len(raw) > max_bytes or not raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return None
    return raw


class ExportJobs:
    """Runs exports in the background and reports progress."""

    def __init__(self, folder):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.jobs = {}
        self.lock = threading.Lock()
        self._encoder = None

    def encoder(self):
        if self._encoder is None:
            exe = tools()['ffmpeg']
            out = subprocess.run([exe, '-hide_banner', '-encoders'], capture_output=True, text=True).stdout if exe else ''
            self._encoder = 'libx264' if 'libx264' in out else 'mpeg4'
        return self._encoder

    def start(self, project, resolve, text_images, *, lufs=None, quality='high'):
        exe = tools()['ffmpeg']
        if not exe:
            raise RuntimeError('ffmpeg is required to export video: sudo apt install ffmpeg')
        if not project['clips']:
            raise ValueError('The timeline is empty. Add some media first.')
        with self.lock:
            if any(j['status'] == 'running' for j in self.jobs.values()):
                raise ValueError('An export is already running. Wait for it to finish.')
        job_id = uuid.uuid4().hex
        tmp = Path(tempfile.mkdtemp(prefix='kokoro-export-'))
        pngs = {}
        for cid, data in (text_images or {}).items():
            raw = decode_png(data)
            if raw and re.fullmatch(r'[A-Za-z0-9_-]+', str(cid)):
                (tmp / f'{cid}.png').write_bytes(raw)
                pngs[str(cid)] = tmp / f'{cid}.png'
        slug = re.sub(r'[^A-Za-z0-9]+', '-', project['name']).strip('-').lower()[:40] or 'video'
        name = f'{slug}-{datetime.now().strftime("%Y%m%d-%H%M%S")}.mp4'
        out = self.dir / name
        args, total, warnings = build_command(project, resolve, pngs, self.dir / f'.{name}.part.mp4',
                                              lufs=lufs, encoder=self.encoder(), quality=quality)
        job = {'id': job_id, 'status': 'running', 'progress': 0.0, 'file': name, 'error': None,
               'warnings': warnings, 'total': total, 'project': project['id'], 'proc': None}
        with self.lock:
            self.jobs[job_id] = job
        threading.Thread(target=self._run, args=(job, [exe, *args], out, tmp), daemon=True).start()
        return self.public(job)

    def _run(self, job, cmd, out, tmp):
        part = out.parent / f'.{out.name}.part.mp4'
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            job['proc'] = proc
            for line in proc.stdout:
                if line.startswith('out_time_us=') or line.startswith('out_time_ms='):
                    try:
                        job['progress'] = min(0.99, int(line.split('=')[1]) / 1e6 / job['total'])
                    except ValueError:
                        pass
            err = proc.stderr.read()
            code = proc.wait()
            if job['status'] == 'cancelled':
                part.unlink(missing_ok=True)
            elif code == 0 and part.is_file():
                part.replace(out)
                job.update(status='done', progress=1.0)
            else:
                part.unlink(missing_ok=True)
                job.update(status='failed', error=(err.strip().splitlines() or ['ffmpeg failed'])[-1][:500])
        except Exception as exc:  # noqa: BLE001
            job.update(status='failed', error=str(exc)[:500])
        finally:
            job['proc'] = None
            shutil.rmtree(tmp, ignore_errors=True)

    def cancel(self, job_id):
        job = self.jobs.get(job_id)
        if job and job['status'] == 'running':
            job['status'] = 'cancelled'
            if job['proc']:
                job['proc'].kill()
            return True
        return False

    def public(self, job):
        return {k: v for k, v in job.items() if k != 'proc'}

    def get(self, job_id):
        job = self.jobs.get(job_id)
        return self.public(job) if job else None

    def exports(self):
        items = []
        for p in sorted(self.dir.glob('*.mp4'), key=lambda p: p.stat().st_mtime, reverse=True):
            if p.name.startswith('.'):
                continue
            items.append({'file': p.name, 'size': p.stat().st_size,
                          'created': datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()})
        return items

    def export_path(self, name):
        if not re.fullmatch(r'[A-Za-z0-9._-]+\.mp4', name or '') or name.startswith('.'):
            return None
        p = self.dir / name
        return p if p.is_file() else None
