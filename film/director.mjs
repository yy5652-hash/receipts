// Films Receipts: the art scenes (art/*.html) and the real web app, frame by frame, in headless Chromium.
//   node director.mjs [--only=<scene>[,<scene>]] [--every=N]   -> work/frames/00000.jpg ... and work/events.json
//   --only without --patch writes previews to work/preview; with --patch it re-renders those scenes in place in work/frames.
//
// The app scenes show the real Receipts web app (this repository's app/ and samples/) served by serve_film.py.
// Nothing in the UI is mocked. The film adds only a camera (the app is captured through a moving 16:9 window),
// a cursor, captions, highlight rings and a "recorded run" tag. The live-run scene plays back the one real audit
// recorded in data/live-run.jsonl: the page's request to /api/audit is answered with those very events, delivered
// at their recorded arrival times on the film clock (sped up, and labelled as such on screen).
import { readFileSync, writeFileSync, mkdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { boot, HERE, ROOT, APP_URL, ART_PORT, VW, VH, DSF, FPS, clamp, lerp, ease, easeOut, wait, until } from './lib.mjs';

const arg = name => process.argv.find(a => a.startsWith(`--${name}=`))?.split('=')[1];
const ONLY = arg('only')?.split(',');
const EVERY = Number(arg('every') || 1);
const timeline = JSON.parse(readFileSync(join(HERE, 'work/timeline.json'), 'utf8'));
const facts = JSON.parse(readFileSync(join(HERE, 'work/facts.json'), 'utf8'));
const runEvents = readFileSync(join(HERE, 'data/live-run.jsonl'), 'utf8').trim().split('\n').map(l => JSON.parse(l));
const DOC = readFileSync(join(ROOT, 'samples/ai-energy-briefing.md'), 'utf8');
const PATCH = process.argv.includes('--patch');
const framesDir = join(HERE, ONLY && !PATCH ? 'work/preview' : 'work/frames');
if (!PATCH) rmSync(framesDir, { recursive: true, force: true });
mkdirSync(framesDir, { recursive: true });
const frameOffset = id => { let n = 0; for (const sc of timeline.scenes) { if (sc.id === id) break; n += Math.round((sc.end - sc.start) * FPS); } return n; };

const { send, js, on } = await boot();
await send('Page.enable'); await send('Runtime.enable');
await send('Page.setBypassCSP', { enabled: true });   // the app's CSP would block the film's own injected stylesheet
await send('Emulation.setLocaleOverride', { locale: 'en-US' });
await send('Emulation.setFocusEmulationEnabled', { enabled: true });
// Guard: the page must never reach the real /api/audit (the film server has no keys, but be certain).
await send('Fetch.enable', { patterns: [{ urlPattern: '*/api/audit*' }] });
on(m => { if (m.method === 'Fetch.requestPaused') { console.error('BLOCKED a real /api/audit request'); send('Fetch.failRequest', { requestId: m.params.requestId, errorReason: 'BlockedByClient' }); } });
await send('Page.addScriptToEvaluateOnNewDocument', { source: `if (location.port === '8101') { ${readFileSync(join(HERE, 'replay-shim.js'), 'utf8')} }` });
on(m => { if (m.method === 'Runtime.exceptionThrown') console.error('PAGE ERROR:', (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text).split('\n').slice(0, 3).join(' | ')); });
const FILM_UI = readFileSync(join(HERE, 'film-ui.js'), 'utf8');

// ---------------------------------------------------------------- capture
let frameNo = 0;
const events = [];
let mode = null;
async function setMode(m) {
  if (mode === m) return;
  mode = m;
  if (m === 'app') await send('Emulation.setDeviceMetricsOverride', { width: VW, height: VH, deviceScaleFactor: DSF, mobile: false });
  else await send('Emulation.setDeviceMetricsOverride', { width: 1920, height: 1080, deviceScaleFactor: 2, mobile: false });
}
let cam = { x: 0, y: 0, w: VW };
let scroll = 0;
async function capture() {
  const clip = mode === 'app'
    ? { x: cam.x, y: cam.y + scroll, width: cam.w, height: (cam.w * 9) / 16, scale: 1920 / (cam.w * DSF) }
    : { x: 0, y: 0, width: 1920, height: 1080, scale: 0.5 };
  const r = await send('Page.captureScreenshot', { format: 'jpeg', quality: 92, clip, optimizeForSpeed: false });
  writeFileSync(join(framesDir, `${String(frameNo).padStart(5, '0')}.jpg`), Buffer.from(r.data, 'base64'));
  frameNo++;
}
async function open(url, readyExpr) {
  await send('Page.navigate', { url });
  await until(() => js(`document.readyState === 'complete' && (${readyExpr})`), url);
  await js('document.fonts.ready.then(() => true)');
  await js(FILM_UI);
}
const settle = () => js('new Promise(r => setTimeout(() => requestAnimationFrame(() => requestAnimationFrame(r)), 20))');
const captionAt = (scene, t) => {
  const line = scene.lines.find(l => t >= l.start - scene.start - 0.12 && t <= l.end - scene.start + 0.4);
  if (!line) return { text: '', a: 0 };
  const a = Math.min(1, (t - (line.start - scene.start - 0.12)) / 0.18, (line.end - scene.start + 0.4 - t) / 0.2);
  return { text: line.text, a: Math.max(0, a) };
};

// ---------------------------------------------------------------- art scenes
async function artScene(sc) {
  if (PATCH) frameNo = frameOffset(sc.id);
  await setMode('art');
  const rel = Object.fromEntries(sc.lines.map(l => [l.id, [l.start - sc.start, l.end - sc.start]]));
  const parts = Object.fromEntries(sc.lines.map(l => [l.id, l.parts.map(p => [p.start - sc.start, p.end - sc.start])]));
  await open(`http://127.0.0.1:${ART_PORT}/${sc.page}`, 'window.ready !== undefined');
  await js(`window.LINES = ${JSON.stringify(rel)}; window.PARTS = ${JSON.stringify(parts)}; window.SCENE_END = ${sc.end - sc.start}; window.FACTS = ${JSON.stringify(facts)}; film.setCam(0, 0, 1920); true`);
  for (const h of await js('window.hitTimes ? window.hitTimes() : []')) events.push({ t: sc.start + h, kind: 'hit' });
  const n = Math.round((sc.end - sc.start) * FPS);
  for (let f = 0; f < n; f++) {
    if (f % EVERY) continue;
    const t = f / FPS, c = captionAt(sc, t);
    await js(`render(${t}); film.caption(${JSON.stringify(c.text)}, ${c.a}); film.frame(${sc.start + t}); true`);
    await capture();
  }
}

// ---------------------------------------------------------------- app scenes: beats
const COLORS = { red: '#d6434a', slate: '#566178', violet: '#8d63d6', green: '#2f9e63', amber: '#d9a21b', ink: '#1f1d19' };
function beats() {
  const list = [];
  return { list, do: (at, fn) => list.push({ at, dur: 0, fn: () => fn() }), span: (at, dur, fn) => list.push({ at, dur, fn }) };
}
const rect = async spec => { for (let i = 0; i < 40; i++) { const r = await js(`film.rect(${JSON.stringify(spec)})`); if (r) return r; await wait(100); } return null; };   // waits for the page (a fetch may still be rendering)
const point = async (spec, dx = 0.5, dy = 0.5) => { await rect(spec); return js(`film.point(${JSON.stringify(spec)}, ${dx}, ${dy})`); };
const union = async (...specs) => {
  const rs = (await Promise.all(specs.map(rect))).filter(Boolean);
  if (!rs.length) return null;
  const x = Math.min(...rs.map(r => r.x)), y = Math.min(...rs.map(r => r.y));
  return { x, y, w: Math.max(...rs.map(r => r.x + r.w)) - x, h: Math.max(...rs.map(r => r.y + r.h)) - y };
};
// A camera window (16:9) that frames `r`; ay is where the middle of r sits vertically in the frame (0.44 keeps it above the captions).
function fit(r, { pad = 40, minW = 640, maxW = VW, ax = 0.5, ay = 0.45, left = false } = {}) {
  if (!r) return { ...cam };
  const w = clamp(Math.max(r.w + 2 * pad, ((r.h + 2 * pad) * 16) / 9, minW), 640, maxW), h = (w * 9) / 16;
  const x = left ? r.x - pad : r.x + r.w * ax - w * ax;
  return { x: clamp(x, 0, VW - w), y: clamp(r.y + r.h / 2 - h * ay, 0, VH - h), w };
}
const WIDE = { x: 0, y: 0, w: VW };
function camTo(B, at, dur, target) {
  let from, to;
  B.span(at, dur, async p => {
    if (!from) { from = { ...cam }; to = typeof target === 'function' ? await target() : target; }
    const e = ease(p);
    cam = { x: lerp(from.x, to.x, e), y: lerp(from.y, to.y, e), w: lerp(from.w, to.w, e) };
  });
}
let cursor = { x: VW / 2, y: VH / 2, show: false };
function cursorTo(B, at, dur, target) {
  let from, to;
  B.span(at, dur, async p => {
    if (!from) { from = { ...cursor }; to = typeof target === 'function' ? await target() : target; }
    const e = ease(p);
    const lift = Math.sin(Math.PI * e) * Math.min(40, Math.hypot(to.x - from.x, to.y - from.y) * 0.1);
    cursor = { x: lerp(from.x, to.x, e), y: lerp(from.y, to.y, e) - lift, show: true };
  });
}
const cursorShow = (B, at, show) => B.do(at, () => { cursor.show = show; });
function scrollTo(B, at, dur, target) {
  let from, to;
  B.span(at, dur, async p => {
    if (from === undefined) { from = scroll; to = typeof target === 'function' ? await target() : target; }
    scroll = await js(`film.scrollTo(${lerp(from, to, ease(p))})`);
  });
}
// scroll so that the element's middle sits `anchor` px from the top of the window
const scrollFor = (spec, anchor = 300, bias = 0) => async () => {
  const r = await rect(spec);
  if (!r) throw new Error(`scrollFor: nothing matches ${JSON.stringify(spec)} at t=${tNow.toFixed(2)}`);
  return clamp(Math.round(r.y + scroll + r.h / 2 - anchor + bias), 0, await js('document.documentElement.scrollHeight - innerHeight'));
};
let pressP = 0;
function click(B, at, spec, { dx = 0.5, dy = 0.5, kind = 'click', settleMs = 0 } = {}) {
  B.span(at, 0.5, p => { pressP = p; });
  B.do(at + 0.08, async () => {
    const pt = await point(spec, dx, dy);
    if (pt) cursor = { ...cursor, x: pt.x, y: pt.y };
    events.push({ t: absNow(), kind });
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: cursor.x, y: cursor.y });
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: cursor.x, y: cursor.y, button: 'left', buttons: 1, clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: cursor.x, y: cursor.y, button: 'left', buttons: 0, clickCount: 1 });
    if (settleMs) await wait(settleMs);
  });
}
const ringAlpha = (p, dur) => Math.min(1, (p * dur) / 0.25, ((1 - p) * dur) / 0.3);
const ring = (B, at, dur, key, spec, color) => B.span(at, dur, p => js(`film.ring(${JSON.stringify(key)}, ${JSON.stringify(spec)}, ${JSON.stringify(color)}, ${p >= 1 ? 0 : ringAlpha(p, dur)})`));
const attr = (B, at, fn) => B.do(at, fn);

