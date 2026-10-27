/* Kokoro Studio – video editor.
 * Timeline + canvas preview. Export happens on the server with ffmpeg; text is
 * drawn by drawText() both here and for export, so preview and file match. */
(() => {
'use strict';
const K = window.KStudio;
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = K.esc;
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const uid = () => Math.random().toString(36).slice(2, 12);
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } },
};
const fmt = s => { s = Math.max(0, s || 0); const m = Math.floor(s / 60); return `${m}:${(s % 60).toFixed(1).padStart(4, '0')}`; };

const FORMATS = {'16:9': [1920, 1080], '9:16': [1080, 1920], '1:1': [1080, 1080], '4:5': [1080, 1350]};
const LANES = [
  {id: 'T1', label: 'Text', kinds: ['text', 'image', 'video', 'color']},
  {id: 'V3', label: 'Layer 3', kinds: ['image', 'video', 'color', 'text']},
  {id: 'V2', label: 'Layer 2', kinds: ['image', 'video', 'color', 'text']},
  {id: 'V1', label: 'Main', kinds: ['image', 'video', 'color', 'text']},
  {id: 'A1', label: 'Voice', kinds: ['audio']},
  {id: 'A2', label: 'Music', kinds: ['audio']},
  {id: 'A3', label: 'Sound', kinds: ['audio']},
];
const DRAW_ORDER = ['V1', 'V2', 'V3', 'T1'];
const FONTS = {
  sans: '"Inter", "Helvetica Neue", Arial, "Liberation Sans", sans-serif',
  serif: 'Georgia, "Times New Roman", "Liberation Serif", serif',
  display: 'Impact, "Anton", "Oswald", "Arial Narrow", "Liberation Sans Narrow", sans-serif',
  rounded: '"Nunito", "Varela Round", "Arial Rounded MT Bold", "Trebuchet MS", sans-serif',
  mono: '"JetBrains Mono", "DejaVu Sans Mono", Menlo, Consolas, monospace',
};
const TEXT_PRESETS = [
  {name: 'Title', box: [0.08, 0.32, 0.84, 0.36], text: {content: 'Your title', size: 0.11, weight: 800, font: 'sans', effect: 'lift'}, anim_in: {type: 'rise', dur: 0.7}, anim_out: {type: 'fade', dur: 0.5}},
  {name: 'Subtitle', box: [0.1, 0.55, 0.8, 0.14], text: {content: 'A short subtitle', size: 0.055, weight: 500, font: 'sans', effect: 'shadow'}, anim_in: {type: 'fade', dur: 0.6}},
  {name: 'Lower third', box: [0.05, 0.74, 0.6, 0.14], text: {content: 'Name Surname\nTitle or place', size: 0.045, weight: 700, align: 'left', effect: 'background', fx_color: '#000000b3'}, anim_in: {type: 'slide-right', dur: 0.6}, anim_out: {type: 'slide-left', dur: 0.5}},
  {name: 'Caption', box: [0.08, 0.8, 0.84, 0.14], text: {content: 'Caption text', size: 0.05, weight: 700, effect: 'background', fx_color: '#000000a6'}},
  {name: 'Bold headline', box: [0.06, 0.08, 0.88, 0.3], text: {content: 'BIG NEWS', size: 0.14, weight: 900, font: 'display', upper: true, effect: 'splice', fx_color: '#e0672a'}, anim_in: {type: 'pop', dur: 0.6}},
  {name: 'Quote', box: [0.12, 0.3, 0.76, 0.4], text: {content: '“Words worth remembering.”', size: 0.07, weight: 400, font: 'serif', italic: true, effect: 'shadow'}},
  {name: 'Neon sign', box: [0.1, 0.35, 0.8, 0.3], text: {content: 'Glow', size: 0.13, weight: 800, font: 'rounded', color: '#ff4fd8', effect: 'neon'}},
  {name: 'Glitch', box: [0.1, 0.35, 0.8, 0.3], text: {content: 'GLITCH', size: 0.13, weight: 900, font: 'display', effect: 'glitch'}},
  {name: 'Scripture', box: [0.1, 0.28, 0.8, 0.44], text: {content: 'Verse text goes here.\n— Book 1:1', size: 0.06, weight: 500, font: 'serif', effect: 'lift'}, anim_in: {type: 'typewriter', dur: 2.5}, anim_out: {type: 'fade', dur: 0.6}},
];
const SHAPE_PRESETS = [
  {name: 'Full background', color: '#1d1b18ff', box: [0, 0, 1, 1], lane: 'V1'},
  {name: 'Dim overlay', color: '#00000080', box: [0, 0, 1, 1], lane: 'V2'},
  {name: 'Bottom band', color: '#000000b3', box: [0, 0.72, 1, 0.2], lane: 'V3'},
  {name: 'Side panel', color: '#f2efe9e6', box: [0, 0, 0.42, 1], lane: 'V3'},
  {name: 'Accent bar', color: '#b8401cff', box: [0.05, 0.68, 0.18, 0.012], lane: 'V3'},
  {name: 'Letterbox bars', color: '#000000ff', box: [0, 0, 1, 0.12], lane: 'V3', twin: [0, 0.88, 1, 0.12]},
];

const MOTION = {fade: [0, 0, 1, 1], rise: [0, 0.06, 1, 1], drop: [0, -0.06, 1, 1], 'slide-left': [1, 0, 1, 0],
  'slide-right': [-1, 0, 1, 0], 'slide-up': [0, 1, 1, 0], 'slide-down': [0, -1, 1, 0], pop: [0, 0, 0.5, 0.5], 'zoom-out': [0, 0, 1.3, 1],
  pan: [0.15, 0, 1, 1], wipe: [0, 0, 1, 0], baseline: [0, 0.05, 1, 0], tumble: [0, 0, 0.5, 1], typewriter: [0, 0, 1, 0]};
const ANIM_ROT = {tumble: -75};
const ANIM_MASK = {wipe: 'wipe-right', baseline: 'wipe-down'}, ANIM_MASK_OUT = {wipe: 'wipe-left', baseline: 'wipe-down'};
const LOOP_FX_LABELS = {none: 'None', breathe: 'Breathe (gentle pulse)', drift: 'Drift (slow float up)'};
const ANIM_LABELS = {none: 'None', fade: 'Fade', rise: 'Rise', drop: 'Drop', 'slide-left': 'Slide left', 'slide-right': 'Slide right',
  'slide-up': 'Slide up', 'slide-down': 'Slide down', pop: 'Pop', 'zoom-out': 'Zoom', pan: 'Pan', wipe: 'Wipe', baseline: 'Baseline',
  tumble: 'Tumble', typewriter: 'Typewriter'};
const TRANSITION_LABELS = {none: 'None', crossfade: 'Dissolve', dip: 'Dip to background', 'slide-left': 'Slide left', 'slide-right': 'Slide right',
  'slide-up': 'Slide up', 'slide-down': 'Slide down', 'wipe-left': 'Line wipe ←', 'wipe-right': 'Line wipe →', 'wipe-up': 'Line wipe ↑',
  'wipe-down': 'Line wipe ↓', circle: 'Circle wipe', clock: 'Clock wipe', 'split-v': 'Split open ↔', 'split-h': 'Split open ↕', zoom: 'Zoom'};
const MASK_TRANSITIONS = ['wipe-left', 'wipe-right', 'wipe-up', 'wipe-down', 'circle', 'clock', 'split-v', 'split-h'];
const TRANSITION_ANIM = {crossfade: 'fade', 'slide-left': 'slide-left', 'slide-right': 'slide-right', 'slide-up': 'slide-up', 'slide-down': 'slide-down', zoom: 'zoom-out'};
/* Same rules as studio/video.py motion_plan(): a transition on clip B holds the clip A that ends
   where B starts (same track) on screen under B while B animates in. */
function prevAdjacent(p, b) {
  const prev = p.clips.filter(a => a !== b && a.kind !== 'audio' && a.lane === b.lane && Math.abs(a.start + a.duration - b.start) < 0.06);
  return prev.sort((x, y) => y.start - x.start)[0] || null;
}
function motionPlan(p) {
  const plan = {};
  const vis = p.clips.filter(c => c.kind !== 'audio');
  for (const c of vis) plan[c.id] = {ext: 0, mask: null, in: c.anim_in?.type && c.anim_in.type !== 'none' ? [c.anim_in.type, c.anim_in.dur] : null,
    out: c.anim_out?.type && c.anim_out.type !== 'none' ? [c.anim_out.type, c.anim_out.dur] : null};
  for (const b of vis) {
    const tr = b.transition; if (!tr || !tr.type || tr.type === 'none') continue;
    const a = prevAdjacent(p, b); if (!a) continue;
    let d = Math.min(tr.dur, b.duration * 0.9);
    if (MASK_TRANSITIONS.includes(tr.type)) { plan[a.id].ext = Math.max(plan[a.id].ext, d); plan[b.id].mask = [tr.type, d]; continue; }
    if (tr.type === 'dip') { d = Math.min(d, a.duration * 0.9); plan[a.id].out = ['fade', d / 2]; plan[b.id].in = ['fade', d / 2]; }
    else { plan[a.id].ext = Math.max(plan[a.id].ext, d); plan[b.id].in = [TRANSITION_ANIM[tr.type], d]; }
  }
  return plan;
}
/* Clip the canvas to the revealed part of a clip during a wipe-type transition (same shapes as export). */
function applyMask(ctx, c, mp, t, W, H) {
  const end = c.start + c.duration + mp.ext;
  let kind = null, p = 1;
  const head = mp.mask || (mp.in && ANIM_MASK[mp.in[0]] ? [ANIM_MASK[mp.in[0]], mp.in[1]] : null);
  if (head && t < c.start + head[1]) { kind = head[0]; p = clamp((t - c.start) / head[1], 0, 1); }
  else if (mp.out && ANIM_MASK_OUT[mp.out[0]] && t > end - mp.out[1]) { kind = ANIM_MASK_OUT[mp.out[0]]; p = 1 - clamp((t - (end - mp.out[1])) / mp.out[1], 0, 1); }
  if (!kind || p >= 1) return;
  const e = p * p * (3 - 2 * p);
  const [x, y, w, h] = c.kind === 'text' ? [0, 0, W, H] : [c.x * W, c.y * H, c.w * W, c.h * H];
  const cx = x + w / 2, cy = y + h / 2;
  ctx.beginPath();
  if (kind === 'wipe-right') ctx.rect(x, y, w * e, h);
  else if (kind === 'wipe-left') ctx.rect(x + w * (1 - e), y, w * e, h);
  else if (kind === 'wipe-down') ctx.rect(x, y, w, h * e);
  else if (kind === 'wipe-up') ctx.rect(x, y + h * (1 - e), w, h * e);
  else if (kind === 'circle') ctx.arc(cx, cy, Math.max(0.01, e * Math.hypot(w / 2, h / 2)), 0, Math.PI * 2);
  else if (kind === 'split-v') ctx.rect(cx - w / 2 * e, y, w * e, h);
  else if (kind === 'split-h') ctx.rect(x, cy - h / 2 * e, w, h * e);
  else if (kind === 'clock') { ctx.moveTo(cx, cy); ctx.arc(cx, cy, Math.hypot(w, h), -Math.PI / 2, -Math.PI / 2 + e * Math.PI * 2); ctx.closePath(); }
  ctx.clip();
}
function motionAt(c, mp, t) {
  const end = c.start + c.duration + mp.ext;
  let dx = 0, dy = 0, sc = 1, a = 1, rot = 0;
  if (c.loop_fx === 'breathe') sc *= 1 + 0.03 * (0.5 - 0.5 * Math.cos(2 * Math.PI * (t - c.start) / 2.4));
  else if (c.loop_fx === 'drift') dy -= 0.035 * clamp((t - c.start) / (end - c.start), 0, 1);
  if (mp.in) {
    const [mx, my, ms, share] = MOTION[mp.in[0]], rem = Math.pow(1 - clamp((t - c.start) / mp.in[1], 0, 1), 3);
    dx += mx * rem; dy += my * rem; sc *= 1 + (ms - 1) * rem; rot += (ANIM_ROT[mp.in[0]] || 0) * rem;
    if (share) a *= clamp((t - c.start) / (mp.in[1] * share), 0, 1);
  }
  if (mp.out) {
    const [mx, my, ms, share] = MOTION[mp.out[0]], rem = Math.pow(1 - clamp((end - t) / mp.out[1], 0, 1), 3);
    dx -= mx * rem; dy -= my * rem; sc *= 1 + (ms - 1) * rem; rot -= (ANIM_ROT[mp.out[0]] || 0) * rem;
    if (share) a *= clamp((end - t) / (mp.out[1] * share), 0, 1);
  }
  return {dx, dy, sc, a, rot};
}

const st = {
  project: null, sel: null, time: 0, playing: false, pps: 60, snap: true,
  undo: [], redo: [], media: {}, takes: {}, els: {}, imgs: {}, saveTimer: 0, saving: false, dirty: false,
  clock0: 0, time0: 0, raf: 0, ready: false, shown: false, job: null, peaks: {}, clipboard: null,
  magnet: store.get('ve-magnet', true),
};

