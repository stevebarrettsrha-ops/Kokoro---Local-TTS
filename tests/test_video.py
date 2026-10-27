"""Video editor backend: media import, project validation and a real ffmpeg export."""
import base64
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio import video as vid  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='needs ffmpeg')


def ff(*args):
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', *args], check=True)


@pytest.fixture
def assets(tmp_path):
    a = tmp_path / 'src'
    a.mkdir()
    ff('-f', 'lavfi', '-i', 'testsrc=size=640x360', '-frames:v', '1', str(a / 'photo.png'))
    ff('-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=25', '-f', 'lavfi', '-i', 'sine=frequency=330',
       '-t', '3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(a / 'clip.mp4'))
    ff('-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=25', '-t', '2', '-c:v', 'mpeg4', str(a / 'phone.mov'))
    ff('-f', 'lavfi', '-i', 'sine=frequency=220', '-t', '4', str(a / 'music.mp3'))
    ff('-f', 'lavfi', '-i', 'color=c=white@0.0:s=320x180,format=rgba', '-frames:v', '1', str(a / 'text.png'))
    return a


def wait_proxy(lib, mid, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if lib.get(mid)['proxy'] in ('ready', 'failed'):
            return lib.get(mid)['proxy']
        time.sleep(0.2)
    return 'timeout'


def test_media_import_kinds_thumbs_and_proxy(tmp_path, assets):
    lib = vid.MediaLibrary(tmp_path / 'media')
    photo = lib.add((assets / 'photo.png').read_bytes(), 'Photo.png')
    clip = lib.add((assets / 'clip.mp4').read_bytes(), 'clip.mp4')
    music = lib.add((assets / 'music.mp3').read_bytes(), 'music.mp3')
    phone = lib.add((assets / 'phone.mov').read_bytes(), 'phone.mov')
    assert (photo['kind'], clip['kind'], music['kind'], phone['kind']) == ('image', 'video', 'audio', 'video')
    assert photo['width'] == 640 and clip['has_audio'] and clip['duration'] == pytest.approx(3, abs=0.1)
    assert lib.thumb(photo['id']) and lib.thumb(clip['id'])
    assert clip['proxy'] is None and phone['proxy'] == 'pending'
    assert wait_proxy(lib, phone['id']) == 'ready'
    assert lib.path(phone['id'], preview=True).name.endswith('.proxy.mp4')
    with pytest.raises(ValueError):
        lib.add(b'not really', 'notes.txt')
    with pytest.raises(ValueError):
        lib.add(b'garbage bytes', 'broken.mp4')
    assert len(lib.list()) == 4 and lib.delete(music['id']) and len(lib.list()) == 3


def test_sanitize_drops_bad_clips_and_clamps(tmp_path, assets):
    lib = vid.MediaLibrary(tmp_path / 'media')
    clip = lib.add((assets / 'clip.mp4').read_bytes(), 'clip.mp4')
    p = vid.sanitize_project({'format': '9:16', 'background': 'red', 'clips': [
        {'kind': 'video', 'media': clip['id'], 'duration': 99, 'in': 1, 'lane': 'V2'},
        {'kind': 'video', 'media': 'f' * 32},                      # unknown media
        {'kind': 'image', 'media': clip['id']},                    # wrong kind
        {'kind': 'audio', 'media': 'take:' + 'a' * 32},            # missing take
        {'kind': 'text', 'text': {'content': 'Hi', 'color': 'javascript:'}, 'lane': 'A1'},
        {'kind': 'bogus'},
    ]}, lib, take_exists=lambda rid: False)
    assert (p['width'], p['height'], p['background']) == (1080, 1920, '#000000')
    kinds = [c['kind'] for c in p['clips']]
    assert kinds == ['video', 'text']
    assert p['clips'][0]['duration'] == 99  # video may run past its source: the last frame holds
    assert p['clips'][1]['lane'] == 'T1' and p['clips'][1]['text']['color'] == '#ffffff'


def test_real_export(tmp_path, assets):
    lib = vid.MediaLibrary(tmp_path / 'media')
    photo = lib.add((assets / 'photo.png').read_bytes(), 'photo.png')
    clip = lib.add((assets / 'clip.mp4').read_bytes(), 'clip.mp4')
    music = lib.add((assets / 'music.mp3').read_bytes(), 'music.mp3')
    take = tmp_path / 'take.wav'
    ff('-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '2', str(take))
    project = vid.sanitize_project({'name': 'Test reel', 'format': '16:9', 'fps': 15, 'background': '#112233', 'clips': [
        {'id': 'bg', 'kind': 'color', 'color': '#ff000080', 'lane': 'V1', 'start': 0, 'duration': 1},
        {'id': 'p1', 'kind': 'image', 'media': photo['id'], 'lane': 'V1', 'start': 1, 'duration': 2, 'zoom': 0.15, 'fade_in': 0.5},
        {'id': 'v1', 'kind': 'video', 'media': clip['id'], 'lane': 'V2', 'start': 2.5, 'duration': 2, 'in': 0.5,
         'x': 0.5, 'y': 0.5, 'w': 0.5, 'h': 0.5, 'fit': 'contain', 'opacity': 0.8, 'fade_out': 0.5},
        {'id': 't1', 'kind': 'text', 'text': {'content': 'Hello'}, 'start': 0.5, 'duration': 3},
        {'id': 'a1', 'kind': 'audio', 'media': 'take:' + 'b' * 32, 'lane': 'A1', 'start': 0.2, 'duration': 2},
        {'id': 'a2', 'kind': 'audio', 'media': music['id'], 'lane': 'A2', 'start': 0, 'duration': 4, 'volume': 0.3, 'fade_out': 1},
    ]}, lib, take_exists=lambda rid: True)
    project['width'], project['height'] = 320, 180  # keep the test fast

    def resolve(mid):
        if mid.startswith('take:'):
            return take, True
        return lib.path(mid), bool(lib.get(mid).get('has_audio'))

    png = 'data:image/png;base64,' + base64.b64encode((assets / 'text.png').read_bytes()).decode()
    jobs = vid.ExportJobs(tmp_path / 'exports')
    job = jobs.start(project, resolve, {'t1': png}, lufs=-16, quality='small')
    for _ in range(600):
        if jobs.get(job['id'])['status'] != 'running':
            break
        time.sleep(0.1)
    done = jobs.get(job['id'])
    assert done['status'] == 'done', done
    out = jobs.export_path(done['file'])
    info = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-print_format', 'json', '-show_streams', '-show_format', str(out)],
                                     capture_output=True, text=True).stdout)
    types = sorted(s['codec_type'] for s in info['streams'])
    assert types == ['audio', 'video']
    assert float(info['format']['duration']) == pytest.approx(4.5, abs=0.25)
    v = next(s for s in info['streams'] if s['codec_type'] == 'video')
    assert (v['width'], v['height']) == (320, 180)
    assert [e['file'] for e in jobs.exports()] == [done['file']]
    assert not list((tmp_path / 'exports').glob('.*'))  # no partial files left


def test_export_guards(tmp_path):
    jobs = vid.ExportJobs(tmp_path / 'exports')
    with pytest.raises(ValueError):
        jobs.start(vid.sanitize_project({}), lambda m: (None, False), {})
    assert jobs.export_path('../secret.mp4') is None and vid.decode_png('data:text/html;base64,AAAA') is None


def test_browser_recording_without_duration_is_fixed(tmp_path):
    raw = tmp_path / 'rec.webm'
    with open(raw, 'wb') as fh:  # streamed WebM (like MediaRecorder) has no duration header
        subprocess.run(['ffmpeg', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc=size=160x120:rate=15', '-f', 'lavfi',
                        '-i', 'sine', '-t', '2', '-c:v', 'libvpx', '-c:a', 'libopus', '-f', 'webm', 'pipe:1'], stdout=fh, check=True)
    lib = vid.MediaLibrary(tmp_path / 'media')
    m = lib.add(raw.read_bytes(), 'Recording.webm')
    assert m['kind'] == 'video' and m['duration'] == pytest.approx(2, abs=0.2) and m['proxy'] is None


def test_text_effects_survive_sanitize():
    p = vid.sanitize_project({'clips': [{'kind': 'text', 'text': {'content': 'Hi', 'effect': 'neon', 'fx_color': '#ff00ff'}},
                                        {'kind': 'text', 'text': {'content': 'x', 'effect': '<script>'}}]})
    assert p['clips'][0]['text']['effect'] == 'neon' and p['clips'][0]['text']['fx_color'] == '#ff00ff'
    assert p['clips'][1]['text']['effect'] is None


def test_motion_plan_transitions_and_animations():
    p = vid.sanitize_project({'clips': [
        {'id': 'a', 'kind': 'color', 'start': 0, 'duration': 3},
        {'id': 'b', 'kind': 'color', 'start': 3, 'duration': 3, 'transition': {'type': 'crossfade', 'dur': 1}},
        {'id': 'c', 'kind': 'color', 'start': 6, 'duration': 2, 'transition': {'type': 'dip', 'dur': 1}},
        {'id': 'd', 'kind': 'color', 'start': 9, 'duration': 2, 'transition': {'type': 'slide-left', 'dur': 1}},  # gap: ignored
        {'id': 't', 'kind': 'text', 'start': 1, 'duration': 2, 'anim_in': {'type': 'pop', 'dur': 0.5}, 'anim_out': {'type': 'bogus'}},
    ]})
    plan = vid.motion_plan(p)
    assert plan['a'] == {'ext': 1.0, 'in': None, 'out': None}
    assert plan['b']['in'] == ('fade', 1.0) and plan['b']['out'] == ('fade', 0.5)
    assert plan['c']['in'] == ('fade', 0.5) and plan['d']['in'] is None
    assert plan['t'] == {'ext': 0.0, 'in': ('pop', 0.5), 'out': None}


def test_export_with_every_transition_and_animation(tmp_path, assets):
    lib = vid.MediaLibrary(tmp_path / 'media')
    photo = lib.add((assets / 'photo.png').read_bytes(), 'photo.png')
    clip = lib.add((assets / 'clip.mp4').read_bytes(), 'clip.mp4')
    clips, t = [], 0.0
    for i, tr in enumerate(vid.TRANSITIONS):
        media = clip if i % 2 else photo
        clips.append({'id': f'c{i}', 'kind': 'video' if i % 2 else 'image', 'media': media['id'], 'start': t, 'duration': 1.2,
                      'transition': {'type': tr, 'dur': 0.5}})
        t += 1.2
    for i, an in enumerate(vid.ANIMS):
        clips.append({'id': f't{i}', 'kind': 'color', 'color': '#ffcc00', 'lane': 'V3', 'start': i * 0.9, 'duration': 0.9,
                      'x': 0.3, 'y': 0.3, 'w': 0.4, 'h': 0.2, 'anim_in': {'type': an, 'dur': 0.3}, 'anim_out': {'type': an, 'dur': 0.3}})
    project = vid.sanitize_project({'fps': 12, 'clips': clips}, lib)
    project['width'], project['height'] = 256, 144
    jobs = vid.ExportJobs(tmp_path / 'exports')
    job = jobs.start(project, lambda m: (lib.path(m), bool(lib.get(m).get('has_audio'))), {}, quality='small')
    for _ in range(900):
        if jobs.get(job['id'])['status'] != 'running':
            break
        time.sleep(0.1)
    done = jobs.get(job['id'])
    assert done['status'] == 'done', done
    dur = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0',
                                str(jobs.export_path(done['file']))], capture_output=True, text=True).stdout)
    assert dur == pytest.approx(len(vid.TRANSITIONS) * 1.2, abs=0.3)


