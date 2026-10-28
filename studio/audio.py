"""Audio finishing for voice-over: trimming, polish, loudness, true-peak limiting."""
from __future__ import annotations

import shutil
import subprocess
from math import gcd

import numpy as np
from scipy import signal
from scipy.ndimage import minimum_filter1d, uniform_filter1d

MODEL_RATE = 24000
RATES = (24000, 44100, 48000)


def db_to_amp(db):
    return 10.0 ** (db / 20.0)


def trim(y, sr=MODEL_RATE, threshold_db=-48.0, head_ms=18, tail_ms=70):
    """Cut the model's variable lead-in/out silence so gaps are exactly as directed.

    Returns (trimmed, samples_removed_from_start).
    """
    y = np.asarray(y, dtype=np.float32)
    if y.size == 0:
        return y, 0
    win = max(1, int(sr * 0.01))
    env = np.sqrt(np.maximum(uniform_filter1d(y.astype(np.float64) ** 2, win), 0.0))
    peak = env.max()
    if peak <= 0:
        return y[:0], 0
    above = np.flatnonzero(env > peak * db_to_amp(threshold_db))
    start = max(0, above[0] - int(sr * head_ms / 1000))
    end = min(y.size, above[-1] + int(sr * tail_ms / 1000))
    return y[start:end], int(start)


