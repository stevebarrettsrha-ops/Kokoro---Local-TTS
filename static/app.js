(() => {
'use strict';

/* ---------- helpers ---------- */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

const COLORS = {
  neutral: '#8b857a', warm: '#c4834f', joyful: '#cf9f22', excited: '#e0672a', curious: '#2f9bb0',
  confident: '#3f7d52', calm: '#5e9e8f', sad: '#4b6fa5', angry: '#b8392f', fearful: '#7d5ca8',
  dramatic: '#8a3f6e', soft: '#a39a8c',
};
const ALIASES = {tender: 'warm', whisper: 'soft', happy: 'joyful', scared: 'fearful', serious: 'confident'};
const SAMPLE = `[calm] Every morning, the harbour wakes up before the town does.

The boats go out in the dark. [beat] By sunrise, they're already halfway to the horizon.

[curious] But have you ever wondered what they're actually looking for?

[dramatic:70] Forty years ago, one crew found something *nobody* expected. [pause=700]

[warm] This is their story.`;

const DEFAULTS = {
  voice: 'af_heart', blend_voice: '', blend: 0.3, blend_mode: 'full', style: 'neutral', intensity: 60,
  speed: 1, pitch: 0, range: 1, energy: 0, punct: 1.1, sentence_gap: 300, paragraph_gap: 700,
  preset: 'youtube', lufs: '-14', rate: '48000', peak: '-1', target: '', polish: true, room_tone: false, mp3: false,
};

const state = {
  catalog: null, styles: {}, style: 'neutral', intensity: 60, blendMode: 'full',
  pron: [], busy: false, take: null, peaks: null, ffmpeg: false,
};

const api = async (url, opts) => {
  let r;
  try { r = await fetch(url, opts); }
  catch { throw Error('Lost contact with the local engine. Check the terminal running ./start.sh.'); }
  if ((r.headers.get('content-type') || '').includes('audio/')) {
    if (!r.ok) throw Error(`Server error ${r.status}`);
    return r;
  }
  const j = await r.json().catch(() => ({error: `Unexpected response (${r.status})`}));
  if (!r.ok) throw Object.assign(Error(j.error || `Server error ${r.status}`), {status: r.status});
  return j;
};

function toast(text) {
  const t = $('#toast'); t.textContent = text; t.classList.add('show');
  clearTimeout(t._tm); t._tm = setTimeout(() => t.classList.remove('show'), 4200);
}
function msg(text, kind = '') { const m = $('#msg'); m.textContent = text; m.className = 'msg ' + kind; }
const fmt = s => { s = Math.max(0, s || 0); const m = Math.floor(s / 60); return `${m}:${(s % 60).toFixed(1).padStart(4, '0')}`; };
const fmtShort = s => { s = Math.round(Math.max(0, s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };

/* ---------- theme ---------- */
function applyTheme(t) {
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
}
applyTheme(store.get('kokoro-theme', null));
$('#themeBtn').onclick = () => {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === 'dark'
    : matchMedia('(prefers-color-scheme: dark)').matches;
  const next = dark ? 'light' : 'dark';
  applyTheme(next); store.set('kokoro-theme', next); paintWave(); drawGauge();
};

/* ---------- views ---------- */
function show(view) {
  $$('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + view));
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.view === view));
  document.body.dataset.view = view;
  if (view !== 'script' && !audio.paused) audio.pause();
  if (view !== 'script') stopPreview();
  if (view === 'takes') loadTakes();
  if (view === 'video') window.KVideo?.onShow();
  else window.KVideo?.onHide();
}
document.body.dataset.view = 'script';
$$('.tab').forEach(t => t.onclick = () => show(t.dataset.view));

/* ---------- emotion model (mirrors studio/styles.py) ---------- */
function styleMoves(id, k) {
  const s = state.styles[id] || state.styles.neutral || {speed: 1, pitch: 0, range: 1, gap: 0, energy: 0};
  k = clamp(k, 0, 1.25);
  return {
    speed: Math.exp(Math.log(s.speed) * k), pitch: s.pitch * k, range: Math.exp(Math.log(s.range) * k),
    gap: s.gap * k, energy: s.energy * k,
  };
}
const canonical = name => { name = String(name || '').toLowerCase(); name = ALIASES[name] || name; return state.styles[name] ? name : null; };

/* ---------- gauge ---------- */
const G = {cx: 120, cy: 122, r: 96, max: 120, a0: Math.PI, a1: 0};
function angleFor(v) { return G.a0 + (G.a1 - G.a0) * (v / G.max); }
function pt(a, r) { return [G.cx + r * Math.cos(a), G.cy - r * Math.sin(a)]; }
function arc(v0, v1, r) {
  const [x0, y0] = pt(angleFor(v0), r), [x1, y1] = pt(angleFor(v1), r);
  return `M${x0.toFixed(2)} ${y0.toFixed(2)} A${r} ${r} 0 0 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}
function drawGauge() {
  const v = state.intensity, color = COLORS[state.style] || COLORS.neutral;
  let ticks = '';
  for (let i = 0; i <= G.max; i += 10) {
    const a = angleFor(i), major = i % 50 === 0 || i === 120;
    const [x0, y0] = pt(a, G.r - (major ? 16 : 10)), [x1, y1] = pt(a, G.r - 4);
    ticks += `<line x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}" class="tick${i > 100 ? ' hot' : ''}${major ? ' major' : ''}"/>`;
    if (major) { const [tx, ty] = pt(a, G.r - 28); ticks += `<text x="${tx}" y="${ty + 4}" class="tick-label">${i}</text>`; }
  }
  const [nx, ny] = pt(angleFor(v), G.r - 6);
  $('#gaugeSvg').innerHTML = `
    <path d="${arc(0, 100, G.r)}" class="track"/>
    <path d="${arc(100, 120, G.r)}" class="track hot"/>
    ${v > 0.5 ? `<path d="${arc(0, Math.min(v, 120), G.r)}" class="fill" style="stroke:${color}"/>` : ''}
    ${ticks}
    <line x1="${G.cx}" y1="${G.cy}" x2="${nx}" y2="${ny}" class="needle"/>
    <circle cx="${G.cx}" cy="${G.cy}" r="6" class="hub"/>`;
  $('#gaugeValue').textContent = Math.round(v);
  $('#gaugeZone').textContent = v < 30 ? 'subtle' : v <= 75 ? 'natural' : v <= 100 ? 'strong' : 'over the top';
  $('#gauge').setAttribute('aria-valuenow', Math.round(v));
  $('#gauge').setAttribute('aria-valuetext', `${Math.round(v)} percent, ${$('#gaugeZone').textContent}`);
  updateReadout();
}
function setIntensity(v) { state.intensity = clamp(Math.round(v), 0, G.max); drawGauge(); changed(); }
(() => {
  const el = $('#gauge');
  const fromEvent = e => {
    const svg = $('#gaugeSvg').getBoundingClientRect();
    const x = (e.clientX - svg.left) / svg.width * 240 - G.cx;
    const y = G.cy - (e.clientY - svg.top) / svg.height * 140;
    let a = Math.atan2(Math.max(y, -20), x);
    if (a < 0) a = x < 0 ? Math.PI : 0;
    setIntensity((G.a0 - a) / (G.a0 - G.a1) * G.max);
  };
  el.addEventListener('pointerdown', e => { el.setPointerCapture(e.pointerId); fromEvent(e); });
  el.addEventListener('pointermove', e => { if (el.hasPointerCapture(e.pointerId)) fromEvent(e); });
  el.addEventListener('keydown', e => {
    const step = e.shiftKey ? 10 : 2;
    if (['ArrowRight', 'ArrowUp'].includes(e.key)) { setIntensity(state.intensity + step); e.preventDefault(); }
    if (['ArrowLeft', 'ArrowDown'].includes(e.key)) { setIntensity(state.intensity - step); e.preventDefault(); }
    if (e.key === 'Home') setIntensity(0);
    if (e.key === 'End') setIntensity(100);
  });
})();

function updateReadout() {
  const m = styleMoves(state.style, state.intensity / 100);
  const sp = m.speed * +$('#speed').value, pi = m.pitch + +$('#pitch').value, rg = m.range * +$('#range').value;
  const sign = n => (n > 0.04 ? '+' : n < -0.04 ? '−' : '±') + Math.abs(n).toFixed(1);
  $('#readout').innerHTML =
    `<span>pace <b>${sp.toFixed(2)}×</b></span><span>pitch <b>${sign(pi)} st</b></span>` +
    `<span>range <b>${Math.round(rg * 100)}%</b></span><span>pauses <b>${m.gap >= 0 ? '+' : '−'}${Math.abs(Math.round(m.gap))} ms</b></span>`;
}

function selectStyle(id) {
  state.style = canonical(id) || 'neutral';
  $$('.emo').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.style === state.style)));
  drawGauge(); changed();
}