/* ---------------------------------------------------------------- data */
const api = K.api;
const jsonPost = (url, body, method = 'POST') => api(url, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
const P = () => st.project;
const clipById = id => P()?.clips.find(c => c.id === id);
const duration = () => Math.max(0, ...P().clips.map(c => c.start + c.duration));
const isVisual = c => c.kind !== 'audio';
const laneOf = id => LANES.find(l => l.id === id);

function mediaUrl(mid, preview = true) {
  if (mid.startsWith('take:')) return `/api/audio/${mid.slice(5)}`;
  const m = st.media[mid];
  return `/api/media/${mid}/file` + (preview && m?.proxy === 'ready' ? '?preview=1' : '');
}
function clipName(c) {
  if (c.kind === 'text') return c.text.content.split('\n')[0] || 'Text';
  if (c.kind === 'color') return 'Shape';
  if (c.media?.startsWith('take:')) return st.takes[c.media.slice(5)]?.name || c.name || 'Voice take';
  return st.media[c.media]?.name || c.name || c.kind;
}

async function loadLibrary() {
  const [m, r] = await Promise.all([api('/api/media'), api('/api/recordings')]);
  st.media = Object.fromEntries(m.media.map(x => [x.id, x]));
  st.takes = Object.fromEntries(r.recordings.map(x => [x.id, x]));
  renderBin();
}

async function openProject(id) {
  stop();
  let project = null;
  if (id) { try { project = (await api(`/api/video/projects/${id}`)).project; } catch { project = null; } }
  if (!project) project = (await jsonPost('/api/video/projects', {name: 'Untitled video', format: '16:9'})).project;
  st.project = project; st.sel = null; st.time = 0; st.undo = []; st.redo = [];
  store.set('ve-last', project.id);
  resetElements();
  syncTop(); layoutStage(); renderAll();
}

function snapshot() { return JSON.stringify(P()); }
function commit(fn, {save = true} = {}) {
  st.undo.push(snapshot()); if (st.undo.length > 150) st.undo.shift();
  st.redo = [];
  fn(P());
  afterChange(save);
}
/* Magnetic Main track (like Canva's scenes): clips on Main play back to back, so deleting or
   trimming one closes the gap and dragging one reorders the sequence. */
function packMain(p) {
  if (!st.magnet) return;
  let t = 0;
  p.clips.filter(c => c.lane === 'V1').sort((a, b) => a.start - b.start).forEach(c => { c.start = Math.round(t * 1000) / 1000; t += c.duration; });
}
function afterChange(save = true) {
  packMain(P());
  pruneElements(); renderAll();
  if (save) scheduleSave();
}
function undo() { if (!st.undo.length) return; st.redo.push(snapshot()); st.project = JSON.parse(st.undo.pop()); afterChange(); }
function redo() { if (!st.redo.length) return; st.undo.push(snapshot()); st.project = JSON.parse(st.redo.pop()); afterChange(); }

function scheduleSave() {
  st.dirty = true; $('#veSaved').textContent = 'Unsaved changes';
  clearTimeout(st.saveTimer); st.saveTimer = setTimeout(save, 700);
}
async function save() {
  if (!P()) return;
  clearTimeout(st.saveTimer);
  if (st.saving) { st.saveTimer = setTimeout(save, 400); return; }
  st.saving = true; $('#veSaved').textContent = 'Saving…';
  try {
    const sent = snapshot();
    const r = await jsonPost(`/api/video/projects/${P().id}`, P(), 'PUT');
    // Keep local edits made while saving; adopt server clean-ups otherwise.
    if (snapshot() === sent) {
      const before = P().clips.length; st.project = r.project;
      if (r.project.clips.length !== before) { renderAll(); K.toast('Some clips referred to deleted media and were removed.'); }
    }
    st.dirty = false; $('#veSaved').textContent = 'Saved';
  } catch (err) { $('#veSaved').textContent = 'Not saved: ' + err.message; }
  finally { st.saving = false; }
}

/* ---------------------------------------------------------------- adding */
function freeLane(candidates, start, dur, ignore) {
  return candidates.find(l => !P().clips.some(c => c.lane === l && c !== ignore && c.start < start + dur - 0.01 && c.start + c.duration > start + 0.01));
}
function laneEnd(lane) { return Math.max(0, ...P().clips.filter(c => c.lane === lane).map(c => c.start + c.duration)); }

function addClip(clip, {select = true} = {}) {
  const c = {id: uid(), x: 0, y: 0, w: 1, h: 1, fit: 'cover', opacity: 1, fade_in: 0, fade_out: 0, zoom: 0, volume: 1, muted: false, in: 0,
    speed: 1, loop: false, ...clip};
  commit(p => p.clips.push(c));
  if (select) select_(c.id);
  return c;
}
function addMedia(meta, at = null, laneHint = null) {
  if (!meta) return;
  const t = at ?? st.time;
  if (meta.kind === 'audio') {
    const dur = meta.duration || 5;
    const lane = laneHint || freeLane(['A2', 'A3', 'A1'], t, dur) || 'A2';
    return addClip({kind: 'audio', media: meta.id, lane, start: t, duration: dur, name: meta.name});
  }
  const dur = meta.kind === 'video' ? (meta.duration || 5) : 5;
  let lane = laneHint, start = t;
  if (!lane) {
    // Like scenes in Canva: pictures and clips play one after another on the Main track.
    lane = 'V1';
    const under = P().clips.find(c => c.lane === 'V1' && t >= c.start && t < c.start + c.duration);
    if (under) start = st.magnet ? under.start + under.duration - 0.001 : laneEnd('V1');
  }
  const fit = fitFor(meta);
  return addClip({kind: meta.kind, media: meta.id, lane, start, duration: dur, name: meta.name, fit});
}
function fitFor(meta) {
  // Pictures with a very different shape from the frame are shown whole rather than cropped.
  const [W, H] = [P().width, P().height];
  if (!meta.width || !meta.height) return 'cover';
  const r = (meta.width / meta.height) / (W / H);
  return r > 1.6 || r < 0.62 ? 'contain' : 'cover';
}
async function addTake(rec, {captions = false, at = null} = {}) {
  if (!rec) return;
  await init();
  st.takes[rec.id] = rec;
  const t = at ?? (P().clips.some(c => c.lane === 'A1') ? Math.max(st.time, 0) : 0);
  const lane = freeLane(['A1', 'A3', 'A2'], t, rec.seconds) || 'A1';
  const clip = addClip({kind: 'audio', media: 'take:' + rec.id, lane, start: t, duration: rec.seconds, name: rec.name});
  if (captions) await addCaptions(clip);
  return clip;
}
const CAPTION_STYLES = {classic: 'Classic (a line at a time)', highlight: 'Highlight the spoken word', single: 'One word at a time'};
let captionStyle = store.get('ve-capstyle', 'highlight');
async function addCaptions(clip, style = captionStyle) {
  const rid = clip.media.slice(5);
  let cues;
  try { cues = (await api(`/api/recordings/${rid}/cues`)).cues; } catch (err) { K.toast('No captions for this take: ' + err.message); return; }
  if (!cues.length) { K.toast('This take has no captions.'); return; }
  const sp = clip.speed || 1;
  const at = s => clip.start + (s - clip.in) / sp;              // take time -> timeline time
  const inside = (a, b) => b > clip.start + 0.02 && a < clip.start + clip.duration - 0.02;
  const base = {fit: 'cover', opacity: 1, fade_in: 0, fade_out: 0, zoom: 0, volume: 1, muted: false, in: 0, speed: 1, loop: false};
  const made = [];
  for (const cue of cues) {
    const words = cue.words?.length ? cue.words : [{w: cue.text.replace(/\n/g, ' '), s: cue.start, e: cue.end}];
    if (style === 'classic') {
      const a = at(cue.start), b = at(cue.end);
      if (!inside(a, b)) continue;
      made.push({...base, kind: 'text', lane: 'T1', start: a, duration: b - a, x: 0.08, y: 0.8, w: 0.84, h: 0.14, name: 'Caption',
        text: textDefaults({content: cue.text.replace(/\n/g, ' '), size: 0.05, weight: 700, effect: 'background', fx_color: '#000000a6'})});
    } else if (style === 'highlight') {
      words.forEach((w, j) => {
        const a = at(w.s), b = at(j + 1 < words.length ? words[j + 1].s : Math.max(w.e, cue.end));
        if (!inside(a, b) || b - a < 0.04) return;
        made.push({...base, kind: 'text', lane: 'T1', start: a, duration: b - a, x: 0.06, y: 0.66, w: 0.88, h: 0.24, name: 'Caption',
          text: textDefaults({content: cue.text.replace(/\n/g, ' '), size: 0.065, weight: 900, font: 'sans', upper: true, effect: 'outline',
            color: '#ffffff', hl_word: j, hl_color: '#ffd400'})});
      });
    } else {
      words.forEach((w, j) => {
        const a = at(w.s), b = at(j + 1 < words.length ? words[j + 1].s : Math.max(w.e, cue.end));
        if (!inside(a, b) || b - a < 0.04) return;
        made.push({...base, kind: 'text', lane: 'T1', start: a, duration: b - a, x: 0.08, y: 0.4, w: 0.84, h: 0.2, name: 'Caption',
          anim_in: {type: 'pop', dur: Math.min(0.15, (b - a) / 2)},
          text: textDefaults({content: w.w.replace(/[.,;:!?…]+$/, ''), size: 0.14, weight: 900, font: 'display', upper: true,
            effect: 'outline', color: '#ffffff'})});
      });
    }
  }
  // Clip each caption to the take and avoid overlaps between neighbours.
  made.forEach((m, i) => {
    m.id = uid();
    const s0 = Math.max(clip.start, m.start), e0 = Math.min(clip.start + clip.duration, m.start + m.duration,
      i + 1 < made.length ? Math.max(made[i + 1].start, s0 + 0.04) : Infinity);
    m.start = Math.round(s0 * 1000) / 1000; m.duration = Math.max(0.04, Math.round((e0 - s0) * 1000) / 1000);
  });
  commit(p => { p.clips.push(...made); });
  K.toast(`Added ${made.length} caption clip${made.length === 1 ? '' : 's'} on the Text track (${CAPTION_STYLES[style].toLowerCase()}).`);
}
function textDefaults(t) {
  return {content: 'Text', font: 'sans', size: 0.07, weight: 700, color: '#ffffff', bg: '', align: 'center',
    italic: false, shadow: false, upper: false, outline: false, effect: 'shadow', fx_color: '#000000', ...t};
}

/* ---------------------------------------------------------------- bin */
function renderBin() {
  const items = Object.values(st.media).sort((a, b) => (b.created || '').localeCompare(a.created || ''));
  $('#veMedia').innerHTML = items.length ? items.map(m => `
    <div class="ve-item" draggable="true" data-media="${m.id}" title="${esc(m.name)} — drag to the timeline or click + to add at the playhead">
      <div class="ve-thumb ${m.kind}">${m.kind === 'audio' ? '<span>♪</span>' : `<img src="/api/media/${m.id}/thumb" alt="" loading="lazy">`}
        ${m.duration ? `<em class="mono">${K.fmtShort(m.duration)}</em>` : ''}
        ${m.proxy === 'pending' ? '<em class="busy">preparing preview…</em>' : ''}</div>
      <div class="ve-item-name">${esc(m.name)}</div>
      <div class="ve-item-act">
        <button class="icon-btn" data-add="${m.id}" title="Add at playhead" aria-label="Add ${esc(m.name)}">+</button>
        <button class="icon-btn" data-delmedia="${m.id}" title="Delete from library" aria-label="Delete ${esc(m.name)}">×</button>
      </div>
    </div>`).join('') : '<p class="muted ve-empty">Nothing uploaded yet.</p>';
  $$('#veMedia [data-add]').forEach(b => b.onclick = () => addMedia(st.media[b.dataset.add]));
  $$('#veMedia [data-delmedia]').forEach(b => b.onclick = () => deleteMedia(b.dataset.delmedia));
  $$('#veMedia .ve-item').forEach(el => el.addEventListener('dragstart', e => e.dataTransfer.setData('text/x-ve', JSON.stringify({media: el.dataset.media}))));

  const takes = Object.values(st.takes).sort((a, b) => (b.created || '').localeCompare(a.created || ''));
  $('#veTakes').innerHTML = (takes.length ? `<label class="mini">Caption style<select class="input" id="veCapStyle">${Object.entries(CAPTION_STYLES).map(([k, v]) => `<option value="${k}" ${captionStyle === k ? 'selected' : ''}>${v}</option>`).join('')}</select></label>` : '') + (takes.length ? takes.map(r => `
    <div class="ve-take" draggable="true" data-take="${r.id}">
      <div class="ve-take-bar">${(r.segments || []).map(s => `<i style="flex-grow:${(s.end - s.start).toFixed(2)};background:${K.COLORS[s.style] || '#888'}"></i>`).join('')}</div>
      <div class="ve-take-name">${esc(r.name || 'Voice take')}</div>
      <div class="muted mono ve-take-meta">${K.fmtShort(r.seconds)} · ${esc(String(r.voice || '').replace('clone:', ''))}</div>
      <div class="ve-take-act">
        <button class="icon-btn" data-hear="${r.id}" title="Listen" aria-label="Listen">▷</button>
        <button class="link-btn" data-addtake="${r.id}">Add</button>
        <button class="link-btn" data-addcap="${r.id}" ${r.captions ? '' : 'disabled'}>Add with captions</button>
      </div>
    </div>`).join('') : '<p class="muted ve-empty">No voice takes yet. Render one in the Script tab.</p>');
  $('#veCapStyle')?.addEventListener('change', e => { captionStyle = e.target.value; store.set('ve-capstyle', captionStyle); });
  $$('#veTakes [data-addtake]').forEach(b => b.onclick = () => addTake(st.takes[b.dataset.addtake]));
  $$('#veTakes [data-addcap]').forEach(b => b.onclick = () => addTake(st.takes[b.dataset.addcap], {captions: true}));
  let hear = null;
  $$('#veTakes [data-hear]').forEach(b => b.onclick = () => { if (hear) hear.pause(); hear = new Audio(`/api/audio/${b.dataset.hear}`); hear.play().catch(() => {}); });
  $$('#veTakes .ve-take').forEach(el => el.addEventListener('dragstart', e => e.dataTransfer.setData('text/x-ve', JSON.stringify({take: el.dataset.take}))));
}
function renderPresets() {
  $('#veTextPresets').innerHTML = TEXT_PRESETS.map((p, i) => `<button class="ve-preset text" data-tp="${i}" style="font-family:${FONTS[p.text.font || 'sans']};font-weight:${p.text.weight};${p.text.italic ? 'font-style:italic;' : ''}">${esc(p.name)}</button>`).join('');
  $$('[data-tp]').forEach(b => b.onclick = () => {
    const p = TEXT_PRESETS[+b.dataset.tp], [x, y, w, h] = p.box;
    const lane = freeLane(['T1', 'V3', 'V2'], st.time, 4) || 'T1';
    addClip({kind: 'text', lane, start: st.time, duration: 4, x, y, w, h, name: p.name, text: textDefaults(p.text),
      ...(p.anim_in ? {anim_in: p.anim_in} : {}), ...(p.anim_out ? {anim_out: p.anim_out} : {})});
  });
  $('#veShapePresets').innerHTML = SHAPE_PRESETS.map((p, i) => `<button class="ve-preset shape" data-sp="${i}"><i style="background:${p.color.slice(0, 7)};opacity:${parseInt(p.color.slice(7, 9) || 'ff', 16) / 255}"></i>${esc(p.name)}</button>`).join('');
  $$('[data-sp]').forEach(b => b.onclick = () => {
    const p = SHAPE_PRESETS[+b.dataset.sp], dur = Math.max(4, duration() - st.time) || 4;
    const mk = box => ({id: uid(), kind: 'color', color: p.color, lane: p.lane, start: st.time, duration: dur, x: box[0], y: box[1], w: box[2], h: box[3],
      fit: 'cover', opacity: 1, fade_in: 0, fade_out: 0, zoom: 0, volume: 1, muted: false, in: 0, name: p.name});
    const first = mk(p.box);
    commit(pr => { pr.clips.push(first); if (p.twin) pr.clips.push(mk(p.twin)); });
    select_(first.id);
  });
}
$$('.ve-bin-tabs button').forEach(b => b.onclick = () => {
  $$('.ve-bin-tabs button').forEach(x => x.classList.toggle('active', x === b));
  $$('.ve-bin-body').forEach(p => { p.hidden = p.dataset.pane !== b.dataset.bin; });
  if (b.dataset.bin === 'voices') loadLibrary().catch(() => {});
});

async function upload(files) {
  files = [...files]; if (!files.length) return [];
  const stateEl = $('#veUploadState');
  stateEl.classList.remove('error');
  const data = new FormData(); files.forEach(f => data.append('files', f));
  stateEl.textContent = `Uploading ${files.length} file${files.length === 1 ? '' : 's'}…`;
  try {
    const r = await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/media');
      xhr.upload.onprogress = e => { if (e.lengthComputable) stateEl.textContent = `Uploading… ${Math.round(e.loaded / e.total * 100)}%`; };
      xhr.onload = () => { let j = {}; try { j = JSON.parse(xhr.responseText); } catch { /* */ } xhr.status < 300 ? resolve(j) : reject(Error(j.error || `Upload failed (${xhr.status})`)); };
      xhr.onerror = () => reject(Error('Upload failed. Is the studio still running?'));
      xhr.send(data);
    });
    r.added.forEach(m => { st.media[m.id] = m; });
    renderBin();
    stateEl.textContent = `Added ${r.added.length} file${r.added.length === 1 ? '' : 's'}.` + (r.errors.length ? ' ' + r.errors.join(' ') : '');
    stateEl.classList.toggle('error', r.errors.length > 0);
    if (r.added.some(m => m.proxy === 'pending')) pollProxies();
    $$('.ve-bin-tabs button')[0].click();
    return r.added;
  } catch (err) { stateEl.textContent = err.message; stateEl.classList.add('error'); $$('.ve-bin-tabs button')[0].click(); return []; }
}
/* Files dropped anywhere in the Video tab are uploaded (never opened by the browser).
   Dropped on a track, they land there; dropped on the picture, at the playhead. */