// ---------------------------------------------------------------- the recorded run on the film clock
// Recorded seconds r (since the request) against film seconds d since the click: 2x while the claims are being read,
// real time while the sources are read and the claims judged (the part to watch), 4x while the real sources are searched.
// The tag on screen says which.
const RUN = { abs0: null, segs: [[facts.live.times.claims, 2], [facts.live.times.lastVerdict, 1], [Infinity, 4]], pushed: 0, doneAt: facts.live.times.done };
function recordedAt(abs) {
  if (RUN.abs0 === null || abs < RUN.abs0) return { r: -1, speed: 0 };
  let d = abs - RUN.abs0, prev = 0;
  for (const [end, speed] of RUN.segs) {
    const film = (end - prev) / speed;
    if (d <= film) return { r: prev + d * speed, speed };
    d -= film; prev = end;
  }
}
let absStart = 0, tNow = 0;
const STAT = { from: 0, until: 0, html: '' };          // the benchmark tag of the fix scene
const absNow = () => absStart + tNow;

async function appScene(sc, plan, { fadeIn = false, fadeOut = false } = {}) {
  if (PATCH) frameNo = frameOffset(sc.id);
  await setMode('app');
  const L = Object.fromEntries(sc.lines.map(l => [l.id, l.start - sc.start]));
  const E = Object.fromEntries(sc.lines.map(l => [l.id, l.end - sc.start]));
  const B = beats();
  STAT.html = ''; STAT.from = STAT.until = 0;
  plan(B, L, E, sc.end - sc.start);
  B.list.sort((a, b) => a.at - b.at);
  const n = Math.round((sc.end - sc.start) * FPS);
  absStart = sc.start;
  const t0 = Date.now();
  for (let f = 0; f < n; f++) {
    const t = f / FPS;
    tNow = t;
    for (const b of B.list) {
      if (b.done || t < b.at) continue;
      const p = b.dur ? clamp((t - b.at) / b.dur) : 1;
      await b.fn(p, t);
      if (p >= 1) b.done = true;
    }
    const abs = sc.start + t;
    // the film clock: the page's own clock follows it, and the recorded run is delivered up to the recorded second
    const { r, speed } = recordedAt(abs);
    let pushed = 0;
    if (r >= 0) {
      pushed = await js(`__vclock.set(${RUN.abs0 * 1000 + r * 1000}); __replayTo(${r})`);
      for (const e of runEvents) if (e.t > (RUN.last ?? -1) && e.t <= r && e.event.type === 'verdict') events.push({ t: abs, kind: 'verdict', label: e.event.verdict.label });
      RUN.last = r;
    } else await js(`__vclock.set(${abs * 1000})`);
    if (f % EVERY) continue;
    if (pushed || r >= 0) await settle();
    const c = captionAt(sc, t);
    const fade = Math.max(fadeIn ? 1 - clamp(t / 0.5) : 0, fadeOut ? clamp((t - (sc.end - sc.start - 0.5)) / 0.5) : 0);
    const hud = r >= 0 && r <= RUN.doneAt + 0.3 ? `Recorded run · ${speed === 1 ? 'real time' : speed + '× speed'}` : '';
    const stat = STAT.until > t && t >= STAT.from ? STAT.html : '';
    await js(`film.setCam(${cam.x}, ${cam.y}, ${cam.w}); film.cursor(${cursor.x}, ${cursor.y}, ${cursor.show}); film.press(${pressP}); film.fade(${fade}); film.caption(${JSON.stringify(c.text)}, ${c.a}); film.hud(${JSON.stringify(hud)}, 1); film.stat(${JSON.stringify(stat)}, ${clamp((t - STAT.from) / 0.4)})`);
    if (cursor.show) await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: cursor.x, y: cursor.y });
    scroll = await js(`film.frame(${abs})`);
    await capture();
  }
  console.log(`${sc.id}: ${n} frames in ${((Date.now() - t0) / 1000).toFixed(0)} s`);
}

