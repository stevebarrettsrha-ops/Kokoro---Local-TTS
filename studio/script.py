"""Script notation -> ordered list of speech and silence items.

Supported directions (case-insensitive):
  [sad]  [sad:40]        emotion, optionally with its own intensity (0-100)
  [default]              go back to the delivery chosen in the side panel
  [pause=750] [pause=1.5s] [beat]   silence (beat = 350 ms)
  [voice=george]         switch speaker (voice id or display name)
  [speed=1.1]            relative pace for the following text (1 = normal)
  *word*                 emphasis (raises lexical stress)
  [word](/fəˈnɛtɪks/)    exact phonemes for one spot (IPA is cleaned up)
  [word](yah-HOO-ah)     sounds-like spelling for one spot; CAPS = stress

Speech is split into sentences so pacing and captions can be controlled per
sentence. Blank lines become paragraph breaks.
"""
from __future__ import annotations

import re

from .phonemes import fix_heteronyms, normalize_ipa
from .styles import canonical_style

MAX_PAUSE_MS = 10000
BEAT_MS = 350

TAG_RE = re.compile(r'\[\s*([A-Za-z_]+)\s*(?:[:=]\s*([^\]\s]+)\s*)?\](?!\()')
LINK_RE = re.compile(r'\[([^\]]+)\]\(([^)]*)\)')
EMPHASIS_RE = re.compile(r'\*([^*\n]{1,120}?)\*')
ABBREVIATIONS = {'mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr', 'st', 'vs', 'etc', 'e.g', 'i.e', 'no',
                 'fig', 'approx', 'inc', 'ltd', 'co', 'mt', 'u.s', 'u.k', 'a.m', 'p.m'}
SENTENCE_END_RE = re.compile(r'([.!?…]+["”’)\]]*)\s+(?=["“‘(\[]?[A-Z0-9])')


def _parse_ms(value):
    value = (value or '').strip().lower()
    try:
        if value.endswith('ms'):
            ms = float(value[:-2])
        elif value.endswith('s'):
            ms = float(value[:-1]) * 1000
        else:
            ms = float(value)
    except ValueError:
        return None
    return int(max(0, min(MAX_PAUSE_MS, ms)))


def split_sentences(text):
    """Split a paragraph into sentences without breaking on common abbreviations."""
    text = ' '.join(text.split())
    if not text:
        return []
    parts, start = [], 0
    for m in SENTENCE_END_RE.finditer(text):
        before = text[start:m.start()].rsplit(' ', 1)[-1].lower().rstrip('.')
        if m.group(1).startswith('.') and before in ABBREVIATIONS:
            continue
        if m.group(1).startswith('.') and len(before) == 1 and before.isalpha():
            continue  # initials such as "J. Smith"
        parts.append(text[start:m.end(1)].strip())
        start = m.end()
    parts.append(text[start:].strip())
    parts = [p for p in parts if p]
    # Very short fragments ("Yes." "OK.") sound unnatural in isolation; attach them.
    merged = []
    for p in parts:
        if merged and (len(p) < 12 or len(merged[-1]) < 12):
            merged[-1] = f'{merged[-1]} {p}'
        else:
            merged.append(p)
    return merged


def apply_pronunciations(text, rules, resolve=None):
    """Replace whole words using user rules.

    `resolve(say)` turns a rule's "say it like" value into phonemes; when it
    returns None (no G2P available) the value is spoken as plain text.
    """
    for rule in rules or []:
        word = str(rule.get('word', '')).strip()
        say = str(rule.get('say', '')).strip()
        if not word or not say or len(word) > 60 or len(say) > 160:
            continue
        pattern = re.compile(r'(?<![\w\[])' + re.escape(word) + r'(?![\w\]])', re.I)
        ps = resolve(say) if resolve else None
        if ps is None and len(say) > 2 and say.startswith('/') and say.endswith('/'):
            ps = normalize_ipa(say)
        if ps:
            text = pattern.sub(lambda m, ps=ps: f'[{m.group(0)}](/{ps}/)', text)
        else:
            text = pattern.sub(lambda _m, say=say: say, text)
    return text


def resolve_links(text, resolve=None):
    """Clean inline [word](...) links: tidy IPA, turn sounds-like into phonemes."""
    def fix(m):
        word, f = m.group(1), m.group(2).strip()
        if re.fullmatch(r'[+-]?\d+(\.5)?|#.*#', f):
            return m.group(0)  # misaki stress / number flags
        ps = resolve(f) if resolve else None
        if ps is None and len(f) > 2 and f.startswith('/') and f.endswith('/'):
            ps = normalize_ipa(f)
        if ps:
            return f'[{word}](/{ps}/)'
        return word if not f else f  # no G2P: speak the respelling as text
    return LINK_RE.sub(fix, text)