const hasFiles = e => [...(e.dataTransfer?.types || [])].includes('Files');
window.addEventListener('dragover', e => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener('drop', async e => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  if (document.body.dataset.view !== 'video') { K.toast('Open the Video tab to add pictures and videos.'); return; }
  if (e.target.closest('#veDrop')) return;  // the upload box handles its own drops
  const laneEl = e.target.closest('.ve-lane'), onStage = e.target.closest('#veStage');
  const at = laneEl ? snapTime(xToTime(e.clientX), null) : st.time;
  const added = await upload(e.dataTransfer.files);
  if (!added.length || (!laneEl && !onStage)) return;
  let t = at;
  for (const m of added) {
    const lane = laneEl && laneOf(laneEl.dataset.lane).kinds.includes(m.kind === 'audio' ? 'audio' : m.kind) ? laneEl.dataset.lane : null;
    const c = addMedia(m, t, lane);
    if (c) t = c.start + c.duration;
  }
});
let proxyTimer = 0;
function pollProxies() {
  clearTimeout(proxyTimer);
  proxyTimer = setTimeout(async () => {
    const m = await api('/api/media').catch(() => null); if (!m) return;
    const changed = m.media.some(x => st.media[x.id] && st.media[x.id].proxy !== x.proxy);
    st.media = Object.fromEntries(m.media.map(x => [x.id, x]));
    if (changed) { renderBin(); resetElements(); draw(); }
    if (m.media.some(x => x.proxy === 'pending')) pollProxies();
  }, 2500);
}
$('#veFiles').onchange = e => { upload(e.target.files); e.target.value = ''; };
const drop = $('#veDrop');
['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); drop.classList.add('over'); } }));
['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, () => drop.classList.remove('over')));
drop.addEventListener('drop', e => { e.preventDefault(); upload(e.dataTransfer.files); });

async function deleteMedia(mid) {
  const used = P().clips.filter(c => c.media === mid).length;
  if (!confirm(used ? `This file is used ${used} time(s) in this video. Delete it and remove those clips?` : 'Delete this file from the library?')) return;
  try {
    await api(`/api/media/${mid}`, {method: 'DELETE'});
    delete st.media[mid];
    if (used) commit(p => { p.clips = p.clips.filter(c => c.media !== mid); });
    renderBin();
  } catch (err) { K.toast(err.message); }
}

/* ---------------------------------------------------------------- media elements */
function resetElements() {
  Object.values(st.els).forEach(el => { el.pause?.(); el.removeAttribute('src'); el.load?.(); });
  st.els = {};
}
function pruneElements() {
  for (const id of Object.keys(st.els)) {
    const c = clipById(id);
    if (!c || st.els[id].dataset.media !== c.media) { st.els[id].pause(); delete st.els[id]; }
  }
}
function elementFor(c) {
  if (c.kind === 'image') {
    let img = st.imgs[c.media];
    if (!img) { img = new Image(); img.onload = () => draw(); img.src = mediaUrl(c.media); st.imgs[c.media] = img; }
    return img;
  }
  if (c.kind !== 'video' && c.kind !== 'audio') return null;
  let el = st.els[c.id];
  if (!el) {
    el = document.createElement(c.kind === 'video' ? 'video' : 'audio');
    el.preload = 'auto'; el.playsInline = true; el.crossOrigin = 'anonymous';
    el.dataset.media = c.media;
    el.src = mediaUrl(c.media);
    if (c.kind === 'video') el.addEventListener('seeked', () => { if (!st.playing) draw(); });
    el.addEventListener('loadeddata', () => { if (!st.playing) { seekEl(c, el, st.time); draw(); } });
    st.els[c.id] = el;
  }
  return el;
}
/* Where in the source file the playhead is: honours speed, looping and the frozen tail. */
function sourceTime(c, el, t) {
  const src = el?.duration || sourceDuration(c);
  let x = c.in + Math.max(0, t - c.start) * (c.speed || 1);
  if (Number.isFinite(src) && x >= src - 0.03) {
    if (c.loop && src > 0.1) x = (x - src) % src;
    else return {t: Math.max(0, src - 0.05), frozen: true};
  }
  return {t: x, frozen: false};
}
function seekEl(c, el, t) {
  const want = sourceTime(c, el, t).t;
  if (el.readyState >= 1 && Math.abs(el.currentTime - want) > 0.04) { try { el.currentTime = Math.max(0, want); } catch { /* */ } }
}
function fadeFactor(c, t) {
  const local = t - c.start, rest = c.start + c.duration - t;
  let a = 1;
  if (c.fade_in > 0) a = Math.min(a, local / c.fade_in);
  if (c.fade_out > 0) a = Math.min(a, rest / c.fade_out);
  return clamp(a, 0, 1);
}
function syncMedia(t, playing) {
  const plan = motionPlan(P());
  for (const c of P().clips) {
    if (c.kind !== 'video' && c.kind !== 'audio') continue;
    const el = elementFor(c);
    const held = t >= c.start + c.duration;  // shown under the next clip during a transition: picture only
    const active = t >= c.start && t < c.start + c.duration + (plan[c.id]?.ext || 0);
    const silent = c.muted || c.volume <= 0 || held;
    el.muted = silent;
    el.volume = clamp(c.volume * fadeFactor(c, t), 0, 1);
    if (!active) { if (!el.paused) el.pause(); continue; }
    el.playbackRate = c.speed || 1;
    const want = sourceTime(c, el, t);
    if (playing) {
      if (want.frozen) { if (!el.paused) el.pause(); if (Math.abs(el.currentTime - want.t) > 0.05) el.currentTime = want.t; }
      else if (el.paused) { if (Math.abs(el.currentTime - want.t) > 0.05) el.currentTime = want.t; el.play().catch(() => {}); }
      else if (Math.abs(el.currentTime - want.t) > 0.25) el.currentTime = want.t;
    } else {
      if (!el.paused) el.pause();
      seekEl(c, el, t);
    }
  }
}

/* ---------------------------------------------------------------- drawing */
function wrapLines(ctx, text, maxW) {
  const out = [];
  for (const para of String(text).split('\n')) {
    const words = para.split(/\s+/).filter(Boolean);
    if (!words.length) { out.push(''); continue; }
    let line = words[0];
    for (const w of words.slice(1)) {
      if (ctx.measureText(line + ' ' + w).width <= maxW) line += ' ' + w;
      else { out.push(line); line = w; }
    }
    out.push(line);
  }
  return out;
}
function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}
function hexToRgba(hex) {
  const h = hex.replace('#', ''), a = h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1;
  return `rgba(${parseInt(h.slice(0, 2), 16)},${parseInt(h.slice(2, 4), 16)},${parseInt(h.slice(4, 6), 16)},${a.toFixed(3)})`;
}
const EFFECTS = ['none', 'shadow', 'lift', 'hollow', 'splice', 'outline', 'echo', 'glitch', 'neon', 'background'];
function effectOf(t) { return EFFECTS.includes(t.effect) ? t.effect : t.outline ? 'outline' : t.shadow ? 'shadow' : 'none'; }
function withAlpha(rgba, a) { return rgba.replace(/[\d.]+\)$/, `${a})`); }
/* Colour adjustments: same matrix as color_matrix() in studio/video.py, applied in the preview through an
   SVG feColorMatrix (sRGB) so it matches the exported colours. */
const FILTER_PRESETS = {none: {}, vivid: {saturation: 0.35, contrast: 0.1}, warm: {warmth: 0.6, saturation: 0.1}, cool: {warmth: -0.6},
  mono: {tone: 'mono', contrast: 0.1}, noir: {tone: 'mono', contrast: 0.45, brightness: -0.05}, sepia: {tone: 'sepia'},
  fade: {contrast: -0.25, saturation: -0.2, brightness: 0.05}, retro: {warmth: 0.4, saturation: -0.25, contrast: -0.1}};
