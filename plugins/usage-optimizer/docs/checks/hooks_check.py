#!/usr/bin/env python3
"""Check the bundled hooks on synthetic transcripts (a development check, not part of the plugin; no real transcript is read).

    python3 docs/checks/hooks_check.py [--hooks <folder>]        default: the plugin's scripts/hooks

Each case writes a small transcript into a temporary folder, runs the real hook scripts on it (state in a scratch
CLAUDE_USAGE_HOOK_STATE) and checks what they say:
  - a big context idle past its lifetime: the stale-cache guard blocks once, the status line says cold;
  - the same transcript right after /compact (a compact_boundary, no call yet): nothing blocks, the status line shows
    "compacted … ago · cache warm";
  - the first call after the compact: as before the compact, with the new size;
  - a record holding a raw U+2028 (which str.splitlines() would cut): the newest call is still found;
  - lines that are valid JSON but not objects, and odd timestamps: skipped, never an error;
  - usage without the cache_creation breakdown: a 5-minute lifetime, like the engine;
  - a /rewind (the newest user record points at an older assistant message): the checkpoint's context, not the
    abandoned branch's; a record pointing at the newest call, or at a user record, changes nothing;
  - the big-read guard: two parallel Reads of two big files each get one deny, the retry of either goes through; a
    folder is never denied.
Exits 1 on the first failure.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = time.time()


def iso(t):
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(t)) + '.%03dZ' % int(t * 1000 % 1000)


def call(t, ctx, cw1=None, cw5=None, extra=None, text='ok'):
    u = {'input_tokens': 1000, 'cache_read_input_tokens': max(0, ctx - 1000 - (cw1 or 0) - (cw5 or 0)),
         'cache_creation_input_tokens': (cw1 or 0) + (cw5 or 0), 'output_tokens': 50}
    if cw1 is not None or cw5 is not None:
        u['cache_creation'] = {'ephemeral_1h_input_tokens': cw1 or 0, 'ephemeral_5m_input_tokens': cw5 or 0}
    d = {'type': 'assistant', 'timestamp': iso(t), 'sessionId': 's', 'requestId': 'r%d' % int(t), 'uuid': 'u%d' % int(t),
         'message': {'id': 'm%d' % int(t), 'model': 'claude-opus-5-5', 'usage': u, 'content': [{'type': 'text', 'text': text}]}}
    if extra:
        d.update(extra)
    return d


def boundary(t):
    return {'type': 'system', 'subtype': 'compact_boundary', 'timestamp': iso(t), 'sessionId': 's', 'uuid': 'b',
            'compactMetadata': {'trigger': 'manual', 'preTokens': 140000, 'postTokens': 12000, 'durationMs': 90000}}


def user(t, text, parent=None):
    d = {'type': 'user', 'timestamp': iso(t), 'sessionId': 's', 'uuid': 'x%d' % int(t), 'message': {'role': 'user', 'content': text}}
    if parent:
        d['parentUuid'] = parent
    return d


class Run:
    def __init__(self, hooks):
        self.hooks = hooks
        self.tmp = tempfile.mkdtemp(prefix='hooks-check-')
        self.state = os.path.join(self.tmp, 'state')
        self.n = 0
        self.fails = 0

    def transcript(self, records):
        self.n += 1
        p = os.path.join(self.tmp, 't%d.jsonl' % self.n)
        with open(p, 'w', encoding='utf-8') as fh:
            for r in records:
                fh.write((r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)) + '\n')
        return p

    def hook(self, script, payload, args=(), fresh=True):
        if fresh:
            shutil.rmtree(self.state, ignore_errors=True)
        env = dict(os.environ, CLAUDE_USAGE_HOOK_STATE=self.state)
        r = subprocess.run([sys.executable, '-S', os.path.join(self.hooks, script)] + list(args), input=json.dumps(payload),
                           capture_output=True, text=True, env=env, timeout=30)
        if r.returncode != 0:
            self.fail('%s exited %d: %s' % (script, r.returncode, r.stderr.strip()[-300:]))
        return r.stdout.strip()

    def guard(self, path, fresh=True):
        return self.hook('stale_cache_guard.py', {'prompt': 'hi', 'session_id': 'sid', 'transcript_path': path},
                         ['--min-context', '60000', '--grace', '180'], fresh)

    def status(self, path, extra=None):
        d = {'session_id': 'sid', 'transcript_path': path, 'model': {'display_name': 'Opus'}}
        d.update(extra or {})
        return self.hook('statusline.py', d)

    def fail(self, msg):
        self.fails += 1
        print('FAIL  ' + msg)

    def check(self, name, ok, got=''):
        print(('PASS  ' if ok else 'FAIL  ') + name + ('' if ok else '   got: %r' % got))
        if not ok:
            self.fails += 1

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--hooks', default=os.path.join(HERE, '..', '..', 'scripts', 'hooks'))
    a = ap.parse_args()
    R = Run(os.path.abspath(a.hooks))
    try:
        old = NOW - 2 * 3600                                     # the session's last call: 2 h ago, 1-hour cache, 141K
        before = [user(old - 60, 'go'), call(old - 30, 140000, cw1=2000), call(old, 141000, cw1=1000)]
        pre = R.transcript(before)
        out = R.guard(pre)
        R.check('idle big session: the guard blocks', '"block"' in out and '141K' in out, out)
        out = R.guard(pre, fresh=False)
        R.check('sent again within the grace: goes through', out == '', out)
        out = R.status(pre)
        R.check('status line before the compact: cold, re-writes 141K', 'ctx 141K' in out and 're-writes 141K' in out, out)

        tb = NOW - 180                                           # /compact finished 3 minutes ago; no call since
        compacted = R.transcript(before + [boundary(tb), user(tb + 1, 'This session is being continued from a previous conversation…')])
        out = R.guard(compacted)
        R.check('right after /compact: the guard lets the prompt through', out == '', out)
        out = R.hook('context_guard.py', {'prompt': 'hi', 'session_id': 'sid', 'transcript_path': compacted}, ['--threshold', '100000'])
        R.check('right after /compact: the context notice stays quiet', out == '', out)
        out = R.status(compacted)
        R.check('status line after /compact: compacted 3 min ago, cache warm', 'compacted 3 min ago' in out and ('cache warm 57 min' in out or 'cache warm 56 min' in out)
                and 'ctx' not in out, out)
        out = R.status(compacted, {'prompt_cache': {'expires_at': NOW + 3000, 'recache_tokens_if_cold': 20000}})
        R.check("status line keeps Claude Code's own cache figures after a compact", ('cache warm 50 min' in out or 'cache warm 49 min' in out), out)
        out = R.status(compacted, {'prompt_cache': {'expires_at': NOW - 10, 'recache_tokens_if_cold': 20000}})
        R.check("status line keeps Claude Code's own cold figure after a compact", 're-writes 20K' in out, out)

        post = R.transcript(before + [boundary(tb), user(tb + 1, 'continued'), call(NOW - 100, 57000, cw1=50000)])
        out = R.guard(post)
        R.check('first call after the compact (57K, under 60K): goes through', out == '', out)
        out = R.status(post)
        R.check('status line after that call: ctx 57K, cache warm', 'ctx 57K' in out and 'cache warm 58 min' in out, out)
        big = R.transcript(before + [boundary(tb), user(tb + 1, 'continued'), call(NOW - 2 * 3600, 90000, cw1=80000)])
        out = R.guard(big)
        R.check('a big, idle post-compact context blocks again with the new size', '"block"' in out and '90K' in out, out)

        sep = R.transcript(before + [call(NOW - 60, 20000, cw1=19000, text='line one line two\u0085three four')])
        out = R.guard(sep)
        R.check('a record with a raw U+2028/NEL is read whole: the fresh 20K call is found', out == '', out)
        out = R.status(sep)
        R.check('status line sees that call', 'ctx 20K' in out and 'cache warm' in out, out)

        odd = R.transcript(before + [call(NOW - 60, 20000, cw1=19000), '["compact_boundary"]', '"assistant usage"', 'null',
                                     '[' * 3000 + ']' * 3000, '{"type":"assistant","message":{"usage":5}}'])
        out = R.guard(odd)
        R.check('non-object JSON lines and a deep one above the call are skipped', out == '', out)
        R.check('status line is not blanked by them', 'ctx 20K' in R.status(odd), R.status(odd))
        side = R.transcript(before + [dict(boundary(NOW - 10), isSidechain=True)])
        out = R.guard(side)
        R.check("a subagent's boundary doesn't hide the main thread's idle call", '"block"' in out, out)

        # a /rewind: calls of 30K, 90K and 180K, then the user's next record points at the 90K call (the branch stays in the file)
        c1, c2, c3 = call(NOW - 400, 30000, cw1=30000), call(NOW - 300, 90000, cw1=60000), call(NOW - 200, 180000, cw1=90000)
        rw = R.transcript([user(NOW - 410, 'a'), c1, user(NOW - 310, 'b', c1['uuid']), c2, user(NOW - 210, 'c', c2['uuid']), c3,
                           user(NOW - 100, 'rewound: go on from here', c2['uuid'])])
        out = R.status(rw)
        R.check('after a /rewind the status line shows the checkpoint context (90K), not the branch (180K)', 'ctx 90K' in out and 'cache warm' in out, out)
        out = R.hook('context_guard.py', {'prompt': 'hi', 'session_id': 'sid', 'transcript_path': rw}, ['--threshold', '100000'])
        R.check('after a /rewind the context notice judges 90K (quiet under 100K)', out == '', out)
        plain = R.transcript([user(NOW - 410, 'a'), c1, user(NOW - 310, 'b', c1['uuid']), c2, user(NOW - 210, 'c', c2['uuid']), c3,
                              user(NOW - 100, 'tool result', c3['uuid'])])
        R.check('a user record pointing at the newest call is not a rewind', 'ctx 180K' in R.status(plain), R.status(plain))
        meta = R.transcript([user(NOW - 410, 'a'), c1, user(NOW - 310, 'b', c1['uuid']), c2, user(NOW - 210, 'c', c2['uuid']), c3,
                             user(NOW - 100, 'meta', 'x%d' % int(NOW - 210))])
        R.check('a user record whose parent is a user record changes nothing', 'ctx 180K' in R.status(meta), R.status(meta))

        ts = R.transcript(before + [call(NOW - 60, 20000, cw1=19000, extra={'timestamp': iso(NOW - 60)[:-1] + '123456Z'})])
        R.check('a 9-digit fraction timestamp parses', 'ctx 20K' in R.status(ts) and 'cache warm' in R.status(ts), R.status(ts))
        ts2 = R.transcript(before + [call(NOW - 60, 20000, cw1=19000, extra={'timestamp': int((NOW - 60) * 1000)})])
        R.check('an epoch-ms timestamp parses', 'cache warm' in R.status(ts2), R.status(ts2))

        five = R.transcript([user(NOW - 700, 'go'), call(NOW - 650, 100000, cw5=90000), call(NOW - 600, 141000, cw5=1000)])
        out = R.guard(five)
        R.check('5-minute writes: idle 10 min is past the lifetime, blocks', '"block"' in out and '5 min' in out, out)
        nobreak = R.transcript([user(NOW - 700, 'go'), call(NOW - 650, 100000), call(NOW - 600, 141000)])
        with open(nobreak, encoding='utf-8') as fh:            # strip the breakdown: cache_creation_input_tokens only
            recs = [json.loads(ln) for ln in fh]
        for r in recs:
            u = (r.get('message') or {}).get('usage')
            if u:
                u.pop('cache_creation', None)
                u['cache_creation_input_tokens'] = 1000
        nobreak = R.transcript(recs)
        out = R.guard(nobreak)
        R.check('no cache_creation breakdown: a 5-minute lifetime, like the engine', '"block"' in out and '5 min' in out, out)

        d = os.path.join(R.tmp, 'bigs')
        os.makedirs(d)
        files = []
        for name in ('x.txt', 'y.txt'):
            p = os.path.join(d, name)
            with open(p, 'w') as fh:
                fh.write('a' * 100 + '\n')
                fh.write(('b' * 100 + '\n') * 1000)
            files.append(p)
        shutil.rmtree(R.state, ignore_errors=True)
        env = dict(os.environ, CLAUDE_USAGE_HOOK_STATE=R.state)
        procs = [subprocess.Popen([sys.executable, '-S', os.path.join(R.hooks, 'big_read_guard.py'), '--max-kb', '60'],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env) for _ in files]
        outs = [pr.communicate(json.dumps({'tool_name': 'Read', 'session_id': 'sid', 'tool_input': {'file_path': f}}))[0] for pr, f in zip(procs, files)]
        R.check('parallel whole Reads of two big files: both denied once', all('deny' in o for o in outs), outs)
        again = [R.hook('big_read_guard.py', {'tool_name': 'Read', 'session_id': 'sid', 'tool_input': {'file_path': f}}, ['--max-kb', '60'], fresh=False)
                 for f in files]
        R.check('the retry of each goes through', all(o == '' for o in again), again)
        R.check("'1,001 lines' is counted", '1,001 lines' in outs[0], outs[0])
        out = R.hook('big_read_guard.py', {'tool_name': 'Read', 'session_id': 'sid', 'tool_input': {'file_path': d}}, ['--max-kb', '0'])
        R.check('a folder is never denied', out == '', out)
    finally:
        R.close()
    if R.fails:
        sys.exit(1)
    print('OK: the hooks behave on every synthetic transcript.')


if __name__ == '__main__':
    main()
