"""Kokoro rendering engine: script -> directed, finished voice-over."""
from __future__ import annotations

import io
import json
import math
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf

from . import audio as fx
from . import captions as cc
from .prosody import Shape, patch_model, shaping
from .script import parse_script
from .styles import OUTPUT_PRESETS, STYLES, blend_style, canonical_style

REPO_ID = 'hexgrad/Kokoro-82M'
SR = fx.MODEL_RATE
MAX_TEXT = 20000
MAX_ITEMS = 600
MAX_SECONDS = 60 * 30

VOICES = {
    'af_heart': ('Heart', 'American', 'Female'),
    'af_bella': ('Bella', 'American', 'Female'),
    'af_nicole': ('Nicole', 'American', 'Female'),
    'af_sarah': ('Sarah', 'American', 'Female'),
    'af_sky': ('Sky', 'American', 'Female'),
    'af_kore': ('Kore', 'American', 'Female'),
    'af_nova': ('Nova', 'American', 'Female'),
    'af_aoede': ('Aoede', 'American', 'Female'),
    'af_alloy': ('Alloy', 'American', 'Female'),
    'af_jessica': ('Jessica', 'American', 'Female'),
    'af_river': ('River', 'American', 'Female'),
    'am_adam': ('Adam', 'American', 'Male'),
    'am_michael': ('Michael', 'American', 'Male'),
    'am_fenrir': ('Fenrir', 'American', 'Male'),
    'am_puck': ('Puck', 'American', 'Male'),
    'am_eric': ('Eric', 'American', 'Male'),
    'am_echo': ('Echo', 'American', 'Male'),
    'am_liam': ('Liam', 'American', 'Male'),
    'am_onyx': ('Onyx', 'American', 'Male'),
    'am_santa': ('Santa', 'American', 'Male'),
    'bf_emma': ('Emma', 'British', 'Female'),
    'bf_isabella': ('Isabella', 'British', 'Female'),
    'bf_alice': ('Alice', 'British', 'Female'),
    'bf_lily': ('Lily', 'British', 'Female'),
    'bm_fable': ('Fable', 'British', 'Male'),
    'bm_george': ('George', 'British', 'Male'),
    'bm_daniel': ('Daniel', 'British', 'Male'),
    'bm_lewis': ('Lewis', 'British', 'Male'),
}
VOICE_NAMES = {k: v[0] for k, v in VOICES.items()}


def bounded(value, lower, upper, fallback):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return min(upper, max(lower, number)) if math.isfinite(number) else fallback


def _flag(value, default):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).lower() in ('1', 'true', 'yes', 'on')


def read_settings(payload):
    """Validate and normalise request settings."""
    voice = payload.get('voice', 'af_heart')
    if voice not in VOICES:
        raise ValueError('Choose a supported Kokoro voice.')
    style = canonical_style(payload.get('style', 'neutral'))
    if not style:
        raise ValueError('Choose a supported delivery style.')
    blend_voice = payload.get('blend_voice') or None
    if blend_voice is not None and (blend_voice not in VOICES or blend_voice == voice):
        blend_voice = None
    lufs = payload.get('lufs', -16)
    lufs = None if lufs in (None, '', 'off') else bounded(lufs, -30, -8, -16)
    rate = int(bounded(payload.get('rate', 48000), 8000, 96000, 48000))
    if rate not in fx.RATES:
        rate = 48000
    target = payload.get('target_seconds')
    target = bounded(target, 1, MAX_SECONDS, None) if target not in (None, '', 0, '0') else None
    rules = payload.get('pronunciations') or []
    if not isinstance(rules, list):
        rules = []
    return {
        'voice': voice, 'style': style,
        'intensity': bounded(payload.get('intensity', 60), 0, 125, 60) / 100,
        'speed': bounded(payload.get('speed', 1), 0.6, 1.6, 1),
        'pitch': bounded(payload.get('pitch', 0), -4, 4, 0),
        'range': bounded(payload.get('range', 1), 0.4, 1.8, 1),
        'energy': bounded(payload.get('energy', 0), -1, 1, 0),
        'punct': bounded(payload.get('punct', 1), 0.5, 2.5, 1),
        'sentence_gap': int(bounded(payload.get('sentence_gap', 300), 0, 2000, 300)),
        'paragraph_gap': int(bounded(payload.get('paragraph_gap', 700), 0, 4000, 700)),
        'blend_voice': blend_voice,
        'blend': bounded(payload.get('blend', 0.3), 0, 1, 0.3) if blend_voice else 0.0,
        'blend_mode': 'delivery' if payload.get('blend_mode') == 'delivery' else 'full',
        'lufs': lufs, 'peak': bounded(payload.get('peak', -1), -6, 0, -1), 'rate': rate,
        'polish': _flag(payload.get('polish'), True), 'room_tone': _flag(payload.get('room_tone'), False),
        'mp3': _flag(payload.get('mp3'), False), 'target_seconds': target,
        'pronunciations': [r for r in rules[:200] if isinstance(r, dict)],
    }