const ADJ_ZERO = {brightness: 0, contrast: 0, saturation: 0, warmth: 0, blur: 0, tone: ''};
const mul = (a, b) => a.map(r => [0, 1, 2].map(j => r[0] * b[0][j] + r[1] * b[1][j] + r[2] * b[2][j]));
const mulv = (a, v) => a.map(r => r[0] * v[0] + r[1] * v[1] + r[2] * v[2]);
function colorMatrix(adj) {
  const kb = 1 + adj.brightness, kc = 1 + adj.contrast, ks = 1 + adj.saturation;
  let m = [[kb * kc, 0, 0], [0, kb * kc, 0], [0, 0, kb * kc]], off = [0.5 * (1 - kc), 0.5 * (1 - kc), 0.5 * (1 - kc)];
  const sat = v => [[0.213 + 0.787 * v, 0.715 - 0.715 * v, 0.072 - 0.072 * v], [0.213 - 0.213 * v, 0.715 + 0.285 * v, 0.072 - 0.072 * v],
    [0.213 - 0.213 * v, 0.715 - 0.715 * v, 0.072 + 0.928 * v]];
  const steps = [sat(ks)];
  if (adj.tone === 'mono') steps.push(sat(0));
  else if (adj.tone === 'sepia') steps.push([[0.393, 0.769, 0.189], [0.349, 0.686, 0.168], [0.272, 0.534, 0.131]]);
  const w = adj.warmth; steps.push([[1 + 0.2 * w, 0, 0], [0, 1 + 0.04 * w, 0], [0, 0, 1 - 0.2 * w]]);
  for (const t of steps) { m = mul(t, m); off = mulv(t, off); }
  return {m, off};
}
function isPlainAdjust(a) { return !a || (!a.brightness && !a.contrast && !a.saturation && !a.warmth && !a.tone); }
function canvasFilter(c, W, H) {
  const a = c.adjust, parts = [];
  if (a && !isPlainAdjust(a)) {
    const id = 'vef-' + c.id, {m, off} = colorMatrix(a);
    const values = [0, 1, 2].map(i => `${m[i].map(v => v.toFixed(5)).join(' ')} 0 ${off[i].toFixed(5)}`).join(' ') + ' 0 0 0 1 0';
    let f = document.getElementById(id);
    if (!f) {
      f = document.createElementNS('http://www.w3.org/2000/svg', 'filter'); f.id = id; f.setAttribute('color-interpolation-filters', 'sRGB');
      f.appendChild(document.createElementNS('http://www.w3.org/2000/svg', 'feColorMatrix')); $('#veFilterDefs').appendChild(f);
    }
    const fe = f.firstChild; fe.setAttribute('type', 'matrix'); if (fe.getAttribute('values') !== values) fe.setAttribute('values', values);
    parts.push(`url(#${id})`);
  }
  if (a?.blur > 0) parts.push(`blur(${(a.blur * 0.02 * Math.min(W, H)).toFixed(2)}px)`);
  return parts.join(' ') || 'none';
}
/* Shared by preview and export: draws a text clip into a W×H frame. */
function textLength(c) { return (c.text.upper ? c.text.content.toUpperCase() : c.text.content).replace(/\n/g, '').length; }
function drawText(ctx, c, W, H, chars = null) {
  const t = c.text, fs = t.size * Math.min(W, H);  // same look in landscape and vertical
  ctx.save();
  ctx.font = `${t.italic ? 'italic ' : ''}${t.weight} ${fs}px ${FONTS[t.font] || FONTS.sans}`;
  ctx.textBaseline = 'middle';
  const bx = c.x * W, by = c.y * H, bw = c.w * W, bh = c.h * H;
  const content = t.upper ? t.content.toUpperCase() : t.content;
  let lines = wrapLines(ctx, content, bw);
  if (chars !== null) {  // Typewriter: same line layout as the finished text, cut after `chars` letters
    let left = chars;
    lines = lines.map(ln => { const keep = ln.slice(0, Math.max(0, left)); left -= ln.length; return keep; });
  }
  const lh = fs * 1.22, blockH = lines.length * lh;
  let y = by + (bh - blockH) / 2 + lh / 2;
  const xFor = w => t.align === 'left' ? bx : t.align === 'right' ? bx + bw - w : bx + (bw - w) / 2;
  const bgColor = t.bg || (effectOf(t) === 'background' ? (t.fx_color && t.fx_color !== '#000000' ? t.fx_color : '#000000b3') : '');
  if (bgColor) {
    ctx.fillStyle = hexToRgba(bgColor);
    lines.forEach((ln, i) => {
      if (!ln) return;
      const w = ctx.measureText(ln).width, padX = fs * 0.32, padY = fs * 0.12;
      roundRect(ctx, xFor(w) - padX, y + i * lh - lh / 2 - padY / 2, w + padX * 2, lh + padY, fs * 0.16);
      ctx.fill();
    });
  }
  const fx = effectOf(t), col = hexToRgba(t.color), fxc = hexToRgba(t.fx_color || '#000000');
  const base = ctx.globalAlpha;
  const each = fn => lines.forEach((ln, i) => { if (ln) fn(ln, xFor(ctx.measureText(ln).width), y + i * lh); });
  const fill = (style, dx = 0, dy = 0) => { ctx.fillStyle = style; each((ln, x, yy) => ctx.fillText(ln, x + dx, yy + dy)); };
  const stroke = (style, width) => { ctx.lineJoin = 'round'; ctx.lineWidth = width; ctx.strokeStyle = style; each((ln, x, yy) => ctx.strokeText(ln, x, yy)); };
  const noShadow = () => { ctx.shadowColor = 'transparent'; ctx.shadowBlur = 0; ctx.shadowOffsetX = 0; ctx.shadowOffsetY = 0; };
  switch (fx) {
    case 'shadow':
      ctx.shadowColor = withAlpha(fxc, 0.6); ctx.shadowBlur = fs * 0.1; ctx.shadowOffsetX = fs * 0.05; ctx.shadowOffsetY = fs * 0.06;
      fill(col); break;
    case 'lift':
      ctx.shadowColor = 'rgba(0,0,0,0.42)'; ctx.shadowBlur = fs * 0.4; ctx.shadowOffsetY = fs * 0.14;
      fill(col); break;
    case 'hollow':
      stroke(col, Math.max(1.5, fs * 0.045)); break;
    case 'splice':
      fill(fxc, fs * 0.07, fs * 0.07); stroke(col, Math.max(1.5, fs * 0.045)); break;
    case 'outline':
      stroke(fxc, Math.max(2, fs * 0.11)); fill(col); break;
    case 'echo':
      for (let k = 3; k >= 1; k--) { ctx.globalAlpha = base * (0.16 + 0.12 * (3 - k)); fill(fxc === 'rgba(0,0,0,1.000)' ? col : fxc, k * fs * 0.055, k * fs * 0.055); }
      ctx.globalAlpha = base; fill(col); break;
    case 'glitch':
      ctx.globalAlpha = base * 0.9; fill('rgb(0,229,255)', -fs * 0.045, 0); fill('rgb(255,0,170)', fs * 0.045, 0);
      ctx.globalAlpha = base; fill(col); break;
    case 'neon':
      ctx.shadowColor = col; ctx.shadowBlur = fs * 0.6; fill(col);
      ctx.shadowBlur = fs * 0.25; fill(col);
      noShadow(); fill('rgba(255,255,255,0.85)'); break;
    default:
      fill(col);
  }
  if (Number.isInteger(t.hl_word) && t.hl_word >= 0) {
    // Dynamic captions: repaint the spoken word in the highlight colour, exactly over itself.
    noShadow(); ctx.globalAlpha = base;
    let k = 0;
    lines.forEach((ln, i) => {
      const words = ln.split(' '), lw = ctx.measureText(ln).width, x0 = xFor(lw), yy = y + i * lh;
      words.forEach((w, j) => {
        if (k === t.hl_word && w) {
          const pre = words.slice(0, j).join(' ') + (j ? ' ' : '');
          ctx.fillStyle = hexToRgba(t.hl_color || '#ffd400');
          ctx.fillText(w, x0 + ctx.measureText(pre).width, yy);
        }
        if (w) k++;
      });
    });
  }
  ctx.restore();
}
function drawMedia(ctx, src, c, W, H, t) {
  const sw = src.videoWidth || src.naturalWidth, sh = src.videoHeight || src.naturalHeight;
  if (!sw || !sh) return;
  const bx = c.x * W, by = c.y * H, bw = c.w * W, bh = c.h * H;
  ctx.save();
  ctx.beginPath(); ctx.rect(bx, by, bw, bh); ctx.clip();
  const zoom = c.kind === 'image' && c.zoom > 0 ? 1 + c.zoom * clamp((t - c.start) / c.duration, 0, 1) : 1;
  const cover = c.fit === 'cover' || zoom > 1;
  const scale = (cover ? Math.max(bw / sw, bh / sh) : Math.min(bw / sw, bh / sh)) * zoom;
  const dw = sw * scale, dh = sh * scale;
  ctx.drawImage(src, bx + (bw - dw) / 2, by + (bh - dh) / 2, dw, dh);
  ctx.restore();
}
const canvas = $('#veCanvas');
function draw() {
  if (!P()) return;
  const ctx = canvas.getContext('2d'), W = canvas.width, H = canvas.height, t = st.time;
  ctx.globalAlpha = 1; ctx.fillStyle = P().background; ctx.fillRect(0, 0, W, H);
  const plan = motionPlan(P());
  const visual = P().clips.filter(isVisual).filter(c => t >= c.start && t < c.start + c.duration + (plan[c.id]?.ext || 0))
    .sort((a, b) => DRAW_ORDER.indexOf(a.lane) - DRAW_ORDER.indexOf(b.lane) || a.start - b.start);
  for (const c of visual) {
    const m = motionAt(c, plan[c.id], t);
    ctx.save();
    ctx.translate(m.dx * W, m.dy * H);
    if (m.sc !== 1 || m.rot) {
      const cx = (c.x + c.w / 2) * W, cy = (c.y + c.h / 2) * H;
      ctx.translate(cx, cy); ctx.scale(m.sc, m.sc); ctx.rotate(m.rot * Math.PI / 180); ctx.translate(-cx, -cy);
    }
    applyMask(ctx, c, plan[c.id], t, W, H);
    {
      const bx = c.kind === 'text' ? 0 : c.x * W, by = c.kind === 'text' ? 0 : c.y * H;
      const cx = (c.x + c.w / 2) * W, cy = (c.y + c.h / 2) * H;
      if (c.rotate) { ctx.translate(cx, cy); ctx.rotate(c.rotate * Math.PI / 180); ctx.translate(-cx, -cy); }
      if (c.flip_h || c.flip_v) { ctx.translate(cx, cy); ctx.scale(c.flip_h ? -1 : 1, c.flip_v ? -1 : 1); ctx.translate(-cx, -cy); }
      if (c.radius > 0 && c.kind !== 'text') {
        const r = c.radius * Math.min(c.w * W, c.h * H);
        roundRect(ctx, c.x * W, c.y * H, c.w * W, c.h * H, r); ctx.clip();
      }
      void bx; void by;
    }
    ctx.globalAlpha = c.opacity * (t < c.start + c.duration ? fadeFactor(c, t) : (c.fade_out > 0 ? 0 : 1)) * m.a;
    if (c.kind === 'color') {
      ctx.fillStyle = hexToRgba(c.color); ctx.fillRect(c.x * W, c.y * H, c.w * W, c.h * H);
    } else if (c.kind === 'text') {
      const mp = plan[c.id];
      const chars = mp.in && mp.in[0] === 'typewriter' ? Math.floor(clamp((t - c.start) / mp.in[1], 0, 1) * textLength(c)) : null;
      drawText(ctx, c, W, H, chars);
    } else {
      const el = elementFor(c);
      if (el && (c.kind === 'image' ? el.complete : el.readyState >= 2)) {
        ctx.filter = canvasFilter(c, W, H); drawMedia(ctx, el, c, W, H, t); ctx.filter = 'none';
      }
    }
    ctx.restore();
  }
  ctx.globalAlpha = 1;
  placeSelection();
  $('#veClock').textContent = `${fmt(t)} / ${fmt(duration())}`;
  $('#vePlayhead').style.left = `${t * st.pps}px`;
}
function layoutStage() {
  if (!P()) return;
  const [W, H] = FORMATS[P().format] || [P().width, P().height];
  const wrap = $('#veStageWrap').getBoundingClientRect();
  const k = Math.min((wrap.width - 24) / W, (wrap.height - 24) / H);
  const cssW = Math.max(120, Math.floor(W * k)), cssH = Math.max(80, Math.floor(H * k));
  const stage = $('#veStage'); stage.style.width = cssW + 'px'; stage.style.height = cssH + 'px';
  const internal = Math.min(1, 1280 / Math.max(W, H));
  canvas.width = Math.round(W * internal); canvas.height = Math.round(H * internal);
  draw();
}
new ResizeObserver(() => { if (st.shown) layoutStage(); }).observe($('#veStageWrap'));

/* ---------------------------------------------------------------- playback */
function tick() {
  if (!st.playing) return;
  st.time = st.time0 + (performance.now() - st.clock0) / 1000;
  const end = duration();
  if (st.time >= end) { st.time = end; stop(); draw(); return; }
  syncMedia(st.time, true);
  draw();
  followPlayhead();
  st.raf = requestAnimationFrame(tick);
}
function play() {
  if (!P() || st.playing) return;
  if (st.time >= duration() - 0.05) st.time = 0;
  st.playing = true; st.time0 = st.time; st.clock0 = performance.now();
  $('#vePlay').textContent = '❚❚'; $('#vePlay').setAttribute('aria-label', 'Pause');
  tick();
}
function stop() {
  st.playing = false; cancelAnimationFrame(st.raf);
  Object.values(st.els).forEach(el => el.pause());
  $('#vePlay').textContent = '▶'; $('#vePlay').setAttribute('aria-label', 'Play');
}
function seek(t) { st.time = clamp(t, 0, Math.max(duration(), 0)); if (st.playing) { st.time0 = st.time; st.clock0 = performance.now(); } syncMedia(st.time, st.playing); draw(); }
function followPlayhead() {
  const sc = $('#veScroll'), x = st.time * st.pps;
  if (x > sc.scrollLeft + sc.clientWidth - 40 || x < sc.scrollLeft) sc.scrollLeft = x - 60;
}
$('#vePlay').onclick = () => (st.playing ? stop() : play());
$('#veStart').onclick = () => seek(0);

/* ---------------------------------------------------------------- timeline */
function renderTimeline() {
  const total = Math.max(duration() + 15, ($('#veScroll').clientWidth || 800) / st.pps);
  const width = total * st.pps;
  $('#veHeads').innerHTML = '<div class="ve-head ruler-head"></div>' + LANES.map(l => `<div class="ve-head ${l.id[0] === 'A' ? 'audio' : ''}">${l.label}</div>`).join('');
  // Ruler
  const steps = [0.5, 1, 2, 5, 10, 15, 30, 60, 120];
  const major = steps.find(s => s * st.pps >= 70) || 300;
  let ticks = '';
  for (let s = 0; s <= total; s += major) ticks += `<span style="left:${s * st.pps}px">${K.fmtShort(s)}</span>`;
  $('#veRuler').innerHTML = ticks; $('#veRuler').style.width = width + 'px';
  $('#veRuler').style.setProperty('--major', `${major * st.pps}px`);
  $('#veLanes').style.width = width + 'px';
  $('#veLanes').innerHTML = LANES.map(l => `<div class="ve-lane ${l.id[0] === 'A' ? 'audio' : ''}" data-lane="${l.id}">${
    P().clips.filter(c => c.lane === l.id).map(clipHtml).join('')}${l.id[0] === 'A' ? '' : junctions(l.id)}</div>`).join('');
  $$('#veLanes .ve-tr').forEach(b => b.addEventListener('pointerdown', e => {
    e.stopPropagation();
    const c = clipById(b.dataset.tr); if (!c) return;
    if (!c.transition || c.transition.type === 'none') commit(() => { c.transition = {type: 'crossfade', dur: 0.6}; });
    select_(c.id);
    $('#veProps [data-trtype]')?.focus();
  }));
  $$('#veLanes .ve-clip').forEach(el => { el.addEventListener('pointerdown', clipPointerDown); el.addEventListener('contextmenu', clipMenu); });
  $$('#veLanes .ve-lane').forEach(el => {
    el.addEventListener('dragover', e => { if (e.dataTransfer.types.includes('text/x-ve')) { e.preventDefault(); el.classList.add('over'); } });
    el.addEventListener('dragleave', () => el.classList.remove('over'));
    el.addEventListener('drop', e => onLaneDrop(e, el));
    el.addEventListener('pointerdown', e => { if (e.target === el) { select_(null); seek(xToTime(e.clientX)); } });
  });
  draw();
}
function junctions(lane) {
  return P().clips.filter(c => c.lane === lane && c.kind !== 'audio' && prevAdjacent(P(), c)).map(c => {
    const on = c.transition && c.transition.type !== 'none';
    return `<button type="button" class="ve-tr${on ? ' on' : ''}" data-tr="${c.id}" style="left:${c.start * st.pps}px"
      title="${on ? 'Transition: ' + TRANSITION_LABELS[c.transition.type] : 'Add a transition'}">${on ? '⧓' : '+'}</button>`;
  }).join('');
}
function peaksKey(c) { return c.media?.startsWith('take:') ? c.media : c.media; }
function waveSvg(c) {
  const key = peaksKey(c), pk = st.peaks[key];
  if (pk === undefined) {
    st.peaks[key] = null;
    const url = key.startsWith('take:') ? `/api/recordings/${key.slice(5)}/peaks` : `/api/media/${key}/peaks`;
    api(url).then(d => { st.peaks[key] = d; renderTimeline(); }).catch(() => { st.peaks[key] = {peaks: [], per_second: 40}; });
    return '';
  }
  if (!pk || !pk.peaks.length) return '';
  const w = Math.max(6, c.duration * st.pps), n = Math.min(600, Math.max(8, Math.floor(w / 2))), src = pk.peaks.length / pk.per_second;
  let d = '';
  for (let i = 0; i <= n; i++) {
    let x = c.in + (i / n) * c.duration * (c.speed || 1);
    if (x >= src) { if (!c.loop) break; x %= src; }
    const v = pk.peaks[Math.min(pk.peaks.length - 1, Math.floor(x * pk.per_second))] / 100;
    d += `M${(i / n * 100).toFixed(2)} ${(50 - v * 46).toFixed(1)}V${(50 + v * 46).toFixed(1)}`;
  }
  return `<svg class="wave" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><path d="${d}"/></svg>`;
}
function clipHtml(c) {
  const left = c.start * st.pps, w = Math.max(6, c.duration * st.pps);
  let inner = '', bg = '';
  if (c.kind === 'video' || c.kind === 'image') bg = `background-image:url(/api/media/${c.media}/thumb)`;
  if (c.kind === 'color') bg = `background:${hexToRgba(c.color)}`;
  if (c.media?.startsWith('take:')) {
    const take = st.takes[c.media.slice(5)];
    if (take?.segments?.length) {
      const span = take.seconds || c.duration;
      inner = '<span class="segs">' + take.segments.map(s => {
        const a = (s.start - c.in) / c.duration * 100, b = (s.end - s.start) / c.duration * 100;
        return a + b < 0 || a > 100 ? '' : `<i style="left:${a}%;width:${b}%;background:${K.COLORS[s.style] || '#888'}"></i>`;
      }).join('') + '</span>';
      void span;
    }
  }
  if (c.kind === 'audio' || (c.kind === 'video' && !c.muted && st.media[c.media]?.has_audio)) inner += waveSvg(c);
  if (c.kind === 'video' && Number.isFinite(sourceDuration(c)) && !c.loop) {
    const playable = (sourceDuration(c) - c.in) / (c.speed || 1);
    if (playable < c.duration - 0.05) inner += `<span class="frozen" style="left:${Math.max(0, playable) * st.pps}px" title="Last frame held"></span>`;
  }
  if ((c.speed || 1) !== 1) inner += `<em class="spd">${(+c.speed).toFixed(2).replace(/0$/, '')}×</em>`;
  const fades = (c.fade_in ? `<b class="fi" style="width:${c.fade_in * st.pps}px"></b>` : '') + (c.fade_out ? `<b class="fo" style="width:${c.fade_out * st.pps}px"></b>` : '');
  return `<div class="ve-clip ${c.kind}${c.media?.startsWith('take:') ? ' take' : ''}${st.sel === c.id ? ' sel' : ''}" data-clip="${c.id}" style="left:${left}px;width:${w}px;${bg}" title="${esc(clipName(c))} · ${c.duration.toFixed(1)}s">
    ${inner}${fades}<span class="lbl">${c.muted ? '🔇 ' : ''}${esc(clipName(c))}</span><s class="eh l"></s><s class="eh r"></s></div>`;
}
function xToTime(clientX) {
  const r = $('#veLanes').getBoundingClientRect();
  return Math.max(0, (clientX - r.left) / st.pps);
}
function snapTime(t, ignoreId, px = 8) {
  if (!st.snap) return t;
  const cands = [0, st.time];
  P().clips.forEach(c => { if (c.id !== ignoreId) cands.push(c.start, c.start + c.duration); });
  let best = t, bd = px / st.pps;
  for (const x of cands) { const d = Math.abs(x - t); if (d < bd) { bd = d; best = x; } }
  return best;
}
function onLaneDrop(e, laneEl) {
  e.preventDefault(); laneEl.classList.remove('over');
  let data; try { data = JSON.parse(e.dataTransfer.getData('text/x-ve')); } catch { return; }
  const t = snapTime(xToTime(e.clientX), null), lane = laneEl.dataset.lane;
  if (data.take) {
    const rec = st.takes[data.take]; if (!rec) return;
    if (lane[0] !== 'A') { K.toast('Voice takes go on an audio track (Voice, Music or Sound).'); return; }
    addClip({kind: 'audio', media: 'take:' + rec.id, lane, start: t, duration: rec.seconds, name: rec.name});
  } else if (data.media) {
    const m = st.media[data.media]; if (!m) return;
    const wantAudio = m.kind === 'audio';
    if (wantAudio !== (lane[0] === 'A')) {
      if (m.kind === 'video' && lane[0] === 'A' && m.has_audio) { addClip({kind: 'audio', media: m.id, lane, start: t, duration: m.duration, name: m.name + ' (sound)'}); return; }
      K.toast(wantAudio ? 'Music and sound go on an audio track.' : 'Pictures and video go on a visual track.'); return;
    }
    addMedia(m, t, lane);
  }
}
$('#veRuler').addEventListener('pointerdown', e => {
  const move = ev => seek(xToTime(ev.clientX));
  move(e);
  const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
  window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
});

