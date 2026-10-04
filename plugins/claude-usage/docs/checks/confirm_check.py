#!/usr/bin/env python3
"""Check that apply.py asks at the terminal and only there (macOS and Linux; a development check, not part of the plugin).

    python3 docs/checks/confirm_check.py [--dir <report dir>] [--id <optimization id>]

It copies the report folder (default ~/.claude-usage, without applied/) into a temporary home with an empty Claude
folder, picks an optimization that changes settings.json (or --id), and runs apply.py there:
  - with no terminal (stdin a pipe, new session): exit 3, nothing changed;
  - on a pseudo-terminal (pty.fork, its controlling terminal), answering n, then Ctrl-C: cancelled, nothing changed;
  - answering y: settings.json changes; then undo, answering y: settings.json is back as it was.
Your own Claude folder and report are never touched. Exits 1 on the first failure.
"""
import argparse
import os
import pty
import shutil
import subprocess
import sys
import tempfile
import time

APPLY = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'scripts', 'apply.py')


def on_pty(args, answer, env):
    """Run apply.py with a pty as its controlling terminal; answer the [y/N] prompt. Returns (exit code, output, asked)."""
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(sys.executable, [sys.executable, APPLY] + args, env)
    out, asked, end = b'', False, time.time() + 60
    while time.time() < end:
        try:
            d = os.read(fd, 4096)
        except OSError:                                     # EIO on Linux once the child is gone
            break
        if not d:
            break
        out += d
        if not asked and b'[y/N]' in out:
            os.write(fd, b'\x03' if answer == 'ctrl-c' else answer.encode() + b'\n')
            asked = True
    _, st = os.waitpid(pid, 0)
    return os.WEXITSTATUS(st) if os.WIFEXITED(st) else -1, out.decode('utf-8', 'replace'), asked


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dir', default=os.path.expanduser('~/.claude-usage'))
    ap.add_argument('--id')
    a = ap.parse_args()
    if os.name == 'nt':
        sys.exit('This check needs a pseudo-terminal (macOS or Linux).')
    tmp = tempfile.mkdtemp(prefix='confirm-check-')
    try:
        home, claude = tmp, os.path.join(tmp, '.claude')
        out = os.path.join(home, '.claude-usage')
        shutil.copytree(a.dir, out, ignore=shutil.ignore_patterns('applied'))
        os.makedirs(claude)
        settings = os.path.join(claude, 'settings.json')
        with open(settings, 'w') as f:
            f.write('{}\n')
        env = {k: v for k, v in os.environ.items() if k not in ('CLAUDE_USAGE_OUT', 'CLAUDE_CONFIG_DIR')}
        env['HOME'] = home
        base = ['--dir', out, '--claude-dir', claude]
        oid = a.id
        if not oid:
            chk = subprocess.run([sys.executable, APPLY, 'check'] + base, env=env, capture_output=True, text=True)
            oid = next((ln.split()[1] for ln in chk.stdout.splitlines()
                        if ln.split()[:1] == ['ok'] and 'settings.json' in ln), None)
            if not oid:
                sys.exit('No optimization in this report changes settings.json; pass --id.')
        before = open(settings).read()

        def expect(name, ok, detail=''):
            print(('PASS  ' if ok else 'FAIL  ') + name)
            if not ok:
                print(detail[-1500:])
                raise SystemExit(1)

        r = subprocess.run([sys.executable, APPLY, 'apply', oid] + base, env=env, input='y\n', capture_output=True,
                           text=True, start_new_session=True)
        expect(f'no terminal: {oid} exits 3, nothing changed', r.returncode == 3 and open(settings).read() == before,
               r.stdout + r.stderr)
        for answer in ('n', 'ctrl-c'):
            code, text, asked = on_pty(['apply', oid] + base, answer, env)
            expect(f'terminal, answer {answer}: asked, cancelled, nothing changed',
                   asked and code == 0 and 'Cancelled' in text and open(settings).read() == before, text)
        code, text, asked = on_pty(['apply', oid] + base, 'y', env)
        expect('terminal, answer y: applied', asked and code == 0 and open(settings).read() != before, text)
        code, text, asked = on_pty(['undo', oid] + base, 'y', env)
        expect('terminal, undo y: settings.json back as it was', asked and code == 0 and open(settings).read() == before,
               text)
        print(f'OK: {oid} asked at the terminal, and only there.')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
