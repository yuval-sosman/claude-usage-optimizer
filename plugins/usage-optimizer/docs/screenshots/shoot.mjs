// Element screenshots of report.html through the Chrome DevTools protocol (Node 22+, no packages; Chrome at the macOS path).
//   node shoot.mjs report.html outdir shots.json
// shots.json: [{name, hash, sel, width?, theme?, prep?, pad?, scale?, maxH?}]; see make_demo.py for the whole recipe.
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const [html, outdir, specFile] = process.argv.slice(2);
const shots = JSON.parse(readFileSync(specFile, 'utf8'));
mkdirSync(outdir, { recursive: true });
const port = 9400 + Math.floor(Math.random() * 400);
const chrome = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
  '--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${mkdtempSync(join(process.env.TMPDIR || tmpdir(), 'cdp-'))}`,
  '--hide-scrollbars', '--no-first-run', '--no-default-browser-check', '--force-color-profile=srgb', 'about:blank'], { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));

let targets;
for (let i = 0; i < 50; i++) {
  try { targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); if (targets.find(t => t.type === 'page')) break; } catch (e) { /* not up yet */ }
  await sleep(200);
}
const ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener('open', r, { once: true }));
let seq = 0; const pending = new Map();
ws.addEventListener('message', ev => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } });
const send = (method, params = {}) => new Promise((res, rej) => { const id = ++seq; pending.set(id, m => m.error ? rej(new Error(method + ': ' + m.error.message)) : res(m.result)); ws.send(JSON.stringify({ id, method, params })); });
const evaluate = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text); return r.result.value; };

await send('Page.enable'); await send('Runtime.enable');
const base = pathToFileURL(html).href;
for (const s of shots) {
  const width = s.width || 1200, scale = s.scale || 2;
  await send('Emulation.setDeviceMetricsOverride', { width, height: s.height || 900, deviceScaleFactor: scale, mobile: false });
  await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: s.theme || 'light' }] });
  await send('Page.navigate', { url: 'about:blank' });
  await sleep(100);
  await send('Page.navigate', { url: base + '#' + (s.hash || 'tab=report') + '&render=all' });
  for (let i = 0; i < 100; i++) { if (await evaluate(`document.body && document.body.getAttribute('data-render-status')`) === 'ok') break; await sleep(100); }
  await sleep(500);
  if (s.prep) { await evaluate(`(async () => { ${s.prep} })()`); await sleep(s.wait || 600); }
  // sel: one element, or several (the crop is their union)
  const rect = await evaluate(`(() => { const els = [].concat(${JSON.stringify(s.sel)}).map(q => document.querySelector(q));
    if (els.some(e => !e)) return null;
    els[0].scrollIntoView({block: 'start'});
    const rs = els.map(e => e.getBoundingClientRect());
    const x0 = Math.min(...rs.map(r => r.left)), y0 = Math.min(...rs.map(r => r.top));
    const x1 = Math.max(...rs.map(r => r.right)), y1 = Math.max(...rs.map(r => r.bottom));
    return {x: x0 + scrollX, y: y0 + scrollY, w: x1 - x0, h: y1 - y0}; })()`);
  if (!rect) { console.log('MISSING', s.name, s.sel); continue; }
  await sleep(400);
  const pad = s.pad ?? 0;
  const h = Math.min(rect.h + 2 * pad, s.maxH || 1e5);
  const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true,
    clip: { x: Math.max(0, rect.x - pad), y: Math.max(0, rect.y - pad), width: rect.w + 2 * pad, height: h, scale: 1 } });
  writeFileSync(join(outdir, s.name + '.png'), Buffer.from(shot.data, 'base64'));
  console.log('ok', s.name, Math.round(rect.w), 'x', Math.round(h));
}
ws.close(); chrome.kill();
process.exit(0);
