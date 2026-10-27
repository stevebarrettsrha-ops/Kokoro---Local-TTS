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
    assert 5000 < cut < 6000 and len(out) < 10500  # keeps soft word endings (130 ms tail)


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
    # Segments span the spoken words; the silence between the two clips is exactly 1 s,
    # plus each clip's own soft lead-out/lead-in.
    assert 1.0 <= b['start'] - a['end'] <= 1.0 + 0.35


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


def test_sentences_read_together_but_captioned_separately(engine):
    text = 'This is the first sentence here. And here is the second one. [sad] Now a sad line follows.'
    meta = engine.synthesize({'text': text, 'rate': 24000, 'sentence_gap': 450})
    calls = engine.pipelines['a'].calls
    assert calls[0]['text'] == 'This is the first sentence here. And here is the second one.'
    assert calls[0]['shape'].stop_ms == pytest.approx(450 - 200)
    assert calls[1]['text'] == 'Now a sad line follows.' and calls[1]['shape'].tilt < 0
    assert [s['text'] for s in meta['segments']] == ['This is the first sentence here.', 'And here is the second one.',
                                                     'Now a sad line follows.']
    srt = (engine.out / f"{meta['id']}.srt").read_text()
    assert 'This is the first sentence here.' in srt and 'And here is the second one.' in srt


def test_flow_off_reads_sentence_by_sentence(engine):
    engine.synthesize({'text': 'One sentence goes here. Another sentence goes here.', 'flow': False, 'rate': 24000})
    assert [c['text'] for c in engine.pipelines['a'].calls] == ['One sentence goes here.', 'Another sentence goes here.']


def test_cloning_installer_runs_setup_script(tmp_path):
    import time
    from studio.cloning import Installer
    (tmp_path / 'setup-cloning.sh').write_text('echo step one\necho step two\nexit 0\n')
    inst = Installer(tmp_path)
    assert inst.status() is None
    inst.start()
    for _ in range(100):
        if inst.status()['status'] != 'running':
            break
        time.sleep(0.05)
    st = inst.status()
    assert st['status'] == 'done' and st['log'] == ['step one', 'step two']
    (tmp_path / 'setup-cloning.sh').write_text('echo oops\nexit 3\n')
    inst.start()
    for _ in range(100):
        if inst.status()['status'] != 'running':
            break
        time.sleep(0.05)
    assert inst.status()['status'] == 'failed' and inst.status()['code'] == 3


def test_takes_store_word_timed_cues(engine, tmp_path, monkeypatch):
    import app as server
    monkeypatch.setattr(server, 'engine', engine)
    monkeypatch.setattr(server, 'OUT', engine.out)
    meta = engine.synthesize({'text': 'Every morning the harbour wakes up early. Then the boats go out.', 'rate': 24000})
    cues = server.app.test_client().get(f"/api/recordings/{meta['id']}/cues").get_json()['cues']
    words = [w for c in cues for w in c['words']]
    assert [w['w'] for w in words][:3] == ['Every', 'morning', 'the']
    assert all(a['s'] <= b['s'] for a, b in zip(words, words[1:]))
    # Older takes without stored cues fall back to the SRT with estimated word times.
    j = json.loads((engine.out / f"{meta['id']}.json").read_text())
    j.pop('cues')
    (engine.out / f"{meta['id']}.json").write_text(json.dumps(j))
    old = server.app.test_client().get(f"/api/recordings/{meta['id']}/cues").get_json()['cues']
    assert old[0]['words'][0]['w'] == 'Every' and old[0]['words'][-1]['e'] <= old[0]['end'] + 1e-6