function buildEmotionUI() {
  const styles = state.catalog.styles;
  $('#emoGrid').innerHTML = styles.map(s =>
    `<button type="button" class="emo" data-style="${s.id}" aria-pressed="false" style="--c:${COLORS[s.id] || '#888'}"><i></i>${esc(s.label)}</button>`).join('');
  $$('.emo').forEach(b => b.onclick = () => selectStyle(b.dataset.style));
  $('#tagChips').innerHTML = styles.filter(s => s.id !== 'neutral').map(s =>
    `<button type="button" class="chip tagchip" data-tag="${s.id}" style="--c:${COLORS[s.id]}" title="Insert [${s.id}]"><i></i>${esc(s.label)}</button>`).join('');
  $$('.tagchip').forEach(b => b.onclick = () => insertTag(`[${b.dataset.tag}]`));
}

/* ---------- sliders & settings ---------- */
const FORMATS = {
  speed: v => `${(+v).toFixed(2)}×`, pitch: v => `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(+v).toFixed(1)} st`,
  range: v => `${Math.round(v * 100)}%`, energy: v => (+v === 0 ? '0' : `${v > 0 ? '+' : '−'}${Math.abs(Math.round(v * 100))}`),
  punct: v => `${(+v).toFixed(2)}×`, sentence_gap: v => `${Math.round(v)} ms`, paragraph_gap: v => `${Math.round(v)} ms`,
  blend: v => `${Math.round(v * 100)}%`,
};
Object.entries(FORMATS).forEach(([id, f]) => {
  const el = $('#' + id), out = $('#' + id + 'Out');
  const upd = () => { out.textContent = f(+el.value); };
  el.addEventListener('input', () => { upd(); updateReadout(); changed(); });
  el.addEventListener('dblclick', () => { el.value = DEFAULTS[id]; el.dispatchEvent(new Event('input')); });
  upd();
});
['voice', 'blendVoice', 'lufs', 'rate', 'peak', 'polish', 'room_tone', 'mp3'].forEach(id => $('#' + id).addEventListener('change', changed));
$('#blendVoice').addEventListener('change', () => { $('#blendRow').hidden = !$('#blendVoice').value; });
$$('[data-blend-mode]').forEach(b => b.onclick = () => setBlendMode(b.dataset.blendMode));
function setBlendMode(m) { state.blendMode = m === 'delivery' ? 'delivery' : 'full'; $$('[data-blend-mode]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.blendMode === state.blendMode))); changed(); }

function parseTarget(v) {
  v = String(v || '').trim();
  if (!v) return null;
  const m = v.match(/^(\d+):(\d{1,2}(?:\.\d+)?)$/);
  const s = m ? +m[1] * 60 + +m[2] : parseFloat(v);
  return Number.isFinite(s) && s > 0 ? s : null;
}
$('#target').addEventListener('input', () => { updateEstimate(); changed(); });

function readSettings() {
  return {
    voice: $('#voice').value === '__add' ? lastVoice : $('#voice').value, variation: state.variation || 0, blend_voice: $('#blendVoice').value, blend: +$('#blend').value, blend_mode: state.blendMode,
    style: state.style, intensity: state.intensity,
    speed: +$('#speed').value, pitch: +$('#pitch').value, range: +$('#range').value, energy: +$('#energy').value,
    punct: +$('#punct').value, sentence_gap: +$('#sentence_gap').value, paragraph_gap: +$('#paragraph_gap').value,
    preset: $('#preset').value, lufs: $('#lufs').value, rate: $('#rate').value, peak: $('#peak').value,
    target: $('#target').value, polish: $('#polish').checked, room_tone: $('#room_tone').checked, mp3: $('#mp3').checked,
  };
}
function setSelect(el, v) { if (v !== undefined && [...el.options].some(o => o.value === String(v))) el.value = String(v); }
function applySettings(s) {
  s = {...DEFAULTS, ...(s || {})};
  setSelect($('#voice'), s.voice); setSelect($('#blendVoice'), s.blend_voice || '');
  state.variation = Number(s.variation) || 0; if (typeof updateVoiceMode === 'function' && state.catalog) updateVoiceMode();
  $('#blendRow').hidden = !$('#blendVoice').value;
  ['blend', 'speed', 'pitch', 'range', 'energy', 'punct', 'sentence_gap', 'paragraph_gap'].forEach(k => {
    const el = $('#' + k), n = Number(s[k]);
    if (Number.isFinite(n)) { el.value = String(clamp(n, +el.min, +el.max)); el.dispatchEvent(new Event('input')); }
  });
  ['preset', 'lufs', 'rate', 'peak'].forEach(k => setSelect($('#' + k), s[k]));
  $('#target').value = s.target || '';
  ['polish', 'room_tone', 'mp3'].forEach(k => { $('#' + k).checked = !!s[k]; });
  setBlendMode(s.blend_mode);
  state.intensity = clamp(Number(s.intensity) || 0, 0, 120);
  selectStyle(s.style);
}
function payload(text) {
  const s = readSettings();
  return {
    ...s, text, target_seconds: parseTarget(s.target), name: $('#projectName').value.trim(),
    pronunciations: state.pron.filter(p => p.word.trim() && p.say.trim()),
  };
}

$('#preset').addEventListener('change', () => {
  const p = state.catalog.presets[$('#preset').value];
  if (!p) return;
  setSelect($('#lufs'), p.lufs); setSelect($('#rate'), p.rate); setSelect($('#peak'), p.peak);
  for (const [k, id] of [['speed', 'speed'], ['sentence_gap', 'sentence_gap'], ['paragraph_gap', 'paragraph_gap'], ['punct', 'punct']]) {
    const el = $('#' + id); el.value = p[k]; el.dispatchEvent(new Event('input'));
  }
  toast(`Output set up for ${p.label}.`);
  changed();
});

$('#resetBtn').onclick = () => { applySettings(DEFAULTS); toast('Direction reset.'); };

/* ---------- pronunciation list ---------- */
function renderPron() {
  $('#pronList').innerHTML = state.pron.map((p, i) => `
    <div class="pron">
      <input class="input" data-i="${i}" data-k="word" value="${esc(p.word)}" placeholder="Word" aria-label="Word">
      <span class="arrow">→</span>
      <input class="input mono-ish" data-i="${i}" data-k="say" value="${esc(p.say)}" placeholder="yah-HOO-ah" aria-label="Say it like"${p.hint ? ` title="Sounds like: ${esc(p.hint)}"` : ''}>
      <button class="icon-btn" data-test="${i}" aria-label="Hear this word" title="Hear it">▷</button>
      <button class="icon-btn" data-del="${i}" aria-label="Remove" title="Remove">×</button>
    </div>`).join('');
  $('#pronCount').textContent = state.pron.length ? `${state.pron.length} word${state.pron.length === 1 ? '' : 's'}` : 'applies to every take';
  $$('#pronList input').forEach(inp => inp.oninput = () => { state.pron[+inp.dataset.i][inp.dataset.k] = inp.value; changed(); });
  $$('#pronList [data-del]').forEach(b => b.onclick = () => { state.pron.splice(+b.dataset.del, 1); renderPron(); changed(); });
  $$('#pronList [data-test]').forEach(b => b.onclick = () => testWord(state.pron[+b.dataset.test].word));
}
async function showPhonemes(text) {
  const out = $('#pronCheck');
  try {
    const r = await api('/api/phonemize', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload(text))});
    out.innerHTML = r.sentences.map(x => `<div><span>${esc(x.text)}</span> <b class="mono">${esc(x.phonemes ?? '?')}</b></div>`).join('');
    out.hidden = false;
  } catch (err) { out.textContent = err.message; out.hidden = false; }
}
async function testWord(word) {
  word = String(word || '').trim();
  if (!word) { toast('Type the word first.'); return; }
  await showPhonemes(word);
  preview(word);
}
$('#addPron').onclick = () => { state.pron.push({word: '', say: ''}); renderPron(); $$('#pronList .pron:last-child input')[0]?.focus(); };
$('#addPack').onclick = () => {
  const pack = state.catalog?.packs?.hebrew;
  if (!pack) { toast('Start the engine to load the name pack.'); return; }
  const have = new Set(state.pron.map(p => p.word.trim().toLowerCase()));
  const add = pack.words.filter(w => !have.has(w.word.toLowerCase()));
  state.pron.push(...add.map(w => ({...w})));
  renderPron(); changed();
  toast(add.length ? `Added ${add.length} names. Edit any you say differently.` : 'Those names are already in your list.');
};
$('#checkLine').onclick = () => {
  const line = lineAtCursor().replace(TAG_RE, '').trim();
  if (!line) { toast('Put the cursor in a line first.'); return; }
  showPhonemes(line);
};

