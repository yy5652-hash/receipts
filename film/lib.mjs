// Shared plumbing for the film scripts: processes, the Chromium connection, frame capture.
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const HERE = dirname(fileURLToPath(import.meta.url));
export const ROOT = join(HERE, '..');
export const SHELL = process.env.CHROME_SHELL || '/Users/hudsonyu/Documents/Codex/2026-09-28/referenced-chatgpt-conversation-this-is-an-4/work/ai4s-v2/.pw-browsers/chromium_headless_shell-1243/chrome-headless-shell-mac-arm64/chrome-headless-shell';
export const APP_PORT = 8101, ART_PORT = 8102, DEBUG = 9342, FPS = 30;
export const APP_URL = `http://127.0.0.1:${APP_PORT}/`;
export const VW = 1440, VH = 810, DSF = 3;                 // the app is filmed in a 1440x810 window
export const wait = ms => new Promise(r => setTimeout(r, ms));
export const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
export const lerp = (a, b, p) => a + (b - a) * p;
export const ease = p => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
export const easeOut = p => 1 - Math.pow(1 - p, 3);

const procs = [];
export function start(cmd, args, opts) { const p = spawn(cmd, args, { stdio: 'ignore', ...opts }); procs.push(p); return p; }
process.on('exit', () => procs.forEach(p => p.kill()));

export async function until(fn, what, tries = 160) {
  for (let i = 0; i < tries; i++) { try { const v = await fn(); if (v) return v; } catch { /* not yet */ } await wait(250); }
  throw new Error(`timed out waiting for ${what}`);
}
export const up = async url => { try { return (await fetch(url)).ok; } catch { return false; } };

export async function boot() {
  if (!(await up(`${APP_URL}api/config`))) start(join(ROOT, '.venv/bin/python'), [join(HERE, 'serve_film.py'), String(APP_PORT)], { cwd: ROOT });
  if (!(await up(`http://127.0.0.1:${ART_PORT}/film-ui.js`))) start('python3', ['-m', 'http.server', String(ART_PORT), '--bind', '127.0.0.1'], { cwd: HERE });
  start(SHELL, [`--remote-debugging-port=${DEBUG}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), 'receipts-film-'))}`, '--window-size=1920,1080', '--hide-scrollbars', '--force-color-profile=srgb', '--font-render-hinting=none', 'about:blank']);
  await until(() => up(`${APP_URL}api/config`), 'the Receipts server');
  await until(() => up(`http://127.0.0.1:${ART_PORT}/film-ui.js`), 'the art server');
  const wsUrl = await until(async () => (await (await fetch(`http://127.0.0.1:${DEBUG}/json`)).json()).find(t => t.type === 'page')?.webSocketDebuggerUrl, 'Chromium');
  const ws = new WebSocket(wsUrl);
  await new Promise(r => { ws.onopen = r; });
  let nextId = 0;
  const pending = new Map();
  const handlers = [];
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { const { resolve, reject } = pending.get(m.id); pending.delete(m.id); m.error ? reject(new Error(m.error.message)) : resolve(m.result); }
    else if (m.method) handlers.forEach(h => h(m));
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => { const id = ++nextId; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params })); });
  const js = async expression => {
    const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(`${r.exceptionDetails.exception?.description || r.exceptionDetails.text}\n in: ${expression.slice(0, 240)}`);
    return r.result.value;
  };
  return { send, js, on: h => handlers.push(h) };
}