def prepare_speech(sentence, rules=None, resolve=None, heteronyms=True):
    text = resolve_links(sentence, resolve)
    text = apply_pronunciations(text, rules, resolve)
    if heteronyms:
        text = fix_heteronyms(text)
    return apply_emphasis(text)


def apply_emphasis(text):
    def stress(m):
        words = []
        for w in m.group(1).split():
            if LINK_RE.fullmatch(w) or not re.search(r'\w', w):
                words.append(w)
                continue
            core = re.match(r'^(\W*)(.*?)(\W*)$', w)
            words.append(f'{core.group(1)}[{core.group(2)}](+2){core.group(3)}')
        return ' '.join(words)
    return EMPHASIS_RE.sub(stress, text)


def display_text(text):
    """What a viewer should read in captions: no markup, original words."""
    text = LINK_RE.sub(lambda m: m.group(1), text)
    text = EMPHASIS_RE.sub(lambda m: m.group(1), text)
    return ' '.join(text.split())


def parse_script(source, voices=None, pronunciations=None, resolve=None, heteronyms=True):
    """Return (items, warnings).

    `voices` maps voice id -> display name and is used to validate [voice=...].
    Speech items carry style=None when the panel's default style should apply.
    """
    voices = voices or {}
    by_name = {name.lower(): vid for vid, name in voices.items()}
    items, warnings = [], []
    state = {'style': None, 'intensity': None, 'voice': None, 'speed': 1.0}

    def add_text(piece):
        paragraphs = re.split(r'\n\s*\n+', piece)
        for i, para in enumerate(paragraphs):
            for sentence in split_sentences(para):
                shown = display_text(sentence)
                if not re.search(r'\w', shown):
                    continue
                speak = prepare_speech(sentence, pronunciations, resolve, heteronyms)
                items.append({'kind': 'speech', 'text': shown, 'speak': speak, **state})
            if i < len(paragraphs) - 1:
                items.append({'kind': 'pause', 'ms': None, 'para': True})

    offset = 0
    for m in TAG_RE.finditer(source):
        add_text(source[offset:m.start()])
        offset = m.end()
        name, value = m.group(1).lower(), m.group(2)
        style = canonical_style(name)
        if style:
            state['style'] = style
            state['intensity'] = None
            if value is not None:
                try:
                    state['intensity'] = max(0.0, min(125.0, float(value.rstrip('%')))) / 100
                except ValueError:
                    warnings.append(f'Ignored intensity in [{m.group(0)[1:-1]}].')
        elif name == 'default':
            state.update(style=None, intensity=None)
        elif name in ('pause', 'wait', 'silence'):
            ms = _parse_ms(value or '600')
            if ms is None:
                warnings.append(f'Could not read pause length in {m.group(0)}.')
            elif ms:
                items.append({'kind': 'pause', 'ms': ms, 'para': False})
        elif name == 'beat':
            items.append({'kind': 'pause', 'ms': BEAT_MS, 'para': False})
        elif name in ('voice', 'speaker'):
            key = (value or '').lower()
            if key in ('default', 'main', ''):
                state['voice'] = None
            elif key in voices:
                state['voice'] = key
            elif key in by_name:
                state['voice'] = by_name[key]
            else:
                warnings.append(f'Unknown voice "{value}" - kept the current voice.')
        elif name in ('speed', 'pace', 'rate'):
            try:
                v = float((value or '1').rstrip('x%'))
                v = v / 100 if v > 3 else v
                state['speed'] = max(0.6, min(1.6, v))
            except ValueError:
                warnings.append(f'Could not read speed in {m.group(0)}.')
        else:
            warnings.append(f'Unknown direction {m.group(0)} was removed.')
    add_text(source[offset:])

    # Tidy: no leading/trailing paragraph pauses, no double paragraph pauses.
    tidy = []
    for item in items:
        if item['kind'] == 'pause' and item.get('para'):
            if not tidy or (tidy[-1]['kind'] == 'pause'):
                continue
        tidy.append(item)
    while tidy and tidy[-1]['kind'] == 'pause' and tidy[-1].get('para'):
        tidy.pop()
    return tidy, warnings
