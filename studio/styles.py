"""Emotion and output presets.

Kokoro has no explicit emotion input, so each emotion is described as a set of
prosody moves that the engine applies *inside* the model:

  speed   multiplier on Kokoro's duration predictor (native, artifact free)
  pitch   semitone offset applied to the predicted F0 curve
  range   multiplier on F0 movement around the speaker's mean (intonation width)
  tilt    semitones of rise (+) or fall (-) across each phrase
  tremor  semitones of 5.5 Hz wobble on voiced frames (nervous voice)
  energy  -1..1 nudge to the predicted energy curve (vocal effort)
  gain    dB applied to the rendered phrase
  gap     extra milliseconds of silence after each sentence
  punct   multiplier on the model's own pause length at commas/full stops

Values are the full-strength (100 %) setting; intensity scales them toward
neutral.
"""
from __future__ import annotations

import math

NEUTRAL = {'speed': 1.0, 'pitch': 0.0, 'range': 1.0, 'tilt': 0.0, 'tremor': 0.0,
           'energy': 0.0, 'gain': 0.0, 'gap': 0, 'punct': 1.0}

STYLES = {
    'neutral':   {**NEUTRAL, 'label': 'Neutral'},
    'warm':      {'label': 'Warm', 'speed': 0.95, 'pitch': 0.3, 'range': 1.08, 'tilt': -0.4, 'tremor': 0.0,
                  'energy': -0.1, 'gain': -0.5, 'gap': 70, 'punct': 1.12},
    'joyful':    {'label': 'Joyful', 'speed': 1.07, 'pitch': 1.0, 'range': 1.32, 'tilt': 0.3, 'tremor': 0.0,
                  'energy': 0.25, 'gain': 0.8, 'gap': -30, 'punct': 0.92},
    'excited':   {'label': 'Excited', 'speed': 1.14, 'pitch': 1.8, 'range': 1.5, 'tilt': 0.5, 'tremor': 0.0,
                  'energy': 0.45, 'gain': 1.5, 'gap': -80, 'punct': 0.8},
    'curious':   {'label': 'Curious', 'speed': 0.98, 'pitch': 0.6, 'range': 1.25, 'tilt': 1.0, 'tremor': 0.0,
                  'energy': 0.05, 'gain': 0.0, 'gap': 40, 'punct': 1.05},
    'confident': {'label': 'Confident', 'speed': 1.0, 'pitch': -0.5, 'range': 1.12, 'tilt': -0.6, 'tremor': 0.0,
                  'energy': 0.3, 'gain': 1.0, 'gap': 40, 'punct': 1.05},
    'calm':      {'label': 'Calm', 'speed': 0.93, 'pitch': -0.3, 'range': 0.8, 'tilt': -0.3, 'tremor': 0.0,
                  'energy': -0.2, 'gain': -1.0, 'gap': 120, 'punct': 1.2},
    'sad':       {'label': 'Sad', 'speed': 0.86, 'pitch': -1.2, 'range': 0.65, 'tilt': -1.1, 'tremor': 0.08,
                  'energy': -0.35, 'gain': -1.5, 'gap': 180, 'punct': 1.35},
    'angry':     {'label': 'Angry', 'speed': 1.06, 'pitch': 0.4, 'range': 1.38, 'tilt': -0.7, 'tremor': 0.0,
                  'energy': 0.55, 'gain': 2.0, 'gap': -30, 'punct': 0.85},
    'fearful':   {'label': 'Fearful', 'speed': 1.12, 'pitch': 1.3, 'range': 1.15, 'tilt': 0.3, 'tremor': 0.35,
                  'energy': -0.1, 'gain': -0.5, 'gap': 40, 'punct': 1.0},
    'dramatic':  {'label': 'Dramatic', 'speed': 0.88, 'pitch': -0.7, 'range': 1.42, 'tilt': -0.9, 'tremor': 0.0,
                  'energy': 0.2, 'gain': 0.5, 'gap': 250, 'punct': 1.5},
    'soft':      {'label': 'Soft', 'speed': 0.92, 'pitch': -0.2, 'range': 0.72, 'tilt': -0.2, 'tremor': 0.0,
                  'energy': -0.6, 'gain': -3.0, 'gap': 100, 'punct': 1.1},
}

# Older scripts and projects used these names.
ALIASES = {'tender': 'warm', 'whisper': 'soft', 'happy': 'joyful', 'scared': 'fearful', 'serious': 'confident'}

# Starting points for common video and audio deliverables.
OUTPUT_PRESETS = {
    'youtube':   {'label': 'YouTube narration', 'lufs': -14, 'peak': -1.0, 'rate': 48000,
                  'speed': 1.0, 'sentence_gap': 300, 'paragraph_gap': 700, 'punct': 1.1},
    'explainer': {'label': 'Explainer / tutorial', 'lufs': -16, 'peak': -1.0, 'rate': 48000,
                  'speed': 0.97, 'sentence_gap': 340, 'paragraph_gap': 800, 'punct': 1.15},
    'documentary': {'label': 'Documentary', 'lufs': -16, 'peak': -1.0, 'rate': 48000,
                    'speed': 0.92, 'sentence_gap': 450, 'paragraph_gap': 1000, 'punct': 1.3},
    'shorts':    {'label': 'Shorts / Reels / TikTok', 'lufs': -14, 'peak': -1.0, 'rate': 48000,
                  'speed': 1.1, 'sentence_gap': 150, 'paragraph_gap': 320, 'punct': 0.85},
    'ad':        {'label': 'Ad / promo', 'lufs': -14, 'peak': -1.0, 'rate': 48000,
                  'speed': 1.05, 'sentence_gap': 220, 'paragraph_gap': 450, 'punct': 0.95},
    'podcast':   {'label': 'Podcast', 'lufs': -16, 'peak': -1.0, 'rate': 44100,
                  'speed': 1.0, 'sentence_gap': 320, 'paragraph_gap': 700, 'punct': 1.1},
    'broadcast': {'label': 'Broadcast (EBU R128)', 'lufs': -23, 'peak': -1.0, 'rate': 48000,
                  'speed': 1.0, 'sentence_gap': 320, 'paragraph_gap': 700, 'punct': 1.1},
    'audiobook': {'label': 'Audiobook', 'lufs': -20, 'peak': -3.0, 'rate': 44100,
                  'speed': 0.95, 'sentence_gap': 420, 'paragraph_gap': 1100, 'punct': 1.25},
}


def canonical_style(name):
    name = str(name or '').strip().lower()
    name = ALIASES.get(name, name)
    return name if name in STYLES else None


def blend_style(name, intensity):
    """Return the prosody moves for `name` at `intensity` (0..1.25)."""
    style = STYLES[canonical_style(name) or 'neutral']
    k = max(0.0, min(1.25, float(intensity)))
    out = {}
    for key, base in NEUTRAL.items():
        target = style[key]
        if key in ('speed', 'range', 'punct'):
            # Multiplicative controls interpolate in log space.
            out[key] = math.exp(math.log(target) * k)
        else:
            out[key] = base + (target - base) * k
    return out


def public_styles():
    return [{'id': k, 'label': v['label'], **{p: v[p] for p in NEUTRAL}} for k, v in STYLES.items()]