/* Right-click menu on timeline clips. */
function clipMenu(e) {
  e.preventDefault();
  const c = clipById(e.currentTarget.dataset.clip); if (!c) return;
  select_(c.id);
  const items = [['Split at playhead', split], ['Trim start to playhead', () => trimToPlayhead('start')], ['Trim end to playhead', () => trimToPlayhead('end')],
    ['Duplicate', duplicate], ['Copy', copy], ...(st.clipboard ? [['Paste at playhead', paste]] : []),
    ...(c.kind === 'video' ? [['Freeze frame at playhead', freezeFrame]] : []),
    ...(c.kind === 'video' || c.kind === 'audio' ? [[c.muted ? 'Unmute' : 'Mute', () => commit(() => { c.muted = !c.muted; })]] : []),
    ...(c.media?.startsWith('take:') ? [['Add captions', () => addCaptions(c)]] : []),
    ['Delete', remove]];
  const m = $('#veMenu');
  m.innerHTML = items.map(([label], i) => `<button type="button" data-mi="${i}" class="${label === 'Delete' ? 'danger' : ''}">${label}</button>`).join('');
  m.hidden = false;
  const r = m.getBoundingClientRect();
  m.style.left = Math.min(e.clientX, innerWidth - r.width - 8) + 'px'; m.style.top = Math.min(e.clientY, innerHeight - r.height - 8) + 'px';
  m.querySelectorAll('[data-mi]').forEach(b => b.onclick = () => { m.hidden = true; items[+b.dataset.mi][1](); });
}
document.addEventListener('pointerdown', e => { if (!e.target.closest('#veMenu')) $('#veMenu').hidden = true; });
function clipPointerDown(e) {
  if (e.button !== 0) return;
  e.stopPropagation();
  const el = e.currentTarget, c = clipById(el.dataset.clip);
  if (!c) return;
  select_(c.id);
  const media = c.kind === 'video' || c.kind === 'audio';
  const mode = e.target.classList.contains('eh') ? (e.target.classList.contains('l') ? 'trimL' : 'trimR')
    : (e.altKey && media ? 'slip' : 'move');
  const x0 = e.clientX, orig = {...c}, before = snapshot();
  const sp = c.speed || 1, src = media ? sourceDuration(c) : Infinity;
  // Pictures, text and video can be stretched freely (video holds its last frame); audio stops at its end unless looped.
  const maxDur = c.kind === 'audio' && !c.loop ? (src - orig.in) / sp : Infinity;
  let moved = false;
  const onMove = ev => {
    const dt = (ev.clientX - x0) / st.pps;
    if (!moved && Math.abs(ev.clientX - x0) < 3 && mode === 'move' && Math.abs(ev.clientY - e.clientY) < 6) return;
    moved = true;
    if (mode === 'move') {
      let ns = snapTime(Math.max(0, orig.start + dt), c.id);
      const nsEnd = snapTime(ns + orig.duration, c.id);
      if (nsEnd !== ns + orig.duration && Math.abs(nsEnd - (ns + orig.duration)) < 8 / st.pps) ns = nsEnd - orig.duration;
      c.start = Math.max(0, ns);
      const under = document.elementFromPoint(ev.clientX, ev.clientY)?.closest('.ve-lane');
      if (under && laneOf(under.dataset.lane).kinds.includes(c.kind)) c.lane = under.dataset.lane;
    } else if (mode === 'trimR') {
      const end = snapTime(orig.start + orig.duration + dt, c.id);
      c.duration = clamp(end - orig.start, 0.1, maxDur);
    } else if (mode === 'slip') {
      // Keep the clip where it is; slide which part of the source plays inside it.
      const room = c.loop ? Infinity : Math.max(0, src - orig.duration * sp);
      c.in = clamp(orig.in - dt * sp, 0, c.kind === 'video' ? Math.max(0, src - 0.1) : room);
    } else {
      let ns = snapTime(orig.start + dt, c.id);
      ns = clamp(ns, Math.max(0, orig.start - (media ? orig.in / sp : 1e9)), orig.start + orig.duration - 0.1);
      const shift = ns - orig.start;
      c.start = ns; c.duration = orig.duration - shift;
      if (media) c.in = Math.max(0, orig.in + shift * sp);
    }
    renderTimeline(); renderProps(); syncMedia(st.time, false);
  };
  const onUp = () => {
    window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp);
    if (moved) { st.undo.push(before); st.redo = []; afterChange(); }
  };
  window.addEventListener('pointermove', onMove); window.addEventListener('pointerup', onUp);
}
function sourceDuration(c) {
  if (c.media?.startsWith('take:')) return st.takes[c.media.slice(5)]?.seconds || c.in + c.duration;
  return st.media[c.media]?.duration || Infinity;
}
$('#veZoom').oninput = e => { const center = st.time; st.pps = +e.target.value; renderTimeline(); $('#veScroll').scrollLeft = center * st.pps - $('#veScroll').clientWidth / 2; };
$('#veSnap').onchange = e => { st.snap = e.target.checked; };
$('#veScroll').addEventListener('scroll', () => { $('#veHeads').scrollTop = $('#veScroll').scrollTop; });

/* ---------------------------------------------------------------- stage editing */
function stageRect() { return canvas.getBoundingClientRect(); }
function placeSelection() {
  const c = clipById(st.sel), sel = $('#veSel');
  if (!c || !isVisual(c) || !(st.time >= c.start && st.time < c.start + c.duration)) { sel.hidden = true; return; }
  const r = $('#veStage').getBoundingClientRect();
  sel.hidden = false;
  Object.assign(sel.style, {left: `${c.x * r.width}px`, top: `${c.y * r.height}px`, width: `${c.w * r.width}px`, height: `${c.h * r.height}px`});
}
canvas.addEventListener('pointerdown', e => {
  const r = stageRect(), px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
  const hits = P().clips.filter(isVisual).filter(c => st.time >= c.start && st.time < c.start + c.duration
    && px >= c.x && px <= c.x + c.w && py >= c.y && py <= c.y + c.h)
    .sort((a, b) => DRAW_ORDER.indexOf(b.lane) - DRAW_ORDER.indexOf(a.lane));
  const current = hits.findIndex(c => c.id === st.sel);
  // Clicking again cycles to the layer underneath.
  const pick = current >= 0 && e.altKey ? hits[(current + 1) % hits.length] : (current >= 0 ? hits[current] : hits[0]);
  select_(pick ? pick.id : null);
  if (pick) startStageDrag(e, pick, 'move');
});
$('#veSel').addEventListener('pointerdown', e => {
  const c = clipById(st.sel); if (!c) return;
  startStageDrag(e, c, e.target.dataset.h || 'move');
});
function startStageDrag(e, c, mode) {
  e.preventDefault(); e.stopPropagation();
  const r = stageRect(), x0 = e.clientX, y0 = e.clientY, o = {...c}, before = snapshot();
  const aspect = o.w / o.h, keep = c.kind !== 'text' && c.kind !== 'color';
  let moved = false;
  const onMove = ev => {
    const dx = (ev.clientX - x0) / r.width, dy = (ev.clientY - y0) / r.height;
    if (!moved && Math.abs(ev.clientX - x0) + Math.abs(ev.clientY - y0) < 3) return;
    moved = true;
    let gv = false, gh = false;
    if (mode === 'move') {
      let nx = o.x + dx, ny = o.y + dy;
      if (st.snap) {
        const sx = [[0, nx], [0.5 - o.w / 2, nx], [1 - o.w, nx]].find(([v]) => Math.abs(v - nx) < 0.012);
        const sy = [[0, ny], [0.5 - o.h / 2, ny], [1 - o.h, ny]].find(([v]) => Math.abs(v - ny) < 0.012);
        if (sx) { nx = sx[0]; gv = Math.abs(sx[0] - (0.5 - o.w / 2)) < 1e-6; }
        if (sy) { ny = sy[0]; gh = Math.abs(sy[0] - (0.5 - o.h / 2)) < 1e-6; }
      }
      c.x = nx; c.y = ny;
    } else {
      let x1 = o.x, y1 = o.y, x2 = o.x + o.w, y2 = o.y + o.h;
      if (mode.includes('w')) x1 += dx; else x2 += dx;
      if (mode.includes('n')) y1 += dy; else y2 += dy;
      let w = Math.max(0.03, x2 - x1), h = Math.max(0.03, y2 - y1);
      if (keep && !ev.shiftKey) {
        if (w / h > aspect) w = h * aspect; else h = w / aspect;
      }
      c.w = w; c.h = h;
      c.x = mode.includes('w') ? o.x + o.w - w : o.x;
      c.y = mode.includes('n') ? o.y + o.h - h : o.y;
    }
    $('#veGuideV').hidden = !gv; $('#veGuideH').hidden = !gh;
    draw(); renderPropsValues();
  };
  const onUp = () => {
    window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp);
    $('#veGuideV').hidden = true; $('#veGuideH').hidden = true;
    if (moved) { st.undo.push(before); st.redo = []; afterChange(); }
  };
  window.addEventListener('pointermove', onMove); window.addEventListener('pointerup', onUp);
}

