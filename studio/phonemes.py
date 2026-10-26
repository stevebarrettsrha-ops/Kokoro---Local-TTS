"""Pronunciation helpers: sounds-like respelling, IPA clean-up, word packs.

Kokoro reads phonemes in misaki's notation, which is IPA with a few
single-letter diphthongs:  A = eɪ (day)  I = aɪ (my)  O = oʊ (go)
W = aʊ (now)  Y = ɔɪ (boy)  Q = əʊ (UK go).  Stress (ˈ) goes right
before the stressed vowel, e.g. Kokoro = kˈOkəɹO.

Users can give a pronunciation three ways:
  liv              sounds-like spelling, run through Kokoro's own G2P
  yah-HOO-ah       syllables; the CAPITALISED syllable is stressed
  /jɑhˈuɑ/         phonemes (misaki or ordinary IPA, cleaned up here)
"""
from __future__ import annotations

import re

US_VOCAB = frozenset('AIOWYbdfhijklmnpstuvwzæðŋɑɔəɛɜɡɪɹɾʃʊʌʒʤʧˈˌθᵊᵻʔ')
GB_VOCAB = frozenset('AIQWYabdfhijklmnpstuvwzðŋɑɒɔəɛɜɡɪɹʃʊʌʒʤʧˈˌːθᵊ')
VOWELS = frozenset('AIOQWYaiuæɑɒɔəɛɜɪʊʌᵻ')
STRESS = 'ˈˌ'

# Ordinary IPA (and common typing shortcuts) -> misaki symbols. Order matters.
_IPA = [
    ('t͡ʃ', 'ʧ'), ('d͡ʒ', 'ʤ'), ('tʃ', 'ʧ'), ('dʒ', 'ʤ'),
    ('eɪ', 'A'), ('ei', 'A'), ('aɪ', 'I'), ('ai', 'I'), ('aʊ', 'W'), ('au', 'W'),
    ('ɔɪ', 'Y'), ('oɪ', 'Y'), ('oʊ', 'O'), ('ou', 'O'), ('əʊ', 'Q'),
    ('ɝ', 'ɜɹ'), ('ɚ', 'əɹ'), ('ɫ', 'l'), ('ɐ', 'ə'), ('ɒ', 'ɑ'),
    ('g', 'ɡ'), ('r', 'ɹ'), ('ʁ', 'ɹ'), ('ɾ', 'ɾ'), ('y', 'j'), ('x', 'k'), ('χ', 'k'), ('ħ', 'h'),
    ('ç', 'h'), ('e', 'ɛ'), ('o', 'O'), ("'", 'ˈ'), ('ˑ', ''), ('.', ''), ('-', ''),
]

