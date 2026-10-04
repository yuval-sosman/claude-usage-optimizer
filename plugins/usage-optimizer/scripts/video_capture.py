#!/usr/bin/env python3
"""Record a timeline page to MP4 (or to still PNGs) with a headless Chromium browser and ffmpeg.

The page must define window.__duration (seconds), window.__ready (a promise: fonts loaded) and window.__seek(t), which
draws the frame at time t; it is loaded with ?record. Every frame is drawn on demand and captured, so the video is exact
and doesn't depend on the machine's speed. Frames are drawn at `scale` × the page size and scaled down, for sharper text.

  python3 video_capture.py page.html out.mp4 [--fps 30] [--scale 2]
  python3 video_capture.py page.html stills/ --stills 3,12.5,40

Talks to the browser over the Chrome DevTools protocol: through a private pipe on macOS and Linux (no port another
program could connect to), and a WebSocket on 127.0.0.1 on Windows. The browser runs with a throwaway profile and no
network at all (a dead proxy, no DNS, background services off); the page itself makes no requests either (its CSP).
Standard library only; works with Chrome, Edge, Chromium and Brave on macOS, Linux and Windows. Set CLAUDE_USAGE_BROWSER /
FFMPEG to use a specific binary (it must be a browser / ffmpeg by name). Nothing is ever installed: install_hint() only
says how the user can install either on this machine.
"""
import argparse
import base64
import errno
import glob
import json
import os
import pathlib
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

ROLE = {'browser': 'draws each frame', 'ffmpeg': 'encodes the frames into the MP4'}
MANUAL = {'browser': 'https://www.google.com/chrome/ (or Edge, Chromium, Brave; or set CLAUDE_USAGE_BROWSER to its program)',
          'ffmpeg': 'https://ffmpeg.org/download.html (or set FFMPEG to the program)'}
WINGET = ' -e --accept-source-agreements --accept-package-agreements'
INSTALL = {       # package manager: {tool: command}; a command with sudo, or choco (an admin shell), is one the user runs
    'brew': {'ffmpeg': 'brew install ffmpeg', 'browser': 'brew install --cask google-chrome'},
    'winget': {'ffmpeg': 'winget install --id Gyan.FFmpeg' + WINGET, 'browser': 'winget install --id Google.Chrome' + WINGET},
    'scoop': {'ffmpeg': 'scoop install ffmpeg'},
    'choco': {'ffmpeg': 'choco install ffmpeg -y', 'browser': 'choco install googlechrome -y'},
    'apt-get': {'ffmpeg': 'sudo apt-get install -y ffmpeg',
                'browser': 'cd /tmp && curl -fsSLO https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb && '
                           'sudo apt-get install -y ./google-chrome-stable_current_amd64.deb'},
    'dnf': {'ffmpeg': 'sudo dnf install -y ffmpeg', 'browser': 'sudo dnf install -y https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm'},
    'pacman': {'ffmpeg': 'sudo pacman -S --noconfirm ffmpeg', 'browser': 'sudo pacman -S --noconfirm chromium'},
    'zypper': {'ffmpeg': 'sudo zypper install -y ffmpeg', 'browser': 'sudo zypper install -y chromium'},
    'apk': {'ffmpeg': 'sudo apk add ffmpeg', 'browser': 'sudo apk add chromium'},
}
MANAGERS = {'darwin': ['brew'], 'win32': ['winget', 'scoop', 'choco']}             # anything else: the Linux ones


def _manager(name):
    if name == 'brew':
        return shutil.which('brew') or next((p for p in ('/opt/homebrew/bin/brew', '/usr/local/bin/brew') if os.path.isfile(p)), None)
    return shutil.which(name)


def install_hint(tool):
    """How the user can install 'ffmpeg' or 'browser' on this machine: {'command', 'url'}; command is None when no package
    manager is found. Printed for the user to run themselves, never run by this plugin."""
    names = MANAGERS.get(sys.platform, ['apt-get', 'dnf', 'pacman', 'zypper', 'apk'])
    for name in names:
        cmd = INSTALL.get(name, {}).get(tool)
        if cmd and _manager(name):
            if cmd.startswith('brew ') and not shutil.which('brew'):
                cmd = _manager('brew') + cmd[4:]          # Homebrew is installed but not on this shell's PATH
            if hasattr(os, 'geteuid') and os.geteuid() == 0:
                cmd = cmd.replace('sudo ', '')
            return {'command': cmd, 'url': MANUAL[tool]}
    return {'command': None, 'url': MANUAL[tool]}


