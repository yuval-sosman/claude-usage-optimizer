"""Shared helpers for the usage-optimizer hooks: read the tail of the session transcript, price tokens, keep tiny state.

Hooks must be fast and must never get in the way: the helpers swallow their own errors and return None (or False),
except arg(), which raises on a malformed value so that the hook's own catch-all makes it do nothing. They run on every
prompt or tool call, so heavy modules (argparse, datetime) are left out or imported only when needed.
"""
import json
import os
import time

HERE = os.path.dirname(os.path.realpath(__file__))
STATE = os.environ.get('CLAUDE_USAGE_HOOK_STATE') or os.path.join(HERE, 'state')      # next to the installed hooks
FIRST = 64 * 1024            # bytes of transcript read first: the last API call is usually a few KB from the end
TAIL = 3 * 1024 * 1024       # at most this many, when a large tool result follows the last call


def arg(name, default):
    """--name N or --name=N from the command line as an int (the last one wins), else default. A malformed value raises,
    so the hook does nothing (argparse, which costs more to import than the rest of a hook, exited 2 instead)."""
    import sys
    v = default
    for i, a in enumerate(sys.argv[1:], 1):
        if a == name:
            v = int(sys.argv[i + 1])
        elif a.startswith(name + '='):
            v = int(a[len(name) + 1:])
    return v


def read_stdin():
    import sys
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def parse_ts(s):
    """An ISO timestamp (any number of fraction digits, Z or an offset) or an epoch number (s or ms) as epoch seconds."""
    import datetime as dt
    import re
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        return float(s) / 1000 if s > 1e11 else float(s)
    try:
        s = re.sub(r'(\.\d{6})\d+', r'\1', str(s).strip().replace('Z', '+00:00'))      # 3.8–3.10 take 3 or 6 digits only
        s = re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', s)                                 # +0000 → +00:00
        return dt.datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def last_usage(transcript):
    """The newest main-thread API call in the transcript: its end time, context size, model and cache lifetime.

    Scans back from the end of the file (at most TAIL bytes) until it has the newest call and a cache write to tell the
    lifetime by, which is usually within the first 64 KB read. After a /compact, until the next call, the result is the
    boundary instead: kind 'compacted', t the time the compaction finished (the cached prefix's clock restarted then), and
    ctx 0, since the context the summary leaves is not in the transcript (a guard that compares ctx with a threshold does
    nothing, which is right: the old context is gone). A call has kind 'call'. After a /rewind (the newest user record
    points at an older assistant message), the checkpoint's call, with the newest call's time."""
    st = {'last': None, 'first': None, 'parent': None, 'w1': 0, 'w5': 0}
    batches = tail(transcript)
    while True:
        try:
            lines = next(batches, None)
        except Exception:
            return None
        if lines is None or scan(lines, st):
            break
    last, w1, w5 = st['last'] or st['first'], st['w1'], st['w5']     # a parent never found in the tail: the newest call
    if last is None:
        return None
    if last['t'] is None:                      # an unreadable timestamp: the file was last written at or after the call
        try:
            last['t'] = os.path.getmtime(transcript)
        except OSError:
            return None
    last['ttl'] = 3600 if w1 >= w5 and w1 > 0 else 300 if w5 > 0 else 3600
    return last


def tail(path):
    """The lines of the file's last TAIL bytes in batches, newest batch first: the last FIRST bytes, then 8x as much at a
    time. Each byte is read once; a line cut by a batch's start goes with the next (older) batch, except at TAIL."""
    size = os.path.getsize(path)
    stop = max(0, size - TAIL)
    with open(path, 'rb') as fh:
        pos, n, carry = size, FIRST, b''
        while True:
            start = max(stop, size - n)
            fh.seek(start)
            buf = (fh.read() if pos == size else fh.read(pos - start)) + carry      # the first read goes to the end,
            pos, n, carry = start, n * 8, b''                                        # as the file may have grown
            if pos > stop:
                cut = buf.find(b'\n')
                carry, buf = (buf, b'') if cut < 0 else (buf[:cut], buf[cut + 1:])
            yield [b.decode('utf-8', 'replace') for b in buf.split(b'\n')]     # on \n only: str.splitlines() would
            if pos == stop:                                                      # also cut a record at a raw U+2028/NEL
                return


