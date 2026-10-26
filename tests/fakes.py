"""A stand-in for KPipeline: produces tone 'speech' with word timestamps."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from studio import prosody

SR = 24000


@dataclass
class Tok:
    text: str
    whitespace: str
    start_ts: float | None = None
    end_ts: float | None = None


@dataclass
class Result:
    graphemes: str
    audio: np.ndarray
    tokens: list


class FakePipeline:
    def __init__(self, lang='a'):
        self.lang_code = lang
        self.voices = {}
        self.calls = []
        self.g2p = lambda text: (text, [])

    def load_voice(self, voice):
        if voice not in self.voices:
            import torch
            g = torch.Generator().manual_seed(sum(map(ord, voice)))
            self.voices[voice] = torch.randn(510, 1, 256, generator=g)
        return self.voices[voice]

    def __call__(self, text, voice, speed=1.0, split_pattern=None):
        shape = prosody.active_shape()
        self.calls.append({'text': text, 'voice': voice, 'speed': speed, 'shape': shape})
        words = text.replace('[', '').replace(']', ' ').split()
        lead, per_word = 0.15, 0.32 / speed
        n = int(SR * (lead * 2 + per_word * len(words)))
        t = np.arange(n) / SR
        f0 = 160 * 2 ** ((shape.pitch if shape else 0) / 12)
        y = (0.2 * np.sin(2 * np.pi * f0 * t)).astype(np.float32)
        y[: int(SR * lead)] = 0
        y[-int(SR * lead):] = 0
        tokens, pos = [], lead
        for w in words:
            tokens.append(Tok(w, ' ', pos, pos + per_word * 0.9))
            pos += per_word
        yield Result(text, y, tokens)
