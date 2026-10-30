"""Tests that run without Kokoro weights. `pytest -q` from the project folder."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from studio import audio as fx  # noqa: E402
from studio import captions as cc  # noqa: E402
from studio.engine import VOICE_NAMES, Engine, read_settings  # noqa: E402
from studio.script import apply_emphasis, apply_pronunciations, parse_script, split_sentences  # noqa: E402
from studio.styles import STYLES, blend_style, canonical_style  # noqa: E402
from fakes import FakePipeline  # noqa: E402


# ---------- script notation ----------
def test_parse_tags_pauses_and_paragraphs():
    items, warnings = parse_script('[dramatic] Hello there, old friend. [pause=800] [joyful:40] I am so happy!\n\nHello again, everyone.')
    kinds = [i['kind'] for i in items]
    assert kinds == ['speech', 'pause', 'speech', 'pause', 'speech'], items
    assert items[0]['style'] == 'dramatic' and items[2]['style'] == 'joyful'
    assert items[2]['intensity'] == pytest.approx(0.4)
    assert items[1]['ms'] == 800 and items[3].get('para')
    assert items[4]['style'] == 'joyful'
    assert not warnings


def test_parse_voice_speed_default_and_unknown():
    items, warnings = parse_script('[sad] One line here. [voice=george] [speed=1.2] Second line here. [default] [voice=default] Third line here. [laughs] ok then',
                                   VOICE_NAMES)
    speech = [i for i in items if i['kind'] == 'speech']
    assert speech[0]['style'] == 'sad' and speech[0]['voice'] is None
    assert speech[1]['voice'] == 'bm_george' and speech[1]['speed'] == pytest.approx(1.2)
    assert speech[2]['style'] is None and speech[2]['voice'] is None
    assert any('laughs' in w for w in warnings)
    assert 'laughs' not in ' '.join(s['speak'] for s in speech)


def test_pause_formats_and_limits():
    assert parse_script('[pause=1.5s] hi there friend')[0][0]['ms'] == 1500
    assert parse_script('[pause=99999] hi there friend')[0][0]['ms'] == 10000
    assert parse_script('[beat] hi there friend')[0][0]['ms'] == 350


def test_old_style_names_still_work():
    assert canonical_style('tender') == 'warm' and canonical_style('whisper') == 'soft'
    items, _ = parse_script('[whisper] Keep your voice down please.')
    assert items[0]['style'] == 'soft'


def test_sentences_respect_abbreviations_and_merge_fragments():
    s = split_sentences('Dr. Smith arrived at 9 a.m. today. Yes. Then we left for the station. It was late.')
    assert s[0].startswith('Dr. Smith arrived')
    assert any('Yes.' in x for x in s) and 'Yes.' not in s
    assert len(s) == 3


def test_phoneme_links_pass_through_and_emphasis():
    items, warnings = parse_script('Say [Kokoro](/kˈOkəɹO/) with me, *really* loudly now.')
    assert not warnings
    assert '[Kokoro](/kˈOkəɹO/)' in items[0]['speak']
    assert '[really](+2)' in items[0]['speak']
    assert items[0]['text'] == 'Say Kokoro with me, really loudly now.'
    assert apply_emphasis('*two words*') == '[two](+2) [words](+2)'


def test_pronunciation_rules():
    rules = [{'word': 'SQL', 'say': 'sequel'}, {'word': 'Kokoro', 'say': '/kˈOkəɹO/'}]
    out = apply_pronunciations('Learn SQL and sql with Kokoro, not NoSQL.', rules)
    assert out == 'Learn sequel and sequel with [Kokoro](/kˈOkəɹO/), not NoSQL.'


# ---------- emotion model ----------
def test_blend_style_scales_toward_neutral():
    zero = blend_style('sad', 0)
    assert zero['speed'] == pytest.approx(1) and zero['pitch'] == 0 and zero['range'] == pytest.approx(1)
    full = blend_style('sad', 1)
    assert full['speed'] == pytest.approx(STYLES['sad']['speed'])
    half = blend_style('sad', 0.5)
    assert 1 > half['speed'] > full['speed'] and full['pitch'] < half['pitch'] < 0


# ---------- audio chain ----------
def test_loudness_matches_bs1770_reference():
    for sr in (24000, 48000):
        t = np.arange(sr * 4) / sr
        assert fx.loudness(np.sin(2 * np.pi * 997 * t), sr) == pytest.approx(-3.01, abs=0.05)


def test_finish_hits_target_and_ceiling():
    rng = np.random.default_rng(3)
    y = (rng.standard_normal(24000 * 8) * 0.05).astype(np.float32)
    y[24000:24100] = 0.95  # a spike the limiter must catch
    out, rate, report = fx.finish(y, rate=48000, lufs=-14, peak_db=-1)
    assert rate == 48000 and len(out) == len(y) * 2
    assert report['lufs'] == pytest.approx(-14, abs=0.6)
    assert report['true_peak'] <= -0.9


def test_trim_reports_offset():
    y = np.zeros(24000, dtype=np.float32)
    y[6000:12000] = 0.3
    out, cut = fx.trim(y)
    assert 5000 < cut < 6000 and len(out) < 9000


# ---------- captions ----------
def test_word_merge_and_cues():
    class T:
        def __init__(self, text, ws, s, e):
            self.text, self.whitespace, self.start_ts, self.end_ts = text, ws, s, e
    toks = [T('I', ' ', 0.0, 0.1), T('do', '', 0.1, 0.3), T("n't", '', 0.3, 0.4), T(',', ' ', None, None), T('know', '', 0.5, 0.8), T('.', '', None, None)]
    words = cc.words_from_tokens(toks, offset_s=1.0)
    assert [w['w'] for w in words] == ['I', "don't,", 'know.']
    assert words[0]['start'] == pytest.approx(1.0) and words[2]['end'] == pytest.approx(1.8)
    long = [{'w': f'word{i}', 'start': i * 0.3, 'end': i * 0.3 + 0.25} for i in range(40)]
    cues = cc.build_cues([{'words': long}])
    assert len(cues) > 1
    assert all(len(line) <= cc.MAX_LINE for c in cues for line in c['text'].split('\n'))
    srt = cc.to_srt(cues)
    assert srt.startswith('1\n00:00:00,000 --> ') and cc.to_vtt(cues).startswith('WEBVTT')


# ---------- engine end to end (fake model) ----------
@pytest.fixture
def engine(tmp_path):
    e = Engine(tmp_path)
    e.patched = True
    e.pipelines['a'] = FakePipeline('a')
    e.pipelines['b'] = FakePipeline('b')
    return e


def test_guards():
    with pytest.raises(ValueError):
        read_settings({'voice': 'nope'})
    with pytest.raises(ValueError):
        read_settings({'style': 'nope'})


def test_synthesize_writes_take_with_captions(engine, tmp_path):
    meta = engine.synthesize({'text': '[sad] Such a loss for all of us. [pause=250] [joyful] Welcome back, my friend!\n\n[voice=george] Good to be home again.',
                              'voice': 'af_heart', 'style': 'neutral', 'intensity': 80, 'rate': 48000, 'lufs': -16})
    wav = tmp_path / f'{meta["id"]}.wav'
    y, sr = sf.read(wav)
    assert sr == 48000 and len(y) / sr == pytest.approx(meta['seconds'], abs=0.01)
    assert meta['lufs'] == pytest.approx(-16, abs=0.8)
    assert [s['style'] for s in meta['segments']] == ['sad', 'joyful', 'joyful']
    assert meta['segments'][2]['voice'] == 'bm_george'
    assert engine.pipelines['b'].calls, 'UK voice should use the British pipeline'
    sad_call = engine.pipelines['a'].calls[0]
    assert sad_call['shape'].pitch < 0 and sad_call['shape'].range < 1 and sad_call['speed'] < 1
    srt = (tmp_path / f'{meta["id"]}.srt').read_text()
    assert 'Such a loss' in srt and 'Good to be home again.' in srt
    saved = json.loads((tmp_path / f'{meta["id"]}.json').read_text())
    assert saved['text'].startswith('[sad]') and saved['captions'] >= 3
    # Segment times are ordered and inside the take.
    starts = [s['start'] for s in meta['segments']]
    assert starts == sorted(starts) and meta['segments'][-1]['end'] <= meta['seconds'] + 0.05


def test_explicit_pause_is_exact(engine):
    meta = engine.synthesize({'text': 'First sentence is here. [pause=1000] Second sentence is here.', 'lufs': 'off', 'polish': False, 'rate': 24000})
    a, b = meta['segments']
    assert b['start'] - a['end'] == pytest.approx(1.0, abs=0.01)


def test_fit_to_duration(engine):
    text = 'One two three four five. Six seven eight nine ten.\n\nEleven twelve thirteen fourteen. Fifteen sixteen seventeen.'
    loose = engine.synthesize({'text': text, 'target_seconds': 9.0, 'rate': 24000})
    assert loose['seconds'] == pytest.approx(9.0, abs=0.15)
    tight = engine.synthesize({'text': text, 'target_seconds': 4.5, 'rate': 24000})
    assert tight['fit']['passes'] == 2 and tight['seconds'] == pytest.approx(4.5, abs=0.4)


def test_voice_blend_keys(engine):
    pytest.importorskip('torch')
    engine.synthesize({'text': 'Blend these two voices nicely.', 'voice': 'af_heart', 'blend_voice': 'af_bella',
                       'blend': 0.4, 'blend_mode': 'delivery'})
    pipe = engine.pipelines['a']
    key = pipe.calls[-1]['voice']
    assert key.startswith('af_heart+af_bella@0.40:delivery')
    mix, base, other = pipe.voices[key], pipe.voices['af_heart'], pipe.voices['af_bella']
    assert (mix[..., :128] == base[..., :128]).all()
    assert np.allclose(mix[..., 128:].numpy(), (base[..., 128:] * 0.6 + other[..., 128:] * 0.4).numpy(), atol=1e-6)


def test_preview_returns_wav_bytes(engine, tmp_path):
    data, info = engine.synthesize({'text': '[excited] Preview this quick line now.', 'rate': 24000}, save=False)
    assert data[:4] == b'RIFF' and info['seconds'] > 0.5
    assert not list(tmp_path.glob('*.wav'))


# ---------- flask routes ----------
def test_routes(engine, tmp_path, monkeypatch):
    pytest.importorskip('flask')
    import app as server
    monkeypatch.setattr(server, 'engine', engine)
    monkeypatch.setattr(server, 'OUT', tmp_path)
    client = server.app.test_client()
    assert client.get('/api/voices').get_json()['styles'][0]['id'] == 'neutral'
    assert client.get('/', headers={'Host': 'evil.example:8765'}).status_code == 403
    r = client.post('/api/generate', json={'text': 'Hello from the test suite, friend.', 'mp3': False})
    rec = r.get_json()['recording']
    assert client.get(rec['audio_url']).status_code == 200
    assert client.get(rec['srt_url']).data.startswith(b'1\n')
    assert len(client.get('/api/recordings').get_json()['recordings']) == 1
    assert client.post('/api/preview', json={'text': 'Short preview line here.'}).mimetype == 'audio/wav'
    assert client.delete(f'/api/recordings/{rec["id"]}').status_code == 200
    assert client.get(rec['audio_url']).status_code == 404
    assert client.post('/api/generate', data='x').status_code == 415


# ---------- pronunciation ----------
from studio.phonemes import HEBREW_PACK, US_VOCAB, fix_heteronyms, make_resolver, normalize_ipa, respell  # noqa: E402


def fake_g2p(text):
    table = {'yah': 'jˈɑ', 'hoo': 'hˈu', 'ah': 'ˈɑ', 'liv': 'lˈɪv', 'shah': 'ʃˈɑ', 'mah': 'mˈɑ', 'yeem': 'jˈim'}
    return table.get(text, text)


def test_pack_uses_only_kokoro_symbols():
    for word, ps, _hint in HEBREW_PACK:
        assert set(ps) <= US_VOCAB, (word, set(ps) - US_VOCAB)
        assert ps.count('ˈ') == 1, word


def test_respelling_and_ipa():
    assert respell('yah-HOO-ah', fake_g2p) == 'jɑhˈuɑ'
    assert respell('shah-MAH-yeem', fake_g2p) == 'ʃɑmˈɑjim'
    assert respell('liv', fake_g2p) == 'lˈɪv'
    assert normalize_ipa('/jaˈhuːa/') == 'jɑˈhuɑ'
    assert normalize_ipa("/ʃaˈmajim/") == 'ʃɑˈmɑjim'
    assert normalize_ipa('/maɪ/') == 'mI'
    assert normalize_ipa('/tʃeɪn/') == 'ʧAn'
    assert normalize_ipa('jɑhˈuɑ') == 'jɑhˈuɑ'  # misaki input is left alone


def test_live_heteronym():
    assert fix_heteronyms('Live by every word.') == '[Live](/lˈɪv/) by every word.'
    assert fix_heteronyms('We will live forever.') == 'We will [live](/lˈɪv/) forever.'
    assert fix_heteronyms('Join the live stream.') == 'Join the [live](/lˈIv/) stream.'
    assert fix_heteronyms('In five minutes we go live.') == 'In five minutes we go [live](/lˈIv/).'
    assert fix_heteronyms('[live](/lˈIv/) and lives and alive') == '[live](/lˈIv/) and lives and alive'


def test_rules_and_inline_links_become_phonemes():
    resolve = make_resolver(fake_g2p)
    rules = [{'word': 'Yahuah', 'say': 'yah-HOO-ah'}, {'word': 'Qadash', 'say': '/kɑdˈɑʃ/'}]
    items, _ = parse_script('Yahuah is qadash. Say [Shamayim](shah-MAH-yeem) and live well.', pronunciations=rules, resolve=resolve)
    speak = ' '.join(i['speak'] for i in items)
    assert '[Yahuah](/jɑhˈuɑ/)' in speak and '[qadash](/kɑdˈɑʃ/)' in speak
    assert '[Shamayim](/ʃɑmˈɑjim/)' in speak and '[live](/lˈɪv/)' in speak
    assert items[0]['text'].startswith('Yahuah is qadash.')  # captions keep the real spelling


def test_engine_phonemize_and_pack_in_catalog(engine):
    from studio.engine import public_catalog
    assert len(public_catalog()['packs']['hebrew']['words']) >= 50
    engine.pipelines['a'].g2p = lambda t: (f'<{t}>', [])
    out = engine.phonemize({'text': 'Praise Yahuah.', 'pronunciations': [{'word': 'Yahuah', 'say': '/jɑhˈuɑ/'}]})
    assert out['sentences'][0]['phonemes'] == '<Praise [Yahuah](/jɑhˈuɑ/).>'


# ---------- cloned voices ----------
from studio.cloning import CloneWorker, VoiceLibrary, prepare_reference  # noqa: E402


def _speechy(seconds, sr=24000, gap_at=None):
    t = np.arange(int(sr * seconds)) / sr
    y = 0.3 * np.sin(2 * np.pi * 150 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    if gap_at is not None:
        y[int(gap_at[0] * sr):int(gap_at[1] * sr)] = 0
    return y.astype(np.float32)


def _wav_bytes(y, sr=24000):
    import io
    buf = io.BytesIO()
    sf.write(buf, y, sr, format='WAV')
    return buf.getvalue()


def test_reference_prep_picks_cleanest_window_first():
    y = _speechy(25, gap_at=(1, 8))  # long silence early on
    ref, info = prepare_reference(y)
    assert len(ref) / 24000 <= 30
    assert info['best_window_start'] >= 7.5
    with pytest.raises(ValueError):
        prepare_reference(_speechy(3))


def test_library_add_list_delete(tmp_path):
    lib = VoiceLibrary(tmp_path / 'voices')
    v = lib.add(_wav_bytes(_speechy(12), 44100), 'marcus.wav', 'Marcus', 'Male')
    assert v['id'] == 'clone:marcus' and lib.names() == {'clone:marcus': 'Marcus'}
    v2 = lib.add(_wav_bytes(_speechy(12)), 'm2.wav', 'Marcus')
    assert v2['id'] != v['id']
    assert lib.get('clone:../etc') is None
    assert lib.delete('clone:marcus') and len(lib.list()) == 1


@pytest.fixture
def cloning_engine(tmp_path):
    worker = CloneWorker(python=sys.executable, script=Path(__file__).parent / 'fake_clone_worker.py', root=tmp_path)
    e = Engine(tmp_path / 'out', voices_dir=tmp_path / 'voices', cloner=worker)
    e.patched = True
    e.pipelines['a'] = FakePipeline('a')
    e.pipelines['b'] = FakePipeline('b')
    e.library.add(_wav_bytes(_speechy(12)), 'deep.wav', 'Marcus', 'Male')
    yield e
    worker.close()


def test_cloned_voice_render_with_emotion_and_pronunciation(cloning_engine):
    e = cloning_engine
    rules = [{'word': 'Yahuah', 'say': '/jɑhˈuɑ/', 'hint': 'yah-HOO-ah'}, {'word': 'Qadash', 'say': 'kah-DAHSH'}]
    meta = e.synthesize({'text': '[calm] Praise Yahuah, he is Qadash. [voice=heart] [excited] And now a Kokoro line!',
                         'voice': 'clone:marcus', 'pronunciations': rules, 'rate': 24000})
    assert [s['voice'] for s in meta['segments']] == ['clone:marcus', 'af_heart']
    assert e.pipelines['a'].calls[-1]['text'].startswith('And now')
    srt = (e.out / f'{meta["id"]}.srt').read_text()
    assert 'Praise Yahuah, he is Qadash.' in srt  # captions keep the real spelling
    out = e.phonemize({'text': 'Praise Yahuah, he is Qadash.', 'voice': 'clone:marcus', 'pronunciations': rules})
    assert out['sentences'][0]['phonemes'] == 'Praise yah-hoo-ah, he is kah-dahsh.'


def test_cloned_voice_errors_are_reported(cloning_engine, tmp_path):
    with pytest.raises(RuntimeError, match='stopped unexpectedly'):
        cloning_engine.synthesize({'text': 'CRASH', 'voice': 'clone:marcus'})
    # The worker restarts on the next request.
    assert cloning_engine.synthesize({'text': 'Back again after a crash.', 'voice': 'clone:marcus'})['seconds'] > 0
    missing = Engine(tmp_path / 'o2', voices_dir=tmp_path / 'voices', cloner=CloneWorker(python=tmp_path / 'nope', root=tmp_path))
    with pytest.raises(RuntimeError, match='setup-cloning'):
        missing.synthesize({'text': 'Hello there friend.', 'voice': 'clone:marcus'})
    with pytest.raises(ValueError):
        cloning_engine.synthesize({'text': 'Hi there.', 'voice': 'clone:nobody'})


def test_clone_routes(cloning_engine, monkeypatch):
    import app as server
    monkeypatch.setattr(server, 'engine', cloning_engine)
    c = server.app.test_client()
    import io
    r = c.post('/api/voices/clone', data={'name': 'Grace', 'gender': 'Female', 'file': (io.BytesIO(_wav_bytes(_speechy(15))), 'g.wav')})
    assert r.status_code == 400 and 'permission' in r.get_json()['error']
    r = c.post('/api/voices/clone', data={'name': 'Grace', 'consent': 'yes', 'file': (io.BytesIO(_wav_bytes(_speechy(15))), 'g.wav')})
    assert r.get_json()['voice']['id'] == 'clone:grace'
    cat = c.get('/api/voices').get_json()
    assert {v['id'] for v in cat['cloned']} == {'clone:marcus', 'clone:grace'} and cat['cloning']['installed']
    assert c.get('/api/voices/clone/grace/sample').mimetype == 'audio/wav'
    assert c.delete('/api/voices/clone/grace').status_code == 200


def test_cross_site_writes_are_blocked(engine, tmp_path, monkeypatch):
    import io
    import app as server
    monkeypatch.setattr(server, 'engine', engine)
    monkeypatch.setattr(server, 'OUT', tmp_path)
    c = server.app.test_client()
    form = lambda: {'name': 'X', 'consent': 'yes', 'file': (io.BytesIO(b'RIFF'), 'x.wav')}
    # Any web page can send a plain form POST to 127.0.0.1; only the studio's own page may write.
    assert c.post('/api/voices/clone', data=form(), headers={'Origin': 'https://evil.example'}).status_code == 403
    assert c.post('/api/media', data={}, headers={'Origin': 'null'}).status_code == 403
    assert c.post('/api/media', data={}, headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    assert c.delete('/api/recordings/' + '0' * 32, headers={'Origin': 'http://evil.example'}).status_code == 403
    # Same-origin requests and reads still work.
    assert c.post('/api/media', data={}, headers={'Origin': 'http://127.0.0.1:8765'}).status_code == 400
    assert c.post('/api/media', data={}, headers={'Origin': 'http://localhost:8765'}).status_code == 400
    assert c.get('/api/voices', headers={'Origin': 'https://evil.example'}).status_code == 200


def test_voice_tag_matches_names_with_spaces():
    items, warnings = parse_script('Hi there everyone. [voice=my-dad] Hello there, kids.', {**VOICE_NAMES, 'clone:my-dad-1a2b': 'My Dad'})
    assert [i['voice'] for i in items if i['kind'] == 'speech'] == [None, 'clone:my-dad-1a2b'] and not warnings


def test_blockwise_true_peak_matches_whole_signal():
    # Long takes are processed in blocks to keep memory flat; results must not change at block edges.
    from scipy import signal
    y = np.random.default_rng(3).standard_normal(fx._CHUNK * 2 + 777) * 0.3
    up = np.abs(signal.resample_poly(y, 4, 1))
    whole = np.maximum(up[: len(y) * 4].reshape(-1, 4).max(axis=1), np.abs(y))
    assert np.array_equal(fx._peak_env(y), whole)
    assert fx.true_peak_db(fx.limit(y, 48000, -1.0)) <= -0.99


def test_recordings_are_paged_and_summarised(tmp_path, monkeypatch):
    import app as server
    monkeypatch.setattr(server, 'OUT', tmp_path)
    for i in range(230):
        rid = f'{i:032x}'
        (tmp_path / f'{rid}.wav').write_bytes(b'RIFF')
        (tmp_path / f'{rid}.json').write_text(json.dumps({'id': rid, 'name': f'Take {i}', 'created': f'2026-01-01T00:{i // 60:02d}:{i % 60:02d}',
                                                          'text': 'x' * 5000, 'segments': [{'start': 0, 'end': 1, 'style': 'sad', 'text': 'x' * 200}]}))
    c = server.app.test_client()
    first = c.get('/api/recordings?limit=50').get_json()
    assert first['total'] == 230 and len(first['recordings']) == 50 and first['more']
    assert first['recordings'][0]['name'] == 'Take 229' and first['recordings'][0]['text']
    last = c.get('/api/recordings?offset=200&limit=50').get_json()
    assert len(last['recordings']) == 30 and not last['more'] and last['recordings'][-1]['name'] == 'Take 0'
    summary = c.get('/api/recordings?summary=1').get_json()['recordings']
    assert len(summary) == 230  # the video editor sees every take, not just the newest 200
    assert 'text' not in summary[0] and summary[0]['segments'] == [{'start': 0, 'end': 1, 'style': 'sad'}]
    assert summary[0]['audio_url'].startswith('/api/audio/')
    (tmp_path / f'{0:032x}.wav').unlink()  # a take whose audio is gone is not listed
    assert c.get('/api/recordings?limit=1').get_json()['total'] == 229


def test_voice_cutter_source_suggest_and_cut(tmp_path):
    from studio.cloning import VoiceLibrary, best_part
    lib = VoiceLibrary(tmp_path / 'voices')
    # 90 s: music-free speech only between 40 s and 70 s, silence elsewhere.
    y = np.zeros(24000 * 90, np.float32)
    y[24000 * 40:24000 * 70] = _speechy(30)
    src = tmp_path / 'podcast.wav'
    sf.write(src, y, 48000 // 2)
    opened = lib.open_source(src, 'podcast.wav')
    assert opened['seconds'] == pytest.approx(90, abs=0.01) and len(opened['peaks']) == 1600
    a, b = opened['suggest']
    assert 40 <= a and b <= 70.5 and b - a == pytest.approx(20)  # the best part lies inside the speech
    assert best_part(y[:24000 * 12]) == (0.0, 12.0)  # short recordings: all of it
    voice = lib.add_from_source(opened['id'], 45, 65, 'Pod Host', 'Male')
    assert voice['id'] == 'clone:pod-host' and 18 <= voice['seconds'] <= 21
    meta = json.loads((tmp_path / 'voices' / 'pod-host.json').read_text())
    assert meta['cut'] == [45, 65] and meta['source_file'] == 'podcast.wav'
    assert lib.source_path(opened['id']) is None  # the opened recording is cleaned up
    with pytest.raises(ValueError, match='no longer open'):
        lib.add_from_source(opened['id'], 0, 10, 'Again')
    second = lib.open_source(src, 'podcast.wav')
    with pytest.raises(ValueError, match='at least 6'):
        lib.add_from_source(second['id'], 0, 3, 'Silence')  # selected only silence -> too little speech
    assert lib.source_path('../../etc/passwd') is None and not lib.delete_source('nope')


def test_voice_cutter_routes_with_video_and_media(cloning_engine, monkeypatch, tmp_path):
    import io
    import shutil
    import subprocess
    import app as server
    from studio import video as vid
    monkeypatch.setattr(server, 'engine', cloning_engine)
    c = server.app.test_client()
    r = c.post('/api/voices/sources', data={'file': (io.BytesIO(_wav_bytes(_speechy(15))), 'me.wav')})
    src = r.get_json()['source']
    assert src['suggest'] == [0.0, 15.0]
    assert c.get(f'/api/voices/sources/{src["id"]}/audio').mimetype == 'audio/wav'
    r = c.post('/api/voices/clone', data={'name': 'Cut', 'source': src['id'], 'start': '2', 'end': '14'})
    assert r.status_code == 400 and 'permission' in r.get_json()['error']
    r = c.post('/api/voices/clone', data={'name': 'Cut', 'consent': 'yes', 'source': src['id'], 'start': '2', 'end': '14'})
    assert r.get_json()['voice']['id'] == 'clone:cut'
    assert c.post('/api/voices/sources', data={'file': (io.BytesIO(b'not audio'), 'x.mp3')}).status_code == 400
    if not shutil.which('ffmpeg'):
        return
    # The sound of a video, and a file from the video editor's uploads.
    wav = tmp_path / 's.wav'
    sf.write(wav, _speechy(12), 24000)
    mp4 = tmp_path / 'clip.mp4'
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=160x120:d=12', '-i', str(wav),
                    '-shortest', '-c:v', 'libx264', '-c:a', 'aac', str(mp4)], check=True)
    r = c.post('/api/voices/sources', data={'file': (io.BytesIO(mp4.read_bytes()), 'clip.mp4')})
    assert r.get_json()['source']['seconds'] == pytest.approx(12, abs=0.2)
    lib = vid.MediaLibrary(tmp_path / 'media')
    monkeypatch.setattr(server, 'media', lib)
    m = lib.add(mp4.read_bytes(), 'Interview.mp4')
    r = c.post('/api/voices/sources', data={'media': m['id']})
    assert r.get_json()['source']['name'] == 'Interview'
    assert c.post('/api/voices/sources', data={'media': 'f' * 32}).status_code == 400


def test_clone_worker_waits_for_background_setup(tmp_path):
    import os
    import subprocess
    (tmp_path / '.venv-chatterbox' / 'bin').mkdir(parents=True)
    (tmp_path / '.venv-chatterbox' / 'bin' / 'python').write_text('')
    (tmp_path / 'cloning').mkdir()
    (tmp_path / 'cloning' / 'chatterbox_worker.py').write_text('')
    w = CloneWorker(root=tmp_path)
    assert w.available() and w.status()['setup'] is None
    lock = tmp_path / '.venv-chatterbox.installing'
    setup = subprocess.Popen(['bash', '-c', 'sleep 30', 'setup-cloning.sh'])  # stands in for the running setup
    try:
        lock.write_text(str(setup.pid))
        assert not w.available() and w.status()['setup'] == 'installing'
        with pytest.raises(RuntimeError, match='still being installed'):
            w.speak('hi', tmp_path / 'x.wav')
    finally:
        setup.kill(); setup.wait()
    lock.write_text(str(os.getpid()))  # after a reboot the id belongs to some other program
    assert not w.available() and w.status()['setup'] == 'interrupted'
    proc = subprocess.Popen(['true']); proc.wait()
    lock.write_text(str(proc.pid))  # setup stopped part-way
    assert not w.available() and w.status()['setup'] == 'interrupted'
    lock.unlink()
    assert w.available()


def test_best_part_prefers_speech_over_music_and_nan_is_safe(tmp_path):
    from studio.cloning import VoiceLibrary, best_part
    sr = 24000
    t = np.arange(sr * 30) / sr
    music = (0.25 * (np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 330 * t))).astype(np.float32)  # steady bed
    y = np.concatenate([music, _speechy(30)])
    a, b = best_part(y)
    assert a >= 29.5, (a, b)  # the speech, not the music intro
    a, b = best_part(np.concatenate([_speechy(30), music]))
    assert b <= 30.5, (a, b)  # ... nor the music outro
    # A float file with NaN/inf samples opens with a valid (finite) waveform.
    bad = _speechy(12)
    bad[100:200] = np.nan
    bad[300] = np.inf
    src = tmp_path / 'nan.wav'
    sf.write(src, bad, sr, subtype='FLOAT')
    opened = VoiceLibrary(tmp_path / 'v').open_source(src, 'nan.wav')
    json.dumps(opened, allow_nan=False)


def test_two_voices_with_one_name_at_once(tmp_path):
    import threading
    from studio.cloning import VoiceLibrary
    lib = VoiceLibrary(tmp_path / 'voices')
    out = []
    threads = [threading.Thread(target=lambda: out.append(lib.add(_wav_bytes(_speechy(12)), 'd.wav', 'Dad')['id'])) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(set(out)) == 4 and len(lib.list()) == 4


def test_real_bugs_are_not_reported_as_bad_input(monkeypatch):
    import app as server
    monkeypatch.setattr(server.media, 'list', lambda: (_ for _ in ()).throw(ValueError('too many values to unpack')))
    monkeypatch.setitem(server.app.config, 'PROPAGATE_EXCEPTIONS', False)
    r = server.app.test_client().get('/api/media')
    assert r.status_code == 500  # a bug, logged as one, not a misleading 400


def test_every_ffmpeg_call_is_detached_from_the_terminal():
    # A child that reads the studio's terminal freezes a backgrounded studio (SIGTTOU) and eats keys.
    import ast
    for f in Path(__file__).resolve().parents[1].glob('studio/*.py'):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, 'attr', '') in ('run', 'Popen') \
                    and getattr(node.func.value, 'id', '') == 'subprocess':
                kw = {k.arg for k in node.keywords}
                assert kw & {'stdin', 'input'}, f'{f.name}:{node.lineno} has no stdin='