def scan(lines, st):
    """Go on from st through lines, newest first: True once it has the newest call (or, with no call after it, the newest
    /compact boundary: the summary's own call is not in the transcript) and a cache write at or before it to tell the
    lifetime by. One parse per line; anything that is not a JSON object is skipped.

    A /rewind leaves the abandoned branch in the file: the newest user record then points (parentUuid) at an assistant
    message older than the newest one. The call to go by is that older message's (the checkpoint's context), with the
    newest call's time (every call since read and so refreshed the cached prefix)."""
    for ln in reversed(lines):
        want_user = st['parent'] is None and st['first'] is None and '"user"' in ln and '"parentUuid"' in ln
        boundary = st['last'] is None and st['first'] is None and '"compact_boundary"' in ln
        if not (want_user or boundary or ('"assistant"' in ln and '"usage"' in ln)):
            continue
        try:
            d = json.loads(ln)
        except Exception:                      # a cut line, or one nested past the recursion limit
            continue
        if not isinstance(d, dict) or d.get('isSidechain'):
            continue
        typ = d.get('type')
        if typ == 'user':
            if want_user:                      # the newest user record, newer than every call: a prompt, a tool result…
                p = d.get('parentUuid')
                st['parent'] = p if isinstance(p, str) and p else False
            continue
        if typ == 'system':
            if boundary and d.get('subtype') == 'compact_boundary':
                st['last'] = dict(kind='compacted', t=parse_ts(d.get('timestamp') or ''), model='', ctx=0, geo=None, speed=None)
            continue
        if typ != 'assistant':
            continue
        m = d.get('message') or {}
        u = m.get('usage') or {}
        if not isinstance(u, dict) or not u or m.get('model') == '<synthetic>':
            continue
        cc = u.get('cache_creation') or {}
        w1, w5 = cc.get('ephemeral_1h_input_tokens'), cc.get('ephemeral_5m_input_tokens')
        if w1 is None and w5 is None:          # no breakdown (an older CLI, a gateway): a 5-minute write, as the engine counts it
            w1, w5 = 0, u.get('cache_creation_input_tokens')
        st['w1'] += w1 or 0
        st['w5'] += w5 or 0
        cd = dict(kind='call', t=parse_ts(d.get('timestamp') or ''), model=m.get('model') or '',
                  ctx=(u.get('input_tokens') or 0) + (u.get('cache_read_input_tokens') or 0) + (u.get('cache_creation_input_tokens') or 0),
                  geo=u.get('inference_geo'), speed=u.get('speed'), mid=m.get('id') or d.get('uuid') or '')
        if st['first'] is None:
            st['first'] = cd
        if st['parent']:                       # the newest user record points at a call: this one?
            if d.get('uuid') == st['parent']:
                if st['last'] is None:         # an older message than the newest: a /rewind to it
                    st['last'] = dict(cd, t=st['first']['t']) if cd['mid'] != st['first']['mid'] else st['first']
                st['parent'] = False
        elif st['last'] is None:
            st['last'] = cd
        if st['last'] is not None and st['w1'] + st['w5'] > 0:
            return True
    return False


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


def price(model, kind, call=None):
    """USD per token from prices.json next to this script (or the plugin's), else None. Unknown Claude models are priced
    like the nearest version of their family, and ids in its _aliases (a Bedrock application inference profile) like the
    model they map to (never a Claude id). With call (from last_usage()), the report's multipliers too: a Bedrock inference
    profile other than global. or an inference_geo other than "global" (_modifiers), and fast mode (the model's "fast")."""
    import re
    parts = lambda k: (lambda mm: (mm.group(1), (int(mm.group(2)), int(mm.group(3) or 0))) if mm else (None, None))(
        re.match(r'claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?$', k))
    for p in (os.path.join(HERE, 'prices.json'), os.path.join(os.path.dirname(HERE), 'prices.json')):
        try:
            with open(p, encoding='utf-8') as fh:
                js = json.load(fh)
            table = {canon_model(k): v for k, v in js.items() if not k.startswith('_') and isinstance(v, dict)}
        except Exception:
            continue
        bare = re.sub(r'\[.*?\]$', '', (model or '').strip())
        alias = (js.get('_aliases') or {}).get(bare) if 'claude-' not in bare.lower() else None     # a Claude id is never aliased
        cm = canon_model(alias if isinstance(alias, str) else model)
        r = table.get(cm)
        if r is None:
            fam, v = parts(cm)
            same = sorted((parts(k)[1], k) for k in table if fam and parts(k)[0] == fam)
            if same:
                older = [k for kv, k in same if kv <= v]
                r = table[older[-1] if older else same[0][1]]
        if isinstance(r, dict) and kind in r:
            x = r[kind] / 1e6
            call = call or {}
            prof = re.search(r'(?:^|[/:])([a-z]{2,6}(?:-[a-z]+)?)\.anthropic\.', model or '', re.I)
            geo = str(call.get('geo') or '').lower()
            where = (('bedrock_regional' if prof.group(1).lower() != 'global' else None) if prof
                     else 'api_regional' if geo not in ('', 'global', 'not_available', 'none') else None)
            mod = (js.get('_modifiers') or {}).get(where) if where else None
            fam, v = parts(cm)
            if isinstance(mod, dict) and fam and v >= tuple(mod.get('from') or (0, 0)):
                x *= mod.get('mult') or 1
            if call.get('speed') == 'fast':
                x *= r.get('fast') or 1
            return x
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
    """Whether the state was written. A guard blocks only when it was: otherwise the retry it promises could never pass."""
    try:
        os.makedirs(STATE, mode=0o700, exist_ok=True)           # file paths and context sizes: yours only
        path = os.path.join(STATE, name + '.json')
        tmp = f'{path}.{os.getpid()}.tmp'                       # written whole, then moved: a hook running at the same
        with open(tmp, 'w', encoding='utf-8') as fh:            # time (parallel tool calls) never reads a torn file
            json.dump(value, fh)
        os.replace(tmp, path)
    except Exception:
        return False
    try:
        mark = os.path.join(STATE, '.swept')             # keep the folder small (swept at most once an hour):
        if abs(time.time() - (os.path.getmtime(mark) if os.path.exists(mark) else 0)) > 3600:   # abs: the clock may go back
            open(mark, 'w').close()
            for f in os.listdir(STATE):                  # drop this module's state older than two days (nothing else)
                p = os.path.join(STATE, f)
                if (f.endswith('.json') or f.endswith('.tmp')) and os.path.isfile(p) and time.time() - os.path.getmtime(p) > 2 * 86400:
                    os.remove(p)
    except Exception:
        pass
    return True


def emit(obj):
    print(json.dumps(obj))