/* ---------- editor ---------- */
const script = $('#script'), backdrop = $('#backdrop');
const TAG_RE = /\[\s*([A-Za-z_]+)\s*(?:[:=]\s*([^\]\s]+)\s*)?\](?!\()/g;

function highlight() {
  const src = script.value;
  let html = '', last = 0;
  const re = /\[\s*([A-Za-z_]+)\s*(?:[:=]\s*([^\]\s]+)\s*)?\](?!\()|\[[^\]\n]+\]\([^)\n]*\)|\*[^*\n]{1,120}?\*/g;
  let m;
  while ((m = re.exec(src))) {
    html += esc(src.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith('*')) html += `<mark class="em">${esc(tok)}</mark>`;
    else if (m[1]) {
      const name = m[1].toLowerCase(), st = canonical(name);
      const known = st || ['default', 'pause', 'wait', 'silence', 'beat', 'voice', 'speaker', 'speed', 'pace', 'rate'].includes(name);
      html += `<mark class="tg${known ? '' : ' bad'}" style="--c:${st ? COLORS[st] : 'var(--muted)'}">${esc(tok)}</mark>`;
    } else html += `<mark class="ph">${esc(tok)}</mark>`;
    last = m.index + tok.length;
  }
  backdrop.innerHTML = html + esc(src.slice(last)) + '\n';
  backdrop.scrollTop = script.scrollTop;
}
script.addEventListener('scroll', () => { backdrop.scrollTop = script.scrollTop; });

function insertAtCursor(text, wrapEnd = null) {
  const s = script.selectionStart, e = script.selectionEnd, sel = script.value.slice(s, e);
  const insert = wrapEnd !== null && sel ? text + sel + wrapEnd : text;
  script.setRangeText(insert, s, e, 'end');
  script.focus();
  onEdit();
}
function insertTag(tag) {
  const s = script.selectionStart, e = script.selectionEnd;
  if (e > s) insertAtCursor(tag + ' ', ' [default]');
  else {
    const before = script.value.slice(0, s);
    insertAtCursor((before && !/\s$/.test(before) ? ' ' : '') + tag + ' ');
  }
}
$$('[data-insert]').forEach(b => b.onclick = () => insertAtCursor(b.dataset.insert));
$('#emphasisBtn').onclick = () => {
  const s = script.selectionStart, e = script.selectionEnd;
  if (e > s) insertAtCursor('*', '*'); else toast('Select the words to emphasise first.');
};
$('#voiceTag').onchange = e => { if (e.target.value) insertAtCursor(`[voice=${e.target.value}] `); e.target.value = ''; };