/* ---------------------------------------------------------------- selection & props */
function select_(id) {
  st.sel = id;
  $$('#veLanes .ve-clip').forEach(el => el.classList.toggle('sel', el.dataset.clip === id));
  renderProps(); draw();
}
const num = (v, d = 2) => (+v).toFixed(d).replace(/\.?0+$/, '') || '0';
function field(label, html, cls = '') { return `<label class="mini ${cls}">${label}${html}</label>`; }
function renderProps() {
  const box = $('#veProps'), c = clipById(st.sel);
  if (!P()) { box.innerHTML = ''; return; }
  if (!c) {
    box.innerHTML = `<h2>Video</h2>
      <p class="note">Nothing selected. Click something on the timeline or the picture to edit it.</p>
      <div class="ve-facts mono"><span>${P().width}×${P().height}</span><span>${P().fps} fps</span><span>${fmt(duration())}</span><span>${P().clips.length} clips</span></div>
      ${field('Frame rate', `<select class="input" data-proj="fps">${[24, 25, 30, 60].map(f => `<option ${P().fps === f ? 'selected' : ''}>${f}</option>`).join('')}</select>`)}
      <h3>How it fits together</h3>
      <ul class="tips"><li><b>Uploads</b>: pictures, video and music you add.</li><li><b>Voices</b>: takes from the Script tab. Add them with captions to get on-screen text timed to the voice.</li>
      <li>Higher tracks draw on top. <b>Text</b> is the top track, <b>Main</b> the bottom.</li><li>Drag clip edges to trim. Drag on the picture to move, and use the corners to resize (hold Shift to stretch).</li></ul>`;
    box.querySelector('[data-proj=fps]').onchange = e => commit(p => { p.fps = +e.target.value; });
    return;
  }
  const visual = isVisual(c), media = c.kind === 'video' || c.kind === 'audio';
  let html = `<h2>${esc({video: 'Video clip', image: 'Picture', text: 'Text', color: 'Shape', audio: c.media?.startsWith('take:') ? 'Voice take' : 'Audio'}[c.kind])}</h2>
    <div class="ve-clipname">${esc(clipName(c))}</div>
    <div class="row two">${field('Start (s)', `<input class="input mono" type="number" step="0.1" min="0" data-k="start">`)}${field('Length (s)', `<input class="input mono" type="number" step="0.1" min="0.1" data-k="duration">`)}</div>`;
  if (c.kind === 'text') {
    const t = c.text;
    html += `<label class="mini">Text<textarea class="input ve-text" rows="3" data-t="content">${esc(t.content)}</textarea></label>
      <div class="row two">${field('Font', `<select class="input" data-t="font">${Object.keys(FONTS).map(f => `<option value="${f}" ${t.font === f ? 'selected' : ''}>${f[0].toUpperCase() + f.slice(1)}</option>`).join('')}</select>`)}
      ${field('Weight', `<select class="input" data-t="weight">${[[400, 'Regular'], [500, 'Medium'], [700, 'Bold'], [800, 'Extra bold'], [900, 'Black']].map(([v, n]) => `<option value="${v}" ${t.weight === v ? 'selected' : ''}>${n}</option>`).join('')}</select>`)}</div>
      <div class="slider"><label>Size</label><output>${Math.round(t.size * 1000) / 10}</output><input type="range" min="0.02" max="0.3" step="0.005" value="${t.size}" data-t="size"></div>
      <div class="row two">${field('Colour', `<input type="color" class="ve-color" value="${t.color.slice(0, 7)}" data-t="color">`)}
</div>
      <div class="ve-toggles">
        <span class="seg-toggle">${['left', 'center', 'right'].map(a => `<button type="button" data-align="${a}" aria-pressed="${t.align === a}">${a[0].toUpperCase() + a.slice(1)}</button>`).join('')}</span>
      </div>
      <div class="checks small">
        <label><input type="checkbox" data-tf="italic" ${t.italic ? 'checked' : ''}> Italic</label>
        <label><input type="checkbox" data-tf="upper" ${t.upper ? 'checked' : ''}> Capitals</label>
      </div>
      <h3>Effects</h3>
      <div class="ve-effects">${EFFECTS.map(e => `<button type="button" class="ve-fx" data-fx="${e}" aria-pressed="${effectOf(t) === e}" title="${e[0].toUpperCase() + e.slice(1)}"><canvas width="160" height="104" data-fxprev="${e}"></canvas><span>${e[0].toUpperCase() + e.slice(1)}</span></button>`).join('')}</div>
      ${['shadow', 'splice', 'outline', 'echo', 'background'].includes(effectOf(t)) ? field('Effect colour', `<input type="color" class="ve-color" value="${(t.fx_color || '#000000').slice(0, 7)}" data-fxcolor>`) : ''}`;
  }
  if (c.kind === 'color') {
    html += `<div class="row two">${field('Colour', `<input type="color" class="ve-color" value="${c.color.slice(0, 7)}" data-color>`)}
      <div class="slider"><label>See-through</label><output>${Math.round((1 - parseInt(c.color.slice(7, 9) || 'ff', 16) / 255) * 100)}%</output><input type="range" min="0" max="1" step="0.01" value="${1 - parseInt(c.color.slice(7, 9) || 'ff', 16) / 255}" data-alpha></div></div>`;
  }
  if (visual) {
    html += `<h3>Position</h3><div class="row four">${['x', 'y', 'w', 'h'].map(k => field({x: 'Left', y: 'Top', w: 'Width', h: 'Height'}[k] + ' %', `<input class="input mono" type="number" step="1" data-pos="${k}">`)).join('')}</div>
      <div class="ve-align">${[['l', '⇤', 'Align left'], ['c', '↔', 'Centre horizontally'], ['r', '⇥', 'Align right'], ['t', '⤒', 'Align top'], ['m', '↕', 'Centre vertically'], ['b', '⤓', 'Align bottom']]
        .map(([k, i, label]) => `<button type="button" class="icon-btn" data-align2="${k}" title="${label}" aria-label="${label}">${i}</button>`).join('')}</div>
      <div class="row ve-btnrow"><button type="button" class="btn ghost" data-act="fill">Fill frame</button><button type="button" class="btn ghost" data-act="center">Center</button>
      ${c.kind === 'video' || c.kind === 'image' ? `<span class="seg-toggle"><button type="button" data-fit="cover" aria-pressed="${c.fit === 'cover'}" title="Fill the box, cropping edges">Crop</button><button type="button" data-fit="contain" aria-pressed="${c.fit === 'contain'}" title="Show the whole picture">Fit</button></span>` : ''}</div>
      <div class="slider"><label>Opacity</label><output>${Math.round(c.opacity * 100)}%</output><input type="range" min="0" max="1" step="0.01" value="${c.opacity}" data-k="opacity"></div>`;
    if (c.kind === 'image') html += `<div class="slider"><label>Slow zoom (Ken Burns)</label><output>${Math.round(c.zoom * 100)}%</output><input type="range" min="0" max="0.4" step="0.01" value="${c.zoom}" data-k="zoom"></div>`;
    html += `<h3>Rotate &amp; shape</h3>
      <div class="slider"><label>Rotation</label><output>${Math.round(c.rotate || 0)}°</output><input type="range" min="-180" max="180" step="1" value="${c.rotate || 0}" data-k="rotate"></div>
      <div class="ve-btnrow"><button type="button" class="btn ghost" data-act="rot90">Rotate 90°</button>
        ${c.kind !== 'text' ? `<button type="button" class="btn ghost" data-act="fliph" aria-pressed="${!!c.flip_h}">Flip ↔</button><button type="button" class="btn ghost" data-act="flipv" aria-pressed="${!!c.flip_v}">Flip ↕</button>` : ''}</div>
      ${c.kind !== 'text' ? `<div class="slider"><label>Corner rounding</label><output>${Math.round((c.radius || 0) * 200)}%</output><input type="range" min="0" max="0.5" step="0.01" value="${c.radius || 0}" data-k="radius"></div>` : ''}`;
    if (c.kind === 'image' || c.kind === 'video') {
      const a = {...ADJ_ZERO, ...(c.adjust || {})};
      const current = Object.entries(FILTER_PRESETS).find(([, p]) => Object.keys(ADJ_ZERO).every(k => k === 'blur' || (p[k] ?? ADJ_ZERO[k]) === a[k]))?.[0];
      html += `<h3>Filters</h3><div class="ve-filters">${Object.keys(FILTER_PRESETS).map(n => `<button type="button" class="ve-filter" data-filter="${n}" aria-pressed="${current === n}">
          <canvas width="72" height="48" data-fprev="${n}"></canvas><span>${n[0].toUpperCase() + n.slice(1)}</span></button>`).join('')}</div>
        <h3>Adjust</h3>
        ${[['brightness', 'Brightness'], ['contrast', 'Contrast'], ['saturation', 'Saturation'], ['warmth', 'Warmth']].map(([k, label]) =>
          `<div class="slider"><label>${label}</label><output>${Math.round(a[k] * 100)}</output><input type="range" min="-1" max="1" step="0.01" value="${a[k]}" data-adj="${k}"></div>`).join('')}
        <div class="slider"><label>Blur</label><output>${Math.round(a.blur * 100)}</output><input type="range" min="0" max="1" step="0.01" value="${a.blur}" data-adj="blur"></div>
        <button type="button" class="link-btn ve-small" data-act="adjreset">Reset adjustments</button>`;
    }
    html += `<div class="row two"><label class="mini">Layer<select class="input" data-lane>${LANES.filter(l => l.kinds.includes(c.kind)).map(l => `<option value="${l.id}" ${c.lane === l.id ? 'selected' : ''}>${l.label}</option>`).join('')}</select></label></div>`;
  } else {
    html += `<div class="row two"><label class="mini">Track<select class="input" data-lane>${LANES.filter(l => l.kinds.includes('audio')).map(l => `<option value="${l.id}" ${c.lane === l.id ? 'selected' : ''}>${l.label}</option>`).join('')}</select></label></div>`;
  }
  if (visual) {
    const prev = prevAdjacent(P(), c), tr = c.transition || {type: 'none', dur: 0.6};
    const ai = c.anim_in || {type: 'none', dur: 0.6}, ao = c.anim_out || {type: 'none', dur: 0.6};
    const sel = (attr, labels, cur) => `<select class="input" ${attr}>${Object.entries(labels).map(([k, v]) => `<option value="${k}" ${cur === k ? 'selected' : ''}>${v}</option>`).join('')}</select>`;
    html += `<h3>Animation</h3>`;
    if (prev) html += `<div class="row two">${field('Transition from previous', sel('data-trtype', TRANSITION_LABELS, tr.type))}${field('Length (s)', `<input class="input mono" type="number" step="0.1" min="0.1" max="5" value="${num(tr.dur)}" data-trdur>`)}</div>
      ${tr.type !== 'none' ? '<button type="button" class="link-btn ve-small" data-act="trall">Use this transition for every scene change on this track</button>' : ''}`;
    const inLabels = Object.fromEntries(Object.entries(ANIM_LABELS).filter(([k]) => k !== 'typewriter' || c.kind === 'text'));
    const outLabels = Object.fromEntries(Object.entries(ANIM_LABELS).filter(([k]) => k !== 'typewriter'));
    html += `<div class="row two">${field('Entrance', sel('data-anim="anim_in"', inLabels, ai.type))}${field('Length (s)', `<input class="input mono" type="number" step="0.1" min="0.1" max="5" value="${num(ai.dur)}" data-animdur="anim_in">`)}</div>
      <div class="row two">${field('Exit', sel('data-anim="anim_out"', outLabels, ao.type))}${field('Length (s)', `<input class="input mono" type="number" step="0.1" min="0.1" max="5" value="${num(ao.dur)}" data-animdur="anim_out">`)}</div>
      ${field('While on screen', sel('data-loopfx', LOOP_FX_LABELS, c.loop_fx || 'none'))}
      <button type="button" class="btn ghost wide" data-act="preview-anim">Preview animation</button>`;
  }
  html += `<h3>Fades</h3><div class="row two">${field('Fade in (s)', `<input class="input mono" type="number" step="0.1" min="0" max="10" data-k="fade_in">`)}${field('Fade out (s)', `<input class="input mono" type="number" step="0.1" min="0" max="10" data-k="fade_out">`)}</div>`;
  if (media) {
    html += `<h3>Sound</h3><div class="slider"><label>Volume</label><output>${Math.round(c.volume * 100)}%</output><input type="range" min="0" max="2" step="0.01" value="${c.volume}" data-k="volume"></div>
      <div class="checks small"><label><input type="checkbox" data-muted ${c.muted ? 'checked' : ''}> Mute</label></div>
      ${field('Starts from (s into the file)', `<input class="input mono" type="number" step="0.1" min="0" data-k="in">`)}
      <p class="note">Alt + drag the clip on the timeline to choose which part plays.</p>`;
    html += `<h3>Speed</h3><div class="slider"><label>Playback speed</label><output>${(c.speed || 1).toFixed(2)}×</output>
      <input type="range" min="0.25" max="2" step="0.05" value="${c.speed || 1}" data-speed></div>
      <div class="ve-btnrow">${[0.5, 1, 1.5, 2].map(v => `<button type="button" class="btn ghost" data-speedset="${v}">${v}×</button>`).join('')}</div>
      <div class="checks small"><label><input type="checkbox" data-loop ${c.loop ? 'checked' : ''}> Loop to fill the clip</label></div>`;
    if (c.kind === 'video') html += `<button type="button" class="btn ghost wide" data-act="freeze">Freeze frame at playhead (2 s)</button>`;
  }
  if (c.media?.startsWith('take:')) html += `<h3>Captions</h3>${field('Style', `<select class="input" data-capstyle>${Object.entries(CAPTION_STYLES).map(([k, v]) => `<option value="${k}" ${captionStyle === k ? 'selected' : ''}>${v}</option>`).join('')}</select>`)}
    <button type="button" class="btn ghost wide" data-act="captions">Add captions as text</button>`;
  html += `<div class="row ve-btnrow end"><button type="button" class="btn ghost" data-act="trimstart" title="Q">Trim start to playhead</button><button type="button" class="btn ghost" data-act="trimend" title="W">Trim end to playhead</button></div>
    <div class="row ve-btnrow"><button type="button" class="btn ghost" data-act="split">Split</button><button type="button" class="btn ghost" data-act="dup">Duplicate</button><button type="button" class="btn ghost danger" data-act="del">Delete</button></div>`;
  box.innerHTML = html;
  renderPropsValues();
  bindProps(c);
}
function renderPropsValues() {
  const c = clipById(st.sel); if (!c) return;
  const box = $('#veProps');
  box.querySelectorAll('[data-k]').forEach(el => {
    if (el.type === 'number' && document.activeElement !== el) el.value = num(c[el.dataset.k]);
  });
  box.querySelectorAll('[data-pos]').forEach(el => { if (document.activeElement !== el) el.value = Math.round(c[el.dataset.pos] * 1000) / 10; });
}
function bindProps(c) {
  const box = $('#veProps');
  let pending = null;
  // Sliders edit live and commit once on release, so one drag = one undo step.
  const live = (el, apply) => {
    el.addEventListener('input', () => { if (!pending) pending = snapshot(); apply(); const o = el.parentElement.querySelector('output'); if (o) o.textContent = outFmt(el); renderTimeline(); });
    el.addEventListener('change', () => { if (pending) { st.undo.push(pending); st.redo = []; pending = null; afterChange(); } });
  };
  const outFmt = el => el.dataset.k === 'rotate' ? `${Math.round(el.value)}°` : el.dataset.k === 'radius' ? `${Math.round(el.value * 200)}%`
    : el.dataset.adj !== undefined ? `${Math.round(el.value * 100)}`
    : el.dataset.k === 'opacity' || el.dataset.k === 'volume' || el.dataset.k === 'zoom' ? `${Math.round(el.value * 100)}%`
    : el.dataset.alpha !== undefined ? `${Math.round(el.value * 100)}%` : el.dataset.t === 'size' ? `${Math.round(el.value * 1000) / 10}` : el.value;
  box.querySelectorAll('[data-k]').forEach(el => {
    const k = el.dataset.k;
    if (el.type === 'range') live(el, () => { c[k] = +el.value; });
    else el.addEventListener('change', () => commit(() => {
      let v = Math.max(0, +el.value || 0);
      if (k === 'duration') v = clamp(v, 0.1, sourceDuration(c) - c.in);
      if (k === 'in') v = clamp(v, 0, sourceDuration(c) - 0.1);
      c[k] = v;
    }));
  });
  box.querySelectorAll('[data-pos]').forEach(el => el.addEventListener('change', () => commit(() => { c[el.dataset.pos] = clamp((+el.value || 0) / 100, el.dataset.pos === 'w' || el.dataset.pos === 'h' ? 0.01 : -2, 4); })));
  box.querySelectorAll('[data-t]').forEach(el => {
    const k = el.dataset.t;
    if (k === 'size') live(el, () => { c.text.size = +el.value; });
    else if (k === 'content') {
      el.addEventListener('input', () => { if (!pending) pending = snapshot(); c.text.content = el.value; draw(); renderTimeline(); scheduleSave(); });
      el.addEventListener('change', () => { if (pending) { st.undo.push(pending); st.redo = []; pending = null; } });
    } else if (k === 'bgcolor') el.addEventListener('input', () => { c.text.bg = el.value + 'b3'; box.querySelector('[data-tbg]').checked = true; draw(); scheduleSave(); });
    else el.addEventListener(el.type === 'color' ? 'input' : 'change', () => { c.text[k] = k === 'weight' ? +el.value : el.value; draw(); renderTimeline(); scheduleSave(); });
  });
  box.querySelector('[data-tbg]')?.addEventListener('change', e => commit(() => { c.text.bg = e.target.checked ? box.querySelector('[data-t=bgcolor]').value + 'b3' : ''; }));
  box.querySelectorAll('[data-fxprev]').forEach(cv => {
    const ctx = cv.getContext('2d');
    ctx.fillStyle = '#f2efe9'; ctx.fillRect(0, 0, cv.width, cv.height);
    const fx = cv.dataset.fxprev;
    const dark = ['neon', 'glitch'].includes(fx);
    if (dark) { ctx.fillStyle = '#1d1b18'; ctx.fillRect(0, 0, cv.width, cv.height); }
    drawText(ctx, {x: 0, y: 0, w: 1, h: 1, text: {...c.text, content: 'Ag', upper: false, size: 0.82, align: 'center', bg: '',
      color: dark ? (fx === 'neon' ? c.text.color : '#ffffff') : (['hollow', 'splice'].includes(fx) ? '#1f1d1a' : '#1f1d1a'),
      effect: fx, fx_color: fx === 'splice' ? '#c4834f' : fx === 'outline' ? '#c9c2b5' : fx === 'background' ? '#c9c2b5' : '#000000'}}, cv.width, cv.height);
  });
  box.querySelectorAll('[data-fx]').forEach(el => el.onclick = () => commit(() => {
    c.text.effect = el.dataset.fx; c.text.shadow = false; c.text.outline = false;
    if (el.dataset.fx === 'background' && !c.text.bg) c.text.fx_color = c.text.fx_color && c.text.fx_color !== '#000000' ? c.text.fx_color : '#000000b3';
  }));
  box.querySelector('[data-fxcolor]')?.addEventListener('input', e => {
    c.text.fx_color = e.target.value + (effectOf(c.text) === 'background' ? 'b3' : ''); draw(); scheduleSave();
  });
  box.querySelectorAll('[data-tf]').forEach(el => el.addEventListener('change', () => commit(() => { c.text[el.dataset.tf] = el.checked; })));
  box.querySelectorAll('[data-align]').forEach(el => el.onclick = () => commit(() => { c.text.align = el.dataset.align; }));
  box.querySelector('[data-color]')?.addEventListener('input', e => { c.color = e.target.value + (c.color.slice(7, 9) || 'ff'); draw(); renderTimeline(); scheduleSave(); });
  const alpha = box.querySelector('[data-alpha]');
  if (alpha) live(alpha, () => { c.color = c.color.slice(0, 7) + Math.round((1 - alpha.value) * 255).toString(16).padStart(2, '0'); });
  box.querySelectorAll('[data-fit]').forEach(el => el.onclick = () => commit(() => { c.fit = el.dataset.fit; }));
  box.querySelector('[data-trtype]')?.addEventListener('change', e => commit(() => { c.transition = {type: e.target.value, dur: c.transition?.dur || 0.6}; }));
  box.querySelector('[data-trdur]')?.addEventListener('change', e => commit(() => { c.transition = {type: c.transition?.type || 'crossfade', dur: clamp(+e.target.value || 0.6, 0.1, 5)}; }));
  box.querySelector('[data-capstyle]')?.addEventListener('change', e => { captionStyle = e.target.value; store.set('ve-capstyle', captionStyle); });
  box.querySelectorAll('[data-align2]').forEach(b => b.onclick = () => commit(() => {
    const k = b.dataset.align2;
    if (k === 'l') c.x = 0; if (k === 'c') c.x = 0.5 - c.w / 2; if (k === 'r') c.x = 1 - c.w;
    if (k === 't') c.y = 0; if (k === 'm') c.y = 0.5 - c.h / 2; if (k === 'b') c.y = 1 - c.h;
  }));
  box.querySelector('[data-loopfx]')?.addEventListener('change', e => commit(() => { c.loop_fx = e.target.value; }));
  box.querySelectorAll('[data-anim]').forEach(el => el.addEventListener('change', () => commit(() => { const k = el.dataset.anim; c[k] = {type: el.value, dur: c[k]?.dur || 0.6}; })));
  box.querySelectorAll('[data-animdur]').forEach(el => el.addEventListener('change', () => commit(() => { const k = el.dataset.animdur; c[k] = {type: c[k]?.type || 'fade', dur: clamp(+el.value || 0.6, 0.1, 5)}; })));
  box.querySelector('[data-lane]')?.addEventListener('change', e => commit(() => { c.lane = e.target.value; }));
  box.querySelector('[data-muted]')?.addEventListener('change', e => commit(() => { c.muted = e.target.checked; }));
  box.querySelectorAll('[data-adj]').forEach(el => live(el, () => { c.adjust = {...ADJ_ZERO, ...(c.adjust || {}), [el.dataset.adj]: +el.value}; draw(); }));
  box.querySelectorAll('[data-filter]').forEach(b => b.onclick = () => commit(() => {
    c.adjust = {...ADJ_ZERO, blur: c.adjust?.blur || 0, ...FILTER_PRESETS[b.dataset.filter]};
  }));
  // Filter tiles show this clip's own picture with each preset.
  const src = c.kind === 'image' ? st.imgs[c.media] : st.els[c.id];
  box.querySelectorAll('[data-fprev]').forEach(cv => {
    const ctx = cv.getContext('2d'); ctx.fillStyle = '#555'; ctx.fillRect(0, 0, cv.width, cv.height);
    if (!src || !(src.naturalWidth || src.videoWidth)) return;
    const fake = {...c, id: c.id + '-p-' + cv.dataset.fprev, x: 0, y: 0, w: 1, h: 1, zoom: 0, fit: 'cover',
      adjust: {...ADJ_ZERO, ...FILTER_PRESETS[cv.dataset.fprev]}};
    ctx.filter = canvasFilter(fake, cv.width, cv.height); drawMedia(ctx, src, fake, cv.width, cv.height, c.start); ctx.filter = 'none';
  });
  const setSpeed = v => commit(() => {
    // Like Canva: faster playback makes the clip shorter (same part of the source plays).
    const old = c.speed || 1; c.speed = clamp(+v, 0.25, 2);
    c.duration = Math.max(0.1, Math.round(c.duration * old / c.speed * 1000) / 1000);
  });
  box.querySelector('[data-speed]')?.addEventListener('input', e => { e.target.parentElement.querySelector('output').textContent = (+e.target.value).toFixed(2) + '×'; });
  box.querySelector('[data-speed]')?.addEventListener('change', e => setSpeed(e.target.value));
  box.querySelectorAll('[data-speedset]').forEach(b => b.onclick = () => setSpeed(b.dataset.speedset));
  box.querySelector('[data-loop]')?.addEventListener('change', e => commit(() => { c.loop = e.target.checked; }));
  box.querySelectorAll('[data-act]').forEach(el => el.onclick = () => {
    const a = el.dataset.act;
    if (a === 'fill') commit(() => Object.assign(c, {x: 0, y: 0, w: 1, h: 1}));
    if (a === 'rot90') commit(() => { c.rotate = (((c.rotate || 0) + 90 + 180) % 360) - 180; });
    if (a === 'fliph') commit(() => { c.flip_h = !c.flip_h; });
    if (a === 'flipv') commit(() => { c.flip_v = !c.flip_v; });
    if (a === 'adjreset') commit(() => { c.adjust = {...ADJ_ZERO}; });
    if (a === 'center') commit(() => { c.x = 0.5 - c.w / 2; c.y = 0.5 - c.h / 2; });
    if (a === 'captions') addCaptions(c);
    if (a === 'trall') commit(p => { p.clips.forEach(x => { if (x.lane === c.lane && x.kind !== 'audio' && prevAdjacent(p, x)) x.transition = {...c.transition}; }); });
    if (a === 'preview-anim') { seek(Math.max(0, c.start - (prevAdjacent(P(), c) ? 1 : 0.3))); play(); setTimeout(() => { if (st.playing && st.time > c.start + c.duration + 0.4) stop(); }, (c.duration + 1.6) * 1000); }
    if (a === 'split') split();
    if (a === 'trimstart') trimToPlayhead('start');
    if (a === 'trimend') trimToPlayhead('end');
    if (a === 'freeze') freezeFrame();
    if (a === 'dup') duplicate();
    if (a === 'del') remove();
  });
}