def _jpeg_with_orientation(src_jpg, orientation):
    """Insert a minimal EXIF block with the given orientation into a JPEG."""
    data = src_jpg.read_bytes()
    tiff = b'MM\x00\x2a\x00\x00\x00\x08' + b'\x00\x01' + b'\x01\x12\x00\x03\x00\x00\x00\x01' + \
        orientation.to_bytes(2, 'big') + b'\x00\x00' + b'\x00\x00\x00\x00'
    app1 = b'Exif\x00\x00' + tiff
    seg = b'\xff\xe1' + (len(app1) + 2).to_bytes(2, 'big') + app1
    return data[:2] + seg + data[2:]


def test_phone_photo_orientation_and_odd_formats(tmp_path):
    ff('-f', 'lavfi', '-i', 'testsrc=size=400x200', '-frames:v', '1', str(tmp_path / 'wide.jpg'))
    rotated = _jpeg_with_orientation(tmp_path / 'wide.jpg', 6)
    assert vid.jpeg_orientation(tmp_path / 'wide.jpg') == 1
    (tmp_path / 'r.jpg').write_bytes(rotated)
    assert vid.jpeg_orientation(tmp_path / 'r.jpg') == 6
    lib = vid.MediaLibrary(tmp_path / 'media')
    m = lib.add(rotated, 'IMG_0001.JPG')                       # upper-case extension, EXIF says "rotate 90"
    assert m['kind'] == 'image' and (m['width'], m['height']) == (200, 400)
    assert vid.jpeg_orientation(lib.path(m['id'])) == 1      # stored upright, no EXIF left to double-rotate
    ff('-f', 'lavfi', '-i', 'testsrc=size=120x80', '-frames:v', '1', str(tmp_path / 'scan.tiff'))
    t = lib.add((tmp_path / 'scan.tiff').read_bytes(), 'scan.tiff')
    assert t['kind'] == 'image' and t['file'].endswith('.png')
    noext = lib.add((tmp_path / 'wide.jpg').read_bytes(), 'photo_from_phone')   # no extension at all
    assert noext['kind'] == 'image'
    with pytest.raises(ValueError, match='HEIC'):
        lib.add(b'xx', 'IMG_2.HEIC')


