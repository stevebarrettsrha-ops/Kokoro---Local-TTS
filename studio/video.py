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
IMAGE_EXT = {'png', 'jpg', 'jpeg', 'webp', 'bmp'}
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


def write_json(path, data, indent=2):
    """Write JSON atomically so a crash or two overlapping saves never leave a half-written file."""
    path = Path(path)
    tmp = path.with_name(f'.{path.name}.{uuid.uuid4().hex[:8]}.tmp')
    try:
        tmp.write_text(json.dumps(data, indent=indent), encoding='utf-8')
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def tools():
    return {'ffmpeg': shutil.which('ffmpeg'), 'ffprobe': shutil.which('ffprobe')}


def _num(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, v)) if math.isfinite(v) else default


# ---------------------------------------------------------------- probing
def probe(path):
    """Duration, size and streams of a media file (ffprobe)."""
    exe = tools()['ffprobe']
    if not exe:
        raise RuntimeError('ffprobe (part of ffmpeg) is required for the video editor: sudo apt install ffmpeg')
    proc = subprocess.run([exe, '-v', 'error', '-print_format', 'json', '-show_streams', '-show_format', str(path)],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise ValueError('That file could not be read as audio, video or an image.')
    data = json.loads(proc.stdout or '{}')
    streams = data.get('streams', [])
    video = next((s for s in streams if s.get('codec_type') == 'video'
                  and not s.get('disposition', {}).get('attached_pic')), None)
    audio = next((s for s in streams if s.get('codec_type') == 'audio'), None)
    duration = _num(data.get('format', {}).get('duration'), 0, 1e7, 0.0)
    info = {'duration': round(duration, 3), 'has_audio': audio is not None, 'has_video': video is not None,
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


# ---------------------------------------------------------------- media
class MediaLibrary:
    def __init__(self, folder):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)

    def recover(self):
        """Tidy up after a previous run. Call once the studio knows it is the only one running."""
        for stale in self.dir.glob('.upload-*'):  # left behind if the studio stopped mid-upload
            stale.unlink(missing_ok=True)
        for m in self.list():  # a proxy that was being made when the studio stopped
            if m.get('proxy') == 'pending':
                threading.Thread(target=self._make_proxy, args=(m['id'],), daemon=True).start()

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
        if ext in IMAGE_EXT:
            kind = 'image'
        elif ext in VIDEO_EXT:
            kind = 'video'
        elif ext in AUDIO_EXT:
            kind = 'audio'
        elif ext in ('heic', 'heif'):
            raise ValueError('HEIC photos are not supported yet. Export them as JPG first (most phones can share as JPG).')
        else:
            raise ValueError(f'"{filename}" is not a supported picture, video or audio file.')
        size = src.stat().st_size if src.exists() else 0
        if not size:
            raise ValueError(f'"{filename}" is empty.')
        mid = uuid.uuid4().hex
        stored = f'{mid}.{ext}'
        path = self.dir / stored
        shutil.move(str(src), path)
        try:
            info = probe(path)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        if kind in ('video', 'audio') and not info['duration']:
            # Browser recordings (MediaRecorder WebM) often carry no duration; a copy-remux adds it.
            fixed = path.with_name(f'{mid}.fixed.{ext}')
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
                'ext': ext, 'created': now(), 'size': size, **info}
        if kind == 'image':
            meta['duration'] = None
        needs_proxy = kind == 'video' and (ext not in ('mp4', 'webm', 'm4v') or info['codec'] not in BROWSER_VIDEO)
        meta['proxy'] = 'pending' if needs_proxy else None
        write_json(self._meta_path(mid), meta)
        if kind in ('image', 'video'):
            self._make_thumb(meta)
        if needs_proxy:
            threading.Thread(target=self._make_proxy, args=(mid,), daemon=True).start()
        return self.get(mid)

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
        if not meta:  # deleted while the proxy was being made
            (self.dir / f'{mid}.proxy.mp4').unlink(missing_ok=True)
        else:
            meta['proxy'] = status
            write_json(self._meta_path(mid), meta)

    def rename(self, mid, name):
        meta = self.get(mid)
        if not meta:
            return None
        meta['name'] = ' '.join(str(name).split())[:80] or meta['name']
        write_json(self._meta_path(mid), meta)
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
                    clip['duration'] = round(min(clip['duration'], meta['duration'] - clip['in']), 3)
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
        write_json(self.dir / f'{project["id"]}.json', project, indent=1)
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