def missing(tool):
    """One sentence for a missing tool: what it's for and how to get it."""
    h = install_hint(tool)
    how = f'you can install it with `{h["command"]}`' if h['command'] else f'get it from {h["url"]}'
    return f'No {"Chromium-based browser" if tool == "browser" else "ffmpeg"} (it {ROLE[tool]}): {how}.'


BROWSER_NAMES = {'google chrome', 'google chrome canary', 'google chrome beta', 'google-chrome', 'google-chrome-stable',
                 'google-chrome-beta', 'google-chrome-unstable', 'chrome', 'chromium', 'chromium-browser', 'microsoft edge',
                 'microsoft-edge', 'microsoft-edge-stable', 'microsoft-edge-beta', 'msedge', 'brave browser', 'brave-browser',
                 'brave', 'chrome-headless-shell'}


def _named(path, names):
    """Whether a program is one of these by its file name (so --browser / --ffmpeg can't launch anything else)."""
    base = os.path.basename(path or '').lower()
    return (base[:-4] if base.endswith('.exe') else base) in names


def find_browser(explicit=None):
    """A Chromium-based browser that can run headless: the one given, $CLAUDE_USAGE_BROWSER / $CHROME_PATH, else the usual
    places. Only a program named like one of BROWSER_NAMES."""
    cands = [explicit, os.environ.get('CLAUDE_USAGE_BROWSER'), os.environ.get('CHROME_PATH')]
    if sys.platform == 'darwin':
        for app in ('Google Chrome', 'Chromium', 'Microsoft Edge', 'Brave Browser'):
            for base in ('/Applications', os.path.expanduser('~/Applications')):
                cands.append(os.path.join(base, app + '.app', 'Contents', 'MacOS', app))
    elif os.name == 'nt':
        for var in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
            base = os.environ.get(var)
            if base:
                cands += [os.path.join(base, 'Google', 'Chrome', 'Application', 'chrome.exe'),
                          os.path.join(base, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
                          os.path.join(base, 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe')]
    for name in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'microsoft-edge',
                 'microsoft-edge-stable', 'brave-browser', 'chrome', 'msedge'):
        cands.append(shutil.which(name))
    for c in cands:
        if c and _named(c, BROWSER_NAMES) and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def find_ffmpeg(explicit=None):
    """ffmpeg: the one given, $FFMPEG, on PATH, else where installers put it (a fresh install isn't on this shell's PATH yet)."""
    cands = [explicit, os.environ.get('FFMPEG'), shutil.which('ffmpeg')]
    if os.name == 'nt':
        local = os.environ.get('LOCALAPPDATA') or ''
        cands += [os.path.join(local, 'Microsoft', 'WinGet', 'Links', 'ffmpeg.exe'),
                  os.path.join(os.path.expanduser('~'), 'scoop', 'shims', 'ffmpeg.exe'),
                  os.path.join(os.environ.get('PROGRAMDATA') or 'C:\\ProgramData', 'chocolatey', 'bin', 'ffmpeg.exe')]
        cands += sorted(glob.glob(os.path.join(local, 'Microsoft', 'WinGet', 'Packages', 'Gyan.FFmpeg*', '*', 'bin', 'ffmpeg.exe')))
    else:
        cands += ['/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg', '/usr/bin/ffmpeg', '/snap/bin/ffmpeg']
    for c in cands:
        if c and _named(c, {'ffmpeg'}) and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def h264_encoder(ffmpeg):
    """The best H.264 encoder this ffmpeg has (builds differ), else MPEG-4 part 2, which every build has."""
    try:
        listing = subprocess.run([ffmpeg, '-hide_banner', '-encoders'], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        listing = ''
    for enc in ('libx264', 'libopenh264', 'h264_videotoolbox', 'h264_mf'):
        if f' {enc} ' in listing:
            return enc
    return 'mpeg4'


class DevTools:
    """One page's DevTools session over a plain WebSocket (RFC 6455: masked client frames, unmasked server frames)."""

    def __init__(self, ws_url, timeout=60):
        rest = ws_url.split('://', 1)[1]
        hostport, path = rest.split('/', 1)
        host, port = hostport.rsplit(':', 1)
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f'GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                           f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        self.buf = bytearray()
        while b'\r\n\r\n' not in self.buf:
            self._fill()
        end = self.buf.index(b'\r\n\r\n')
        status = bytes(self.buf[:end]).split(b'\r\n', 1)[0]
        del self.buf[:end + 4]
        if b' 101' not in status:
            raise ConnectionError('the browser refused the DevTools connection: ' + status.decode(errors='replace'))
        self.seq = 0

    def _fill(self):
        chunk = self.sock.recv(1 << 20)
        if not chunk:
            raise ConnectionError('the browser closed the DevTools connection')
        self.buf += chunk

    def _take(self, n):
        while len(self.buf) < n:
            self._fill()
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def _send(self, payload, op=1):
        n = len(payload)
        head = bytearray([0x80 | op])
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += struct.pack('>H', n)
        else:
            head.append(0x80 | 127)
            head += struct.pack('>Q', n)
        mask = os.urandom(4)
        body = (int.from_bytes(payload, 'big') ^ int.from_bytes((mask * (n // 4 + 1))[:n], 'big')).to_bytes(n, 'big') if n else b''
        self.sock.sendall(bytes(head) + mask + body)

    def _message(self):
        parts = []
        while True:
            b1, b2 = self._take(2)
            op, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack('>H', self._take(2))[0]
            elif n == 127:
                n = struct.unpack('>Q', self._take(8))[0]
            mask = self._take(4) if b2 & 0x80 else None
            data = self._take(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 9:                                   # ping
                self._send(data, 0xA)
                continue
            if op == 10:                                  # pong
                continue
            if op == 8:
                raise ConnectionError('the browser closed the DevTools connection')
            parts.append(data)
            if b1 & 0x80:
                return b''.join(parts)

    session = None                                        # a flat-mode target session (the pipe transport)

    def call(self, method, **params):
        self.seq += 1
        msg = {'id': self.seq, 'method': method, 'params': params}
        if self.session and not method.startswith('Target.'):
            msg['sessionId'] = self.session
        self._send(json.dumps(msg).encode())
        while True:
            msg = json.loads(self._message())
            if msg.get('id') == self.seq:
                if 'error' in msg:
                    raise RuntimeError(f'{method}: {msg["error"].get("message")}')
                return msg.get('result') or {}

    def evaluate(self, expr):
        r = self.call('Runtime.evaluate', expression=expr, awaitPromise=True, returnByValue=True)
        if r.get('exceptionDetails'):
            d = r['exceptionDetails']
            raise RuntimeError('page error: ' + ((d.get('exception') or {}).get('description') or d.get('text') or '?'))
        return (r.get('result') or {}).get('value')

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class PipeDevTools(DevTools):
    """The same session over --remote-debugging-pipe: the browser reads commands on its fd 3 and writes replies on fd 4,
    each a JSON message ending in a NUL byte. No port is opened, so no other program can reach the browser."""

    def __init__(self, wfd, rfd):
        self.wfd, self.rfd, self.buf, self.seq = wfd, rfd, bytearray(), 0

    def _send(self, payload, op=1):
        data = payload + b'\0'
        while data:
            data = data[os.write(self.wfd, data):]

    def _message(self):
        while b'\0' not in self.buf:
            try:
                chunk = os.read(self.rfd, 1 << 20)
            except OSError as e:
                if e.errno == errno.EINTR:
                    continue
                raise
            if not chunk:
                raise ConnectionError('the browser closed the DevTools pipe')
            self.buf += chunk
        end = self.buf.index(b'\0')
        msg = bytes(self.buf[:end])
        del self.buf[:end + 1]
        return msg

    def close(self):
        for fd in (self.wfd, self.rfd):
            try:
                os.close(fd)
            except OSError:
                pass


PRIVATE = ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--disable-default-apps',
           '--disable-domain-reliability', '--disable-client-side-phishing-detection', '--disable-breakpad', '--no-pings',
           '--metrics-recording-only', '--disable-features=Translate,OptimizationHints,MediaRouter,AutofillServerCommunication',
           '--proxy-server=http://127.0.0.1:9', '--proxy-bypass-list=<-loopback>', '--host-resolver-rules=MAP * ~NOTFOUND']
# no network: every request goes to a proxy that isn't there, no name resolves, and the background services are off


class Browser:
    """A headless browser with one page, sized to the video frame, with a throwaway profile and no network."""

    def __init__(self, exe, width, height, scale, log=None):
        self.tmp = tempfile.mkdtemp(prefix='claude-usage-video-')
        pipe = os.name != 'nt'                          # Windows can't hand the browser fds 3 and 4
        args = [exe, '--headless=new', '--remote-debugging-pipe' if pipe else '--remote-debugging-port=0',
                f'--user-data-dir={self.tmp}', '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
                '--disable-extensions', '--mute-audio', '--force-color-profile=srgb', '--font-render-hinting=none',
                f'--window-size={width},{height}'] + PRIVATE
        if hasattr(os, 'geteuid') and os.geteuid() == 0:
            args.append('--no-sandbox')                 # Chrome refuses to run as root with its sandbox (containers)
        if pipe:
            self._start_pipe(args + ['about:blank'])
        else:
            self._start_port(args + ['about:blank'], exe)
        self.dt.call('Emulation.setDeviceMetricsOverride', width=width, height=height, deviceScaleFactor=scale, mobile=False)

    def _start_pipe(self, args):
        import fcntl
        cmd_r, cmd_w = os.pipe()                        # we write commands, the browser reads them on fd 3
        out_r, out_w = os.pipe()                        # the browser writes replies on fd 4, we read them
        hi = [fcntl.fcntl(fd, fcntl.F_DUPFD, 10) for fd in (cmd_r, out_w)]     # above 4, so the dup2s can't clash

        def child():
            os.dup2(hi[0], 3)
            os.dup2(hi[1], 4)
        try:
            self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         pass_fds=(3, 4), preexec_fn=child)
        finally:
            for fd in [cmd_r, out_w] + hi:
                os.close(fd)
        self.dt = PipeDevTools(cmd_w, out_r)
        page = None
        for _ in range(100):
            page = next((t for t in self.dt.call('Target.getTargets').get('targetInfos') or [] if t.get('type') == 'page'), None)
            if page or self.proc.poll() is not None:
                break
            time.sleep(0.1)
        if not page:
            self.quit()
            raise RuntimeError('the browser opened no page')
        self.dt.session = self.dt.call('Target.attachToTarget', targetId=page['targetId'], flatten=True)['sessionId']

    def _start_port(self, args, exe):
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file = os.path.join(self.tmp, 'DevToolsActivePort')
        for _ in range(300):
            if os.path.exists(port_file):
                with open(port_file, encoding='utf-8') as fh:
                    first = fh.readline().strip()
                if first.isdigit():
                    break
            if self.proc.poll() is not None:
                raise RuntimeError(f'the browser exited at start ({exe})')
            time.sleep(0.1)
        else:
            self.quit()
            raise RuntimeError('the browser did not open a DevTools port within 30 s')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))        # never through a proxy: it's localhost
        page = None
        for _ in range(100):
            try:
                targets = json.loads(opener.open(f'http://127.0.0.1:{first}/json/list', timeout=5).read())
                page = next((t for t in targets if t.get('type') == 'page'), None)
            except OSError:
                page = None
            if page:
                break
            time.sleep(0.1)
        if not page:
            self.quit()
            raise RuntimeError('the browser opened no page')
        self.dt = DevTools(page['webSocketDebuggerUrl'])

    def load(self, html):
        self.dt.call('Page.navigate', url=pathlib.Path(os.path.abspath(html)).as_uri() + '?record')
        for _ in range(300):
            try:
                if self.dt.evaluate("document.readyState === 'complete' && typeof window.__seek === 'function'"):
                    break
            except RuntimeError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError('the video page did not load (no window.__seek)')
        self.dt.evaluate('window.__ready')
        return float(self.dt.evaluate('window.__duration'))

    def frame(self, t, fmt='jpeg'):
        self.dt.evaluate(f'window.__seek({t:.4f})')
        params = {'format': fmt, 'optimizeForSpeed': True}
        if fmt == 'jpeg':
            params['quality'] = 94
        return base64.b64decode(self.dt.call('Page.captureScreenshot', **params)['data'])

    def quit(self):
        if getattr(self, 'dt', None):
            self.dt.close()
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        for _ in range(20):                               # Windows keeps the profile locked for a moment after exit
            shutil.rmtree(self.tmp, ignore_errors=True)
            if not os.path.exists(self.tmp):
                break
            time.sleep(0.2)


def record(html, mp4, browser, ffmpeg, size=1080, fps=30, scale=2.0, log=None):
    """Draw every frame of the page and encode it to an H.264 MP4 at size × size."""
    log = log or (lambda *_: None)
    b = Browser(browser, size, size, scale)
    enc = None
    try:
        duration = b.load(html)
        n = int(round(duration * fps))
        enc = h264_encoder(ffmpeg)
        tmp_out = mp4 + '.part.mp4'
        cmd = [ffmpeg, '-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', str(fps), '-i', '-',
               '-vf', f'scale={size}:{size}:flags=lanczos,format=yuv420p', '-c:v', enc]
        cmd += {'libx264': ['-preset', 'medium', '-crf', '18', '-profile:v', 'high'], 'mpeg4': ['-q:v', '3']}.get(enc, ['-b:v', '6M'])
        cmd += ['-r', str(fps), '-movflags', '+faststart', tmp_out]
        ff = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        t0, shown = time.time(), 0
        try:
            for i in range(n):
                ff.stdin.write(b.frame(i / fps))
                if time.time() - shown > 10:
                    shown = time.time()
                    log(f'  frame {i + 1}/{n} ({(time.time() - t0):.0f} s)')
            ff.stdin.close()
        except BrokenPipeError:
            pass
        err = ff.stderr.read().decode(errors='replace')
        if ff.wait() != 0:
            raise RuntimeError(f'ffmpeg failed ({enc}): {err.strip()[-600:]}')
        os.replace(tmp_out, mp4)
        log(f'  {n} frames in {time.time() - t0:.0f} s, encoded with {enc}')
        return {'frames': n, 'duration': duration, 'encoder': enc}
    finally:
        b.quit()
        if enc and os.path.exists(mp4 + '.part.mp4'):
            os.remove(mp4 + '.part.mp4')


def stills(html, times, outdir, browser, size=1080, scale=0.5):
    """PNGs of chosen moments (default half size), for a quick look at the layout."""
    os.makedirs(outdir, exist_ok=True)
    b = Browser(browser, size, size, scale)
    paths = []
    try:
        duration = b.load(html)
        for t in times:
            t = max(0.0, min(duration, t))
            p = os.path.join(outdir, f'frame-{t:05.1f}s.png')
            with open(p, 'wb') as fh:
                fh.write(b.frame(t, 'png'))
            paths.append(p)
    finally:
        b.quit()
    return paths


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('html')
    ap.add_argument('out', help='the MP4 to write, or a folder with --stills')
    ap.add_argument('--fps', type=int, default=30)
    ap.add_argument('--scale', type=float, default=2.0)
    ap.add_argument('--stills', help='comma-separated seconds: write PNGs of those moments instead of a video')
    ap.add_argument('--browser')
    ap.add_argument('--ffmpeg')
    a = ap.parse_args(argv)
    browser = find_browser(a.browser)
    if not browser:
        sys.exit(missing('browser'))
    if a.stills:
        for p in stills(a.html, [float(x) for x in a.stills.split(',')], a.out, browser):
            print(p)
        return
    ffmpeg = find_ffmpeg(a.ffmpeg)
    if not ffmpeg:
        sys.exit(missing('ffmpeg'))
    record(a.html, a.out, browser, ffmpeg, fps=a.fps, scale=a.scale, log=lambda *x: print(*x, file=sys.stderr))
    print(a.out)


if __name__ == '__main__':
    main()
