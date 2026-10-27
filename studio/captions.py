"""Word timings -> subtitle cues (SRT / WebVTT) sized for video."""
from __future__ import annotations

import re

MAX_LINE = 42      # common broadcast / YouTube guideline
MAX_LINES = 2
MAX_CUE_SECONDS = 6.0
MIN_CUE_SECONDS = 0.8


def words_from_tokens(tokens, offset_s=0.0):
    """Merge misaki tokens back into whitespace-separated words with times.

    Tokens such as "do" + "n't" + "," carry whitespace='' and are joined to
    form the word "don't,". Returns [{'w', 'start', 'end'}] or [] if timings
    are unavailable.
    """
    words, cur = [], None
    for t in tokens or []:
        text = getattr(t, 'text', '') or ''
        start, end = getattr(t, 'start_ts', None), getattr(t, 'end_ts', None)
        if cur is None:
            cur = {'w': '', 'start': None, 'end': None}
        cur['w'] += text
        if start is not None and cur['start'] is None:
            cur['start'] = start + offset_s
        if end is not None:
            cur['end'] = end + offset_s
        if getattr(t, 'whitespace', ' '):
            words.append(cur)
            cur = None
    if cur is not None:
        words.append(cur)
    words = [w for w in words if w['w'].strip()]
    if not words or all(w['start'] is None for w in words):
        return []
    return words


def align(display, timed, start, end):
    """Attach times to the words the viewer should read.

    If the timed words line up one-to-one with the display words, use them;
    otherwise (pronunciation substitutions, numbers read out, etc.) spread the
    span proportionally by character length.
    """
    shown = display.split()
    if not shown:
        return []
    if timed and len(timed) == len(shown) and all(w['start'] is not None for w in timed):
        out = []
        for word, t in zip(shown, timed):
            out.append({'w': word, 'start': t['start'], 'end': t['end'] if t['end'] is not None else t['start']})
        return out
    if timed:
        s = [w['start'] for w in timed if w['start'] is not None]
        e = [w['end'] for w in timed if w['end'] is not None]
        if s and e:
            start, end = min(s), max(e)
    weights = [len(w) + 1 for w in shown]
    total = float(sum(weights))
    out, t = [], start
    for word, wgt in zip(shown, weights):
        d = (end - start) * wgt / total
        out.append({'w': word, 'start': t, 'end': t + d})
        t += d
    return out


def _wrap(text):
    if len(text) <= MAX_LINE:
        return text
    words = text.split()
    best, best_score = text, None
    for i in range(1, len(words)):
        a, b = ' '.join(words[:i]), ' '.join(words[i:])
        if len(a) > MAX_LINE or len(b) > MAX_LINE:
            continue
        score = abs(len(a) - len(b))
        # Prefer breaking after punctuation.
        if re.search(r'[,;:.!?]$', a):
            score -= 8
        if best_score is None or score < best_score:
            best, best_score = f'{a}\n{b}', score
    return best


def build_cues(sentences):
    """sentences: [{'words': [{'w','start','end'}]}] -> [{'start','end','text'}]"""
    cues = []
    limit = MAX_LINE * MAX_LINES
    for sent in sentences:
        words = sent.get('words') or []
        group = []
        for w in words:
            candidate = ' '.join(x['w'] for x in group + [w])
            too_long = len(candidate) > limit
            too_slow = group and (w['end'] - group[0]['start']) > MAX_CUE_SECONDS
            if group and (too_long or too_slow):
                cues.append(group)
                group = []
            group.append(w)
            # Natural break after a clause once the cue has some substance.
            if re.search(r'[,;:—]$', w['w']) and len(' '.join(x['w'] for x in group)) > limit * 0.6:
                cues.append(group)
                group = []
        if group:
            cues.append(group)
    out = []
    for g in cues:
        out.append({'start': g[0]['start'], 'end': g[-1]['end'], 'text': _wrap(' '.join(x['w'] for x in g)),
                    'words': [{'w': x['w'], 's': round(x['start'], 3), 'e': round(x['end'], 3)} for x in g]})
    # Give short cues reading time without overlapping the next one.
    for i, c in enumerate(out):
        nxt = out[i + 1]['start'] if i + 1 < len(out) else c['end'] + 2
        if c['end'] - c['start'] < MIN_CUE_SECONDS:
            c['end'] = min(c['start'] + MIN_CUE_SECONDS, nxt - 0.04)
        c['end'] = max(c['end'], c['start'] + 0.2)
    return out


def _ts(seconds, sep):
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f'{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}'


def to_srt(cues):
    blocks = [f'{i}\n{_ts(c["start"], ",")} --> {_ts(c["end"], ",")}\n{c["text"]}\n' for i, c in enumerate(cues, 1)]
    return '\n'.join(blocks)


def to_vtt(cues):
    blocks = [f'{_ts(c["start"], ".")} --> {_ts(c["end"], ".")}\n{c["text"]}\n' for c in cues]
    return 'WEBVTT\n\n' + '\n'.join(blocks)


def spread_words(cue):
    """Word timings for a cue that only has text (older takes): share its span by word length."""
    words = cue['text'].split()
    total = float(sum(len(w) + 1 for w in words)) or 1.0
    out, t = [], cue['start']
    for w in words:
        d = (cue['end'] - cue['start']) * (len(w) + 1) / total
        out.append({'w': w, 's': round(t, 3), 'e': round(t + d, 3)})
        t += d
    return out
