#!/usr/bin/env python3
"""UserPromptSubmit hook: tell you (not Claude) when the context has grown past the size where /compact pays off.

Your usage report replays your sessions to find the context size where compacting saves the most (question SV4). This
hook shows a one-line notice when the current context passes it, then stays quiet until it grows another --step tokens
(or drops back, e.g. after /compact, and passes it again). It never blocks.

  python3 context_guard.py --threshold 150000 [--step 50000]
"""
import argparse
import sys

try:
    import _session as S
except Exception:       # a missing or broken helper must never break Claude Code: the hook then does nothing
    S = None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--threshold', type=int, default=150000)
    ap.add_argument('--step', type=int, default=50000, help='repeat the notice every this many more tokens')
    a = ap.parse_args()
    inp = S.read_stdin()
    if not inp.get('transcript_path') or (inp.get('prompt') or '').lstrip().startswith('/'):
        return
    last = S.last_usage(inp['transcript_path'])
    if not last:
        return
    sid = inp.get('session_id') or 'unknown'
    st = S.state_get('ctx-' + sid)
    if last['ctx'] < a.threshold:
        if st:
            S.state_put('ctx-' + sid, {})                     # back under the threshold (/compact, /clear): start over
        return
    nxt = st.get('next', 0)
    if last['ctx'] < 0.7 * st.get('last', 0):                  # the context shrank a lot since the last notice: start over
        nxt = 0
    if last['ctx'] < nxt:
        S.state_put('ctx-' + sid, {'next': nxt, 'last': last['ctx']})
        return
    S.state_put('ctx-' + sid, {'next': last['ctx'] + a.step, 'last': last['ctx']})
    r = S.price(last['model'], 'cr')
    per = f" (≈{S.usd(last['ctx'] * r)} of cache reads per request)" if r else ''
    S.emit({'systemMessage': f"Context is {S.tok(last['ctx'])} tokens{per}. Your usage history says compacting past "
                             f"{S.tok(a.threshold)} pays off: /compact (optionally with what to keep) when this task allows."})


if __name__ == '__main__':
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