# Hebrew-roots and Scripture names Kokoro doesn't know (or anglicises).
# Values are misaki phonemes (American); a sounds-like hint is kept for the UI.
HEBREW_PACK = [
    ('Yahuah', 'jɑhˈuɑ', 'yah-HOO-ah'), ('Yah', 'jˈɑ', 'YAH'), ('Yahusha', 'jɑhˈuʃɑ', 'yah-HOO-shah'),
    ('Yahushua', 'jɑhuʃˈuɑ', 'yah-hoo-SHOO-ah'), ('Yeshua', 'jəʃˈuɑ', 'yeh-SHOO-ah'),
    ('Yehoshua', 'jəhOʃˈuɑ', 'yeh-ho-SHOO-ah'), ('Eloah', 'ɛlˈOɑ', 'el-OH-ah'),
    ('Elohim', 'ɛlOhˈim', 'el-oh-HEEM'), ('Elyon', 'ɛljˈOn', 'el-YOHN'), ('Shaddai', 'ʃɑdˈI', 'shah-DYE'),
    ('Adonai', 'ɑdOnˈI', 'ah-doh-NYE'), ('Qadash', 'kɑdˈɑʃ', 'kah-DAHSH'), ('Qadosh', 'kɑdˈOʃ', 'kah-DOHSH'),
    ('Qodesh', 'kˈOdɛʃ', 'KOH-desh'), ('Qedoshim', 'kədOʃˈim', 'keh-doh-SHEEM'), ('Ruach', 'ɹˈuɑk', 'ROO-akh'),
    ('Shamayim', 'ʃɑmˈɑjim', 'shah-MAH-yeem'), ('Mishlei', 'mɪʃlˈA', 'mish-LAY'),
    ('Bereshith', 'bəɹAʃˈit', 'beh-ray-SHEET'), ('Bereshit', 'bəɹAʃˈit', 'beh-ray-SHEET'),
    ('Shemoth', 'ʃəmˈOt', 'sheh-MOHT'), ('Shemot', 'ʃəmˈOt', 'sheh-MOHT'),
    ('Wayyiqra', 'wɑjikɹˈɑ', 'wah-yik-RAH'), ('Vayikra', 'vɑjikɹˈɑ', 'vah-yik-RAH'),
    ('Bemidbar', 'bəmɪdbˈɑɹ', 'beh-mid-BAR'), ('Debarim', 'dəbɑɹˈim', 'deh-bah-REEM'),
    ('Devarim', 'dəvɑɹˈim', 'deh-vah-REEM'), ('Tehillim', 'təhilˈim', 'teh-hee-LEEM'),
    ('Qoheleth', 'kOhˈɛlɛt', 'koh-HEL-et'), ('Yeshayahu', 'jəʃɑjˈɑhu', 'yeh-shah-YAH-hoo'),
    ('Yirmeyahu', 'jɪɹməjˈɑhu', 'yeer-meh-YAH-hoo'), ('Yisra\'el', 'jɪsɹɑˈɛl', 'yis-rah-EL'),
    ('Yisrael', 'jɪsɹɑˈɛl', 'yis-rah-EL'), ('Yerushalayim', 'jəɹuʃɑlˈɑjim', 'yeh-roo-shah-LAH-yeem'),
    ('Tsiyon', 'tsijˈOn', 'tsee-YOHN'), ('Torah', 'tOɹˈɑ', 'toh-RAH'), ('Tanakh', 'tɑnˈɑk', 'tah-NAKH'),
    ('Shabbat', 'ʃɑbˈɑt', 'shah-BAHT'), ('Shalom', 'ʃɑlˈOm', 'shah-LOHM'), ('Mashiach', 'mɑʃˈiɑk', 'mah-SHEE-akh'),
    ('Moshe', 'mOʃˈɛ', 'moh-SHEH'), ('Dawid', 'dɑwˈid', 'dah-WEED'), ('Sha\'ul', 'ʃɑˈul', 'shah-OOL'),
    ('Kepha', 'kˈɛfɑ', 'KEH-fah'), ('Yochanan', 'jOkɑnˈɑn', 'yoh-khah-NAHN'), ('Miryam', 'mɪɹjˈɑm', 'meer-YAHM'),
    ('Halleluyah', 'hɑlɛlujˈɑ', 'hah-leh-loo-YAH'), ('Hallelu', 'hɑlɛlˈu', 'hah-leh-LOO'),
    ('Amein', 'ɑmˈAn', 'ah-MAYN'), ('Abba', 'ˈɑbɑ', 'AH-bah'), ('Emunah', 'ɛmunˈɑ', 'eh-moo-NAH'),
    ('Mitzvot', 'mɪtsvˈOt', 'mits-VOHT'), ('Mishkan', 'mɪʃkˈɑn', 'mish-KAHN'), ('Pesach', 'pˈɛsɑk', 'PEH-sakh'),
    ('Shavuot', 'ʃɑvuˈOt', 'shah-voo-OHT'), ('Sukkot', 'sukˈOt', 'soo-KOHT'), ('Teruah', 'təɹuˈɑ', 'teh-roo-AH'),
    ('Besorah', 'bəsOɹˈɑ', 'beh-soh-RAH'),
]
PACKS = {'hebrew': {'label': 'Hebrew & Scripture names',
                    'words': [{'word': w, 'say': f'/{p}/', 'hint': h} for w, p, h in HEBREW_PACK]}}