def text_area(c, W, H):
    """Pixel box (x, y, w, h) a text clip can paint into, with room for glows and shadows.

    static/video.js crops its text images to the same box (textArea).
    """
    bh = _even(c['h'] * H)
    m = int(c['text']['size'] * min(W, H) + 0.25 * bh)
    x0, y0 = max(0, int(c['x'] * W) - m), max(0, int(c['y'] * H) - m)
    x1, y1 = min(W, int((c['x'] + c['w']) * W) + m), min(H, int((c['y'] + c['h']) * H) + m)
    return x0, y0, max(2, (x1 - x0) // 2 * 2), max(2, (y1 - y0) // 2 * 2)


def png_size(raw):
    """(width, height) from a PNG's header."""
    return int.from_bytes(raw[16:20], 'big'), int.from_bytes(raw[20:24], 'big')


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
        text_box = None
        bw, bh = _even(c['w'] * W), _even(c['h'] * H)
        bx, by = int(round(c['x'] * W)), int(round(c['y'] * H))
        chain = []
        src = None  # input pad; None for sources generated inside the filter graph
        opacity = c['opacity']
        # Stills (text, pictures, colours) are made once and held with tpad. Feeding ffmpeg a
        # looped image re-decodes it every frame and queues full frames in memory: a few hundred
        # captions used to need >13 GB and get the export killed.
        hold = [f'tpad=stop_mode=clone:stop_duration={dur:.3f}', f'trim=duration={dur:.3f}']
        if c['kind'] == 'color':
            col = c['color'].lstrip('#')
            opacity *= int(col[6:8], 16) / 255 if len(col) == 8 else 1.0  # lavfi colour has no alpha
            chain += [f'color=c=0x{col[:6]}:s={bw}x{bh}:r={fps}:d={1 / fps:.4f}', 'format=rgba', *hold]
        elif c['kind'] == 'text':
            png = text_pngs.get(c['id'])
            if not png:
                warnings.append(f'Text "{c["text"]["content"][:30]}" was skipped (no rendered image).')
                continue
            if isinstance(png, dict):  # already cropped to the text area by the editor
                idx = add_input('-framerate', str(fps), '-i', str(png['path']))
                chain += ['format=rgba', *hold]
                bx, by, text_box = png['x'], png['y'], (png['w'], png['h'])
            else:  # a full-frame image: keep only the text box (plus room for glows and shadows)
                idx = add_input('-framerate', str(fps), '-i', str(png))
                x0, y0, cw, ch = text_area(c, W, H)
                chain += ['format=rgba', f'crop={cw}:{ch}:{x0}:{y0}', *hold]
                bx, by, text_box = x0, y0, (cw, ch)
            src = f'[{idx}:v]'
        else:
            path, has_audio = resolve(c['media'])
            if path is None:
                warnings.append(f'Missing media for "{c.get("name") or c["kind"]}" was skipped.')
                continue
            if c['kind'] == 'image' and c['zoom'] > 0:
                idx = add_input('-i', str(path))
                src = f'[{idx}:v]'
                frames = max(1, int(round(dur * fps)))
                chain.append(f'scale={bw * 2}:{bh * 2}:force_original_aspect_ratio=increase,crop={bw * 2}:{bh * 2},setsar=1')
                chain.append(f"zoompan=z='1+{c['zoom']:.4f}*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                             f':d={frames}:s={bw}x{bh}:fps={fps}')
            else:
                if c['kind'] == 'image':
                    idx = add_input('-framerate', str(fps), '-i', str(path))
                    src = f'[{idx}:v]'
                else:
                    idx = add_input('-ss', f'{c["in"]:.3f}', '-t', f'{dur:.3f}', '-i', str(path))
                    src = f'[{idx}:v]'
                    if ext > 0:  # hold the last frame if the source runs out during a transition
                        chain.append(f'tpad=stop_mode=clone:stop_duration={ext:.3f}')
                if c['fit'] == 'contain':
                    chain.append(f'scale={bw}:{bh}:force_original_aspect_ratio=decrease,format=rgba,'
                                 f'pad={bw}:{bh}:(ow-iw)/2:(oh-ih)/2:color=black@0')
                else:
                    chain.append(f'scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh}')
                chain.append('setsar=1')
                if c['kind'] == 'image':
                    chain += ['format=rgba', *hold]  # scaled once, then held
            chain.append(f'fps={fps},format=rgba')
            if c['kind'] == 'video' and has_audio and not c['muted'] and c['volume'] > 0:
                audio_labels.append(_audio_chain(fc, f'{idx}:a', c, len(audio_labels)))
            if ext > 0:
                chain.append(f'trim=duration={dur:.3f}')
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
        fc.append((src or '') + ','.join(chain) + f'[v{k}]')
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
        idx = add_input('-ss', f'{c["in"]:.3f}', '-t', f'{c["duration"]:.3f}', '-i', str(path))
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
    parts = [f'atrim=0:{dur:.3f}', 'asetpts=PTS-STARTPTS', 'aresample=48000',
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
    """Runs exports in the background and reports progress.

    Each export keeps its request (project snapshot + rendered text images) and
    state in exports/.jobs/<id>/ until it finishes, so an export interrupted by
    a restart starts again by itself (see resume) and the editor can find it.
    """
    KEEP_FINISHED = 30  # finished job records kept for the editor to read

    def __init__(self, folder):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir = self.dir / '.jobs'
        self.jobs_dir.mkdir(exist_ok=True)
        self.jobs = {}
        self.lock = threading.Lock()
        self._encoder = None
        records = []
        for f in self.jobs_dir.glob('*/job.json'):
            try:
                job = json.loads(f.read_text(encoding='utf-8'))
                if ID_RE.fullmatch(job.get('id', '')) and job['id'] == f.parent.name:
                    records.append(job)
            except (OSError, ValueError, AttributeError):
                shutil.rmtree(f.parent, ignore_errors=True)
        records.sort(key=lambda j: j.get('created') or '')
        for job in records:
            job['proc'] = None
            self.jobs[job['id']] = job

    def encoder(self):
        if self._encoder is None:
            exe = tools()['ffmpeg']
            out = subprocess.run([exe, '-hide_banner', '-encoders'], capture_output=True, text=True).stdout if exe else ''
            self._encoder = 'libx264' if 'libx264' in out else 'mpeg4'
        return self._encoder

    def _save(self, job):
        folder = self.jobs_dir / job['id']
        if folder.is_dir():
            write_json(folder / 'job.json', self.public(job))

    def start(self, project, resolve, text_images, *, lufs=None, quality='high'):
        exe = tools()['ffmpeg']
        if not exe:
            raise RuntimeError('ffmpeg is required to export video: sudo apt install ffmpeg')
        if not project['clips']:
            raise ValueError('The timeline is empty. Add some media first.')
        job_id = uuid.uuid4().hex
        job = {'id': job_id, 'status': 'running', 'progress': 0.0, 'file': None, 'error': None,
               'warnings': [], 'total': 0.0, 'project': project['id'], 'created': now(), 'resumed': False,
               'proc': None}
        with self.lock:  # claim the slot first so two clicks can't start two exports
            if any(j['status'] == 'running' for j in self.jobs.values()):
                raise ValueError('An export is already running. Wait for it to finish.')
            self.jobs[job_id] = job
        folder = self.jobs_dir / job_id
        try:
            folder.mkdir()
            images = {}
            for cid, data in (text_images if isinstance(text_images, dict) else {}).items():
                # Either a full-frame data URL, or {png, x, y} cropped to the text area.
                raw = decode_png(data.get('png') if isinstance(data, dict) else data)
                if not raw or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', str(cid)):
                    continue
                entry = {'file': f'{cid}.png'}
                if isinstance(data, dict):
                    w, h = png_size(raw)
                    if not (0 < w <= 2 * project['width'] and 0 < h <= 2 * project['height']):
                        continue
                    entry.update(x=int(_num(data.get('x'), -project['width'], project['width'], 0)),
                                 y=int(_num(data.get('y'), -project['height'], project['height'], 0)), w=w, h=h)
                (folder / entry['file']).write_bytes(raw)
                images[str(cid)] = entry
            slug = re.sub(r'[^A-Za-z0-9]+', '-', project['name']).strip('-').lower()[:40] or 'video'
            name = f'{slug}-{datetime.now().strftime("%Y%m%d-%H%M%S")}.mp4'
            if (self.dir / name).exists():
                name = f'{name[:-4]}-{job_id[:4]}.mp4'
            job['file'] = name
            write_json(folder / 'request.json', {'project': project, 'images': images, 'lufs': lufs, 'quality': quality})
            cmd = self._command(job, resolve)
            self._save(job)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            with self.lock:
                self.jobs.pop(job_id, None)
            raise
        threading.Thread(target=self._run, args=(job, cmd), daemon=True).start()
        return self.public(job)

    def _command(self, job, resolve):
        """Build the ffmpeg command from the job's saved request (also used to resume)."""
        folder = self.jobs_dir / job['id']
        req = json.loads((folder / 'request.json').read_text(encoding='utf-8'))
        pngs = {}
        for cid, e in req['images'].items():
            path = folder / e['file']
            if path.is_file():
                pngs[cid] = {'path': path, **{k: e[k] for k in ('x', 'y', 'w', 'h')}} if 'w' in e else path
        part = self.dir / f'.{job["file"]}.part.mp4'
        args, total, warnings = build_command(req['project'], resolve, pngs, part, lufs=req['lufs'],
                                              encoder=self.encoder(), quality=req['quality'])
        job.update(total=total, warnings=warnings)
        return [tools()['ffmpeg'], *args]

    def recover(self, resolve):
        """After a restart: drop half-written files and old records, and start again any export
        the last run didn't finish. Returns the ids restarted.

        Only call this once the studio knows it is the only one running (app.py binds the port
        first): a second copy started by mistake must not touch a running export's files.
        """
        for stale in self.dir.glob('.*.part.mp4'):
            stale.unlink(missing_ok=True)
        with self.lock:
            finished = sorted((j for j in self.jobs.values() if j['status'] != 'running'), key=lambda j: j.get('created') or '')
            for old in finished[:-self.KEEP_FINISHED]:
                shutil.rmtree(self.jobs_dir / old['id'], ignore_errors=True)
                self.jobs.pop(old['id'], None)
        return self.resume(resolve)

    def resume(self, resolve):
        """Start again any export a restart interrupted. Returns the ids restarted."""
        if not tools()['ffmpeg']:
            return []
        with self.lock:
            pending = sorted((j for j in self.jobs.values() if j['status'] == 'running' and not j['proc']),
                             key=lambda j: j.get('created') or '', reverse=True)
        restarted = []
        for i, job in enumerate(pending):
            try:
                if i:  # only one export runs at a time
                    raise RuntimeError('Interrupted when the studio stopped. Export again.')
                cmd = self._command(job, resolve)
            except Exception as exc:  # noqa: BLE001
                job.update(status='failed', error=str(exc)[:500] or 'Interrupted when the studio stopped.')
                self._finish(job)
                continue
            job.update(progress=0.0, resumed=True, error=None)
            self._save(job)
            threading.Thread(target=self._run, args=(job, cmd), daemon=True).start()
            restarted.append(job['id'])
        return restarted

    def _finish(self, job):
        """Record the outcome and drop the job's request files (only job.json stays)."""
        self._save(job)
        folder = self.jobs_dir / job['id']
        for f in folder.glob('*'):
            if f.name != 'job.json':
                f.unlink(missing_ok=True)

    def _run(self, job, cmd):
        out = self.dir / job['file']
        part = self.dir / f'.{out.name}.part.mp4'
        errlog = None
        try:
            # stderr goes to a file: a pipe nobody reads until the end can fill up and freeze ffmpeg.
            errlog = tempfile.TemporaryFile('w+', encoding='utf-8', errors='replace')
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errlog, text=True)
            job['proc'] = proc
            if job['status'] == 'cancelled':  # cancelled before ffmpeg had started
                proc.kill()
            for line in proc.stdout:
                if line.startswith('out_time_us=') or line.startswith('out_time_ms='):
                    try:
                        job['progress'] = min(0.99, int(line.split('=')[1]) / 1e6 / job['total'])
                    except ValueError:
                        pass
            code = proc.wait()
            errlog.seek(0)
            err = errlog.read()
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
            part.unlink(missing_ok=True)
        finally:
            job['proc'] = None
            if errlog:
                errlog.close()
            self._finish(job)

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

    def active(self, project_id=None):
        """Running exports (newest first), so a reloaded editor can show their progress."""
        with self.lock:
            jobs = [j for j in self.jobs.values() if j['status'] == 'running'
                    and (project_id is None or j.get('project') == project_id)]
        return [self.public(j) for j in sorted(jobs, key=lambda j: j.get('created') or '', reverse=True)]

    def exports(self):
        items = []
        for p in sorted(self.dir.glob('*.mp4'), key=lambda p: p.stat().st_mtime, reverse=True):
            if p.name.startswith('.'):
                continue
            try:
                st = p.stat()
            except OSError:  # deleted while listing
                continue
            items.append({'file': p.name, 'size': st.st_size,
                          'created': datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()})
        return items

    def export_path(self, name):
        if not re.fullmatch(r'[A-Za-z0-9._-]+\.mp4', name or '') or name.startswith('.'):
            return None
        p = self.dir / name
        return p if p.is_file() else None
