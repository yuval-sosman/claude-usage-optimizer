// Renders claude-usage-video.html to an MP4, frame by frame, through the Chrome DevTools protocol (Node 22+, no packages;
// Chrome at the macOS path; ffmpeg on PATH or in $FFMPEG).
//   node promo/render.mjs promo/claude-usage-video.mp4            # 1080 x 1080, 30 fps, drawn at 2x and scaled down
//   node promo/render.mjs promo/claude-usage-short.mp4 --src promo/claude-usage-short.html
//   node promo/render.mjs sheet.png --stills 2,9,14,18,24,30,38,44,50   # one contact sheet of those moments
// The README preview GIFs, from the MP4s (the plugin README shows the short one, the root README the full one):
//   ffmpeg -i promo/claude-usage-short.mp4 -vf "fps=12,scale=600:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" -loop 0 promo/claude-usage-short.gif
//   ffmpeg -i promo/claude-usage-video.mp4 -vf "fps=10,scale=600:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" -loop 0 promo/claude-usage-video.gif
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const argv = process.argv.slice(2);
const opt = (name, def) => { const i = argv.indexOf('--' + name); return i >= 0 ? argv[i + 1] : def; };
const out = resolve(argv.find((a, i) => !a.startsWith('--') && !(i > 0 && argv[i - 1].startsWith('--'))) || 'claude-usage-video.mp4');
const fps = +opt('fps', 30), scale = +opt('scale', 2), SIZE = 1080;
const stills = opt('stills') ? opt('stills').split(',').map(Number) : null;
const html = opt('src') ? resolve(opt('src')) : join(dirname(fileURLToPath(import.meta.url)), 'claude-usage-video.html');
const ffmpegBin = process.env.FFMPEG || 'ffmpeg';

const port = 9400 + Math.floor(Math.random() * 400);
const chrome = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
  '--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${mkdtempSync(join(process.env.TMPDIR || tmpdir(), 'cdp-'))}`,
  '--hide-scrollbars', '--no-first-run', '--no-default-browser-check', '--force-color-profile=srgb', '--font-render-hinting=none',
  'about:blank'], { stdio: 'ignore' });
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
await send('Emulation.setDeviceMetricsOverride', { width: SIZE, height: SIZE, deviceScaleFactor: stills ? 1 : scale, mobile: false });
await send('Page.navigate', { url: pathToFileURL(html).href + '?record' });
for (let i = 0; i < 100; i++) { if (await evaluate(`typeof window.__seek === 'function'`)) break; await sleep(100); }
await evaluate('window.__ready');
await sleep(300);
const duration = await evaluate('window.__duration');

const times = stills || Array.from({ length: Math.round(duration * fps) }, (_, i) => i / fps);
const vf = stills
  ? `scale=540:540,tile=${Math.min(3, times.length)}x${Math.ceil(times.length / 3)}`
  : `scale=${SIZE}:${SIZE}:flags=lanczos,format=yuv420p`;
const ff = spawn(ffmpegBin, ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(stills ? 1 : fps), '-i', '-',
  '-vf', vf, ...(stills ? ['-frames:v', '1'] : ['-c:v', 'libx264', '-preset', 'slow', '-crf', '17', '-profile:v', 'high', '-movflags', '+faststart']), out],
  { stdio: ['pipe', 'inherit', 'inherit'] });
const done = new Promise((res, rej) => ff.on('close', code => code === 0 ? res() : rej(new Error('ffmpeg exited with ' + code))));

const started = Date.now();
for (let i = 0; i < times.length; i++) {
  await evaluate(`window.__seek(${times[i]})`);
  const shot = await send('Page.captureScreenshot', { format: stills ? 'png' : 'jpeg', quality: stills ? undefined : 95, optimizeForSpeed: true });
  if (!ff.stdin.write(Buffer.from(shot.data, 'base64'))) await new Promise(r => ff.stdin.once('drain', r));
  if (!stills && i % 150 === 0) console.log(`frame ${i}/${times.length}  ${((Date.now() - started) / 1000).toFixed(0)}s`);
}
ff.stdin.end();
await done;
ws.close(); chrome.kill();
console.log('wrote', out);
