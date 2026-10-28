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
    assert p['clips'][0]['duration'] == pytest.approx(2.0, abs=0.1)
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
    assert not list((tmp_path / 'exports').glob('.*.part.mp4'))  # no partial files left
    assert [f.name for f in (tmp_path / 'exports' / '.jobs' / done['id']).iterdir()] == ['job.json']  # request files dropped


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


def test_project_save_is_atomic(tmp_path):
    store = vid.ProjectStore(tmp_path / 'projects')
    p = store.save(vid.sanitize_project({'name': 'Atomic'}))
    store.save({**p, 'name': 'Again'})
    assert store.get(p['id'])['name'] == 'Again'
    assert [f.name for f in (tmp_path / 'projects').iterdir()] == [f'{p["id"]}.json']  # no temp files left


def test_stale_files_are_cleaned_on_start(tmp_path):
    (tmp_path / 'media').mkdir()
    (tmp_path / 'media' / '.upload-abc').write_bytes(b'x')
    (tmp_path / 'exports').mkdir()
    (tmp_path / 'exports' / '.video-1.mp4.part.mp4').write_bytes(b'x')
    lib, jobs = vid.MediaLibrary(tmp_path / 'media'), vid.ExportJobs(tmp_path / 'exports')
    # Opening the folders changes nothing (a second studio started by mistake must not
    # break a running export); recover() tidies up once the studio owns the port.
    assert (tmp_path / 'media' / '.upload-abc').exists() and (tmp_path / 'exports' / '.video-1.mp4.part.mp4').exists()
    lib.recover()
    jobs.recover(lambda mid: (None, False))
    assert not list((tmp_path / 'media').iterdir()) and not list((tmp_path / 'exports').glob('*.mp4'))


def test_second_export_is_refused_and_bad_build_frees_the_slot(tmp_path, assets):
    lib = vid.MediaLibrary(tmp_path / 'media')
    pic = lib.add((assets / 'photo.png').read_bytes(), 'p.png')
    project = vid.sanitize_project({'clips': [{'kind': 'image', 'media': pic['id'], 'duration': 1}]}, lib)
    jobs = vid.ExportJobs(tmp_path / 'exports')

    def broken(_mid):
        raise RuntimeError('boom')
    with pytest.raises(RuntimeError):
        jobs.start(project, broken, {})
    assert not jobs.jobs  # a failed start doesn't block the next export
    resolve = lambda mid: (lib.path(mid), False)
    first = jobs.start(project, resolve, 'not a dict')
    with pytest.raises(ValueError, match='already running'):
        jobs.start(project, resolve, {})
    end = time.time() + 60
    while jobs.get(first['id'])['status'] == 'running' and time.time() < end:
        time.sleep(0.2)
    assert jobs.get(first['id'])['status'] == 'done'


def test_upload_route_reports_every_bad_file(tmp_path, monkeypatch):
    import io
    import app as server
    monkeypatch.setattr(server, 'media', vid.MediaLibrary(tmp_path / 'media'))

    def slow(*_a, **_k):
        raise subprocess.TimeoutExpired('ffprobe', 60)
    monkeypatch.setattr(vid, 'probe', slow)
    r = server.app.test_client().post('/api/media', data={'files': [(io.BytesIO(b'x' * 10), 'a.mp4'), (io.BytesIO(b'y'), 'b.txt')]})
    body = r.get_json()
    assert r.status_code == 400 and len(body['errors']) == 2 and 'a.mp4' in body['errors'][0]


def test_interrupted_export_resumes_after_restart(tmp_path, assets, monkeypatch):
    lib = vid.MediaLibrary(tmp_path / 'media')
    pic = lib.add((assets / 'photo.png').read_bytes(), 'p.png')
    text = {'kind': 'text', 'id': 'cap1', 'start': 0, 'duration': 1, 'x': 0.1, 'y': 0.7, 'w': 0.8, 'h': 0.2,
            'text': {'content': 'hi', 'size': 0.05}}
    project = vid.sanitize_project({'name': 'Resume me', 'clips': [{'kind': 'image', 'media': pic['id'], 'duration': 1}, text]}, lib)
    png = 'data:image/png;base64,' + base64.b64encode((assets / 'text.png').read_bytes()).decode()
    resolve = lambda mid: (lib.path(mid), False)
    # The studio stops right after the export was accepted: its thread never runs.
    with monkeypatch.context() as m:
        m.setattr(vid.threading, 'Thread', lambda *a, **k: type('T', (), {'start': lambda self: None})())
        first = vid.ExportJobs(tmp_path / 'exports').start(project, resolve, {'cap1': {'png': png, 'x': 192, 'y': 720}})
    again = vid.ExportJobs(tmp_path / 'exports')  # restart
    assert [j['id'] for j in again.active(project['id'])] == [first['id']]
    assert again.recover(resolve) == [first['id']]
    end = time.time() + 60
    while again.get(first['id'])['status'] == 'running' and time.time() < end:
        time.sleep(0.2)
    job = again.get(first['id'])
    assert job['status'] == 'done' and job['resumed'] and again.export_path(job['file'])
    assert not again.active()
    assert vid.ExportJobs(tmp_path / 'exports').get(first['id'])['status'] == 'done'  # outcome survives too


def test_cropped_text_images_are_placed_by_offset(tmp_path):
    project = vid.sanitize_project({'clips': [{'kind': 'text', 'id': 't', 'start': 0, 'duration': 1, 'x': 0.3, 'y': 0.4, 'w': 0.4, 'h': 0.2,
                                               'text': {'content': 'x'}}]})
    args, _, warnings = vid.build_command(project, lambda m: (None, False),
                                          {'t': {'path': tmp_path / 't.png', 'x': 500, 'y': 300, 'w': 900, 'h': 400}}, tmp_path / 'o.mp4')
    graph = args[args.index('-filter_complex') + 1]
    assert "overlay=x='500':y='300'" in graph and 'crop=' not in graph and not warnings
    assert '-loop' not in args  # stills are decoded once, not once per frame
