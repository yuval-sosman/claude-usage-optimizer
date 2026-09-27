"""Shared helpers for the claude-usage hooks: read the tail of the session transcript, price tokens, keep tiny state.

Hooks must be fast and must never get in the way: every helper swallows its own errors and returns None.
"""
import datetime as dt
import json
import os
import time

HERE = os.path.dirname(os.path.realpath(__file__))
STATE = os.environ.get('CLAUDE_USAGE_HOOK_STATE') or os.path.join(HERE, 'state')      # next to the installed hooks
TAIL = 3 * 1024 * 1024       # bytes of transcript to scan from the end


def read_stdin():
    import sys
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def parse_ts(s):
    try:
        return dt.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
    except Exception:
        return None


def last_usage(transcript):
    """The newest main-thread API call in the transcript: its end time, context size, model and cache lifetime."""
    try:
        size = os.path.getsize(transcript)
        with open(transcript, 'rb') as fh:
            fh.seek(max(0, size - TAIL))
            lines = fh.read().decode('utf-8', 'replace').splitlines()
    except Exception:
        return None
    last, w1, w5 = None, 0, 0
    for ln in reversed(lines):
        if '"assistant"' not in ln or '"usage"' not in ln:
            continue
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if d.get('type') != 'assistant' or d.get('isSidechain'):
            continue
        m = d.get('message') or {}
        u = m.get('usage') or {}
        if not u or m.get('model') == '<synthetic>':
            continue
        cc = u.get('cache_creation') or {}
        w1 += cc.get('ephemeral_1h_input_tokens') or 0
        w5 += cc.get('ephemeral_5m_input_tokens') or 0
        if last is None:
            last = dict(t=parse_ts(d.get('timestamp') or ''), model=m.get('model') or '',
                        ctx=(u.get('input_tokens') or 0) + (u.get('cache_read_input_tokens') or 0) + (u.get('cache_creation_input_tokens') or 0))
        if w1 + w5 > 0 and last is not None:
            break
    if last is None or last['t'] is None:
        return None
    last['ttl'] = 3600 if w1 >= w5 and w1 > 0 else 300 if w5 > 0 else 3600
    return last


def canon_model(m):
    """Same normalisation as usage_report.py: provider prefixes/suffixes off, old names reordered."""
    import re
    if not isinstance(m, str) or not m or m.startswith('<'):
        return m or ''
    s = m.strip().lower()
    i = s.rfind('claude-')
    if i < 0:
        return m
    s = re.sub(r'\[.*?\]', '', s[i:])
    s = re.split(r'[@/]', s)[0]
    s = re.sub(r'-v\d+(?::\d+)?$|:\d+$', '', s)
    s = re.sub(r'-(?:latest|\d{8})$', '', s).replace('.', '-').replace('_', '-')
    old = re.match(r'claude-(\d+)(?:-(\d+))?-(opus|sonnet|haiku)$', s)
    if old:
        s = f'claude-{old.group(3)}-{old.group(1)}' + (f'-{old.group(2)}' if old.group(2) else '')
    return re.sub(r'^(claude-[a-z]+-\d+)-0$', r'\1', s)


def price(model, kind):
    """USD per token from prices.json next to this script (or the plugin's), else None. Unknown Claude models are priced
    like the nearest version of their family."""
    import re
    parts = lambda k: (lambda mm: (mm.group(1), (int(mm.group(2)), int(mm.group(3) or 0))) if mm else (None, None))(
        re.match(r'claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?$', k))
    cm = canon_model(model)
    for p in (os.path.join(HERE, 'prices.json'), os.path.join(os.path.dirname(HERE), 'prices.json')):
        try:
            with open(p, encoding='utf-8') as fh:
                table = {canon_model(k): v for k, v in json.load(fh).items() if not k.startswith('_') and isinstance(v, dict)}
        except Exception:
            continue
        r = table.get(cm)
        if r is None:
            fam, v = parts(cm)
            same = sorted((parts(k)[1], k) for k in table if fam and parts(k)[0] == fam)
            if same:
                older = [k for kv, k in same if kv <= v]
                r = table[older[-1] if older else same[0][1]]
        if isinstance(r, dict) and kind in r:
            return r[kind] / 1e6
    return None


def usd(x):
    return f'${x:,.2f}' if x >= 0.01 else '<$0.01'


def tok(n):
    return f'{n / 1e6:.1f}M' if n >= 1e6 else f'{n / 1e3:.0f}K' if n >= 1e3 else str(int(n))


def span(sec):
    sec = int(sec)
    if sec < 90:
        return f'{sec}s'
    if sec < 3600:
        return f'{sec // 60} min'
    return f'{sec // 3600}h' + (f' {sec % 3600 // 60:02d}m' if sec % 3600 >= 60 else '')


def state_get(name):
    try:
        with open(os.path.join(STATE, name + '.json'), encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return {}


def state_put(name, value):
    try:
        os.makedirs(STATE, exist_ok=True)
        with open(os.path.join(STATE, name + '.json'), 'w', encoding='utf-8') as fh:
            json.dump(value, fh)
        for f in os.listdir(STATE):                      # keep the folder small: drop state older than two days
            p = os.path.join(STATE, f)
            if time.time() - os.path.getmtime(p) > 2 * 86400:
                os.remove(p)
    except Exception:
        pass


def emit(obj):
    print(json.dumps(obj))