/* client-side estimate (mirrors studio/script.py closely enough for a preview) */
function splitSentences(p) {
  const parts = p.replace(/\s+/g, ' ').trim().split(/(?<=[.!?…]["”’)\]]*)\s+(?=["“‘(\[]?[A-Z0-9])/);
  const out = [];
  for (const s of parts) { if (!s) continue; if (out.length && (s.length < 12 || out[out.length - 1].length < 12)) out[out.length - 1] += ' ' + s; else out.push(s); }
  return out;
}
function clientParse(src) {
  const items = [], warnings = [];
  let cur = {style: null, intensity: null, speed: 1};
  let offset = 0, m;
  const addText = (piece, base) => {
    const paras = piece.split(/\n\s*\n+/);
    let searchFrom = base;
    paras.forEach((para, i) => {
      for (const s of splitSentences(para)) {
        const clean = s.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').replace(/\*/g, '');
        if (!/\w/.test(clean)) continue;
        const words = (clean.match(/\S+/g) || []).length;
        const at = src.indexOf(s.slice(0, 12), searchFrom);
        if (at >= 0) searchFrom = at + 1;
        items.push({kind: 'speech', text: clean, words, pos: at, ...cur});
      }
      if (i < paras.length - 1) items.push({kind: 'pause', para: true});
    });
  };
  TAG_RE.lastIndex = 0;
  while ((m = TAG_RE.exec(src))) {
    addText(src.slice(offset, m.index), offset);
    offset = m.index + m[0].length;
    const name = m[1].toLowerCase(), val = m[2], st = canonical(name);
    if (st) cur = {...cur, style: st, intensity: val != null && !isNaN(parseFloat(val)) ? clamp(parseFloat(val), 0, 125) / 100 : null};
    else if (name === 'default') cur = {...cur, style: null, intensity: null};
    else if (['pause', 'wait', 'silence'].includes(name)) {
      let v = String(val || '600').toLowerCase(), ms = v.endsWith('ms') ? parseFloat(v) : v.endsWith('s') ? parseFloat(v) * 1000 : parseFloat(v);
      if (Number.isFinite(ms) && ms > 0) items.push({kind: 'pause', ms: Math.min(ms, 10000)});
    } else if (name === 'beat') items.push({kind: 'pause', ms: 350});
    else if (['speed', 'pace', 'rate'].includes(name)) { let v = parseFloat(val); if (v > 3) v /= 100; if (Number.isFinite(v)) cur = {...cur, speed: clamp(v, 0.6, 1.6)}; }
    else if (['voice', 'speaker'].includes(name)) {
      const ok = !val || ['default', 'main'].includes(val.toLowerCase()) || state.catalog?.voices.some(v => v.id === val.toLowerCase() || v.name.toLowerCase() === val.toLowerCase());
      if (!ok) warnings.push(`Unknown voice "${val}"`);
    } else warnings.push(`Unknown direction [${m[1]}]`);
  }
  addText(src.slice(offset), offset);
  return {items, warnings};
}

function estimate(items, s) {
  const WPS = 2.75;
  let t = 0, prev = null;
  const out = [];
  for (const it of items) {
    if (it.kind === 'pause') {
      const ms = it.para ? s.paragraph_gap + (prev ? prev.gap : 0) : it.ms;
      out.push({kind: 'pause', dur: ms / 1000, para: !!it.para});
      t += ms / 1000; prev = it.para ? prev : null; continue;
    }
    const st = it.style || s.style, k = it.intensity == null ? s.intensity / 100 : it.intensity;
    const mv = styleMoves(st, k);
    if (prev && out.length && out[out.length - 1].kind === 'speech') {
      const end = prev.text.trim().slice(-1);
      const g = /[.!?…"”)]/.test(end) ? s.sentence_gap + prev.gap : /[,;:—–-]/.test(end) ? s.sentence_gap * 0.45 : 70;
      out.push({kind: 'gap', dur: Math.max(0, g) / 1000}); t += Math.max(0, g) / 1000;
    }
    const punct = (it.text.match(/[,;:—]/g) || []).length * 0.12 * (s.punct - 1);
    const dur = it.words / (WPS * s.speed * it.speed * mv.speed) + 0.15 + punct;
    out.push({kind: 'speech', dur, style: st, text: it.text, pos: it.pos});
    t += dur; prev = {text: it.text, gap: mv.gap};
  }
  return {total: t, parts: out};
}

let estTimer = 0;
function updateEstimate() {
  const src = script.value;
  const words = (src.replace(TAG_RE, ' ').match(/\S+/g) || []).length;
  const {items, warnings} = clientParse(src);
  const s = readSettings();
  const est = estimate(items, s);
  const target = parseTarget(s.target);
  $('#count').textContent = `${words.toLocaleString()} words · ${src.length.toLocaleString()} chars`;
  let label = items.length ? `≈ ${fmtShort(est.total)}` : '0:00';
  if (target && items.length) {
    const d = est.total - target;
    label += `  ·  target ${fmtShort(target)} (${d >= 0 ? '+' : '−'}${Math.abs(d).toFixed(0)}s, will fit)`;
  }
  $('#estimate').textContent = label;
  $('#warnings').textContent = warnings.length ? warnings.slice(0, 2).join(' · ') : '';
  const bar = $('#segbar');
  if (!est.parts.length) { bar.innerHTML = '<span class="seg-empty">Your script\'s shape will show here as you write.</span>'; return; }
  bar.innerHTML = est.parts.map((p, i) => p.kind === 'speech'
    ? `<button class="seg" style="flex-grow:${p.dur.toFixed(3)};--c:${COLORS[p.style] || COLORS.neutral}" data-pos="${p.pos}" title="${esc(state.styles[p.style]?.label || p.style)} · ${p.dur.toFixed(1)}s — ${esc(p.text.slice(0, 90))}"></button>`
    : `<span class="seg gap${p.kind === 'pause' ? ' pause' : ''}" style="flex-grow:${p.dur.toFixed(3)}" title="${p.para ? 'Paragraph pause' : p.kind === 'pause' ? 'Pause' : 'Gap'} · ${(p.dur * 1000).toFixed(0)} ms"></span>`).join('');
  $$('#segbar button.seg').forEach(b => b.onclick = () => {
    const pos = +b.dataset.pos; if (pos < 0) return;
    script.focus(); script.setSelectionRange(pos, pos);
    const lines = script.value.slice(0, pos).split('\n').length;
    script.scrollTop = Math.max(0, (lines - 3) * parseFloat(getComputedStyle(script).lineHeight));
  });
}

function onEdit() { highlight(); clearTimeout(estTimer); estTimer = setTimeout(updateEstimate, 120); changed(); }
script.addEventListener('input', onEdit);

/* ---------- autosave (this browser only) ---------- */
let saveTimer = 0;
function changed() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => store.set('kokoro-draft', state.startFailed
    // The engine didn't answer at start-up, so the panel shows fallback values: keep the saved settings.
    ? {...(store.get('kokoro-draft', null) || {}), text: script.value, name: $('#projectName').value, pron: state.pron}
    : {text: script.value, name: $('#projectName').value, settings: readSettings(), pron: state.pron}), 400);
  clearTimeout(estTimer); estTimer = setTimeout(updateEstimate, 150);
}
$('#projectName').addEventListener('input', changed);

/* ---------- project files ---------- */
$('#sampleBtn').onclick = () => {
  if (script.value.trim() && !confirm('Replace the current script with the sample?')) return;
  script.value = SAMPLE; $('#projectName').value = 'Harbour - opening'; onEdit(); toast('Sample script loaded.');
};
$('#clearBtn').onclick = () => {
  if (!script.value || confirm('Clear the script?')) { script.value = ''; onEdit(); script.focus(); }
};
$('#exportBtn').onclick = () => {
  const data = {format: 'kokoro-studio-project', version: 2, name: $('#projectName').value, text: script.value,
    settings: readSettings(), pronunciations: state.pron};
  const blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = (($('#projectName').value || 'kokoro-project').replace(/[^\w-]+/g, '-').toLowerCase()) + '.json';
  a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 4000);
};
$('#importBtn').onclick = () => $('#fileInput').click();
$('#fileInput').onchange = async e => {
  const f = e.target.files?.[0]; if (!f) return;
  try {
    const text = await f.text();
    if (/\.json$/i.test(f.name)) {
      const d = JSON.parse(text);
      if (!['kokoro-studio-project', 'kokoro-emotion-studio-project'].includes(d.format) || typeof d.text !== 'string') throw Error('not a Kokoro project file');
      script.value = d.text; $('#projectName').value = d.name || f.name.replace(/\.json$/i, '');
      applySettings(d.settings || {}); state.pron = Array.isArray(d.pronunciations) ? d.pronunciations : state.pron; renderPron();
    } else { script.value = text; $('#projectName').value = f.name.replace(/\.\w+$/, ''); }
    onEdit(); toast('Imported ' + f.name);
  } catch (err) { toast('Import failed: ' + err.message); }
  e.target.value = '';
};

/* ---------- player ---------- */
const audio = $('#audio'), canvas = $('#wave');
let raf = 0;
async function loadTake(rec, autoplay = false) {
  state.take = rec;
  audio.src = rec.audio_url + '?v=' + Date.now();
  $('#playBtn').disabled = false;
  $('#tDur').textContent = fmt(rec.seconds);
  $('#meter').textContent = rec.lufs != null && isFinite(rec.lufs) ? `${(+rec.lufs).toFixed(1)} LUFS · ${(+rec.true_peak).toFixed(1)} dBTP` : '— LUFS';
  const link = (id, href) => { const a = $(id); if (href) { a.href = href; a.removeAttribute('aria-disabled'); a.setAttribute('download', ''); } else { a.removeAttribute('href'); a.setAttribute('aria-disabled', 'true'); } };
  link('#dlWav', rec.download_url); link('#dlMp3', rec.mp3_url); link('#dlSrt', rec.srt_url); link('#dlVtt', rec.vtt_url);
  $('#toVideo').disabled = false;
  state.peaks = null; paintWave();
  let ctx = null;
  try {
    const buf = await (await fetch(rec.audio_url)).arrayBuffer();
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    const dec = await ctx.decodeAudioData(buf);
    if (state.take !== rec) return;  // another take was loaded while this one decoded
    const data = dec.getChannelData(0), n = 900, step = Math.max(1, Math.floor(data.length / n));
    const peaks = new Float32Array(n);
    for (let i = 0; i < n; i++) { let p = 0; const s0 = i * step, s1 = Math.min(data.length, s0 + step); for (let j = s0; j < s1; j += 4) p = Math.max(p, Math.abs(data[j])); peaks[i] = p; }
    state.peaks = peaks;
  } catch { if (state.take !== rec) return; state.peaks = new Float32Array(900).fill(0.2); }
  finally { ctx?.close().catch(() => {}); }
  paintWave();
  if (autoplay) audio.play().catch(() => {});
}
function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
function paintWave() {
  const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
  const mid = h / 2, faint = cssVar('--line-strong');
  if (!state.peaks) {
    ctx.fillStyle = faint; ctx.fillRect(0, mid - 0.5, w, 1);
    return;
  }
  const dur = state.take?.seconds || 1, segs = state.take?.segments || [];
  const pos = audio.duration ? audio.currentTime / audio.duration : 0;
  const n = Math.max(40, Math.floor(w / 3)), barW = w / n;
  let peak = 0.02; state.peaks.forEach(p => { peak = Math.max(peak, p); });
  for (let i = 0; i < n; i++) {
    const p0 = Math.floor(i / n * state.peaks.length), p1 = Math.max(p0 + 1, Math.floor((i + 1) / n * state.peaks.length));
    let v = 0; for (let j = p0; j < p1; j++) v = Math.max(v, state.peaks[j]);
    const t = (i + 0.5) / n * dur;
    const seg = segs.find(s => t >= s.start && t <= s.end);
    const color = seg ? (COLORS[seg.style] || COLORS.neutral) : faint;
    const bh = Math.max(1.5, Math.sqrt(v / peak) * (h - 6));
    ctx.globalAlpha = (i / n) <= pos ? 1 : 0.42;
    ctx.fillStyle = color;
    ctx.fillRect(i * barW + 0.5, mid - bh / 2, Math.max(1, barW - 1.2), bh);
  }
  ctx.globalAlpha = 1;
  if (audio.duration) { ctx.fillStyle = cssVar('--ink'); ctx.fillRect(Math.round(pos * w) - 0.5, 0, 1.5, h); }
}
function tick() {
  $('#tCur').textContent = fmt(audio.currentTime);
  const segs = state.take?.segments || [];
  const s = segs.find(x => audio.currentTime >= x.start && audio.currentTime <= x.end + 0.05);
  $('#nowLine').textContent = s ? s.text : '';
  paintWave();
  if (!audio.paused) raf = requestAnimationFrame(tick);
}
audio.addEventListener('play', () => { stopPreview(); $('#playBtn').textContent = '❚❚'; $('#playBtn').setAttribute('aria-label', 'Pause'); cancelAnimationFrame(raf); tick(); });
audio.addEventListener('pause', () => { $('#playBtn').textContent = '▶'; $('#playBtn').setAttribute('aria-label', 'Play'); tick(); });
audio.addEventListener('ended', () => { $('#nowLine').textContent = ''; });
audio.addEventListener('loadedmetadata', () => { $('#tDur').textContent = fmt(audio.duration); tick(); });
$('#playBtn').onclick = () => (audio.paused ? audio.play() : audio.pause());
canvas.addEventListener('click', e => {
  if (!audio.duration) return;
  const r = canvas.getBoundingClientRect();
  audio.currentTime = clamp((e.clientX - r.left) / r.width, 0, 1) * audio.duration; tick();
});
window.addEventListener('resize', () => { paintWave(); });