def fade_edges(y, sr=MODEL_RATE, ms=6):
    n = min(len(y) // 2, int(sr * ms / 1000))
    if n > 1:
        ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
        y = y.copy()
        y[:n] *= ramp
        y[-n:] *= ramp[::-1]
    return y


def room_tone(n, sr, level_db=-66.0, seed=7):
    """Quiet pink noise. Pure digital silence between lines sounds 'cut' in video."""
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(n + 2048)
    b = [0.049922035, -0.095993537, 0.050612699, -0.004408786]
    a = [1, -2.494956002, 2.017265875, -0.522189400]
    pink = signal.lfilter(b, a, white)[2048:]
    pink /= (np.sqrt(np.mean(pink ** 2)) or 1.0)
    return (pink * db_to_amp(level_db)).astype(np.float32)


def polish(y, sr):
    """Gentle voice-over chain: rumble filter, light compression, de-harsh shelf."""
    y = np.asarray(y, dtype=np.float64)
    sos = signal.butter(2, 70, 'highpass', fs=sr, output='sos')
    y = signal.sosfilt(sos, y)
    # RMS compressor, 2.2:1 above -22 dBFS, 8 ms attack / 120 ms release.
    level = np.sqrt(np.maximum(uniform_filter1d(y ** 2, int(sr * 0.012)), 1e-12))
    level_db = 20 * np.log10(level)
    over = np.maximum(0.0, level_db - (-22.0))
    gr_db = -over * (1 - 1 / 2.2)
    att = np.exp(-1.0 / (sr * 0.008))
    rel = np.exp(-1.0 / (sr * 0.120))
    smooth = _smooth_gain(gr_db, att, rel)
    y = y * (10 ** (smooth / 20))
    # A touch of high-shelf cut tames the occasional metallic edge of neural vocoders.
    y = _shelf(y, sr, 7500, -1.5)
    return y.astype(np.float32)


def _smooth_gain(gr_db, att, rel):
    """One-pole attack/release smoothing, vectorised by block for speed."""
    out = np.empty_like(gr_db)
    g = 0.0
    # Block-wise processing keeps this fast in pure numpy (2 ms resolution).
    block = 48
    for i in range(0, len(gr_db), block):
        target = gr_db[i:i + block].min()
        coef = att if target < g else rel
        coef_b = coef ** block
        g = target + (g - target) * coef_b
        out[i:i + block] = g
    return out


def _shelf(y, sr, freq, gain_db):
    A = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * freq / sr
    alpha = np.sin(w0) / 2 * np.sqrt(2)
    cosw = np.cos(w0)
    b = [A * ((A + 1) + (A - 1) * cosw + 2 * np.sqrt(A) * alpha),
         -2 * A * ((A - 1) + (A + 1) * cosw),
         A * ((A + 1) + (A - 1) * cosw - 2 * np.sqrt(A) * alpha)]
    a = [(A + 1) - (A - 1) * cosw + 2 * np.sqrt(A) * alpha,
         2 * ((A - 1) - (A + 1) * cosw),
         (A + 1) - (A - 1) * cosw - 2 * np.sqrt(A) * alpha]
    return signal.lfilter(np.array(b) / a[0], np.array(a) / a[0], y)


def _k_weighting(sr):
    """ITU-R BS.1770 K-weighting filters designed for any sample rate."""
    G, Q, fc = 3.99984385397, 0.7071752369554193, 1681.9744509555319
    K = np.tan(np.pi * fc / sr)
    Vh = 10 ** (G / 20)
    Vb = Vh ** 0.499666774155
    a0 = 1 + K / Q + K * K
    shelf = (np.array([Vh + Vb * K / Q + K * K, 2 * (K * K - Vh), Vh - Vb * K / Q + K * K]) / a0,
             np.array([1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]))
    Q, fc = 0.5003270373253953, 38.13547087613982
    K = np.tan(np.pi * fc / sr)
    a0 = 1 + K / Q + K * K
    hp = (np.array([1.0, -2.0, 1.0]),  # matches the ITU reference coefficients at 48 kHz
          np.array([1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]))
    return shelf, hp


def loudness(y, sr):
    """Integrated loudness in LUFS (mono, gated per BS.1770-4). -inf for silence."""
    y = np.asarray(y, dtype=np.float64)
    if y.size < int(0.4 * sr):
        y = np.pad(y, (0, int(0.4 * sr) - y.size))
    (b1, a1), (b2, a2) = _k_weighting(sr)
    z = signal.lfilter(b2, a2, signal.lfilter(b1, a1, y))
    block, step = int(0.4 * sr), int(0.1 * sr)
    csum = np.concatenate([[0.0], np.cumsum(z * z)])
    starts = np.arange(0, len(z) - block + 1, step)
    ms = (csum[starts + block] - csum[starts]) / block
    with np.errstate(divide='ignore'):
        lk = -0.691 + 10 * np.log10(ms)
    ms = ms[lk > -70]
    if ms.size == 0:
        return float('-inf')
    rel = -0.691 + 10 * np.log10(ms.mean()) - 10
    with np.errstate(divide='ignore'):
        keep = ms[(-0.691 + 10 * np.log10(ms)) > rel]
    if keep.size == 0:
        return float('-inf')
    return float(-0.691 + 10 * np.log10(keep.mean()))


_CHUNK = 1 << 19   # samples per block for oversampled peak detection (keeps memory flat on long takes)
_CONTEXT = 64      # extra samples either side so block edges match a whole-signal pass


def _peak_env(y):
    """Per-sample peak of |y| and its 4x-oversampled reconstruction (true peak), computed block-wise."""
    y = np.asarray(y, dtype=np.float64)
    env = np.empty(len(y), dtype=np.float64)
    for a in range(0, len(y), _CHUNK):
        b = min(len(y), a + _CHUNK)
        lo, hi = max(0, a - _CONTEXT), min(len(y), b + _CONTEXT)
        up = np.abs(signal.resample_poly(y[lo:hi], 4, 1))
        n = hi - lo
        per = up[: n * 4].reshape(n, 4).max(axis=1)[a - lo: a - lo + (b - a)]
        env[a:b] = np.maximum(per, np.abs(y[a:b]))
    return env


def true_peak_db(y):
    y = np.asarray(y, dtype=np.float64)
    if y.size == 0:
        return float('-inf')
    peak = float(_peak_env(y).max())
    return float(20 * np.log10(peak)) if peak > 0 else float('-inf')


def limit(y, sr, ceiling_db=-1.0):
    """Look-ahead true-peak limiter (4x oversampled detection)."""
    y = np.asarray(y, dtype=np.float64)
    ceiling = db_to_amp(ceiling_db)
    if y.size == 0:
        return y.astype(np.float32)
    for _ in range(3):
        env = _peak_env(y)
        if env.max() <= ceiling:
            break
        need = np.minimum(1.0, ceiling / np.maximum(env, 1e-12)) * 0.995
        del env
        win = max(3, int(sr * 0.004)) | 1
        g = uniform_filter1d(minimum_filter1d(need, win), win)
        # Slower recovery so the limiter doesn't pump on every syllable.
        rel = int(sr * 0.06) | 1
        g = np.minimum(g, uniform_filter1d(minimum_filter1d(need, rel), rel))
        del need
        y *= g
    return np.clip(y, -ceiling, ceiling).astype(np.float32)


def resample(y, src, dst):
    if src == dst:
        return np.asarray(y, dtype=np.float32)
    g = gcd(src, dst)
    return signal.resample_poly(np.asarray(y, dtype=np.float64), dst // g, src // g).astype(np.float32)


def finish(y, *, rate=48000, lufs=-16.0, peak_db=-1.0, do_polish=True, tone=False, src_rate=MODEL_RATE):
    """Full output chain. Returns (audio, rate, report)."""
    y = np.asarray(y, dtype=np.float32)
    if do_polish:
        y = polish(y, src_rate)
    y = resample(y, src_rate, rate)
    if tone:
        y = y + room_tone(len(y), rate)
    measured = loudness(y, rate)
    if lufs is not None and np.isfinite(measured):
        y = y * db_to_amp(float(lufs) - measured)
    y = limit(y, rate, peak_db)
    report = {'lufs': round(loudness(y, rate), 1), 'true_peak': round(true_peak_db(y), 1)}
    return y, rate, report


def encode_mp3(wav_path, mp3_path, bitrate='192k'):
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        return False
    proc = subprocess.run([ffmpeg, '-y', '-loglevel', 'error', '-i', str(wav_path), '-codec:a', 'libmp3lame',
                           '-b:a', bitrate, str(mp3_path)], capture_output=True, timeout=300)
    return proc.returncode == 0
