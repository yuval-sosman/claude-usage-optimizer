#!/usr/bin/env python3
"""UserPromptSubmit hook: stop a prompt that would re-write a large, expired prompt cache, once, and say what it costs.

When the session's last API call is older than its cache lifetime and the context is big, sending the next prompt
writes the whole context to the cache again. This hook blocks that first prompt with the numbers and the options
(/clear and start from a summary, /compact, or simply send again). Sending again within --grace seconds goes through.

  python3 stale_cache_guard.py [--min-context 60000] [--grace 180] [--ttl 3600]

Install (settings.json):
  {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command",
     "command": "python3 \"$HOME/.claude/hooks/claude-usage/stale_cache_guard.py\"", "timeout": 10}]}]}}
"""
import sys
import time

try:
    import _session as S
except Exception:       # a missing or broken helper must never break Claude Code: exit and do nothing
    sys.exit(0)


def main():
    min_context = S.arg('--min-context', 60000)                # only guard contexts at least this big (tokens)
    grace = S.arg('--grace', 180)                              # seconds during which sending again goes through
    ttl = S.arg('--ttl', 0)                                    # cache lifetime in seconds (0: detect from the transcript)
    inp = S.read_stdin()
    prompt = (inp.get('prompt') or '').strip()
    if not inp.get('transcript_path') or prompt.startswith('/'):
        return
    last = S.last_usage(inp['transcript_path'])
    if not last or last['ctx'] < min_context:
        return
    ttl = ttl or last['ttl']
    idle = time.time() - last['t']
    if idle < ttl:
        return
    sid = inp.get('session_id') or 'unknown'
    st = S.state_get('stale-' + sid)
    if st.get('warned') and time.time() - st['warned'] < grace:
        S.state_put('stale-' + sid, {})                      # second send: let it through
        return
    if not S.state_put('stale-' + sid, {'warned': time.time()}):
        return                                             # unrecorded, sending again would be blocked again
    w = S.price(last['model'], 'cw1h' if ttl >= 3600 else 'cw5m')
    r = S.price(last['model'], 'cr')
    cost = f" (≈{S.usd(last['ctx'] * w)} at list prices, vs {S.usd(last['ctx'] * r)} for a cache hit)" if w and r else ''
    reason = (f"Paused by the claude-usage cache guard. This session has been idle for {S.span(idle)}, longer than its "
              f"{S.span(ttl)} prompt cache, so this message would re-write all {S.tok(last['ctx'])} tokens of context{cost}.\n"
              f"Options: /clear and start fresh with a short summary · /compact first · or press ↑ and send the same "
              f"message again within {S.span(grace)} to continue as is.")
    S.emit({'decision': 'block', 'reason': reason})


if __name__ == '__main__':
    try:
        main()
    except Exception:
        pass          # a guard must never get in the way
    sys.exit(0)