// ---------------------------------------------------------------- app states
async function loadApp(hash = '') {
  await setMode('app');
  await open(`${APP_URL}${hash}`, "document.querySelectorAll('.example-card').length > 0");
  await js(`window.__replay = ${JSON.stringify({ document: DOC, mode: 'ultra', events: runEvents })}; true`);
  cam = { ...WIDE }; scroll = 0; cursor = { x: VW * 0.8, y: VH * 0.7, show: false };
  RUN.abs0 = null; RUN.last = -1;
}
// For previews of a later scene: the finished audit, reached through the real UI with the recorded events delivered at once.
async function finishedAudit() {
  await loadApp();
  await js(`document.querySelector('#doc-input').focus(); true`);
  await send('Input.insertText', { text: DOC });
  await js(`document.querySelector('#check-btn').click(); true`);
  await wait(200);
  await js('__replayTo(1e9)');
  await until(() => js(`!document.querySelector('#dl-md').disabled && document.querySelector('#progress').hidden`), 'the finished audit');
  scroll = await js('film.scrollTo(96)');         // where the live-run scene leaves the page
}

// ---------------------------------------------------------------- the plans
const claimSel = id => `.claim[data-claim="${id}"]`;
const claimLine = (id, line = 0) => ({ sel: claimSel(id), line });
const PLANS = {
  paste(B, L, E, END) {
    const TA = '#doc-input';
    const click0 = L.p1 - 0.15;                       // the click on "Check citations" lands as the voice says "Nemotron Nano"
    B.do(0, async () => { cam = fit(await union('.hero h1', '#input-form'), { pad: 36, minW: 1000, ay: 0.46 }); cursor = { x: VW * 0.78, y: VH * 0.72, show: false }; });
    cursorTo(B, 0.35, 0.7, () => point(TA, 0.55, 0.4));
    click(B, 1.0, TA, { dx: 0.55, dy: 0.4 });
    B.do(1.25, async () => { events.push({ t: absNow(), kind: 'paste' }); await js(`film.key('⌘V', 1)`); await send('Input.insertText', { text: DOC }); });
    B.span(1.25, 0.9, async p => js(`film.key('⌘V', ${p >= 1 ? 0 : 1})`));
    // the pasted report scrolls back to its first line
    B.span(1.5, 1.3, p => js(`(() => { const ta = document.querySelector('${TA}'); ta.scrollTop = ta.scrollTop * (1 - ${ease(p)}); return 1; })()`));
    camTo(B, 2.6, 0.9, async () => fit(await union('#input-form'), { pad: 30, minW: 1000, ay: 0.5 }));
    cursorTo(B, 2.7, 0.7, () => point('.advanced summary', 0.15, 0.5));
    click(B, 3.5, '.advanced summary', { dx: 0.15 });
    camTo(B, 3.9, 0.8, async () => fit(await union('#mode', '#mode-hint', '#check-btn', TA), { pad: 30, minW: 1000, ay: 0.5 }));
    ring(B, 4.2, 1.3, 'mode', '#mode', COLORS.amber);
    cursorTo(B, 4.5, 0.7, () => point('#check-btn'));
    click(B, click0, '#check-btn');
    B.do(click0 + 0.1, () => { RUN.abs0 = absNow(); });
    // The recorded run, on the film clock (see RUN): claims land at recorded 12.0 s, the last verdict at 22.3 s, done at 31.5 s.
    const T = facts.live.times, run0 = click0 + 0.1;
    const landed = run0 + T.claims / 2;
    const judged = landed + (T.lastVerdict - T.claims);              // real time
    const done = judged + (T.done - T.lastVerdict) / 4;
    const toDoc = 96;
    // while the claims are read: the progress strip and the model line
    camTo(B, click0 + 0.4, 1.0, async () => fit(await rect('.summary'), { pad: 26, minW: 1000, ay: 0.46 }));
    cursorShow(B, click0 + 0.6, false);
    ring(B, click0 + 0.8, 3.4, 'models', '#fact-models', COLORS.amber);
    // the claims land: down to the reference list, where each source chip turns from "looking up" into what was found
    camTo(B, landed - 0.4, 1.0, WIDE);
    scrollTo(B, landed + 0.3, 1.1, async () => { const r = await rect('.refs'); return clamp(Math.round(r.y + scroll - 100), 0, await js('document.documentElement.scrollHeight - innerHeight')); });
    // the judge: back up to the sentences, where the verdicts are landing
    scrollTo(B, L.p3 - 0.5, 1.1, () => toDoc);
    // then the fourth step: a real source for what failed
    scrollTo(B, judged + 0.1, 0.8, () => 0);
    ring(B, judged + 0.7, done - judged, 'step4', '[data-step="suggest"]', COLORS.amber);
    ring(B, done + 0.1, Math.max(0.4, END - done - 0.35), 'real', '#fact-sources-box', COLORS.green);
  },
  findings(B, L, E, END) {
    const CL = (id, line = 0) => ({ sel: claimSel(id), line });
    const evQuote = '#evidence .ev-quote', evCheck = '#evidence .quote-check';
    const quoteCam = async () => { const q = await union('#evidence .ev-source', evQuote, evCheck), a = await rect('#evidence'); return fit({ x: a.x, w: a.w, y: q.y - 24, h: q.h + 24 }, { pad: 24, minW: 640, ay: 0.44 }); };
    const pickClaim = (at, id, dx = 0.3, move = 0.5) => { cursorTo(B, at - move - 0.05, move, () => point(CL(id), dx)); click(B, at, CL(id), { dx }); };
    const chip = id => `.refs .src-chip[data-source="${id}"]`;
    // a close view of the document column only (the evidence panel starts to its right, so no half-cut text)
    const docCam = async spec => { const r = await rect(spec), d = await rect('#doc'), w = 820, h = (w * 9) / 16; return { x: clamp(d.x + d.w + 8 - w, 0, VW - w), y: clamp(r.y + r.h / 2 - 0.4 * h, 0, VH - h), w }; };
    // c2: the briefing says 20%, the IEA says 12%
    B.do(0, () => { cam = { ...WIDE }; cursor = { x: VW * 0.5, y: VH * 0.55, show: false }; });
    scrollTo(B, 0.0, 0.5, scrollFor(CL('c2'), 300));
    cursorShow(B, 0.05, true);
    pickClaim(L.f1 - 0.05, 'c2', 0.3, 0.12);
    ring(B, L.f1 + 1.9, 2.2, 'c2-claim', { sel: claimSel('c2'), text: facts.wrong_growth }, COLORS.red);
    camTo(B, E.f1 + 0.1, 0.8, quoteCam);
    ring(B, L.f2 + 0.2, 2.3, 'c2-quote', { sel: evQuote, text: facts.real_growth }, COLORS.red);
    ring(B, L.f2 + 1.5, 1.6, 'c2-check', evCheck, COLORS.green);
    camTo(B, E.f2 + 0.3, 0.7, WIDE);
    // c3: China and Europe swapped
    pickClaim(L.f3 - 0.1, 'c3', 0.3, 0.45);
    ring(B, L.f3 + 0.3, 1.7, 'c3-claim', { sel: claimSel('c3'), text: 'Europe at 25% and China at 15%' }, COLORS.red);
    ring(B, L.f3 + 0.6, 1.6, 'c3-quote', { sel: evQuote, text: 'China (25%) and Europe (15%)' }, COLORS.red);
    // c6: a forecast that is not in the report
    scrollTo(B, E.f3 + 0.0, 0.5, scrollFor(CL('c6'), 300));
    pickClaim(L.f4 - 0.1, 'c6', 0.3, 0.3);
    ring(B, L.f4 + 1.0, 2.4, 'c6-claim', { sel: claimSel('c6'), text: 'up to 12% of total electricity use by 2030' }, COLORS.slate);
    ring(B, L.f4 + 2.8, 2.5, 'c6-src', '#evidence .ev-source .src-chip', COLORS.green);
    // c10: a paper that has no record anywhere; its reference chip says so
    scrollTo(B, E.f4 - 0.4, 0.7, scrollFor(CL('c10'), 100));
    pickClaim(L.f5 - 0.15, 'c10', 0.3, 0.45);
    scrollTo(B, L.f5 + 0.45, 0.9, scrollFor(chip('s4'), 380));
    ring(B, L.f5 + 1.35, 2.1, 'c10-ref', chip('s4'), COLORS.violet);
    camTo(B, L.f5 + 1.45, 0.7, () => docCam(chip('s4')));
    camTo(B, E.f5 - 0.3, 0.5, WIDE);
    // c12: and a dead link
    scrollTo(B, E.f5 - 0.35, 0.65, scrollFor(CL('c12'), 150));
    pickClaim(L.f6 - 0.15, 'c12', 0.3, 0.45);
    scrollTo(B, L.f6 + 0.15, 0.8, scrollFor(chip('s6'), 380));
    ring(B, L.f6 + 0.9, 1.3, 'c12-ref', chip('s6'), COLORS.violet);
    camTo(B, L.f6 + 1.0, 0.6, () => docCam(chip('s6')));
    camTo(B, E.f6 + 0.2, 0.5, WIDE);
    // every verdict carries a quote that was found in the source
    scrollTo(B, E.f6 + 0.2, 0.5, scrollFor(CL('c7'), 300));
    pickClaim(L.f7 - 0.1, 'c7', 0.3, 0.4);
    camTo(B, L.f7 + 0.6, 0.8, quoteCam);
    ring(B, L.f7 + 1.4, 2.4, 'c7-check', evCheck, COLORS.green);
    cursorShow(B, L.f7 + 0.5, false);
  },
  papers(B, L, E, END) {
    const CL = (id, line = 0) => ({ sel: claimSel(id), line });
    const evQuote = '#evidence .ev-quote', evCheck = '#evidence .quote-check';
    B.do(0, () => { cam = { ...WIDE }; });
    scrollTo(B, 0.0, 0.7, () => 0);
    cursorTo(B, 0.1, 0.65, () => point('#new-check'));
    cursorShow(B, 0.1, true);
    click(B, 0.9, '#new-check');
    scrollTo(B, 1.3, 0.8, async () => { const r = await rect('.example-card'); return clamp(Math.round(r.y + scroll - 380), 0, await js('document.documentElement.scrollHeight - innerHeight')); });
    cursorTo(B, 1.45, 0.7, () => point('.example-card[data-id="ml-related-work"]', 0.4, 0.4));
    click(B, 2.3, '.example-card[data-id="ml-related-work"]', { dx: 0.4, dy: 0.4 });
    B.do(2.5, () => { cam = { ...WIDE }; });
    scrollTo(B, 2.55, 0.65, scrollFor(CL('c2'), 330));
    cursorTo(B, 3.25, 0.45, () => point(CL('c2'), 0.3));
    click(B, L.r2, CL('c2'), { dx: 0.3 });
    ring(B, L.r2 + 0.6, 1.9, 'gpt-claim', { sel: claimSel('c2'), text: '17.5 billion' }, COLORS.red);
    ring(B, L.r2 + 1.1, 1.7, 'gpt-quote', { sel: evQuote, text: '175 billion' }, COLORS.red);
    cursorTo(B, L.r2 + 2.1, 0.55, () => point(CL('c3'), 0.3));
    click(B, L.r2 + 2.8, CL('c3'), { dx: 0.3 });
    ring(B, L.r2 + 3.3, 1.7, 'bert-claim', { sel: claimSel('c3'), text: '85.5' }, COLORS.red);
    ring(B, L.r2 + 3.6, 1.8, 'bert-quote', { sel: evQuote, text: '80.5%' }, COLORS.red);
    cursorShow(B, L.r2 + 4.3, false);
    camTo(B, E.r2 + 0.8, 1.0, async () => { const q = await union('#evidence .ev-source', evQuote, evCheck), a = await rect('#evidence'); return fit({ x: a.x, w: a.w, y: q.y, h: q.h }, { pad: 40, minW: 640, ay: 0.42 }); });
    ring(B, L.r3 + 0.1, 2.4, 'bert-note', '#evidence .src-note', COLORS.amber);
  },
  fix(B, L, E, END) {
    const CL = (id, line = 0) => ({ sel: claimSel(id), line });
    const sugg = '#evidence .ev-suggest';
    B.do(0, () => { RUN.abs0 = null; });
    camTo(B, 0.0, 0.5, WIDE);
    scrollTo(B, 0.0, 0.5, () => 0);
    cursorTo(B, 0.1, 0.5, () => point('#new-check'));
    cursorShow(B, 0.1, true);
    click(B, 0.8, '#new-check');
    scrollTo(B, 1.0, 0.6, async () => { const r = await rect('.example-card'); return clamp(Math.round(r.y + scroll - 380), 0, await js('document.documentElement.scrollHeight - innerHeight')); });
    cursorTo(B, 1.65, 0.55, () => point('.example-card[data-id="ai-energy-briefing"]', 0.4, 0.4));
    click(B, 2.3, '.example-card[data-id="ai-energy-briefing"]', { dx: 0.4, dy: 0.4 });
    scrollTo(B, 2.55, 0.6, scrollFor(CL('c6'), 300));
    cursorTo(B, 3.2, 0.45, () => point(CL('c6'), 0.3));
    click(B, 3.75, CL('c6'), { dx: 0.3 });
    // the evidence panel scrolls to "A source that does say this"
    B.span(4.0, 0.8, async p => { await js(`(() => { const ev = document.querySelector('#evidence'), s = document.querySelector('.ev-suggest'); if (!s) return 0; if (window.__ev0 === undefined) window.__ev0 = ev.scrollTop; const to = ev.scrollTop + s.getBoundingClientRect().top - ev.getBoundingClientRect().top - 90; if (window.__evTo === undefined) window.__evTo = to; ev.scrollTop = window.__ev0 + (window.__evTo - window.__ev0) * ${ease(p)}; return 1; })()`); });
    cursorShow(B, 4.2, false);
    // x2 in the wide view (the selected claim in the document, the real source beside it), then closer on the block
    ring(B, L.x2 + 0.1, 2.3, 'sg-label', '#evidence .suggest-label', COLORS.green);
    camTo(B, L.x2 + 2.2, 0.8, async () => { const q = await rect(sugg), a = await rect('#evidence'); return fit({ x: a.x, w: a.w, y: q.y, h: q.h }, { pad: 26, minW: 640, ay: 0.45 }); });
    ring(B, L.x2 + 3.2, 1.9, 'sg-by', '#evidence .suggest-by', COLORS.amber);
    // the WEF title and the McKinsey quote
    camTo(B, L.x3 - 0.2, 0.8, async () => { const t = await union('#evidence .ev-suggest .src-title', '#evidence .ev-suggest .ev-quote'), a = await rect('#evidence'); return fit({ x: a.x, w: a.w, y: t.y, h: t.h }, { pad: 30, minW: 640, ay: 0.45 }); });
    ring(B, L.x3 + 0.1, 2.6, 'sg-title', '#evidence .ev-suggest .src-title', COLORS.green);
    ring(B, L.x3 + 3.0, 3.4, 'sg-quote', { sel: '#evidence .ev-suggest .ev-quote', text: 'A McKinsey report projects' }, COLORS.amber);
    // the benchmark
    camTo(B, L.x4 + 1.0, 0.9, WIDE);
    B.do(L.x4 - 0.1, () => { STAT.html = `ReceiptsBench: <b>${facts.suggest_k} of ${facts.suggest_n}</b> misattributed or dead-link claims got a supporting source (${facts.suggest_pct}%)`; STAT.from = absNow() - absStart; STAT.until = END; });
    ring(B, L.x4 + 0.2, 3.0, 'sg-check', '#evidence .ev-suggest .quote-check', COLORS.green);
  },
};
const plan = id => PLANS[id] || (() => {});

