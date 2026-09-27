#!/usr/bin/env python3
"""Status line: model · context size · prompt-cache warmth (time left, or what the next message would re-write) · cost.

Uses the prompt_cache and context_window fields Claude Code passes when present, and falls back to the transcript.
  settings.json: {"statusLine": {"type": "command", "command": "python3 \"$HOME/.claude/hooks/claude-usage/statusline.py\""}}
"""
import sys
import time

try:
    import _session as S
except Exception:       # a missing or broken helper must never break Claude Code: exit and do nothing
    sys.exit(0)


def main():
    d = S.read_stdin()
    parts = []
    model = (d.get('model') or {}).get('display_name') or ''
    if model:
        parts.append(model)
    cw = d.get('context_window') or {}
    cu = cw.get('current_usage') or {}
    ctx = sum(cu.get(k) or 0 for k in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')) if cu else 0
    pc = d.get('prompt_cache') or {}
    # the transcript tail is read only when Claude Code didn't pass the context size or the cache expiry
    last = S.last_usage(d['transcript_path']) if d.get('transcript_path') and not (ctx and pc.get('expires_at')) else None
    if not ctx and last:
        ctx = last['ctx']
    if ctx:
        pct = cw.get('used_percentage')
        parts.append(f"ctx {S.tok(ctx)}" + (f" ({pct:.0f}%)" if isinstance(pct, (int, float)) else ''))
    left = None
    if pc.get('expires_at'):
        left = pc['expires_at'] - time.time()
    elif last:
        left = last['t'] + last['ttl'] - time.time()
    if left is not None and ctx:
        if left > 0:
            parts.append(f"cache warm {S.span(left)}")
        else:
            redo = pc.get('recache_tokens_if_cold') or ctx
            parts.append(f"cache cold: next message re-writes {S.tok(redo)}")
    cost = (d.get('cost') or {}).get('total_cost_usd')
    if isinstance(cost, (int, float)) and cost > 0:
        parts.append(S.usd(cost))
    print(' · '.join(parts))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('')
    sys.exit(0)
