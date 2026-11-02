"""Pronunciation helpers: sounds-like respelling, IPA clean-up, word packs.

Kokoro reads phonemes in misaki's notation, which is IPA with a few
single-letter diphthongs:  A = eɪ (day)  I = aɪ (my)  O = oʊ (go)
W = aʊ (now)  Y = ɔɪ (boy)  Q = əʊ (UK go).  Stress (ˈ) goes right
before the stressed vowel, e.g. Kokoro = kˈOkəɹO.

Users can give a pronunciation three ways:
  liv              sounds-like spelling, run through Kokoro's own G2P
  Yah-uh-ah        syllables; the CAPITALISED syllable is stressed (else the first)
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
    ('Yahuah', 'jˈɑəˌɑ', 'YAH-uh-ah'), ('Yah', 'jˈɑ', 'YAH'), ('Yahusha', 'jɑhˈuʃɑ', 'yah-HOO-shah'),
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
# Pack values we shipped before and have since corrected. A saved rule still
# holding an old value is upgraded so old projects pick up the fix.
_RETIRED = {'yahuah': {'/jɑhˈuɑ/', 'yah-HOO-ah'}}
_PACK_BY_WORD = {w.lower(): (f'/{p}/', h) for w, p, h in HEBREW_PACK}


def with_pack(rules, use_pack=True):
    """User rules first (they win), then the built-in names they didn't override."""
    out, have = [], set()
    for r in rules or []:
        word = str(r.get('word', '')).strip().lower()
        say = str(r.get('say', '')).strip()
        if word in _RETIRED and say in _RETIRED[word]:
            say, hint = _PACK_BY_WORD[word]
            r = {**r, 'say': say, 'hint': hint}
        out.append(r)
        if word and say:
            have.add(word)
    if use_pack:
        out += [{'word': w, 'say': f'/{p}/', 'hint': h} for w, p, h in HEBREW_PACK if w.lower() not in have]
    return out


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


# Sounds-like syllables ("yah", "uh", "SHEE", "akh") follow the usual
# pronunciation-respelling key rather than English spelling, so they are read
# with these rules instead of a dictionary (which mangles made-up syllables).
_R_VOWELS = [  # (spelling, open syllable, closed syllable)
    ('eye', 'I', 'I'), ('igh', 'I', 'I'), ('eer', 'ɪɹ', 'ɪɹ'), ('ear', 'ɪɹ', 'ɪɹ'), ('air', 'ɛɹ', 'ɛɹ'),
    ('oor', 'ʊɹ', 'ʊɹ'), ('ah', 'ɑ', 'ɑ'), ('aa', 'ɑ', 'ɑ'), ('ay', 'A', 'A'), ('ai', 'A', 'A'),
    ('ei', 'A', 'A'), ('ey', 'A', 'A'), ('aw', 'ɔ', 'ɔ'), ('au', 'ɔ', 'ɔ'), ('eh', 'ɛ', 'ɛ'),
    ('ee', 'i', 'i'), ('ea', 'i', 'i'), ('ih', 'ɪ', 'ɪ'), ('oh', 'O', 'O'), ('oa', 'O', 'O'),
    ('oe', 'O', 'O'), ('oo', 'u', 'u'), ('ou', 'u', 'u'), ('ow', 'W', 'W'), ('oy', 'Y', 'Y'),
    ('oi', 'Y', 'Y'), ('uh', 'ə', 'ə'), ('ue', 'u', 'u'), ('uu', 'ʊ', 'ʊ'),
    ('a', 'ɑ', 'æ'), ('e', 'ɛ', 'ɛ'), ('i', 'i', 'ɪ'), ('o', 'O', 'ɑ'), ('u', 'u', 'ʌ'),
]
_R_RVOWELS = [('ar', 'ɑɹ'), ('er', 'ɜɹ'), ('ir', 'ɜɹ'), ('ur', 'ɜɹ'), ('or', 'ɔɹ')]
_R_CONS = [
    ('tch', 'ʧ'), ('ch', 'ʧ'), ('sh', 'ʃ'), ('zh', 'ʒ'), ('th', 'θ'), ('dh', 'ð'), ('kh', 'k'),
    ('ng', 'ŋ'), ('ph', 'f'), ('ck', 'k'), ('wh', 'w'), ('gh', 'ɡ'), ('qu', 'kw'), ('tz', 'ts'),
    ('x', 'ks'), ('c', 'k'), ('g', 'ɡ'), ('j', 'ʤ'), ('q', 'k'), ('r', 'ɹ'), ('y', 'j'),
]
_LETTER_VOWELS = set('aeiou')