// ---------------------------------------------------------------- run
let appOpen = false;
for (const sc of timeline.scenes) {
  if (ONLY && !ONLY.includes(sc.id)) continue;
  if (sc.kind === 'art') { await artScene(sc); appOpen = false; continue; }
  if (!appOpen) {
    if (sc.id === 'paste' || !ONLY) await loadApp();
    else if (sc.id === 'fix') { await loadApp('#example=ml-related-work'); await until(() => js("document.querySelectorAll('.claim').length > 0"), 'the recorded example'); }
    else await finishedAudit();
    appOpen = true;
  }
  await appScene(sc, plan(sc.id), { fadeIn: sc.id === 'paste', fadeOut: sc.id === 'fix' });
}
if (PATCH) {
  const old = JSON.parse(readFileSync(join(HERE, 'work/events.json'), 'utf8'));
  const ranges = timeline.scenes.filter(sc => ONLY.includes(sc.id)).map(sc => [sc.start, sc.end]);
  const keep = old.events.filter(e => !ranges.some(([a, b]) => e.t >= a && e.t < b));
  writeFileSync(join(HERE, 'work/events.json'), JSON.stringify({ fps: FPS, frames: old.frames, events: [...keep, ...events].sort((a, b) => a.t - b.t) }, null, 1));
} else writeFileSync(join(HERE, ONLY ? 'work/events.preview.json' : 'work/events.json'), JSON.stringify({ fps: FPS, frames: frameNo, events }, null, 1));
console.log(`done: ${PATCH ? 'patched' : frameNo + ' frames'} -> ${framesDir}`);
process.exit(0);