/* ---------- render & preview ---------- */
function setBusy(on, label) {
  state.busy = on;
  $('#renderBtn').disabled = on; $('#auditionBtn').disabled = on; $('#auditionEmotion').disabled = on;
  $('#renderBtn').classList.toggle('working', on);
  $('#renderLabel').textContent = on ? (label || 'Rendering…') : 'Render';
  $('#led').classList.toggle('busy', on);
}
async function render() {
  if (state.busy) return;
  const text = script.value;
  if (!text.trim()) { msg('Write something in the script first.', 'error'); script.focus(); return; }
  setBusy(true);
  const target = parseTarget($('#target').value);
  msg(target ? 'Rendering and fitting to length… (the first render also loads the model)' : 'Rendering… the first render also loads the model.');
  const t0 = performance.now();
  try {
    const out = await api('/api/generate', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload(text))});
    const r = out.recording;
    const took = ((performance.now() - t0) / 1000).toFixed(1);
    let note = `${fmt(r.seconds)} rendered in ${took}s · ${r.captions} caption cues`;
    if (r.fit?.gap_scale) note += ` · fitted (pauses ×${r.fit.gap_scale}${r.fit.pace ? `, pace ×${r.fit.pace}` : ''})`;
    msg(note, 'ok');
    if (r.warnings?.length) toast(r.warnings.join(' '));
    await loadTake(r, true);
    refreshCount();
  } catch (err) { msg(err.message, 'error'); }
  finally { setBusy(false); }
}
function lineAtCursor() {
  const s = script.selectionStart, e = script.selectionEnd, v = script.value;
  if (e > s) return v.slice(s, e);
  // The sentence around the cursor, keeping the nearest preceding emotion tag.
  const before = v.slice(0, s);
  const lastStop = [...before.matchAll(/[.!?…]["”’)]*\s/g)].pop();
  const start = Math.max(before.lastIndexOf('\n') + 1, lastStop ? lastStop.index + lastStop[0].length : 0);
  let end = v.slice(s).search(/[.!?…](\s|$)|\n/); end = end < 0 ? v.length : s + end + 1;
  let line = v.slice(start, end).trim();
  if (!line) return '';
  if (!/^\s*\[/.test(line)) {
    const tags = [...v.slice(0, start).matchAll(TAG_RE)].map(m => m[0]).filter(t => canonical(t.slice(1).split(/[:=\]]/)[0]) || /^\[default/i.test(t));
    if (tags.length) line = tags[tags.length - 1] + ' ' + line;
  }
  return line;
}
let previewAudio = null;
function stopPreview() {
  if (!previewAudio) return;
  previewAudio.pause(); URL.revokeObjectURL(previewAudio.src); previewAudio = null;
}
async function preview(text) {
  if (state.busy) return;
  if (!text || !text.replace(TAG_RE, '').trim()) { toast('Put the cursor in a line (or select some text) to preview it.'); return; }
  setBusy(true, 'Preview…');
  try {
    const r = await api('/api/preview', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload(text))});
    const blob = await r.blob();
    stopPreview();  // one preview at a time
    const a = previewAudio = new Audio(URL.createObjectURL(blob));
    a.onended = () => { if (previewAudio === a) stopPreview(); };
    await a.play().catch(() => { if (previewAudio === a) stopPreview(); });
    msg('Preview: ' + text.replace(TAG_RE, '').trim().slice(0, 80));
  } catch (err) { msg(err.message, 'error'); }
  finally { setBusy(false); }
}
$('#renderBtn').onclick = render;
$('#auditionBtn').onclick = () => preview(lineAtCursor());
$('#auditionEmotion').onclick = () => {
  const lines = {
    neutral: 'Here is how this voice sounds with a straight, even read.',
    warm: "It's really good to see you again. Come in, sit down.",
    joyful: 'We did it! Honestly, this is the best news all year.',
    excited: "Wait, are you serious? This is incredible, let's go!",
    curious: 'So what happens if we try it the other way around?',
    confident: 'This is the plan. It works, and here is why.',
    calm: 'Take a slow breath in, and let the day settle.',
    sad: 'I kept the letter for years. I never did send it.',
    angry: 'No. I told you twice, and you still went ahead without me.',
    fearful: "Did you hear that? I think there's someone in the hallway.",
    dramatic: 'And then, without warning, the lights went out.',
    soft: "It's late. Everyone else is asleep, so keep it quiet.",
  };
  preview(lines[state.style] || lines.neutral);
};
document.addEventListener('keydown', e => {
  if (document.body.dataset.view !== 'script') return;
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { if ($('dialog[open]')) return; e.preventDefault(); e.shiftKey ? preview(lineAtCursor()) : render(); }
  if (e.key === ' ' && !['TEXTAREA', 'INPUT', 'SELECT', 'BUTTON'].includes(document.activeElement.tagName) && document.activeElement.getAttribute('role') !== 'slider' && state.take) {
    e.preventDefault(); audio.paused ? audio.play() : audio.pause();
  }
});

/* ---------- takes ---------- */
async function refreshCount() {
  try { const d = await api('/api/recordings?limit=1'); $('#takeCount').textContent = d.total || ''; return d.total; }
  catch { return 0; }
}
const TAKES_PAGE = 50;
const takes = {items: [], total: 0};
// First page, or the next page of older takes when `more` is set.
async function loadTakes(more = false) {
  const list = $('#takeList');
  try {
    const d = await api(`/api/recordings?offset=${more ? takes.items.length : 0}&limit=${TAKES_PAGE}`);
    takes.items = more ? takes.items.concat(d.recordings.filter(r => !takes.items.some(x => x.id === r.id))) : d.recordings;
    takes.total = d.total;
    $('#takeCount').textContent = d.total || '';
  } catch (err) {
    if (more) { toast('Could not load older takes: ' + err.message); renderTakes(); return; }
    list.innerHTML = `<p class="empty">Could not load takes: ${esc(err.message)}</p>`; return;
  }
  renderTakes();
}
function renderTakes() {
  const list = $('#takeList'), items = takes.items;
  if (!items.length) { list.innerHTML = '<p class="empty">No takes yet. Render something from the Script tab.</p>'; return; }
  const voiceName = id => state.catalog?.voices.find(v => v.id === id)?.name || id;
  list.innerHTML = items.map((r, i) => `
    <article class="take">
      <button class="icon-btn play-take" data-i="${i}" aria-label="Load and play">▶</button>
      <div class="take-main">
        <div class="take-name">${esc(r.name || 'Untitled take')}</div>
        <div class="take-meta muted">
          <span class="dot" style="--c:${COLORS[r.style] || COLORS.neutral}"></span>${esc(voiceName(r.voice))} · ${esc(state.styles[r.style]?.label || r.style)}
          · <span class="mono">${fmtShort(r.seconds)}</span>${r.lufs != null ? ` · <span class="mono">${(+r.lufs).toFixed(1)} LUFS</span>` : ''}
          · ${new Date(r.created).toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'})}
        </div>
      </div>
      <div class="take-dl">
        <a class="ex" href="${r.download_url}" download>WAV</a>
        ${r.mp3_url ? `<a class="ex" href="${r.mp3_url}" download>MP3</a>` : ''}
        ${r.captions ? `<a class="ex" href="${r.srt_url}" download>SRT</a><a class="ex" href="${r.vtt_url}" download>VTT</a>` : ''}
      </div>
      <div class="take-act">
        <button class="link-btn" data-tovideo="${i}">Add to video</button>
        ${r.text ? `<button class="link-btn" data-open="${i}">Open script</button>` : ''}
        <button class="link-btn danger" data-del="${r.id}">Delete</button>
      </div>
    </article>`).join('') + (takes.total > items.length
      ? `<p class="empty"><button class="btn" id="moreTakes">Show older takes (${takes.total - items.length} more)</button></p>` : '');
  $('#moreTakes')?.addEventListener('click', e => { e.target.disabled = true; loadTakes(true); });
  $$('#takeList .play-take').forEach(b => b.onclick = () => loadTake(items[+b.dataset.i], true));
  $$('#takeList [data-tovideo]').forEach(b => b.onclick = () => window.KVideo?.addTake(items[+b.dataset.tovideo]));
  $$('#takeList [data-open]').forEach(b => b.onclick = () => {
    const r = items[+b.dataset.open];
    if (script.value.trim() && script.value !== r.text && !confirm('Replace the current script with this take\'s script and settings?')) return;
    script.value = r.text; $('#projectName').value = r.name || '';
    applySettings(r.settings || {});
    if (Array.isArray(r.settings?.pronunciations)) { state.pron = r.settings.pronunciations; renderPron(); }
    onEdit(); show('script'); loadTake(r);
  });
  // Scoped: the pronunciation list also has [data-del] buttons.
  $$('#takeList [data-del]').forEach(b => b.onclick = async () => {
    if (!confirm('Delete this take and its caption files?')) return;
    try {
      await api('/api/recordings/' + b.dataset.del, {method: 'DELETE'});
      // Remove it in place so older pages that were loaded (and the scroll position) stay.
      takes.items = takes.items.filter(x => x.id !== b.dataset.del); takes.total = Math.max(0, takes.total - 1);
      $('#takeCount').textContent = takes.total || ''; renderTakes();
    } catch (err) { toast(err.message); }
  });
}
$('#refreshTakes').onclick = () => loadTakes();

/* ---------- voices & cloning ---------- */
function renderVoiceOptions() {
  const cat = state.catalog, keep = $('#voice').value;
  const opt = v => `<option value="${esc(v.id)}">${esc(v.name)} — ${v.accent === 'British' ? 'UK' : 'US'} ${v.gender.toLowerCase()}</option>`;
  const groups = [['American', 'Female'], ['American', 'Male'], ['British', 'Female'], ['British', 'Male']];
  const grouped = groups.map(([a, g]) => `<optgroup label="${a === 'British' ? 'UK' : 'US'} · ${g.toLowerCase()}">${cat.voices.filter(v => v.accent === a && v.gender === g).map(opt).join('')}</optgroup>`).join('');
  const cloned = cat.cloned || [];
  const mine = cloned.length ? `<optgroup label="Your voices">${cloned.map(v => `<option value="${esc(v.id)}">${esc(v.name)}${v.gender ? ' — ' + esc(v.gender.toLowerCase()) : ''}</option>`).join('')}</optgroup>` : '';
  $('#voice').innerHTML = mine + grouped + '<option value="__add">＋ Add a voice from a recording…</option>';
  if (keep && keep !== '__add') setSelect($('#voice'), keep);
  $('#blendVoice').innerHTML = '<option value="">— none —</option>' + grouped;
  const tagNames = [...cloned.map(v => v.name), ...cat.voices.map(v => v.name)];
  $('#voiceTag').innerHTML = '<option value="">Voice…</option>' + tagNames.map(n => `<option value="${esc(n.toLowerCase())}">${esc(n)}</option>`).join('');
  updateVoiceMode();
}
let lastVoice = 'af_heart';
function updateVoiceMode() {
  const v = $('#voice').value, clone = v.startsWith('clone:');
  $('#cloneOpts').hidden = !clone; $('#kokoroOpts').hidden = clone;
  if (v !== '__add') lastVoice = v;
  $('#variationOut').textContent = String((state.variation || 0) + 1);
}
$('#voice').addEventListener('change', () => {
  if ($('#voice').value === '__add') { setSelect($('#voice'), lastVoice); openVoices(); return; }
  updateVoiceMode();
});
$('#newRead').onclick = () => { state.variation = ((state.variation || 0) + 1) % 10000; updateVoiceMode(); changed(); render(); };

function openVoices() {
  renderCloneList();
  refreshCatalog().then(renderCloneList).catch(() => {});  // install state may have changed
  $('#cloneMsg').textContent = ''; $('#cloneMsg').className = 'msg';
  loadCloneMedia();
  $('#voiceDialog').showModal();
}
$('#voiceDialog').addEventListener('close', () => { stopRecording(true); cutStop(); });

/* ---------- voice cutter: choose the part of a recording a cloned voice is made from ---------- */
const cut = {src: null, start: 0, end: 0, drag: null, audio: null, raf: 0};
const cutMsg = (text, kind = '') => { $('#cloneMsg').className = 'msg' + (kind ? ' ' + kind : ''); $('#cloneMsg').textContent = text; };
async function loadCloneMedia() {
  const sel = $('#cloneMedia');
  try {
    const items = (await api('/api/media')).media.filter(m => m.has_audio && (m.kind === 'audio' || m.kind === 'video'));
    sel.innerHTML = '<option value="">From your video uploads…</option>' +
      items.map(m => `<option value="${esc(m.id)}">${esc(m.name)} (${m.kind}, ${fmtShort(m.duration || 0)})</option>`).join('');
    sel.disabled = !items.length;
    if (!items.length) sel.options[0].textContent = 'No uploads with sound yet';
  } catch { sel.disabled = true; }
}
async function openSource(body, label) {
  cutStop();
  const old = cut.src;
  cutMsg(`Opening ${label}…`);
  $('#cloneSubmit').disabled = true;
  try {
    const r = await fetch('/api/voices/sources', {method: 'POST', body});
    const j = await r.json().catch(() => ({error: `Could not open the recording (${r.status})`}));
    if (!r.ok) throw Error(j.error || 'Could not open the recording.');
    cut.src = j.source; [cut.start, cut.end] = j.source.suggest;
    if (old) fetch(`/api/voices/sources/${old.id}`, {method: 'DELETE'}).catch(() => {});
    cut.audio = new Audio(`/api/voices/sources/${cut.src.id}/audio`); cut.audio.preload = 'auto';
    $('#cutter').hidden = false;
    $('#cutName').textContent = `${cut.src.name || label} · ${fmtShort(cut.src.seconds)}`;
    $('#cutStart').max = $('#cutEnd').max = cut.src.seconds;
    cutUpdate();
    cutMsg(cut.src.seconds > 30 ? 'The clearest 20 seconds are selected. Adjust the selection if you like, then create the voice.' : '');
  } catch (err) { cutMsg(err.message, 'error'); }
  finally { $('#cloneSubmit').disabled = false; }
}
$('#cloneFile').onchange = e => {
  const f = e.target.files[0]; if (!f) return;
  const body = new FormData(); body.append('file', f, f.name);
  openSource(body, f.name);
  e.target.value = '';
};
$('#cloneMedia').onchange = e => {
  if (!e.target.value) return;
  const body = new FormData(); body.append('media', e.target.value);
  openSource(body, e.target.selectedOptions[0].textContent);
  e.target.value = '';
};
// Record straight from the microphone.
let recorder = null;
function stopRecording(discard = false) {
  if (!recorder) return;
  recorder.discard = discard;
  if (recorder.mr.state !== 'inactive') recorder.mr.stop();
}
$('#cloneRec').onclick = async () => {
  if (recorder) { stopRecording(); return; }
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: false, noiseSuppression: false, autoGainControl: true}}); }
  catch { cutMsg('The browser could not use a microphone. Allow microphone access, or choose a file instead.', 'error'); return; }
  const type = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'].find(t => window.MediaRecorder?.isTypeSupported(t));
  const mr = new MediaRecorder(stream, type ? {mimeType: type} : undefined), chunks = [], t0 = Date.now();
  recorder = {mr, discard: false};
  const btn = $('#cloneRec'); btn.setAttribute('aria-pressed', 'true');
  const tick = setInterval(() => { btn.textContent = `■ Stop (${fmtShort((Date.now() - t0) / 1000)})`; }, 250);
  mr.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
  mr.onstop = () => {
    clearInterval(tick); stream.getTracks().forEach(t => t.stop());
    btn.textContent = '● Record'; btn.setAttribute('aria-pressed', 'false');
    const discard = recorder.discard; recorder = null;
    if (discard || !chunks.length) return;
    const blob = new Blob(chunks, {type: mr.mimeType || 'audio/webm'});
    const ext = (mr.mimeType || '').includes('mp4') ? 'm4a' : (mr.mimeType || '').includes('ogg') ? 'ogg' : 'webm';
    const body = new FormData(); body.append('file', blob, `recording.${ext}`);
    openSource(body, 'your recording');
  };
  mr.start(500);
  cutMsg('Recording… speak naturally for 20–30 seconds, then press Stop.');
};
function cutUpdate() {
  const d = cut.src?.seconds || 0;
  cut.start = Math.max(0, Math.min(cut.start, d)); cut.end = Math.max(cut.start, Math.min(cut.end, d));
  if (document.activeElement !== $('#cutStart')) $('#cutStart').value = cut.start.toFixed(1);
  if (document.activeElement !== $('#cutEnd')) $('#cutEnd').value = cut.end.toFixed(1);
  const len = cut.end - cut.start, el = $('#cutLen');
  const [cls, note] = len < 6 ? ['bad', 'too short: needs at least 6 s of speech']
    : len < 10 ? ['warn', 'works; 10–30 s is better'] : len <= 30 ? ['ok', 'a good length']
    : ['warn', 'long: the clearest 30 s are used'];
  el.className = 'cut-len ' + cls; el.textContent = `Selected ${len.toFixed(1)} s · ${note}`;
  cutDraw();
}
function cutDraw() {
  const cv = $('#cutWave'); if (!cut.src || $('#cutter').hidden) return;
  const dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
  if (!w) return;
  cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
  const ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
  const d = cut.src.seconds, P = cut.src.peaks, x = t => t / d * w;
  const ink = cssVar('--ink') || '#222', faint = cssVar('--line-strong') || '#bbb';
  ctx.fillStyle = cssVar('--sunk') || 'rgba(0,0,0,0.04)'; ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = 'rgba(46,125,79,0.16)'; ctx.fillRect(x(cut.start), 0, x(cut.end) - x(cut.start), h);
  for (let i = 0; i < w; i++) {
    const a = Math.floor(i / w * P.length), b = Math.max(a + 1, Math.floor((i + 1) / w * P.length));
    let p = 0; for (let j = a; j < b && j < P.length; j++) p = Math.max(p, P[j]);
    const t = (i + 0.5) / w * d, inside = t >= cut.start && t <= cut.end;
    ctx.fillStyle = inside ? ink : faint;
    const bh = Math.max(1, p * (h - 6)); ctx.fillRect(i, (h - bh) / 2, 1, bh);
  }
  ctx.fillStyle = '#2e7d4f';
  for (const t of [cut.start, cut.end]) { ctx.fillRect(Math.round(x(t)) - 1, 0, 3, h); ctx.fillRect(Math.round(x(t)) - 5, 0, 11, 6); }
  if (cut.audio && !cut.audio.paused) { ctx.fillStyle = '#c0392b'; ctx.fillRect(Math.round(x(cut.audio.currentTime)), 0, 2, h); }
}
function cutTime(e) {
  const r = $('#cutWave').getBoundingClientRect();
  return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * (cut.src?.seconds || 0);
}
$('#cutWave').addEventListener('pointerdown', e => {
  if (!cut.src) return;
  const cv = $('#cutWave'), t = cutTime(e), px = cut.src.seconds / cv.clientWidth * 8;  // 8 px grab zone
  cut.drag = Math.abs(t - cut.start) <= px ? {edge: 'start'} : Math.abs(t - cut.end) <= px ? {edge: 'end'}
    : t > cut.start && t < cut.end ? {move: t - cut.start, len: cut.end - cut.start} : {anchor: t};
  if (cut.drag.anchor !== undefined) { cut.prev = [cut.start, cut.end]; cut.start = cut.end = t; }
  cv.setPointerCapture(e.pointerId); cutStop(); cutUpdate();
});
$('#cutWave').addEventListener('pointermove', e => {
  if (!cut.drag) { const t = cutTime(e), px = (cut.src?.seconds || 0) / $('#cutWave').clientWidth * 8;
    $('#cutWave').style.cursor = cut.src && (Math.abs(t - cut.start) <= px || Math.abs(t - cut.end) <= px) ? 'ew-resize' : t > cut.start && t < cut.end ? 'grab' : 'crosshair'; return; }
  const t = cutTime(e), d = cut.src.seconds, g = cut.drag;
  if (g.edge === 'start') cut.start = Math.min(t, cut.end - 0.5);
  else if (g.edge === 'end') cut.end = Math.max(t, cut.start + 0.5);
  else if (g.move !== undefined) { cut.start = Math.max(0, Math.min(d - g.len, t - g.move)); cut.end = cut.start + g.len; }
  else { cut.start = Math.min(g.anchor, t); cut.end = Math.max(g.anchor, t); }
  cutUpdate();
});
const cutDrop = () => {
  if (!cut.drag) return;
  if (cut.drag.anchor !== undefined && cut.end - cut.start < 0.5) {  // a click, not a drag: play from there
    const t = cut.start; [cut.start, cut.end] = cut.prev; cutUpdate(); cutPlay(t);
  }
  cut.drag = null;
};
$('#cutWave').addEventListener('pointerup', cutDrop);
$('#cutWave').addEventListener('pointercancel', cutDrop);
$('#cutStart').addEventListener('change', e => { cut.start = Math.min(+e.target.value || 0, cut.end - 0.5); cutUpdate(); e.target.value = cut.start.toFixed(1); });
$('#cutEnd').addEventListener('change', e => { cut.end = Math.max(+e.target.value || 0, cut.start + 0.5); cutUpdate(); e.target.value = cut.end.toFixed(1); });
$('#cutBest').onclick = () => { if (cut.src) { [cut.start, cut.end] = cut.src.suggest; cutUpdate(); } };
$('#cutAll').onclick = () => { if (cut.src) { cut.start = 0; cut.end = cut.src.seconds; cutUpdate(); } };
function cutStop() {
  cancelAnimationFrame(cut.raf);
  if (cut.audio && !cut.audio.paused) cut.audio.pause();
  $('#cutPlay').textContent = '▷'; $('#cutPlay').setAttribute('aria-label', 'Play the selected part');
  cutDraw();
}
function cutPlay(from) {
  const a = cut.audio; if (!a) return;
  if (!a.paused && from === undefined) { cutStop(); return; }
  const stopAt = from === undefined ? cut.end : cut.src.seconds;
  a.currentTime = from ?? cut.start;
  a.play().then(() => {
    $('#cutPlay').textContent = '❚❚'; $('#cutPlay').setAttribute('aria-label', 'Stop');
    const loop = () => { if (a.paused || a.currentTime >= stopAt) { cutStop(); return; } cutDraw(); cut.raf = requestAnimationFrame(loop); };
    loop();
  }).catch(() => cutMsg('Could not play the recording.', 'error'));
}
$('#cutPlay').onclick = () => cutPlay();
window.addEventListener('resize', () => { if ($('#voiceDialog').open) cutDraw(); });
$('#openVoices').onclick = openVoices;
function renderCloneList() {
  const st = state.catalog?.cloning || {}, list = state.catalog?.cloned || [];
  const [led, text] = st.installed ? ['on', 'Cloning engine installed.']
    : st.setup === 'installing' ? ['busy', 'The cloning engine is being installed in the background (a large download). You can add voices now; they work as soon as it finishes, without a restart. Progress: <span class="mono">data/logs/setup-cloning.log</span>']
    : st.setup === 'interrupted' ? ['err', 'Installing the cloning engine stopped part-way. It carries on the next time you run <span class="mono">./start.sh</span> while online (or run <span class="mono">./setup-cloning.sh</span>).']
    : ['err', 'Cloning engine not installed yet. You can add voices now; to <b>use</b> them, run <span class="mono">./start.sh</span> while online (it installs it in the background), or <span class="mono">./setup-cloning.sh</span>.'];
  // One wrapper span: .engine-state is a flex row, so loose text and tags would become columns.
  $('#cloneEngine').innerHTML = `<span class="led ${led}"></span><span>${text}</span>`;
  $('#cloneList').innerHTML = list.length ? list.map(v => `
    <div class="clone-row">
      <button type="button" class="icon-btn" data-sample="${esc(v.slug)}" title="Play the recording" aria-label="Play ${esc(v.name)}'s recording">▷</button>
      <div><b>${esc(v.name)}</b> <span class="muted">${v.gender ? esc(v.gender.toLowerCase()) + ' · ' : ''}${v.seconds ?? '?'}s</span></div>
      <button type="button" class="link-btn" data-use="${esc(v.id)}">Use</button>
      <button type="button" class="link-btn danger" data-remove="${esc(v.slug)}">Delete</button>
    </div>`).join('') : '<p class="muted">No voices yet.</p>';
  $$('#cloneList [data-sample]').forEach(b => b.onclick = () => { const a = new Audio(`/api/voices/clone/${b.dataset.sample}/sample`); a.play().catch(() => {}); });
  $$('#cloneList [data-use]').forEach(b => b.onclick = () => { setSelect($('#voice'), b.dataset.use); updateVoiceMode(); changed(); $('#voiceDialog').close(); });
  $$('#cloneList [data-remove]').forEach(b => b.onclick = async () => {
    if (!confirm('Delete this voice? Takes already rendered are kept.')) return;
    try { await api('/api/voices/clone/' + b.dataset.remove, {method: 'DELETE'}); await refreshCatalog(); renderCloneList(); }
    catch (err) { toast(err.message); }
  });
}
async function refreshCatalog() {
  const cat = await api('/api/voices');
  state.catalog = {...state.catalog, ...cat};
  renderVoiceOptions();
}
$('#cloneForm').addEventListener('submit', async e => {
  if (e.submitter && e.submitter.value === 'close') return;
  e.preventDefault();
  const form = $('#cloneForm'), m = $('#cloneMsg');
  if (!form.reportValidity()) return;
  if (!cut.src) { cutMsg('Choose a file, record yourself, or pick one of your uploads first.', 'error'); return; }
  if (cut.end - cut.start < 6) { cutMsg('Select at least 6 seconds of speech (10–30 s works best).', 'error'); return; }
  const data = new FormData();
  for (const k of ['name', 'gender', 'consent']) data.append(k, form.elements[k].type === 'checkbox' ? (form.elements[k].checked ? 'yes' : '') : form.elements[k].value);
  data.append('source', cut.src.id); data.append('start', cut.start.toFixed(2)); data.append('end', cut.end.toFixed(2));
  cutStop();
  $('#cloneSubmit').disabled = true; m.className = 'msg'; m.textContent = 'Preparing the voice…';
  try {
    const r = await fetch('/api/voices/clone', {method: 'POST', body: data});
    const j = await r.json().catch(() => ({error: `Upload failed (${r.status})`}));
    if (!r.ok) throw Error(j.error || 'Upload failed');
    await refreshCatalog();
    setSelect($('#voice'), j.voice.id); updateVoiceMode(); changed();
    form.reset(); renderCloneList();
    cut.src = null; cut.audio = null; $('#cutter').hidden = true;
    m.className = 'msg ok';
    m.textContent = `${j.voice.name} is ready and selected.` + (j.cloning.installed ? ' Use "Hear this emotion" to try it.'
      : j.cloning.setup === 'installing' ? ' It can speak once the cloning engine finishes installing.' : ' Run ./start.sh online (or ./setup-cloning.sh) to start using it.');
  } catch (err) { m.className = 'msg error'; m.textContent = err.message; }
  finally { $('#cloneSubmit').disabled = false; }
});