def _syllable(syl, stressed=False, british=False):
    """One respelled syllable -> misaki phonemes (stress mark included)."""
    s = re.sub(r'[^a-z]', '', syl.lower())
    s = re.sub(r'([b-df-hj-np-tv-z])\1', r'\1', s)  # "shabb" -> "shab"
    if not s:
        return ''
    if len(s) > 2 and s.endswith('e') and s[-2] not in _LETTER_VOWELS | {'e'} and set(s[:-1]) & _LETTER_VOWELS:
        s = s[:-1]  # silent final e
    # "dye", "nye", "my": a final y (or ye) after consonants is the vowel in "my".
    m = re.fullmatch(r'([^aeiouy]+)ye?', s)
    if m:
        s = m.group(1) + 'eye'
    out, i, nucleus = [], 0, None
    while i < len(s):
        rest = s[i:]
        hit = None
        if nucleus is None:
            for sp, ph in _R_RVOWELS:  # r-coloured, unless the r starts a new sound
                if rest.startswith(sp) and (len(rest) == len(sp) or rest[len(sp)] not in _LETTER_VOWELS):
                    hit = ('v', sp, ph, ph)
                    break
            if hit is None:
                v = next((x for x in _R_VOWELS if rest.startswith(x[0])
                          and not (x[0][-1] in 'hwy' and len(x[0]) == 2 and rest[2:3] in _LETTER_VOWELS)), None)
                if v and not (v[0] in ('y',) and i == 0):
                    hit = ('v', v[0], v[1], v[2])
        if hit is None:
            if rest[0] in _LETTER_VOWELS:  # a second vowel group: read it plainly
                v = next(x for x in _R_VOWELS if rest.startswith(x[0]))
                out.append(v[1])
                i += len(v[0])
                continue
            c = next(((sp, ph) for sp, ph in _R_CONS if rest.startswith(sp)), (rest[0], rest[0]))
            out.append(c[1])
            i += len(c[0])
            continue
        _, sp, open_ph, closed_ph = hit
        after = s[i + len(sp):]
        # Closed syllables shorten the vowel ("mat"), but "akh"/"ach" keeps the "ah".
        closed = any(ch not in _LETTER_VOWELS and ch != 'h' for ch in after) and not (
            sp == 'a' and after.startswith(('kh', 'ch')))
        nucleus = len(out)
        out.append(closed_ph if closed else open_ph)
        i += len(sp)
    if nucleus is not None:
        if stressed and out[nucleus] == 'ə':
            out[nucleus] = 'ʌ'
        if stressed:
            out[nucleus] = 'ˈ' + out[nucleus]
    elif stressed:
        out.insert(0, 'ˈ')
    ps = ''.join(out)
    if british:
        ps = ps.replace('O', 'Q').replace('æ', 'a').replace('ɜɹ', 'ɜː').replace('ɑɹ', 'ɑː').replace('ɔɹ', 'ɔː')
    return normalize_ipa(ps, british)


def respell(text, g2p=None, british=False):
    """'yah-HOO-ah' / 'Yah-uh-ah' -> phonemes, flowing as one word."""
    words = []
    for word in text.split():
        sylls = [s for s in re.split(r'[-·]', word) if re.search(r'\w', s)]
        if not sylls:
            continue
        marked = [i for i, s in enumerate(sylls) if s.isupper() and len(sylls) > 1]
        if len(sylls) == 1 and not marked:
            ps = ''
            if g2p is not None:
                try:
                    ps = normalize_ipa(g2p(sylls[0].lower()) or '', british)
                except Exception:  # noqa: BLE001 - fall back to the respelling rules
                    ps = ''
            words.append(ps or _syllable(sylls[0], True, british))
            continue
        stressed = set(marked or [0])
        words.append(''.join(_syllable(s, i in stressed, british) for i, s in enumerate(sylls)))
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
        else:
            try:
                out = respell(say, g2p, british) or None
            except Exception:  # noqa: BLE001 - a bad rule must never break a render
                out = None
        cache[say] = out
        return out
    return resolve