# "live" is a heteronym: misaki defaults to "lyve" (live music) unless its
# tagger is sure it's a verb, which it often isn't at the start of a sentence.
# Narration uses the verb far more, so prefer "liv" unless the context says
# broadcast/performance.
_LIVE_ADJ_NEXT = frozenset('''action album audience bait band broadcast broadcasts chat concert coverage demo event
events feed footage music oak performance performances player radio recording round rounds session set show shows
sound stream streams streaming television tv update updates version video wire wires q&a'''.split())
_LIVE_ADJ_PREV = frozenset('go goes going went gone stay stays stayed is are was were be been being'.split())
_LIVE_RE = re.compile(r"(?<![\w\[])(live)(?![\w\]'’])", re.I)


def fix_heteronyms(text):
    def repl(m):
        before = text[:m.start()].split()
        after = text[m.end():].split()
        prev = re.sub(r'\W', '', before[-1]).lower() if before else ''
        nxt = re.sub(r"[^\w&]", '', after[0]).lower() if after else ''
        ends_clause = not after or re.match(r'^[.!?,;:]', text[m.end():].lstrip()[:1] or '.')
        if nxt in _LIVE_ADJ_NEXT or (prev in _LIVE_ADJ_PREV and ends_clause):
            return f'[{m.group(1)}](/lˈIv/)'
        return f'[{m.group(1)}](/lˈɪv/)'
    return _LIVE_RE.sub(repl, text)


def normalize_ipa(ps, british=False):
    """Make user-typed IPA safe for Kokoro (unknown symbols are dropped)."""
    vocab = GB_VOCAB if british else US_VOCAB
    s = ps.strip().strip('/[]').strip()
    out, i = [], 0
    while i < len(s):
        ch = s[i]
        multi = next(((src, dst) for src, dst in _IPA if len(src) > 1 and s.startswith(src, i)), None)
        if multi is None and (ch in vocab or ch == ' '):
            out.append(ch)
            i += 1
            continue
        for src, dst in ([multi] if multi else _IPA):
            if s.startswith(src, i):
                if dst == 'Q' and not british:
                    dst = 'O'
                if dst == 'ɾ' and british:
                    dst = 't'
                out.append(dst)
                i += len(src)
                break
        else:
            if ch == 'a':
                out.append('a' if british else 'ɑ')
            elif ch == 'ː' and british:
                out.append(ch)
            i += 1
    return ''.join(c for c in ''.join(out) if c in vocab or c == ' ').strip()


def _stress_first_vowel(ps):
    for i, c in enumerate(ps):
        if c in VOWELS:
            return ps[:i] + 'ˈ' + ps[i:]
    return 'ˈ' + ps


def respell(text, g2p, british=False):
    """'yah-HOO-ah' -> phonemes using Kokoro's own G2P for each syllable."""
    words = []
    for word in text.split():
        sylls = [s for s in re.split(r'[-·]', word) if re.search(r'\w', s)]
        if not sylls:
            continue
        marked = [i for i, s in enumerate(sylls) if s.isupper() and len(sylls) > 1]
        if len(sylls) == 1 and not marked:
            ps = g2p(sylls[0].lower()) or ''
            words.append(normalize_ipa(ps, british))
            continue
        stressed = set(marked or [0])
        parts = []
        for i, s in enumerate(sylls):
            ps = normalize_ipa(g2p(s.lower()) or '', british)
            ps = ''.join(c for c in ps if c not in STRESS and c != ' ')
            parts.append(_stress_first_vowel(ps) if i in stressed else ps)
        words.append(''.join(parts))
    return ' '.join(w for w in words if w)


def make_resolver(g2p=None, british=False):
    """Return say -> phoneme string (or None to fall back to plain text)."""
    cache = {}

    def resolve(say):
        say = say.strip()
        if say in cache:
            return cache[say]
        if len(say) > 2 and say.startswith('/') and say.endswith('/'):
            out = normalize_ipa(say, british) or None
        elif g2p is not None:
            try:
                out = respell(say, g2p, british) or None
            except Exception:  # noqa: BLE001 - a bad rule must never break a render
                out = None
        else:
            out = None
        cache[say] = out
        return out
    return resolve