/* ---------- startup ---------- */
async function init() {
  try {
    const [status, cat] = await Promise.all([api('/api/status'), api('/api/voices')]);
    state.catalog = cat; state.ffmpeg = status.ffmpeg;
    cat.styles.forEach(s => { state.styles[s.id] = s; });
    renderVoiceOptions();
    $('#preset').innerHTML = Object.entries(cat.presets).map(([k, p]) => `<option value="${k}">${esc(p.label)}</option>`).join('');
    buildEmotionUI();
    if (!status.ffmpeg) { $('#mp3').disabled = true; $('#mp3').parentElement.title = 'Install ffmpeg to enable MP3 export'; }
    const draft = store.get('kokoro-draft', null);
    applySettings(draft?.settings || DEFAULTS);
    state.pron = Array.isArray(draft?.pron) ? draft.pron : [];
    renderPron();
    if (draft?.text) { script.value = draft.text; $('#projectName').value = draft.name || ''; }
    onEdit();
    $('#led').classList.add('on');
    $('#engineText').textContent = status.loaded ? 'Engine ready' : 'Engine ready · model loads on first render';
    $('#led').title = $('#engineText').textContent;
    $('#deviceText').textContent = status.cuda ? `${status.gpu}` : 'CPU';
    if (status.offline) toast('Offline mode: only voices already downloaded can be used.');
    refreshCount();
    msg('Ready.');
  } catch (err) {
    $('#led').classList.add('err');
    $('#engineText').textContent = 'Engine not running'; $('#led').title = 'Engine not running';
    $('#deviceText').textContent = '';
    msg('Start the engine with ./start.sh, then reload this page.', 'error');
    state.catalog = {styles: Object.keys(COLORS).map(id => ({id, label: id[0].toUpperCase() + id.slice(1), speed: 1, pitch: 0, range: 1, gap: 0, energy: 0})), voices: [], presets: {}};
    state.catalog.styles.forEach(s => { state.styles[s.id] = s; });
    buildEmotionUI(); selectStyle('neutral');
    const draft = store.get('kokoro-draft', null);
    state.startFailed = true;
    state.pron = Array.isArray(draft?.pron) ? draft.pron : []; renderPron();
    if (draft?.text) { script.value = draft.text; $('#projectName').value = draft.name || ''; }
    onEdit();
  }
  paintWave();
}
$('#toVideo').onclick = () => { if (state.take) window.KVideo?.addTake(state.take); };

// Shared helpers for the video editor (static/video.js).
window.KStudio = {api, toast, esc, show, COLORS, styles: () => state.styles, refreshCount, fmtShort};

init();
})();