class Engine:
    def __init__(self, out_dir):
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.model = None
        self.pipelines = {}
        self.patched = None
        self.lock = threading.RLock()

    # ---- model -----------------------------------------------------------
    def _load_model(self):
        if self.model is not None:
            return self.model
        try:
            import torch
            from kokoro import KModel
        except ImportError as err:
            raise RuntimeError('Kokoro is not installed. Run ./start.sh to install the dependencies.') from err
        force = os.environ.get('KOKORO_DEVICE', 'auto').lower()
        device = ('cuda' if torch.cuda.is_available() else 'cpu') if force == 'auto' else force
        model = KModel(repo_id=REPO_ID).to(device).eval()
        self.patched = patch_model(model)
        self.model = model
        return model

    def pipeline(self, lang):
        if lang not in self.pipelines:
            from kokoro import KPipeline
            # One shared model for US and UK pipelines halves memory use.
            self.pipelines[lang] = KPipeline(lang_code=lang, repo_id=REPO_ID, model=self._load_model())
        return self.pipelines[lang]

    def voice_ref(self, pipe, voice, blend_voice=None, amount=0.0, mode='full'):
        """Return a voice id or a cached blended voice key usable by the pipeline."""
        if not blend_voice or amount <= 0.001:
            return voice
        key = f'{voice}+{blend_voice}@{amount:.2f}:{mode}'
        if key not in pipe.voices:
            base = pipe.load_voice(voice)
            other = pipe.load_voice(blend_voice)
            mix = base.clone()
            if mode == 'delivery':
                # Kokoro's style vector: [:128] timbre (decoder), [128:] prosody (predictor).
                mix[..., 128:] = base[..., 128:] * (1 - amount) + other[..., 128:] * amount
            else:
                mix = base * (1 - amount) + other * amount
            pipe.voices[key] = mix
        return key

    # ---- rendering -------------------------------------------------------
    def _speak(self, item, cfg, speed_scale, index):
        style = item['style'] or cfg['style']
        intensity = cfg['intensity'] if item['intensity'] is None else item['intensity']
        moves = blend_style(style, intensity)
        voice = item['voice'] or cfg['voice']
        pipe = self.pipeline(voice[0])
        if voice == cfg['voice']:
            ref = self.voice_ref(pipe, voice, cfg['blend_voice'], cfg['blend'], cfg['blend_mode'])
        else:
            ref = voice
        speed = bounded(cfg['speed'] * item['speed'] * moves['speed'] * speed_scale, 0.5, 2.0, 1.0)
        shape = Shape(pitch=cfg['pitch'] + moves['pitch'], range=cfg['range'] * moves['range'],
                      tilt=moves['tilt'], tremor=moves['tremor'],
                      energy=max(-1.0, min(1.0, cfg['energy'] + moves['energy'])),
                      punct=cfg['punct'] * moves['punct'], seed=index * 97)
        pieces, words, t = [], [], 0.0
        with shaping(shape):
            for result in pipe(item['speak'], voice=ref, speed=speed, split_pattern=None):
                wave = getattr(result, 'audio', None) if not isinstance(result, tuple) else result[2]
                if wave is None:
                    continue
                if hasattr(wave, 'detach'):
                    wave = wave.detach().cpu().numpy()
                wave = np.asarray(wave, dtype=np.float32).reshape(-1)
                wave, cut = fx.trim(wave)
                if not wave.size:
                    continue
                if not self.patched and abs(shape.pitch) > 0.04:
                    wave = _fallback_pitch(wave, shape.pitch)
                if pieces:
                    gap = np.zeros(int(SR * 0.12), dtype=np.float32)
                    pieces.append(gap)
                    t += len(gap) / SR
                tokens = None if isinstance(result, tuple) else getattr(result, 'tokens', None)
                words += cc.words_from_tokens(tokens, offset_s=t - cut / SR)
                pieces.append(wave)
                t += len(wave) / SR
        if not pieces:
            raise RuntimeError(f'Kokoro produced no audio for: "{item["text"][:60]}"')
        y = np.concatenate(pieces) * fx.db_to_amp(moves['gain'])
        return {'audio': fx.fade_edges(y.astype(np.float32)), 'words': words, 'style': style,
                'voice': voice, 'gap': moves['gap'], 'text': item['text']}

    def _render_pass(self, items, cfg, speed_scale):
        timeline, last = [], None
        for i, item in enumerate(items):
            if item['kind'] == 'pause':
                if item.get('para'):
                    extra = last['gap'] if last else 0
                    ms = max(0, cfg['paragraph_gap'] + extra)
                    timeline.append({'kind': 'gap', 'ms': ms, 'flex': True})
                else:
                    timeline.append({'kind': 'gap', 'ms': item['ms'], 'flex': False})
                continue
            if last is not None and timeline and timeline[-1]['kind'] == 'speech':
                timeline.append({'kind': 'gap', 'ms': _gap_after(last, cfg), 'flex': True})
            seg = self._speak(item, cfg, speed_scale, i)
            seg['kind'] = 'speech'
            timeline.append(seg)
            last = seg
        return timeline

    def render(self, text, cfg):
        """Return (audio @24 kHz, segments, sentence word lists, warnings, info)."""
        items, warnings = parse_script(text, VOICE_NAMES, cfg['pronunciations'])
        if not any(i['kind'] == 'speech' for i in items):
            raise ValueError('The script has no speakable text.')
        if len(items) > MAX_ITEMS:
            raise ValueError('The script is too long for one take. Split it into parts.')
        timeline = self._render_pass(items, cfg, 1.0)
        info = {'passes': 1}
        target = cfg['target_seconds']
        if target:
            speech = sum(len(s['audio']) for s in timeline if s['kind'] == 'speech') / SR
            fixed = sum(g['ms'] for g in timeline if g['kind'] == 'gap' and not g['flex']) / 1000
            flex = sum(g['ms'] for g in timeline if g['kind'] == 'gap' and g['flex']) / 1000
            room = target - speech - fixed
            if not (flex * 0.5 <= room <= flex * 2.0 + 0.25):
                # Re-render at a new pace so the speech itself fits, then trim gaps.
                ratio = speech / max(0.5, target - fixed - flex)
                scale = bounded(ratio, 0.7, 1.45, 1.0)
                timeline = self._render_pass(items, cfg, scale)
                info['passes'] = 2
                info['pace'] = round(scale, 3)
                speech = sum(len(s['audio']) for s in timeline if s['kind'] == 'speech') / SR
                room = target - speech - fixed
            factor = bounded(room / flex, 0.25, 4.0, 1.0) if flex > 0 else 1.0
            for g in timeline:
                if g['kind'] == 'gap' and g['flex']:
                    g['ms'] = g['ms'] * factor
            info['gap_scale'] = round(factor, 3)

        chunks, segments, sentences, t = [], [], [], 0.0
        for part in timeline:
            if part['kind'] == 'gap':
                n = int(round(SR * part['ms'] / 1000))
                if n:
                    chunks.append(np.zeros(n, dtype=np.float32))
                    t += n / SR
                continue
            a = part['audio']
            dur = len(a) / SR
            words = cc.align(part['text'], [dict(w, start=w['start'], end=w['end']) for w in part['words']], 0.0, dur)
            for w in words:
                w['start'] = round(t + max(0.0, min(dur, w['start'])), 3)
                w['end'] = round(t + max(0.0, min(dur, w['end'])), 3)
            sentences.append({'words': words})
            segments.append({'start': round(t, 3), 'end': round(t + dur, 3), 'style': part['style'],
                             'voice': part['voice'], 'text': part['text'][:200]})
            chunks.append(a)
            t += dur
        data = np.concatenate(chunks)
        if len(data) > SR * MAX_SECONDS:
            raise ValueError('Recording exceeded the 30-minute safety limit.')
        return data, segments, sentences, warnings, info

    def synthesize(self, payload, save=True):
        text = payload.get('text', '')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Enter some text to speak.')
        if len(text) > MAX_TEXT:
            raise ValueError(f'The current limit is {MAX_TEXT:,} characters per take.')
        cfg = read_settings(payload)
        with self.lock:
            data, segments, sentences, warnings, info = self.render(text, cfg)
        y, rate, report = fx.finish(data, rate=cfg['rate'], lufs=cfg['lufs'], peak_db=cfg['peak'],
                                    do_polish=cfg['polish'], tone=cfg['room_tone'])
        if not save:
            buf = io.BytesIO()
            sf.write(buf, y, rate, format='WAV', subtype='PCM_16')
            return buf.getvalue(), {'seconds': round(len(y) / rate, 2), **report, 'warnings': warnings}
        cues = cc.build_cues(sentences)
        rid = uuid.uuid4().hex
        wav = self.out / f'{rid}.wav'
        sf.write(str(wav), y, rate, subtype='PCM_16' if rate == SR else 'PCM_24')
        has_mp3 = cfg['mp3'] and fx.encode_mp3(wav, self.out / f'{rid}.mp3')
        (self.out / f'{rid}.srt').write_text(cc.to_srt(cues), encoding='utf-8')
        (self.out / f'{rid}.vtt').write_text(cc.to_vtt(cues), encoding='utf-8')
        name = str(payload.get('name') or '').strip()[:80] or _title_from(text)
        settings = {k: v for k, v in payload.items() if k not in ('text', 'name')}
        meta = {
            'id': rid, 'name': name, 'voice': cfg['voice'], 'style': cfg['style'],
            'seconds': round(len(y) / rate, 2), 'rate': rate, 'created': datetime.now(timezone.utc).isoformat(),
            'chunks': len(segments), 'lufs': report['lufs'], 'true_peak': report['true_peak'],
            'target_lufs': cfg['lufs'], 'mp3': bool(has_mp3), 'captions': len(cues), 'fit': info,
            'warnings': warnings, 'segments': segments, 'text': text, 'settings': settings,
        }
        (self.out / f'{rid}.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
        return meta


def _gap_after(seg, cfg):
    """Silence after a phrase, read from how the phrase ended."""
    end = seg['text'].rstrip()[-1:] if seg['text'].strip() else '.'
    base = cfg['sentence_gap']
    if end in '.!?…"”)':
        ms = base + seg['gap']
        if seg['text'].rstrip().endswith(('...', '…')):
            ms += 180
    elif end in ',;:—–-':
        ms = base * 0.45 + seg['gap'] * 0.5
    else:
        ms = 70
    return max(0, ms)


def _title_from(text):
    clean = re.sub(r'\[[^\]]*\](?!\()', ' ', text)
    clean = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', clean).replace('*', '')
    clean = ' '.join(clean.split())
    return (clean[:60] + ('…' if len(clean) > 60 else '')) or 'Untitled take'


def _fallback_pitch(y, semitones):
    """Used only if the installed Kokoro is too different to patch."""
    try:
        import librosa
    except ImportError:
        return y
    return librosa.effects.pitch_shift(y=y, sr=SR, n_steps=semitones).astype(np.float32)


def public_catalog():
    return {
        'voices': [{'id': k, 'name': v[0], 'accent': v[1], 'gender': v[2]} for k, v in VOICES.items()],
        'styles': [{'id': k, 'label': v['label'], **{p: v[p] for p in ('speed', 'pitch', 'range', 'tilt', 'energy', 'gap')}}
                   for k, v in STYLES.items()],
        'presets': OUTPUT_PRESETS,
    }