/* ---------------------------------------------------------------- edits */
function split() {
  const c = clipById(st.sel), t = st.time;
  if (!c || t <= c.start + 0.05 || t >= c.start + c.duration - 0.05) { K.toast('Put the playhead inside the selected clip to split it.'); return; }
  const b = {...JSON.parse(JSON.stringify(c)), id: uid(), start: t, duration: c.start + c.duration - t, fade_in: 0};
  if (c.kind === 'video' || c.kind === 'audio') b.in = c.in + (t - c.start);
  commit(p => { const a = p.clips.find(x => x.id === c.id); a.duration = t - a.start; a.fade_out = 0; p.clips.push(b); });
  select_(b.id);
}
function trimToPlayhead(which) {
  const c = clipById(st.sel), t = st.time;
  if (!c || t <= c.start + 0.05 || t >= c.start + c.duration - 0.05) { K.toast('Put the playhead inside the selected clip first.'); return; }
  commit(() => {
    if (which === 'end') { c.duration = t - c.start; return; }
    const cut = t - c.start;
    if (c.kind === 'video' || c.kind === 'audio') c.in += cut * (c.speed || 1);
    c.start = t; c.duration -= cut;
  });
}
async function freezeFrame() {
  const c = clipById(st.sel), t = st.time;
  if (!c || c.kind !== 'video') return;
  if (t < c.start || t > c.start + c.duration) { K.toast('Put the playhead on the video first.'); return; }
  const at = sourceTime(c, st.els[c.id], t).t;
  let still;
  try { still = (await jsonPost(`/api/media/${c.media}/frame`, {t: at})).media; } catch (err) { K.toast(err.message); return; }
  st.media[still.id] = still; renderBin();
  const hold = 2;
  commit(p => {
    const inside = t > c.start + 0.05 && t < c.start + c.duration - 0.05;
    let after = null;
    if (inside) {  // split, then put the still between the halves
      after = {...JSON.parse(JSON.stringify(c)), id: uid(), start: t, duration: c.start + c.duration - t, fade_in: 0, transition: {type: 'none', dur: 0.6}};
      after.in = c.in + (t - c.start) * (c.speed || 1);
      c.duration = t - c.start; c.fade_out = 0;
      p.clips.push(after);
    }
    // Make room on this track, then drop the still in.
    p.clips.filter(x => x.lane === c.lane && x !== c && x.start >= t - 0.001).forEach(x => { x.start += hold; });
    p.clips.push({id: uid(), kind: 'image', media: still.id, name: still.name, lane: c.lane, start: t, duration: hold,
      x: c.x, y: c.y, w: c.w, h: c.h, fit: c.fit, opacity: c.opacity, fade_in: 0, fade_out: 0, zoom: 0, volume: 1, muted: false,
      in: 0, speed: 1, loop: false});
  });
  K.toast('Frozen frame added (2 s). Drag its edge to make it longer.');
}
function copy() {
  const c = clipById(st.sel); if (!c) return;
  st.clipboard = JSON.parse(JSON.stringify(c)); K.toast('Copied. Ctrl+V pastes at the playhead.');
}
function paste() {
  if (!st.clipboard) return;
  const d = {...JSON.parse(JSON.stringify(st.clipboard)), id: uid(), start: st.time};
  const lanes = d.kind === 'audio' ? [d.lane, 'A1', 'A2', 'A3'] : [d.lane, 'V2', 'V3', 'T1', 'V1'];
  d.lane = freeLane(lanes, d.start, d.duration) || d.lane;
  if (d.lane === 'V1' && st.magnet) {
    const under = P().clips.find(x => x.lane === 'V1' && st.time >= x.start && st.time < x.start + x.duration);
    if (under) d.start = under.start + under.duration - 0.001;
  }
  commit(p => p.clips.push(d)); select_(d.id);
}
function duplicate() {
  const c = clipById(st.sel); if (!c) return;
  const d = {...JSON.parse(JSON.stringify(c)), id: uid()};
  d.start = c.start + c.duration;
  const lane = freeLane([c.lane], d.start, d.duration) ? c.lane : (freeLane(laneOf(c.lane).kinds.includes('audio') ? ['A1', 'A2', 'A3'] : ['V1', 'V2', 'V3', 'T1'], d.start, d.duration) || c.lane);
  d.lane = lane;
  commit(p => p.clips.push(d)); select_(d.id);
}
function remove() {
  const c = clipById(st.sel); if (!c) return;
  commit(p => { p.clips = p.clips.filter(x => x.id !== c.id); });
  select_(null);
}
$('#veSplit').onclick = split; $('#veDup').onclick = duplicate; $('#veDel').onclick = remove;
$('#veTrimS').onclick = () => trimToPlayhead('start'); $('#veTrimE').onclick = () => trimToPlayhead('end');
$('#veMagnet').checked = st.magnet;
$('#veMagnet').onchange = e => { st.magnet = e.target.checked; store.set('ve-magnet', st.magnet); if (st.magnet) commit(() => {}); };
$('#veUndo').onclick = undo; $('#veRedo').onclick = redo;