def test_speed_loop_and_freeze_export(tmp_path, assets):
    lib = vid.MediaLibrary(tmp_path / 'media')
    clip = lib.add((assets / 'clip.mp4').read_bytes(), 'clip.mp4')        # 3 s with sound
    music = lib.add((assets / 'music.mp3').read_bytes(), 'music.mp3')     # 4 s
    p = vid.sanitize_project({'fps': 12, 'clips': [
        {'id': 'fast', 'kind': 'video', 'media': clip['id'], 'start': 0, 'duration': 1.5, 'speed': 2},
        {'id': 'held', 'kind': 'video', 'media': clip['id'], 'start': 1.5, 'duration': 6, 'in': 1},   # 2 s of video, 4 s frozen
        {'id': 'loop', 'kind': 'audio', 'media': music['id'], 'lane': 'A2', 'start': 0, 'duration': 7.5, 'loop': True},
        {'id': 'cut', 'kind': 'audio', 'media': music['id'], 'lane': 'A3', 'start': 0, 'duration': 20},  # clamped to 4 s
    ]}, lib)
    assert p['clips'][1]['duration'] == 6 and p['clips'][2]['duration'] == 7.5
    assert p['clips'][3]['duration'] == pytest.approx(4, abs=0.1)
    p['width'], p['height'] = 160, 90
    jobs = vid.ExportJobs(tmp_path / 'exports')
    job = jobs.start(p, lambda m: (lib.path(m), bool(lib.get(m).get('has_audio'))), {}, quality='small')
    for _ in range(600):
        if jobs.get(job['id'])['status'] != 'running':
            break
        time.sleep(0.1)
    done = jobs.get(job['id'])
    assert done['status'] == 'done', done
    out = jobs.export_path(done['file'])
    dur = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(out)],
                               capture_output=True, text=True).stdout)
    assert dur == pytest.approx(7.5, abs=0.25)
    # Looped music is still sounding near the end.
    level = subprocess.run(['ffmpeg', '-v', 'info', '-ss', '6.8', '-i', str(out), '-t', '0.5', '-af', 'volumedetect', '-f', 'null', '-'],
                           capture_output=True, text=True).stderr
    mean = float(level.split('mean_volume:')[1].split('dB')[0])
    assert mean > -40


def test_freeze_frame_and_peaks(tmp_path, assets):
    lib = vid.MediaLibrary(tmp_path / 'media')
    clip = lib.add((assets / 'clip.mp4').read_bytes(), 'clip.mp4')
    still = lib.freeze_frame(clip['id'], 1.2)
    assert still['kind'] == 'image' and (still['width'], still['height']) == (320, 240)
    pk = lib.peaks(clip['id'])
    assert pk['per_second'] == 40 and len(pk['peaks']) == pytest.approx(120, abs=4) and max(pk['peaks']) == 100
    assert lib.peaks(still['id']) is None
    with pytest.raises(ValueError):
        lib.freeze_frame(still['id'], 0)
