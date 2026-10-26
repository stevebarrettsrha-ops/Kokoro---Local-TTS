"""In-model prosody control for Kokoro.

Kokoro (a StyleTTS2 derivative) predicts a pitch curve (F0, in Hz, 80 frames/s)
and an energy curve, then hands both to its iSTFTNet vocoder. Shaping those
curves *before* the vocoder changes intonation without the phasey artifacts of
pitch-shifting finished audio, and keeps formants (the voice's timbre) intact.

`patch_model(model)` replaces `model.forward_with_tokens` with a copy of
Kokoro 0.9.x's implementation that reads the active `Shape` and:
  * scales the duration of punctuation tokens (comma / full-stop pauses),
  * re-shapes F0: pitch offset, intonation range, phrase tilt, tremor,
  * nudges the energy curve (vocal effort).
With a neutral Shape the output is identical to stock Kokoro.
"""
from __future__ import annotations

import contextlib
import math
import threading
import types
from dataclasses import dataclass, field

F0_FRAME_RATE = 80.0  # Kokoro: 600 samples per duration frame, F0 is upsampled x2.
PUNCTUATION = ',.!?;:—…'

_local = threading.local()


@dataclass
class Shape:
    pitch: float = 0.0     # semitones
    range: float = 1.0     # x intonation width
    tilt: float = 0.0      # semitones rise/fall across the phrase
    tremor: float = 0.0    # semitones of 5.5 Hz wobble
    energy: float = 0.0    # -1..1
    punct: float = 1.0     # x pause length at punctuation
    seed: int = field(default=0)

    def is_neutral(self):
        return (abs(self.pitch) < 1e-3 and abs(self.range - 1) < 1e-3 and abs(self.tilt) < 1e-3
                and abs(self.tremor) < 1e-3 and abs(self.energy) < 1e-3 and abs(self.punct - 1) < 1e-3)


def active_shape():
    return getattr(_local, 'shape', None)


@contextlib.contextmanager
def shaping(shape):
    previous = active_shape()
    _local.shape = shape
    try:
        yield
    finally:
        _local.shape = previous


def shape_f0(f0, shape):
    """Return a reshaped copy of an F0 tensor shaped [batch, frames] (Hz)."""
    import torch

    if shape is None or (abs(shape.pitch) < 1e-3 and abs(shape.range - 1) < 1e-3
                         and abs(shape.tilt) < 1e-3 and abs(shape.tremor) < 1e-3):
        return f0
    # Soft voicing weight: unvoiced frames (F0 ~ 0) pass through untouched, and
    # the ramp avoids a hard edge at voicing onsets.
    w = ((f0 - 45.0) / 30.0).clamp(0.0, 1.0)
    log_f0 = torch.log2(f0.clamp(min=1.0))
    total = w.sum(dim=-1, keepdim=True)
    if float(total.min()) < 3:
        return f0 * (2.0 ** (shape.pitch / 12.0)) if abs(shape.pitch) >= 1e-3 else f0
    mean = (log_f0 * w).sum(dim=-1, keepdim=True) / total
    dev = (log_f0 - mean).clamp(-1.0, 1.0)  # at most one octave from the mean
    n = f0.shape[-1]
    idx = torch.arange(n, device=f0.device, dtype=f0.dtype)
    ramp = (idx / max(1, n - 1)) - 0.5
    target = mean + dev * shape.range + (shape.pitch + shape.tilt * ramp) / 12.0
    if shape.tremor:
        phase = (shape.seed % 628) / 100.0
        wobble = torch.sin(2 * math.pi * 5.5 * idx / F0_FRAME_RATE + phase)
        # Slight irregularity so it reads as a shaky voice rather than vibrato.
        wobble = wobble * (0.75 + 0.25 * torch.sin(2 * math.pi * 1.3 * idx / F0_FRAME_RATE))
        target = target + shape.tremor * wobble / 12.0
    shaped = (2.0 ** target).clamp(55.0, 700.0)
    return f0 * (1 - w) + shaped * w


def shape_energy(n_curve, f0, shape):
    if shape is None or abs(shape.energy) < 1e-3:
        return n_curve
    w = ((f0 - 45.0) / 30.0).clamp(0.0, 1.0)
    return n_curve + 0.35 * shape.energy * w


def _forward_with_tokens(self, input_ids, ref_s, speed=1):
    """Kokoro 0.9.x KModel.forward_with_tokens with prosody hooks."""
    import torch

    shape = active_shape()
    input_lengths = torch.full((input_ids.shape[0],), input_ids.shape[-1],
                               device=input_ids.device, dtype=torch.long)
    text_mask = torch.arange(input_lengths.max()).unsqueeze(0).expand(input_lengths.shape[0], -1).type_as(input_lengths)
    text_mask = torch.gt(text_mask + 1, input_lengths.unsqueeze(1)).to(self.device)
    bert_dur = self.bert(input_ids, attention_mask=(~text_mask).int())
    d_en = self.bert_encoder(bert_dur).transpose(-1, -2)
    s = ref_s[:, 128:]
    d = self.predictor.text_encoder(d_en, s, input_lengths, text_mask)
    x, _ = self.predictor.lstm(d)
    duration = self.predictor.duration_proj(x)
    duration = torch.sigmoid(duration).sum(axis=-1) / speed
    if shape is not None and abs(shape.punct - 1) > 1e-3 and self._studio_punct_ids is not None:
        mask = torch.isin(input_ids, self._studio_punct_ids.to(input_ids.device))
        duration = torch.where(mask, duration * shape.punct, duration)
    pred_dur = torch.round(duration).clamp(min=1).long().squeeze()
    indices = torch.repeat_interleave(torch.arange(input_ids.shape[1], device=self.device), pred_dur)
    pred_aln_trg = torch.zeros((input_ids.shape[1], indices.shape[0]), device=self.device)
    pred_aln_trg[indices, torch.arange(indices.shape[0])] = 1
    pred_aln_trg = pred_aln_trg.unsqueeze(0).to(self.device)
    en = d.transpose(-1, -2) @ pred_aln_trg
    F0_pred, N_pred = self.predictor.F0Ntrain(en, s)
    if shape is not None:
        N_pred = shape_energy(N_pred, F0_pred, shape)
        F0_pred = shape_f0(F0_pred, shape)
    t_en = self.text_encoder(input_ids, input_lengths, text_mask)
    asr = t_en @ pred_aln_trg
    audio = self.decoder(asr, F0_pred, N_pred, ref_s[:, :128]).squeeze()
    return audio, pred_dur


def patch_model(model):
    """Install the hooks. Returns False (model untouched) if the layout differs."""
    import torch

    needed = ('bert', 'bert_encoder', 'predictor', 'text_encoder', 'decoder', 'vocab')
    if not all(hasattr(model, a) for a in needed) or not hasattr(model.predictor, 'F0Ntrain'):
        return False
    ids = sorted({model.vocab[p] for p in PUNCTUATION if p in getattr(model, 'vocab', {})})
    model._studio_punct_ids = torch.tensor(ids, dtype=torch.long) if ids else None
    model.forward_with_tokens = types.MethodType(torch.no_grad()(_forward_with_tokens), model)
    model._studio_patched = True
    return True