document.addEventListener('keydown', e => {
  if (document.body.dataset.view !== 'video' || $('dialog[open]')) return;
  const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName);
  const mod = e.ctrlKey || e.metaKey;
  if (mod && e.key.toLowerCase() === 'z') { if (typing) return; e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
  if (mod && e.key.toLowerCase() === 'y') { if (typing) return; e.preventDefault(); redo(); return; }
  if (mod && e.key.toLowerCase() === 'd') { e.preventDefault(); duplicate(); return; }
  if (mod && e.key.toLowerCase() === 's') { e.preventDefault(); save(); return; }
  if (mod && e.key.toLowerCase() === 'c' && !typing) { e.preventDefault(); copy(); return; }
  if (mod && e.key.toLowerCase() === 'v' && !typing) { e.preventDefault(); paste(); return; }
  if (mod && e.key.toLowerCase() === 'x' && !typing) { e.preventDefault(); copy(); remove(); return; }
  if (typing) return;
  if (e.key === ' ') { e.preventDefault(); st.playing ? stop() : play(); }
  else if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); remove(); }
  else if (e.key.toLowerCase() === 's') split();
  else if (e.key.toLowerCase() === 'q') trimToPlayhead('start');
  else if (e.key.toLowerCase() === 'w') trimToPlayhead('end');
  else if (e.key.toLowerCase() === 'm') { const c = clipById(st.sel); if (c && (c.kind === 'video' || c.kind === 'audio')) commit(() => { c.muted = !c.muted; }); }
  else if (e.key === 'ArrowLeft') seek(st.time - (e.shiftKey ? 1 : 1 / P().fps));
  else if (e.key === 'ArrowRight') seek(st.time + (e.shiftKey ? 1 : 1 / P().fps));
  else if (e.key === 'Home') seek(0);
  else if (e.key === 'End') seek(duration());
  else if (e.key === 'Escape') select_(null);
});

/* ---------------------------------------------------------------- top bar */
function syncTop() {
  $('#veName').value = P().name === 'Untitled video' ? '' : P().name;
  $('#veFormat').value = P().format;
  $('#veBg').value = P().background.slice(0, 7);
  $('#veSaved').textContent = 'Saved';
}
$('#veName').addEventListener('change', e => commit(p => { p.name = e.target.value.trim() || 'Untitled video'; }));
$('#veFormat').addEventListener('change', e => {
  const f = e.target.value;
  commit(p => { p.format = f; [p.width, p.height] = FORMATS[f]; });
  layoutStage();
});
$('#veBg').addEventListener('change', e => commit(p => { p.background = e.target.value; }));
const DESIGNS = [
  {name: 'Landscape video', format: '16:9', note: '1920 × 1080'},
  {name: 'YouTube video', format: '16:9', note: '1920 × 1080'},
  {name: 'YouTube Shorts', format: '9:16', note: '1080 × 1920'},
  {name: 'TikTok video', format: '9:16', note: '1080 × 1920'},
  {name: 'Instagram Reel', format: '9:16', note: '1080 × 1920'},
  {name: 'Mobile video', format: '9:16', note: '1080 × 1920'},
  {name: 'Facebook video', format: '4:5', note: '1080 × 1350'},
  {name: 'Instagram post', format: '4:5', note: '1080 × 1350'},
  {name: 'Square video', format: '1:1', note: '1080 × 1080'},
];
function openDesigns() {
  const q = ($('#veDesignSearch').value || '').toLowerCase();
  $('#veDesigns').innerHTML = DESIGNS.filter(d => !q || d.name.toLowerCase().includes(q) || d.format.includes(q)).map((d, i) => {
    const [w, h] = FORMATS[d.format], k = 92 / Math.max(w, h);
    return `<button type="button" class="ve-design" data-design="${DESIGNS.indexOf(d)}">
      <span class="ve-design-art"><i style="width:${Math.round(w * k)}px;height:${Math.round(h * k)}px"><b>▶</b></i></span>
      <span class="ve-design-name">${esc(d.name)}</span><span class="muted mono">${d.format} · ${d.note}</span></button>`;
  }).join('') || '<p class="muted">No match.</p>';
  $$('[data-design]').forEach(b => b.onclick = async () => {
    const d = DESIGNS[+b.dataset.design];
    $('#veDesignDialog').close();
    if (st.dirty) await save();
    const p = (await jsonPost('/api/video/projects', {name: d.name, format: d.format})).project;
    await openProject(p.id);
    K.toast(`New ${d.name.toLowerCase()} started. Earlier videos are kept in Projects.`);
  });
  if (!$('#veDesignDialog').open) $('#veDesignDialog').showModal();
}
$('#veDesignSearch').addEventListener('input', openDesigns);
$('#veNew').onclick = openDesigns;

/* ---------------------------------------------------------------- record yourself */
let rec = null;
$('#veRecord').onclick = async () => {
  const dlg = $('#veRecordDialog'), v = $('#veCam'), m = $('#veRecMsg');
  m.className = 'msg'; m.textContent = ''; $('#veRecUse').hidden = true; $('#veRecStart').hidden = false; $('#veRecStop').hidden = true;
  dlg.showModal();
  try {
    const [W, H] = FORMATS[P().format];
    const stream = await navigator.mediaDevices.getUserMedia({audio: true, video: {width: {ideal: W >= H ? 1920 : 1080}, height: {ideal: W >= H ? 1080 : 1920}}});
    rec = {stream, chunks: [], blob: null};
    v.srcObject = stream; v.muted = true; await v.play().catch(() => {});
  } catch (err) { m.className = 'msg error'; m.textContent = 'Camera or microphone not available: ' + err.message; }
};
$('#veRecStart').onclick = () => {
  if (!rec?.stream) return;
  const type = ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm'].find(t => window.MediaRecorder?.isTypeSupported(t));
  rec.chunks = []; rec.blob = null;
  rec.mr = new MediaRecorder(rec.stream, type ? {mimeType: type, videoBitsPerSecond: 8e6} : undefined);
  rec.mr.ondataavailable = e => { if (e.data.size) rec.chunks.push(e.data); };
  rec.mr.onstop = () => {
    rec.blob = new Blob(rec.chunks, {type: 'video/webm'});
    $('#veRecUse').hidden = false; $('#veRecStart').hidden = false; $('#veRecStart').textContent = 'Record again';
    $('#veRecMsg').textContent = `Recorded ${(rec.blob.size / 1048576).toFixed(1)} MB. Add it to your uploads, or record again.`;
  };
  rec.mr.start(1000); rec.t0 = Date.now();
  $('#veRecStart').hidden = true; $('#veRecStop').hidden = false; $('#veRecUse').hidden = true;
  const tickRec = () => { if (rec?.mr?.state === 'recording') { $('#veRecMsg').textContent = '● Recording ' + fmt((Date.now() - rec.t0) / 1000); setTimeout(tickRec, 200); } };
  tickRec();
};
$('#veRecStop').onclick = () => { if (rec?.mr?.state === 'recording') rec.mr.stop(); $('#veRecStop').hidden = true; };
$('#veRecUse').onclick = async () => {
  if (!rec?.blob) return;
  const file = new File([rec.blob], `Recording ${new Date().toLocaleString().replace(/[/:]/g, '-')}.webm`, {type: 'video/webm'});
  $('#veRecordDialog').close();
  await upload([file]);
};
$('#veRecordDialog').addEventListener('close', () => {
  if (rec?.mr?.state === 'recording') rec.mr.stop();
  rec?.stream?.getTracks().forEach(t => t.stop());
  $('#veCam').srcObject = null; $('#veRecStart').textContent = 'Start recording';
});
function renderAll() { renderTimeline(); renderProps(); }

/* ---------------------------------------------------------------- projects dialog */
$('#veOpen').onclick = async () => {
  if (st.dirty) await save();
  const [pr, ex] = await Promise.all([api('/api/video/projects'), api('/api/video/exports')]);
  $('#veProjectList').innerHTML = pr.projects.map(p => `
    <div class="clone-row"><span class="mono muted">${esc(p.format)}</span>
      <div><b>${esc(p.name)}</b> <span class="muted">${p.clips} clips · ${K.fmtShort(p.duration)} · ${new Date(p.updated).toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'})}</span></div>
      <button type="button" class="link-btn" data-openp="${p.id}" ${p.id === P().id ? 'disabled' : ''}>${p.id === P().id ? 'Open now' : 'Open'}</button>
      <button type="button" class="link-btn danger" data-delp="${p.id}">Delete</button></div>`).join('') || '<p class="muted">No projects yet.</p>';
  $('#veExportList').innerHTML = ex.exports.map(x => `
    <div class="clone-row"><span class="mono muted">MP4</span>
      <div><b>${esc(x.file)}</b> <span class="muted">${(x.size / 1048576).toFixed(1)} MB</span></div>
      <a class="link-btn" href="/api/video/exports/${encodeURIComponent(x.file)}?download=1">Download</a>
      <button type="button" class="link-btn danger" data-delx="${esc(x.file)}">Delete</button></div>`).join('') || '<p class="muted">Nothing exported yet.</p>';
  $$('[data-openp]').forEach(b => b.onclick = async () => { $('#veOpenDialog').close(); await openProject(b.dataset.openp); });
  $$('[data-delp]').forEach(b => b.onclick = async () => {
    if (!confirm('Delete this video project? Uploaded media and exported videos are kept.')) return;
    await api(`/api/video/projects/${b.dataset.delp}`, {method: 'DELETE'});
    if (b.dataset.delp === P().id) { $('#veOpenDialog').close(); await openProject(null); } else $('#veOpen').click();
  });
  $$('[data-delx]').forEach(b => b.onclick = async () => {
    if (!confirm('Delete this exported video file?')) return;
    await api(`/api/video/exports/${encodeURIComponent(b.dataset.delx)}`, {method: 'DELETE'}); $('#veOpen').click();
  });
  if (!$('#veOpenDialog').open) $('#veOpenDialog').showModal();
};

/* ---------------------------------------------------------------- export */
function renderTextImages(scale = 1) {
  // Same rounding as _even() in studio/video.py so text images match the export size exactly.
  const even = v => { const n = Math.max(2, Math.round(v)); return n + (n % 2); };
  const out = {}, W = even(P().width * scale), H = even(P().height * scale);
  const off = document.createElement('canvas'); off.width = W; off.height = H;
  const ctx = off.getContext('2d');
  const plan = motionPlan(P());
  for (const c of P().clips.filter(c => c.kind === 'text')) {
    ctx.clearRect(0, 0, W, H); drawText(ctx, c, W, H);
    const full = off.toDataURL('image/png');
    const mp = plan[c.id];
    if (mp?.in && mp.in[0] === 'typewriter') {
      const n = textLength(c), steps = Math.max(2, Math.min(60, n)), seq = [];
      for (let i = 0; i < steps; i++) {
        ctx.clearRect(0, 0, W, H); drawText(ctx, c, W, H, Math.floor(i / steps * n));
        seq.push(off.toDataURL('image/png'));
      }
      out[c.id] = {full, seq, seq_dur: mp.in[1]};
    } else out[c.id] = full;
  }
  return out;
}
$('#veExport').onclick = () => {
  if (!P().clips.length) { K.toast('Add something to the timeline first.'); return; }
  stop();
  $('#veExSummary').textContent = `${P().name} · ${P().width}×${P().height} · ${fmt(duration())} · saved to creations/exports/`;
  $('#veExProgress').hidden = true; $('#veExResult').innerHTML = ''; $('#veExMsg').textContent = '';
  $('#veExGo').disabled = false; $('#veExCancel').hidden = true;
  $('#veExportDialog').showModal();
};
$('#veExportForm').addEventListener('submit', async e => {
  if (e.submitter && e.submitter.value === 'close') return;
  e.preventDefault();
  const form = new FormData($('#veExportForm'));
  $('#veExGo').disabled = true; $('#veExMsg').className = 'msg'; $('#veExMsg').textContent = 'Preparing…';
  try {
    await document.fonts?.ready;
    const res = +form.get('resolution') || 1080, scale = res / Math.min(P().width, P().height);
    const body = {project: P(), text_images: renderTextImages(scale), quality: form.get('quality'), lufs: form.get('lufs'), resolution: res};
    const r = await jsonPost(`/api/video/projects/${P().id}/export`, body);
    st.job = r.job.id; st.dirty = false; $('#veSaved').textContent = 'Saved';
    $('#veExProgress').hidden = false; $('#veExCancel').hidden = false; $('#veExMsg').textContent = 'Exporting…';
    pollJob();
  } catch (err) { $('#veExMsg').className = 'msg error'; $('#veExMsg').textContent = err.message; $('#veExGo').disabled = false; }
});
$('#veExCancel').onclick = async () => { if (st.job) await jsonPost(`/api/video/jobs/${st.job}/cancel`, {}); };
async function pollJob() {
  if (!st.job) return;
  let job;
  try { job = (await api(`/api/video/jobs/${st.job}`)).job; } catch { setTimeout(pollJob, 1000); return; }
  const pct = Math.round(job.progress * 100);
  $('#veExBar').style.width = pct + '%'; $('#veExPct').textContent = pct + '%';
  if (job.status === 'running') { setTimeout(pollJob, 600); return; }
  st.job = null; $('#veExCancel').hidden = true; $('#veExGo').disabled = false;
  const m = $('#veExMsg');
  if (job.status === 'done') {
    const url = `/api/video/exports/${encodeURIComponent(job.file)}`;
    m.className = 'msg ok'; m.textContent = 'Done.' + (job.warnings.length ? ' ' + job.warnings.join(' ') : '');
    $('#veExResult').innerHTML = `<video class="ve-result" src="${url}" controls preload="metadata"></video>
      <p><a class="btn" href="${url}?download=1">Download ${esc(job.file)}</a> <span class="muted">Also saved in creations/exports/</span></p>`;
  } else if (job.status === 'cancelled') { m.className = 'msg'; m.textContent = 'Export cancelled.'; }
  else { m.className = 'msg error'; m.textContent = 'Export failed: ' + (job.error || 'unknown error'); }
}

/* ---------------------------------------------------------------- lifecycle */
let booting = null;
function init() {
  // One shared start-up: callers arriving while it runs (e.g. "Send to video") wait for the same promise.
  if (!booting) booting = (async () => {
    try {
      const status = await api('/api/video/status');
      $('#veNoFfmpeg').hidden = !!status.ffmpeg;
      renderPresets();
      await loadLibrary();
      let last = store.get('ve-last', null);
      if (!last) last = (await api('/api/video/projects')).projects[0]?.id || null;  // reopen the latest work
      await openProject(last);
      st.ready = true;
      if (Object.values(st.media).some(m => m.proxy === 'pending')) pollProxies();
    } catch (err) { booting = null; K.toast('Video editor: ' + err.message); throw err; }
  })();
  return booting;
}
window.KVideo = {
  async onShow() { st.shown = true; try { await init(); } catch { return; } await loadLibrary().catch(() => {}); layoutStage(); renderAll(); },
  onHide() { st.shown = false; stop(); if (st.dirty) save(); },
  async addTake(rec, opts) {
    K.show('video');
    await init();
    await addTake(rec, opts);
    K.toast(`"${rec.name || 'Voice take'}" added to the Voice track. Select it and choose "Add captions as text" for on-screen words.`);
  },
  drawText,
};
window.addEventListener('beforeunload', e => { if (st.dirty) { save(); e.preventDefault(); } });
})();
