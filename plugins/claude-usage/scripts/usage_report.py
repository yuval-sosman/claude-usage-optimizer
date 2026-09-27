#!/usr/bin/env python3
"""Claude Code usage report (the engine of the claude-usage plugin).

Reads the transcripts Claude Code keeps under <claude dir>/projects, answers the questions in docs/QUESTIONS.md by plain
counting (no LLM involved), and writes to <out> (default $CLAUDE_USAGE_OUT, else <claude dir>-usage, e.g. ~/.claude-usage).
<claude dir> is --claude-dir, else $CLAUDE_CONFIG_DIR, else ~/.claude (on Windows %USERPROFILE%\\.claude):

  report.html          self-contained report: Report, Insights and Optimizations tabs, per-project selector
  data/metrics.json    the data the report renders
  data/digest.md       every number, compactly, for the claude-usage skills to read (they never read transcripts)
  data/config.json     your current setup (settings, hooks, MCP servers, skills), secrets removed
  data/*.csv           per-call, per-tool-call, per-session, per-subagent and per-cache-miss tables

<out>/insights.json and <out>/optimizations.json (written by the skills) are embedded when present; --render re-embeds
them fast. The full layout is in layout.py.

Usage: python3 usage_report.py [--claude-dir DIR] [--out DIR] [--since YYYY-MM-DD] [--until YYYY-MM-DD]
                               [--days N | --all] [--prices FILE] [--open] [--quiet] [--render]
Without --since, --days or --all it covers the last 60 days (DEFAULT_DAYS).
Only the Python 3.8+ standard library is used.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import contextlib
import csv
import datetime as dt
import functools
import glob
import json
import math
import os
import pathlib
import re
import shlex
import sys
import time
import traceback
import webbrowser

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import layout  # noqa: E402
VERSION = '2.0'
DEFAULT_DAYS = 60     # the window without --since, --days or --all: recent enough to act on, long enough to see habits
USER_HOME = os.path.expanduser('~')


def claude_dir(arg=None):
    """The folder Claude Code keeps its data in (the one with projects/ inside): --claude-dir, else $CLAUDE_CONFIG_DIR (what
    Claude Code itself honours), else ~/.claude. A path to the projects/ folder itself is accepted too."""
    d = os.path.abspath(os.path.expanduser(arg or os.environ.get('CLAUDE_CONFIG_DIR') or os.path.join(USER_HOME, '.claude')))
    if os.path.basename(d) == 'projects' and not os.path.isdir(os.path.join(d, 'projects')):
        d = os.path.dirname(d)
    return d


def default_out(cdir=None):
    """$CLAUDE_USAGE_OUT, else a folder next to the Claude folder named after it: ~/.claude -> ~/.claude-usage. It stays outside
    the Claude folder because Claude Code guards every write under .claude/ (a skill's Write there prompts, or fails headless)."""
    return os.path.abspath(os.path.expanduser(os.environ.get('CLAUDE_USAGE_OUT') or (cdir or claude_dir()).rstrip('/\\') + '-usage'))


def legacy_out(cdir):
    """Where earlier versions wrote reports (inside the Claude folder), if one is still there."""
    p = os.path.join(cdir, 'usage-report')
    return p if os.path.isfile(os.path.join(p, 'metrics.json')) else None


def tilde(p):
    """A path with the home directory shown as ~ (display only)."""
    return '~' + p[len(USER_HOME):] if isinstance(p, str) and (p == USER_HOME or p.startswith(USER_HOME + os.sep)) else p


def slash(p):
    """Forward slashes, so path patterns work the same on Windows."""
    return p.replace('\\', '/') if isinstance(p, str) else p
SECRET = re.compile(r'key|token(?!s)|secret|password|passwd|auth|credential|cookie|bearer', re.I)   # not MAX_…_TOKENS
# secrets inside a value (a hook command, an env value, a URL), each replaced by <redacted>
SECRET_IN_TEXT = [
    (re.compile(r'(\b[a-z][a-z0-9+.-]*://)[^\s/@]+@', re.I), r'\1<redacted>@'),                  # user[:password]@ in a URL
    (re.compile(r'\b(bearer|basic)(\s+)[\w.~+/=-]{8,}', re.I), r'\1\2<redacted>'),               # Authorization: Bearer …
    (re.compile(r'''(\b[\w.-]*(?:key|token(?!s)|secret|password|passwd|credential)[\w.-]*\s*[=:]\s*)(["']?)[^\s"'&;,]+''', re.I),
     r'\1\2<redacted>'),                                                                           # FOO_TOKEN=…, api_key: …
    (re.compile(r'''(--[\w-]*(?:key|token(?!s)|secret|password)[\w-]*\s+)(["']?)[^\s"']+''', re.I), r'\1\2<redacted>'),  # --api-key …
    (re.compile(r'\b(?:sk-[\w-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|glpat-[\w-]{20,}|xox[abprs]-[\w-]{10,}'
                r'|AKIA[0-9A-Z]{16}|AIza[\w-]{35})'), '<redacted>'),                                 # well-known key formats
]
TOKENISH = re.compile(r'^(?=.*\d)(?=.*[a-z])(?=.*[A-Z])[\w+/=.-]{24,}$')                          # a random-looking value on its own

WAITING_TOOLS = {'AskUserQuestion', 'ExitPlanMode', 'EnterPlanMode'}
EDIT_TOOLS = {'Edit', 'Write', 'MultiEdit', 'NotebookEdit'}
COORD_TOOLS = {'Agent', 'Task', 'SendMessage', 'ListAgents', 'TaskOutput', 'TaskStop', 'Monitor', 'Skill',
               'ToolSearch', 'TodoWrite', 'CronCreate', 'CronDelete', 'CronList'} | WAITING_TOOLS
MISS_MIN = 1000        # re-written tokens above this count as a cache miss
IDLE_CAP = 300         # gaps longer than this don't count as active time (seconds)
IMAGE_TOKENS = 1600    # weight when splitting context growth between an image and text that arrived with it
BIG_CTX = 100_000
TOKEN_TYPES = [('cache_read', 'Cache read', 1), ('write_1h', 'Cache write 1h', 2), ('output', 'Output', 3),
               ('write_5m', 'Cache write 5m', 4), ('input', 'Input (uncached)', 5)]
NO_DATA = 'no data in this scope'


# ----------------------------------------------------------------------------------------------- helpers

def parse_ts(s):
    try:
        return dt.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
    except (ValueError, AttributeError, TypeError):
        return None


def local(t):
    return dt.datetime.fromtimestamp(t)


@functools.lru_cache(maxsize=None)
def day(t):
    """The local date. Cached: every scope asks again for the same timestamps."""
    return local(t).strftime('%Y-%m-%d')


def uniq(xs):
    """Distinct values in first-seen order. A set's order changes from run to run (hash seeds), and with it the order of
    ties in a sort and the last digits of a float sum."""
    return list(dict.fromkeys(xs))


def pctl(vals, p):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] if lo == hi else v[lo] + (v[hi] - v[lo]) * (k - lo)


def median(vals):
    return pctl(vals, 50)


def mean(vals):
    v = [x for x in vals if x is not None]
    return sum(v) / len(v) if v else None


def share(a, b):
    return 100.0 * a / b if b else None


def r1(x):
    return None if x is None else round(x, 1)


def r2(x):
    return None if x is None else round(x, 2)


def f_int(n):
    return f'{int(round(n)):,}' if n is not None else '–'


def f_tok(n):
    if n is None:
        return '–'
    n = float(n)
    for div, suf in ((1e9, 'B'), (1e6, 'M'), (1e3, 'K')):
        if abs(n) >= div:
            v = n / div
            return f'{v:.1f}{suf}' if v < 100 else f'{v:.0f}{suf}'
    return f'{n:.0f}'


def f_usd(x):
    if x is None:
        return '–'
    if abs(x) >= 100:
        return f'${x:,.0f}'
    if abs(x) >= 1:
        return f'${x:,.2f}'
    return f'${x:.3f}' if x else '$0'


def f_pct(x):
    if x is None:
        return '–'
    return f'{x:.1f}%' if abs(x) < 10 else f'{x:.0f}%'


def f_dur(s):
    if s is None:
        return '–'
    s = float(s)
    if s < 60:
        return f'{s:.0f}s'
    if s < 3600:
        return f'{s / 60:.0f} min'
    h = int(s // 3600)
    m = int(round((s - h * 3600) / 60))
    return f'{h}h {m:02d}m' if m else f'{h}h'


def text_of(d):
    c = (d.get('message') or {}).get('content')
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return '\n'.join(b.get('text') or '' for b in c if isinstance(b, dict) and b.get('type') == 'text')
    return ''


def images_in(content):
    if isinstance(content, list):
        return sum(1 for b in content if isinstance(b, dict) and b.get('type') == 'image')
    return 0


def str_len(x):
    """Characters of text in a JSON value (base64 image data excluded)."""
    if isinstance(x, str):
        return len(x)
    if isinstance(x, dict):
        if x.get('type') == 'base64' or 'data' in x and isinstance(x.get('data'), str) and len(x['data']) > 2000:
            return 0
        return sum(str_len(v) for k, v in x.items() if k not in ('type', 'media_type'))
    if isinstance(x, list):
        return sum(str_len(v) for v in x)
    return 0


def images_deep(x):
    if isinstance(x, dict):
        return (1 if x.get('type') == 'image' else 0) + sum(images_deep(v) for v in x.values())
    if isinstance(x, list):
        return sum(images_deep(v) for v in x)
    return 0


FAIL_RX = re.compile(r'error:|FAILED|\bfailed\b|Exit code [1-9]')     # a tool result that reports a failure


def reports_failure(text):
    """FAIL_RX.search(text), faster on long tool output: the regex runs only when a plain substring check can't decide."""
    return 'error:' in text or 'FAILED' in text or (('failed' in text or 'Exit code ' in text) and FAIL_RX.search(text) is not None)


def slim(d):
    """Drop, in place, the bulk of a record that no card reads: tool output and file contents are most of a transcript's
    bytes, and keeping them made memory grow with every transcript. A tool_result block keeps only what Model._tools
    reads, as `_res` = (characters, first 600 characters, images, failure flag); a Read result keeps its line counts.
    Edit results lose their copies of the file and strings, thinking blocks their text and signature, images their data,
    and usage its per-iteration breakdown (cards read the totals)."""
    tur = d.get('toolUseResult')
    if isinstance(tur, dict):
        f = tur.get('file')
        if isinstance(f, dict):
            f.pop('content', None)
            f.pop('base64', None)
        for k in ('stdout', 'stderr', 'originalFile', 'oldString', 'newString'):
            tur.pop(k, None)
    msg = d.get('message')
    if not isinstance(msg, dict):
        return
    if isinstance(msg.get('usage'), dict):
        msg['usage'].pop('iterations', None)
    for b in msg['content'] if isinstance(msg.get('content'), list) else []:
        if not isinstance(b, dict):
            continue
        typ = b.get('type')
        if typ == 'tool_result' and 'content' in b:
            bc = b.pop('content')
            if isinstance(bc, str):
                text = bc
            elif isinstance(bc, list):
                text = '\n'.join(x['text'] if isinstance(x.get('text'), str) else '' for x in bc
                                 if isinstance(x, dict) and x.get('type') == 'text')
            else:
                text = ''
            b['_res'] = (len(text), text[:600], images_in(bc), reports_failure(text))
        elif typ == 'image' and isinstance(b.get('source'), dict):
            b['source'] = {k: v for k, v in b['source'].items() if k != 'data'}
        elif typ in ('thinking', 'redacted_thinking'):
            for k in ('thinking', 'signature', 'data'):
                b.pop(k, None)


ALIASES = {}    # ids that name no model (a Bedrock application inference profile ARN, a gateway's own name) → canonical id
PROFILE = re.compile(r'(?:^|[/:])([a-z]{2,6}(?:-[a-z]+)?)\.anthropic\.', re.I)   # a Bedrock inference profile: global., us., eu.…
MODEL_WORDS = {'opus', 'sonnet', 'haiku', 'fable', 'mythos', 'default', 'best', 'opusplan'}       # Claude Code's own model aliases


def names_no_model(v):
    """Whether an id names no Claude model by itself (a Bedrock application inference profile ARN, a gateway's own name), so
    only an alias can say which model it is. A Claude id (anything with claude- in it) or one of Claude Code's model words
    never does: those resolve on their own, and aliasing them would re-price a native model."""
    s = re.sub(r'\[.*?\]$', '', (v or '').strip().lower())
    return bool(s) and 'claude-' not in s and s not in MODEL_WORDS and not s.startswith('<')


def canon_model(m):
    """One id per model, whatever the provider wrote: 'us.anthropic.claude-sonnet-4-5-20250929-v1:0',
    'claude-sonnet-4-5@20250929', 'claude-opus-5[1m]' and 'claude-3-5-sonnet-latest' become 'claude-sonnet-4-5',
    'claude-opus-5' and 'claude-sonnet-3-5'. An id in ALIASES (see model_aliases) becomes the model it stands for. Ids that are
    not Claude models are returned unchanged."""
    if not isinstance(m, str) or not m or m.startswith('<'):
        return m or '?'
    if ALIASES and names_no_model(m):
        a = ALIASES.get(re.sub(r'\[.*?\]$', '', m.strip()))
        if a:
            return a
    return _canon_id(m)


def where_of(model_id, usage):
    """Where a call ran, as far as its model id and usage say, for pricing and the report's notes:
    bedrock_global / bedrock_regional: a Bedrock inference profile, global. or any other (us., eu., apac., jp., au., us-gov.…);
    bedrock: Bedrock without a profile prefix (its Messages endpoint's anthropic.… ids, an application inference profile ARN),
    where global or regional isn't recorded; api_global / api_regional: the Claude API's usage.inference_geo, "global" or any
    other location ("us"). None when nothing says (a subscription reports "not_available"): a native call at list price."""
    mid = model_id if isinstance(model_id, str) else ''
    prof = PROFILE.search(mid)
    if prof:
        return 'bedrock_global' if prof.group(1).lower() == 'global' else 'bedrock_regional'
    if re.match(r'anthropic\.claude-|arn:aws[\w-]*:bedrock:', mid, re.I):
        return 'bedrock'
    geo = str((usage or {}).get('inference_geo') or '').lower()
    if geo in ('', 'not_available', 'none'):
        return None
    return 'api_global' if geo == 'global' else 'api_regional'


MODEL_VARS = {'ANTHROPIC_DEFAULT_OPUS_MODEL': 'opus', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'sonnet',
              'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'haiku', 'ANTHROPIC_SMALL_FAST_MODEL': 'haiku'}


def model_aliases(prices, settings):
    """ALIASES for ids that name no Claude model, in this order: prices.json's _aliases; a settings file's modelOverrides
    (a Claude id mapped to a Bedrock application inference profile ARN, read backwards); and ANTHROPIC_DEFAULT_<FAMILY>_MODEL
    set to such an ARN, in a settings file's env or this process's environment (the version isn't known, so it is priced as
    the family's model the report compares against and listed in prices.guessed). Returns the aliases."""
    out = {k: v for k, v in prices.aliases.items() if names_no_model(k)}
    envs = [js.get('env') for js in settings if isinstance(js, dict) and isinstance(js.get('env'), dict)] + [os.environ]
    for js in settings:
        mo = js.get('modelOverrides') if isinstance(js, dict) else None
        for cid, target in (mo.items() if isinstance(mo, dict) else ()):         # a malformed settings file is skipped
            if (isinstance(target, str) and names_no_model(target) and target not in out
                    and model_parts(_canon_id(cid))[0]):                          # only a Claude model can be priced
                out[target] = _canon_id(cid)
    for env in envs:
        for var, fam in MODEL_VARS.items():
            v = re.sub(r'\[.*?\]$', '', str(env.get(var) or '').strip())
            if names_no_model(v) and v not in out and prices.pick(fam):
                out[v] = prices.pick(fam)
                prices.guessed[v] = (prices.pick(fam), var)
    return out


@functools.lru_cache(maxsize=None)
def _canon_id(m):
    """canon_model for a model id string. Cached: it runs for every API call of every scope, and ids are few."""
    s = m.strip().lower()
    i = s.rfind('claude-')
    if i < 0:
        return m
    s = s[i:]
    s = re.sub(r'\[.*?\]', '', s)                                   # claude-opus-5[1m]
    s = re.split(r'[@/]', s)[0]                                      # vertex @20250929, trailing /…
    s = re.sub(r'-v\d+(?::\d+)?$|:\d+$', '', s)                        # bedrock -v1:0
    s = re.sub(r'-(?:latest|\d{8})$', '', s)                          # -latest, -20251001
    s = s.replace('.', '-').replace('_', '-')
    old = re.match(r'claude-(\d+)(?:-(\d+))?-(opus|sonnet|haiku)$', s)  # claude-3-5-sonnet → claude-sonnet-3-5
    if old:
        s = f'claude-{old.group(3)}-{old.group(1)}' + (f'-{old.group(2)}' if old.group(2) else '')
    s = re.sub(r'^(claude-[a-z]+-\d+)-0$', r'\1', s)                   # claude-opus-4-0 → claude-opus-4
    return s


def model_parts(m):
    """(family, (major, minor)) of a canonical id, or (None, None)."""
    mm = re.match(r'claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?$', m or '')
    return (mm.group(1), (int(mm.group(2)), int(mm.group(3) or 0))) if mm else (None, None)


def model_name(m):
    if not m or m == '<synthetic>':
        return 'Client error'
    if str(m).startswith('arn:'):                                  # a Bedrock profile no alias maps to a model
        return 'Bedrock ' + clip(str(m).rsplit('/', 1)[-1], 24)
    fam, v = model_parts(canon_model(m))
    if fam:
        return f'{fam.title()} {v[0]}' + (f'.{v[1]}' if v[1] else '')
    mm = re.match(r'claude-(\d+)(?:-(\d+))?$', canon_model(m))
    return f"Claude {mm.group(1)}" + (f'.{mm.group(2)}' if mm.group(2) else '') if mm else m


def short_path(p, cwd=None):
    if not p:
        return ''
    p, cwd = slash(p), slash(cwd)
    wt = re.search(r'/\.claude/worktrees/[^/]+/(.+)$', p)
    if wt:
        return wt.group(1)
    home = slash(USER_HOME)
    if cwd and p.startswith(cwd.rstrip('/') + '/'):
        return p[len(cwd.rstrip('/')) + 1:]
    return '~' + p[len(home):] if p == home or p.startswith(home + '/') else p


def clip(s, n=90):
    s = s or ''
    if len(s) > 4 * n:          # long text: when a prefix fills the clip, the rest can't change it (and is costly to collapse)
        head = re.sub(r'\s+', ' ', s[:4 * n]).lstrip()
        if len(head) > n + 1:
            return head[:n - 1] + '…'
    s = re.sub(r'\s+', ' ', s).strip()
    return s if len(s) <= n else s[:n - 1] + '…'


def clip_path(p, n=60):
    p = p or ''
    return p if len(p) <= n else '…' + p[-(n - 1):]


def fill_days(days):
    """Every calendar day between the first and last (so bar charts don't hide idle days)."""
    days = sorted(days)
    if not days:
        return days
    a, b = dt.date.fromisoformat(days[0]), dt.date.fromisoformat(days[-1])
    if (b - a).days > 120:
        return days
    return [(a + dt.timedelta(n)).isoformat() for n in range((b - a).days + 1)]


def bucketize(values, edges, labels):
    counts = [0] * len(labels)
    for v in values:
        if v is None:
            continue
        i = bisect.bisect_right(edges, v)
        counts[min(i, len(labels) - 1)] += 1
    return counts


# ------------------------------------------------------------------------------------------ report blocks

def kpi(label, value, unit, sub=None):
    d = {'label': label, 'value': value, 'unit': unit}
    if sub:
        d['sub'] = sub
    return d


def K(*items):
    return {'kind': 'kpis', 'items': [i for i in items if i]}


def S(name, values, slot=None, axis=None, unit=None):
    """axis=2 (vertical bars only) draws the series against a second value axis on the right, in its own unit."""
    d = {'name': name, 'values': values}
    if slot:
        d['slot'] = slot
    if axis:
        d['axis'] = axis
    if unit:
        d['unit'] = unit
    return d


def BAR(categories, series, unit, orient='h', stacked=False, title=None, labels=True, wrap_labels=False):
    if not categories or not any(any(v for v in s['values'] if v) for s in series):
        return None
    c = {'type': 'bar', 'orient': orient, 'categories': categories, 'series': series, 'unit': unit,
         'stacked': stacked, 'valueLabels': labels}
    if title:
        c['title'] = title
    if wrap_labels:
        c['wrapLabels'] = True
    return {'kind': 'chart', 'chart': c}


def _top_other(keys, n, *series, name=None, other='Other'):
    """At most n categories: every key when they fit, else the first n-1 and '<other> (N)' summing the rest.
    keys come sorted, most important first; each series maps key → value. Returns (categories, [values per series])."""
    top, rest = (keys[:n - 1], keys[n - 1:]) if len(keys) > n else (list(keys), [])
    cats = [name(k) if name else k for k in top] + ([f'{other} ({len(rest)})'] if rest else [])
    return cats, [[s[k] for k in top] + ([sum(s[k] for k in rest)] if rest else []) for s in series]


def TABS(tabs, title=None, collapsed=True, sub=None, desc=None):
    """Blocks behind one collapsible header, one tab shown at a time: tabs = [(label, block or [blocks]), …].
    sub: a short line in the header; desc: a paragraph at the top of the opened section."""
    out = []
    for lbl, b in tabs:
        bl = [x for x in (b if isinstance(b, list) else [b]) if x]
        if bl:
            out.append({'label': lbl, 'block': bl[0]} if len(bl) == 1 else {'label': lbl, 'blocks': bl})
    if not out:
        return None
    d = {'kind': 'tabs', 'title': title or 'Details', 'collapsed': collapsed, 'tabs': out}
    if sub:
        d['sub'] = sub
    if desc:
        d['desc'] = desc
    return d


def PIE(categories, values, unit, title=None, center=None, note=None, width=None, slots=None, total=None):
    """A donut: parts of one whole (legend lists each part with its share). slots: a colour per part (1–8)."""
    if not categories or not any(v for v in values if v):
        return None
    c = {'type': 'pie', 'categories': categories, 'series': [{'name': title or 'Share', 'values': values}], 'unit': unit}
    if slots:
        c['slots'] = slots
    if total is not None:
        c['total'] = total          # the exact whole, for the centre (the parts may be rounded)
    for k, v in (('title', title), ('centerLabel', center), ('note', note)):
        if v:
            c[k] = v
    d = {'kind': 'chart', 'chart': c}
    if width:
        d['width'] = width
    return d


def LINE(x, series, unit, x_kind='date', title=None, area=False, ref_lines=None, x_unit=None, indexed=False):
    """indexed: each series is a multiple of its own average (unit 'x'), with the real value per point in s['raw']."""
    if not x or not series:
        return None
    c = {'type': 'line', 'x': x, 'xKind': x_kind, 'series': series, 'unit': unit, 'area': area}
    if indexed:
        c['indexed'] = True
    if title:
        c['title'] = title
    if ref_lines:
        c['refLines'] = ref_lines
    if x_unit:
        c['xUnit'] = x_unit
    return {'kind': 'chart', 'chart': c}


def SCATTER(points, x_unit, y_unit, groups=None, log_x=False, log_y=False, title=None, x_label=None, y_label=None):
    if not points:
        return None
    c = {'type': 'scatter', 'points': points, 'xUnit': x_unit, 'yUnit': y_unit, 'logX': log_x, 'logY': log_y}
    for k, v in (('groups', groups), ('title', title), ('xLabel', x_label), ('yLabel', y_label)):
        if v:
            c[k] = v
    return {'kind': 'chart', 'chart': c}


def HEAT(rows, cols, values, unit, title=None):
    c = {'type': 'heatmap', 'rows': rows, 'cols': cols, 'values': values, 'unit': unit}
    if title:
        c['title'] = title
    return {'kind': 'chart', 'chart': c}


def TABLE(columns, rows, title=None, limit=None, width=None):
    """columns: (key, label, unit), or (key, label, unit, {'code': True}) for an identifier shown whole on one line."""
    if not rows:
        return None
    cols = [{'key': k, 'label': lbl, **({'unit': u} if u else {}), 'align': 'l' if u in (None, 'text') else 'r', **(x[0] if x else {})}
            for k, lbl, u, *x in columns]
    d = {'kind': 'table', 'columns': cols, 'rows': rows}
    if title:
        d['title'] = title
    if limit and len(rows) > limit:
        d['limit'] = limit
    if width:
        d['width'] = width
    return d


def hide(block):
    """Keep a block in metrics.json and the digest without drawing it (None stays None)."""
    return dict(block, hidden=True) if block else None


def TEXT(text):
    return {'kind': 'text', 'text': text}


def LIST(items, title=None):
    if not items:
        return None
    d = {'kind': 'list', 'items': items}
    if title:
        d['title'] = title
    return d


HIDDEN_CARDS = {'OV6', 'ME4', 'ME5', 'EX10', 'EX11', 'EX12', 'EX13', 'EX14', 'EX15'}   # computed and kept in metrics.json / digest.md, not shown (OV6's medians sit in the headline)


CARD_ORDER = {}   # display position when it should differ from the number (e.g. {'CX9': 1.5}); ids stay stable for links


CARD_SECTION = {}   # section when it differs from the id's letters (e.g. {'TL1': 'EX'}), so moving a card between sections keeps its id and links


def section_of(c):
    return c.get('section') or re.match(r'[A-Za-z]*', c['id']).group().upper()


def card(cid, q, scope, blocks, why=None, insight=None, note=None, empty=None):
    blocks = [b for b in (blocks or []) if b]
    d = {'id': cid, 'q': q, 'scope': scope, 'blocks': blocks}
    if cid in HIDDEN_CARDS:
        d['hidden'] = True
    if cid in CARD_ORDER:
        d['order'] = CARD_ORDER[cid]
    if cid in CARD_SECTION:
        d['section'] = CARD_SECTION[cid]
    if why:
        d['why'] = why
    if insight:
        d['insight'] = insight
    if note:
        d['note'] = note
    if empty or not blocks:
        d['empty'] = empty or NO_DATA
    return d


# ------------------------------------------------------------------------------------------------ prices

class Prices:
    def __init__(self, path):
        self.table, self.source, self.missing, self.estimated, self.compare = {}, '', set(), {}, []
        self.aliases, self.modifiers, self.guessed = {}, {}, {}
        if path and os.path.exists(path):
            with open(path, encoding='utf-8') as fh:
                for k, v in json.load(fh).items():
                    if k == '_source' and isinstance(v, str):
                        self.source = v
                    elif k == '_compare' and isinstance(v, list):
                        self.compare = [canon_model(x) for x in v]
                    elif k == '_aliases' and isinstance(v, dict):
                        self.aliases = {str(a): _canon_id(b) for a, b in v.items() if isinstance(b, str) and not a.startswith('_')}
                    elif k == '_modifiers' and isinstance(v, dict):
                        self.modifiers = {a: b for a, b in v.items() if isinstance(b, dict) and not a.startswith('_')}
                    elif not k.startswith('_') and isinstance(v, dict):
                        self.table[canon_model(k)] = v
        self.compare = [k for k in self.compare if k in self.table] or sorted(self.table)
        self._memo = {}

    def rate(self, model):
        """List prices of a model. Unknown Claude models get the nearest version of their family (newest older one, else
        the oldest newer one) and are listed in `estimated`; anything else is `missing` and costs $0."""
        if model in self._memo:
            return self._memo[model]
        cm = canon_model(model)
        r = self.table.get(cm)
        if r is None:
            fam, v = model_parts(cm)
            same = sorted((model_parts(k)[1], k) for k in self.table if model_parts(k)[0] == fam) if fam else []
            if same:
                older = [k for kv, k in same if kv <= v]
                use = older[-1] if older else same[0][1]
                r = self.table[use]
                self.estimated[cm] = use
            elif model and model != '<synthetic>':
                self.missing.add(model)
        self._memo[model] = r
        return r

    def pick(self, family):
        """The model of that family the report compares against (from _compare), else the newest one priced."""
        for k in self.compare:
            if model_parts(k)[0] == family:
                return k
        same = sorted((model_parts(k)[1], k) for k in self.table if model_parts(k)[0] == family)
        return same[-1][1] if same else None

    def mult(self, model, where=None, fast=False):
        """What a call pays over list price: _modifiers[where] (bedrock_regional, api_regional; global routing has none) for
        models from its version on, times the model's own "fast" premium when it ran in fast mode."""
        x = 1.0
        mod = self.modifiers.get(where) if where else None
        if mod:
            fam, v = model_parts(canon_model(model))
            if fam and v >= tuple(mod.get('from') or (0, 0)):
                x *= mod.get('mult') or 1
        if fast:
            x *= (self.rate(model) or {}).get('fast') or 1
        return x

    def cost(self, model, u, mult=1.0):
        """What the usage cost at this model's list prices, times mult (a call's pm, from mult())."""
        r = self.rate(model)
        if not r:
            return None
        cc = u.get('cache_creation') or {}
        w5, w1 = cc.get('ephemeral_5m_input_tokens'), cc.get('ephemeral_1h_input_tokens')
        if w5 is None and w1 is None:
            w5, w1 = u.get('cache_creation_input_tokens') or 0, 0
        return {'input': (u.get('input_tokens') or 0) * r['in'] / 1e6 * mult,
                'output': (u.get('output_tokens') or 0) * r['out'] / 1e6 * mult,
                'cache_read': (u.get('cache_read_input_tokens') or 0) * r['cr'] / 1e6 * mult,
                'write_5m': (w5 or 0) * r['cw5m'] / 1e6 * mult,
                'write_1h': (w1 or 0) * r['cw1h'] / 1e6 * mult}

    def per_token(self, model, kind, mult=1.0):
        r = self.rate(model) or {}
        return (r.get(kind) or 0) / 1e6 * mult


# ---------------------------------------------------------------------------------------------- loading

REPEATED = {'type', 'sessionId', 'session_id', 'cwd', 'gitBranch', 'userType', 'version', 'entrypoint', 'role', 'slug', 'model',
            'service_tier', 'inference_geo', 'speed', 'stop_reason', 'effort', 'permissionMode', 'promptSource', 'agentId',
            'name', 'subtype', 'level', 'hookEvent'}          # keys whose values recur from record to record


def json_decoder():
    """json.loads for transcript lines, with records sharing their strings: one object per distinct key, and per distinct
    value of a REPEATED key. json.loads makes new ones for every record, which was a third of the memory."""
    pool = {}
    share = pool.setdefault

    def pairs(kv):
        return {share(k, k): (share(v, v) if k in REPEATED and type(v) is str else v) for k, v in kv}
    return json.JSONDecoder(object_pairs_hook=pairs).decode


def last_time(path, n=65536):
    """The latest timestamp among the whole records in the last n bytes of a transcript (records are appended in time
    order), or None when that tail holds none."""
    try:
        with open(path, 'rb') as fh:
            fh.seek(0, os.SEEK_END)
            end = fh.tell()
            fh.seek(max(0, end - n))
            lines = fh.read().split(b'\n')
    except OSError:
        return None
    times = []
    for line in lines[1:] if end > n else lines:                      # the first line of a tail is cut
        try:
            d = json.loads(line.decode('utf-8', errors='replace'))
        except ValueError:
            continue
        if isinstance(d, dict) and isinstance(d.get('timestamp'), str):
            times.append(parse_ts(d['timestamp']))
    times = [t for t in times if t is not None]
    return max(times) if times else None


class Source:
    """Everything read from disk once. Scopes are built from subsets of `recs`."""

    def __init__(self, projects_dir, since=None, until=None, log=print, home=None):
        self.dir = os.path.abspath(os.path.expanduser(projects_dir))
        self.home = home or os.path.dirname(self.dir)          # the Claude Code config folder (~/.claude by default)
        self.recs, self.bad_lines, self.files = [], 0, 0
        decode = json_decoder()
        for path in sorted(glob.glob(os.path.join(self.dir, '**', '*.jsonl'), recursive=True)):
            parts = os.path.relpath(path, self.dir).split(os.sep)
            proj, sub = parts[0], 'subagents' in parts[:-1]
            if since and os.path.getmtime(path) < since - 86400:     # untouched since before the range (a day's slack)...
                last = last_time(path)
                if last is not None and last < since:                # ...and so is its last record (a copy can keep an old mtime)
                    continue
            self.files += 1
            with open(path, encoding='utf-8', errors='replace') as fh:
                for i, line in enumerate(fh):
                    if not line.strip():
                        continue
                    try:
                        d = decode(line)
                    except ValueError:
                        self.bad_lines += 1
                        continue
                    if not isinstance(d, dict):
                        continue
                    t = parse_ts(d['timestamp']) if isinstance(d.get('timestamp'), str) else None
                    if t is not None and ((since and t < since) or (until and t >= until)):
                        continue
                    d['_t'], d['_proj'], d['_sub'], d['_f'], d['_i'] = t, proj, sub, path, i
                    slim(d)
                    self.recs.append(d)
        log(f'  read {len(self.recs):,} records from {self.files} transcript files')
        self.metas = {}
        for path in glob.glob(os.path.join(self.dir, '**', 'subagents', '*.meta.json'), recursive=True):
            aid = os.path.basename(path)[len('agent-'):-len('.meta.json')]
            try:
                with open(path, encoding='utf-8') as fh:
                    self.metas[aid] = json.load(fh)
            except (ValueError, OSError):
                pass
        self.persisted = collections.defaultdict(list)
        for path in glob.glob(os.path.join(self.dir, '**', 'tool-results', '*'), recursive=True):
            if os.path.isfile(path):
                self.persisted[os.path.relpath(path, self.dir).split(os.sep)[0]].append(os.path.getsize(path))
        self.settings = self._json(os.path.join(self.home, 'settings.json')) or {}
        self.local_settings = self._json(os.path.join(self.home, 'settings.local.json')) or {}
        self.plugins = (self._json(os.path.join(self.home, 'plugins', 'installed_plugins.json')) or {}).get('plugins') or {}
        cj = {}
        for path in (os.path.join(self.home, '.config.json'), os.path.join(self.home, '.claude.json'),     # $CLAUDE_CONFIG_DIR
                     os.path.join(os.path.dirname(self.home), '.claude.json'), os.path.join(USER_HOME, '.claude.json')):
            js = self._json(path)
            if isinstance(js, dict) and ('projects' in js or 'mcpServers' in js):
                cj = js
                break
        mcp_type = lambda v: (v.get('type') or ('http' if v.get('url') else 'stdio')) if isinstance(v, dict) else '?'
        self.mcp = {'user': {k: mcp_type(v) for k, v in (cj.get('mcpServers') or {}).items()}, 'projects': {}}
        for path, pj in (cj.get('projects') or {}).items():
            if isinstance(pj, dict) and (pj.get('mcpServers') or pj.get('disabledMcpjsonServers') or pj.get('enabledMcpjsonServers')):
                self.mcp['projects'][path] = {'servers': {k: mcp_type(v) for k, v in (pj.get('mcpServers') or {}).items()},
                                              'enabledMcpjsonServers': pj.get('enabledMcpjsonServers') or [],
                                              'disabledMcpjsonServers': pj.get('disabledMcpjsonServers') or []}
        self.cwd = {}
        cnt, seen = collections.defaultdict(collections.Counter), set()
        for d in self.recs:                       # a project dir is named after where its sessions started
            if d.get('cwd') and not d['_sub'] and d['_f'] not in seen:
                seen.add(d['_f'])
                cnt[d['_proj']][d['cwd']] += 1
        for p in sorted({d['_proj'] for d in self.recs}):       # sorted: set order changes from run to run
            self.cwd[p] = cnt[p].most_common(1)[0][0] if cnt[p] else None
        self.project_settings = {}                  # settings files of every project that has sessions
        for p, cwd in self.cwd.items():
            for name in ('settings.json', 'settings.local.json'):
                path = os.path.join(cwd, '.claude', name) if cwd else None
                js = self._json(path) if cwd else None
                if isinstance(js, dict):
                    self.project_settings.setdefault(p, {})[path] = js

    @staticmethod
    def _json(path):
        try:
            with open(path, encoding='utf-8') as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def label(self, proj):
        cwd = self.cwd.get(proj)
        if not cwd:
            m = re.match(r'(.*?)--claude-worktrees-(.+)$', proj)
            if m:
                return f'{os.path.basename(m.group(1).replace("-", "/"))} ▸ {clip(m.group(2), 28)}'
            return short_path(proj.replace('-', '/')) or proj
        m = re.match(r'(.*)/\.claude/worktrees/(.+)$', slash(cwd))
        if m:
            return f'{os.path.basename(m.group(1))} ▸ {clip(m.group(2), 28)}'
        return short_path(cwd) or cwd

    def scopes(self):
        by_dir = collections.Counter(d['_proj'] for d in self.recs if d.get('type') == 'assistant'
                                     and (d.get('message') or {}).get('model') not in (None, '<synthetic>'))
        groups = collections.defaultdict(list)
        for p in by_dir:
            groups[re.sub(r'--claude-worktrees-.*$', '', p)].append(p)
        out = [{'id': 'all', 'label': 'All projects', 'kind': 'all', 'dirs': set(by_dir)}]
        for key, dirs in sorted(groups.items(), key=lambda kv: -sum(by_dir[p] for p in kv[1])):
            dirs.sort(key=lambda p: (p != key, -by_dir[p]))
            if len(dirs) > 1:
                gid = 'g:' + key
                base = self.label(key) if key in by_dir else key
                out.append({'id': gid, 'label': f'{base} + {len(dirs) - 1} worktree{"s" if len(dirs) > 2 else ""}',
                            'kind': 'group', 'dirs': set(dirs)})
                for p in dirs:
                    out.append({'id': 'd:' + p, 'label': self.label(p) + (' (checkout)' if p == key else ''),
                                'kind': 'dir', 'parent': gid, 'dirs': {p}})
            else:
                out.append({'id': 'd:' + dirs[0], 'label': self.label(dirs[0]), 'kind': 'group', 'dirs': {dirs[0]}})
        return out

def redact(x, key=''):
    """Settings with secrets removed: every value under a secret-looking key (lists included), secrets inside other
    values (credentials in URLs, Bearer tokens, FOO_TOKEN=…, --api-key …, well-known key formats), and any value that
    looks like a random token on its own."""
    if isinstance(x, dict):
        return {k: redact(v, k) for k, v in x.items()}
    if isinstance(x, list):
        return [redact(v, key) for v in x]
    if isinstance(x, str):
        if (key and SECRET.search(key)) or TOKENISH.match(x):
            return '<redacted>'
        for rx, sub in SECRET_IN_TEXT:
            x = rx.sub(sub, x)
        return x[:400] + '…' if len(x) > 400 else x
    if key and SECRET.search(key) and x is not None and not isinstance(x, bool):
        return '<redacted>'
    return x


def config_snapshot(src):
    """What the optimize skill needs to know about the current setup; secrets redacted."""
    def settings_view(js):
        v = {k: js[k] for k in js if k not in ('permissions',)}
        perms = js.get('permissions') or {}
        if perms:
            v['permissions'] = {k: (f'{len(val)} rules' if isinstance(val, list) else val) for k, val in perms.items()}
        return redact(v)
    projects = {}
    for p, files in src.project_settings.items():
        projects[src.label(p)] = {path: settings_view(js) for path, js in files.items()}
    def skill_names(root, pattern):
        out = []
        for path in sorted(glob.glob(os.path.join(root, pattern))):
            name = os.path.basename(os.path.dirname(path)) if path.endswith('SKILL.md') else os.path.splitext(os.path.basename(path))[0]
            try:
                with open(path, encoding='utf-8', errors='replace') as fh:
                    head = fh.read(1500)
                mm = re.search(r'^name:\s*(.+)$', head, re.M)
                if mm and path.endswith('SKILL.md'):
                    name = mm.group(1).strip().strip('"\'')
                manual = bool(re.search(r'^disable-model-invocation:\s*true', head, re.M))
            except OSError:
                manual = False
            out.append({'name': name, 'path': tilde(path), 'model_invocable': not manual})
        return out
    inv = {'user_skills': skill_names(src.home, os.path.join('skills', '*', 'SKILL.md')),
           'user_commands': skill_names(src.home, os.path.join('commands', '*.md')),
           'skills_dir_plugins': [os.path.basename(os.path.dirname(os.path.dirname(p_))) for p_ in
                                  glob.glob(os.path.join(src.home, 'skills', '*', '.claude-plugin', 'plugin.json'))],
           'synced_skills': sorted({os.path.basename(os.path.dirname(p_)) for p_ in
                                    glob.glob(os.path.join(src.home, 'skills', 'synced', '*', '*', 'SKILL.md'))}),
           'project_skills': {}, 'plugin_skills': {}}
    for p_, cwd in src.cwd.items():
        if cwd:
            sk = skill_names(cwd, os.path.join('.claude', 'skills', '*', 'SKILL.md')) + skill_names(cwd, os.path.join('.claude', 'commands', '*.md'))
            if sk:
                inv['project_skills'][src.label(p_)] = sk
    for name, inst in src.plugins.items():
        for i in (inst if isinstance(inst, list) else [inst]):
            root = (i or {}).get('installPath')
            if root:
                inv['plugin_skills'][name] = [x['name'] for x in skill_names(root, os.path.join('skills', '*', 'SKILL.md'))]
    return {'skills_inventory': inv,
            'user_settings': {os.path.join(src.home, 'settings.json'): settings_view(src.settings)},
            'user_local_settings': {os.path.join(src.home, 'settings.local.json'): settings_view(src.local_settings)} if src.local_settings else {},
            'project_settings': projects,
            'mcp_servers': src.mcp,
            'installed_plugins': {k: [{kk: vv for kk, vv in (i or {}).items() if kk in ('scope', 'version', 'installPath')}
                                      for i in (v if isinstance(v, list) else [v])] for k, v in src.plugins.items()},
            'home': USER_HOME, 'claude_dir': tilde(src.home)}


# ------------------------------------------------------------------------------------------ classifiers

def thread_key(d):
    return (d.get('sessionId'), d.get('agentId') if d.get('isSidechain') else 'main')


_BASH = [
    ('test', re.compile(r'\b(swift test|pytest|py\.test|(npm|yarn|pnpm) (run )?test|jest|vitest|go test|cargo test|'
                        r'xcodebuild test|swift run \S*(Tests?|Checks?)\b|ctest|rspec|phpunit|mvn test|gradle test)')),
    ('build', re.compile(r'\b(swift build|xcodebuild|make\b|(npm|yarn|pnpm) (run )?build|cargo build|go build|tsc\b|'
                         r'gradle\b|mvn\b|cmake\b|webpack|vite build|next build)')),
    ('git', re.compile(r'(^|[;&|(]\s*|\s)(git|gh)\s')),
    ('run script', re.compile(r'\b(python3?|node|ruby|bash|sh|deno|bun)\b[^|;&]*<<')),
    ('write file', re.compile(r"cat\s*>|cat\s*<<|<<\s*['\"]?\w*EOF['\"]?\s*>|\btee\s|sed\s+-i|\b(cp|mv|mkdir|rm|touch)\s")),
    ('run script', re.compile(r'\b(python3?|node|ruby|swift run|deno|bun|osascript|open)\b|(^|\s)\./|\bbash\s+\S')),
    ('search/inspect', re.compile(r'\b(grep|rg|find|ls|cat|head|tail|wc|jq|sed|awk|stat|du|file|tree|which|ps|lsof|'
                                  r'diff|sort|uniq|echo|pwd|sqlite3|plutil|defaults)\b')),
]


def bash_class(cmd):
    return _bash_class(cmd if isinstance(cmd, str) else '')


@functools.lru_cache(maxsize=None)
def _bash_class(cmd):
    """bash_class for a command string. Cached: every scope that holds a command classifies it again."""
    for name, rx in _BASH:
        if rx.search(cmd):
            return name
    return 'other'


def tool_phase(name, inp):
    if name in EDIT_TOOLS:
        return 'build'
    if name == 'Bash':
        return {'test': 'verify', 'build': 'verify', 'git': 'git', 'write file': 'build'}.get(
            bash_class((inp or {}).get('command', '')), 'explore')
    if name in COORD_TOOLS:
        return 'coordinate'
    return 'explore'


def tool_group(name):
    return 'MCP tools' if name.startswith('mcp__') else name


def tool_detail(tu, cwd=None):
    i, n = tu['input'] or {}, tu['name']
    if n in ('Read', 'Edit', 'Write', 'MultiEdit', 'NotebookEdit'):
        return clip_path(short_path(i.get('file_path') or i.get('notebook_path') or '', cwd), 64)
    if n == 'Bash':
        return clip(i.get('command', ''), 70)
    if n in ('Grep', 'Glob'):
        return clip(i.get('pattern', ''), 60)
    if n in ('Agent', 'Task'):
        return clip(i.get('description') or i.get('prompt') or '', 60)
    if n == 'WebFetch':
        return clip(i.get('url', ''), 70)
    if n == 'WebSearch':
        return clip(i.get('query', ''), 70)
    if n == 'Skill':
        return i.get('skill', '')
    return clip(json.dumps(i, ensure_ascii=False), 60)


INJECT_FIELDS = {
    'hook_additional_context': ['content'], 'hook_system_message': ['content'], 'skill_listing': ['content'],
    'deferred_tools_delta': ['addedLines'], 'agent_listing_delta': ['addedLines'],
    'mcp_instructions_delta': ['addedBlocks'], 'total_tokens_reminder': ['text'], 'edited_text_file': ['snippet'],
    'diagnostics': ['files'], 'queued_command': ['prompt'], 'read_truncation_notice': ['banner'], 'model': ['text'],
    'session_context': ['context'], 'date': ['date'], 'date_change': ['newDate'], 'environment': ['snapshot'],
    'prompt_snapshot': [], 'hook_success': [], 'hook_non_blocking_error': [], 'hook_cancelled': [],
    'deferred_tools_record': ['entries'],
}
ATT_SOURCE = {
    'hook_additional_context': 'Hook context', 'hook_system_message': 'Hook context',
    'skill_listing': 'Skill listing', 'deferred_tools_delta': 'Deferred-tool list', 'deferred_tools_record': 'Deferred-tool list',
    'agent_listing_delta': 'Agent listing', 'mcp_instructions_delta': 'MCP instructions',
    'edited_text_file': 'File-change notices', 'read_truncation_notice': 'File-change notices',
    'diagnostics': 'LSP diagnostics', 'queued_command': 'Queued prompts & notifications',
    'plan_mode': 'Mode notices', 'plan_mode_exit': 'Mode notices', 'plan_mode_reentry': 'Mode notices',
    'auto_mode': 'Mode notices', 'auto_mode_exit': 'Mode notices', 'command_permissions': 'Mode notices',
}


def injected_chars(a):
    fields = INJECT_FIELDS.get(a.get('type'))
    if fields is None:
        fields = [k for k in a if k != 'type']
    return sum(str_len(a.get(f)) for f in fields)


def att_source(typ):
    return ATT_SOURCE.get(typ) or ('Reminders & session setup' if typ in (
        'total_tokens_reminder', 'date', 'date_change', 'model', 'session_context', 'environment', 'credential_org',
        'remote_session_change') else 'Other attachments')


def hook_script(cmd):
    cmd = cmd or ''
    m = re.search(r"['\"]?([^'\"\s]+\.(?:sh|py|js|ts|rb))['\"]?", cmd)
    return os.path.basename(m.group(1)) if m else clip(cmd, 40)


# ------------------------------------------------------------------------------------------------ model

class Model:
    """All derived structures for one scope (a set of project directories)."""

    def __init__(self, src, recs, prices):
        self.src, self.recs, self.prices = src, recs, prices
        self.facts, self.cache = {}, {}
        self.dirs = sorted({d['_proj'] for d in recs})
        self._calls()
        self._threads()
        self._tools()
        self._user_side()
        self._sessions()
        self._turns()
        self._subagents()
        self._items()
        self.usd = sum(c['usd'] for c in self.real)

    # -- API calls ---------------------------------------------------------------------------------------
    def _calls(self):
        calls = {}
        for d in self.recs:
            if d.get('type') != 'assistant':
                continue
            m = d.get('message')
            if not isinstance(m, dict) or not isinstance(m.get('usage'), dict):
                continue
            key = (m.get('id'), d.get('requestId'))
            t = d['_t']
            c = calls.get(key)
            if c is None:
                c = calls[key] = dict(
                    key=key, sid=d.get('sessionId'), agent=d.get('agentId') if d.get('isSidechain') else None,
                    proj=d['_proj'], model=canon_model(m.get('model')), raw=m.get('model'), effort=d.get('effort'), skill=d.get('attributionSkill'),
                    agt=d.get('attributionAgent'), t0=t, t1=t, u=m['usage'], stop=None, diag=None, blocks=[],
                    seen=set(), entry=d.get('entrypoint'), ver=d.get('version'),
                    err=d.get('error') if d.get('isApiErrorMessage') else None, errtext=None)
            else:
                if t is not None:
                    c['t0'] = t if c['t0'] is None else min(c['t0'], t)
                    c['t1'] = t if c['t1'] is None else max(c['t1'], t)
                if (m['usage'].get('output_tokens') or 0) >= (c['u'].get('output_tokens') or 0):
                    c['u'] = m['usage']
            if m.get('stop_reason'):
                c['stop'] = m['stop_reason']
            if m.get('diagnostics'):
                c['diag'] = m['diagnostics']
            for k in ('effort', 'skill', 'agt'):
                if c[k] is None:
                    c[k] = d.get({'effort': 'effort', 'skill': 'attributionSkill', 'agt': 'attributionAgent'}[k])
            if c['err'] and c['errtext'] is None:
                c['errtext'] = clip(text_of(d), 160)
            for b in m.get('content') or []:
                if not isinstance(b, dict):
                    continue
                bid = b.get('id') or (b.get('type'), hash(json.dumps(b, sort_keys=True, ensure_ascii=False)))
                if bid not in c['seen']:
                    c['seen'].add(bid)
                    c['blocks'].append((b, t))
        self.calls = []
        for c in calls.values():
            u = c['u']
            c.pop('seen')
            c['synthetic'] = c['model'] == '<synthetic>'
            c['inp'] = u.get('input_tokens') or 0
            c['cr'] = u.get('cache_read_input_tokens') or 0
            c['cw'] = u.get('cache_creation_input_tokens') or 0
            cc = u.get('cache_creation') or {}
            w5, w1 = cc.get('ephemeral_5m_input_tokens'), cc.get('ephemeral_1h_input_tokens')
            if w5 is None and w1 is None:
                w5, w1 = c['cw'], 0
            c['cw5'], c['cw1'] = w5 or 0, w1 or 0
            c['out'] = u.get('output_tokens') or 0
            c['think'] = (u.get('output_tokens_details') or {}).get('thinking_tokens') or 0
            c['ctx'] = c['inp'] + c['cr'] + c['cw']
            stu = u.get('server_tool_use') or {}
            c['web'] = (stu.get('web_search_requests') or 0) + (stu.get('web_fetch_requests') or 0)
            c['where'] = where_of(c.pop('raw'), u)
            c['fast'] = u.get('speed') == 'fast'
            c['pm'] = 1.0 if c['synthetic'] else self.prices.mult(c['model'], c['where'], c['fast'])
            parts = None if c['synthetic'] else self.prices.cost(c['model'], u, c['pm'])
            c['cost'] = parts or {}
            c['usd'] = sum(parts.values()) if parts else 0.0
            c['tools'] = [(b, t) for b, t in c['blocks'] if b.get('type') == 'tool_use']
            c['text_chars'] = sum(len(b.get('text') or '') for b, _ in c['blocks'] if b.get('type') == 'text')
            if c['t0'] is None:
                continue
            self.calls.append(c)
        self.calls.sort(key=lambda c: (c['t0'], c['t1']))
        self.real = [c for c in self.calls if not c['synthetic']]
        self.errors = [c for c in self.calls if c['synthetic'] or c['err']]

    # -- threads: request start, gaps, cache misses -----------------------------------------------------
    def _threads(self):
        self.threads = collections.defaultdict(list)
        for c in self.real:
            self.threads[(c['sid'], c['agent'] or 'main')].append(c)
        ev, comp = collections.defaultdict(list), collections.defaultdict(list)
        for d in self.recs:
            if d.get('type') in ('user', 'attachment') and d['_t'] is not None and d.get('sessionId'):
                ev[thread_key(d)].append(d['_t'])
            elif d.get('type') == 'system' and d.get('subtype') == 'compact_boundary' and d['_t'] is not None:
                comp[thread_key(d)].append(d['_t'])
        for v in list(ev.values()) + list(comp.values()):
            v.sort()
        self.ttl = {}
        for key, t in self.threads.items():
            t.sort(key=lambda c: (c['t0'], c['t1']))
            e, cp = ev.get(key, []), comp.get(key, [])
            w1, w5 = sum(c['cw1'] for c in t), sum(c['cw5'] for c in t)
            ttl = 3600 if (w1 >= w5 and w1 > 0) else 300 if w5 > 0 else (3600 if key[1] == 'main' else 300)
            self.ttl[key] = ttl
            prev = None
            for i, c in enumerate(t):
                c.update(thread=key, idx=i, ttl=ttl, is_sub=key[1] != 'main', prev=prev)
                j = bisect.bisect_right(e, c['t0']) - 1
                st = e[j] if j >= 0 else c['t0']
                if prev:
                    st = max(st, prev['t1'])
                c['start'] = min(st, c['t0'])
                c['gen'] = max(0.0, c['t1'] - c['start'])
                c['gap'] = (c['start'] - prev['start']) if prev else None
                # a compaction replaces the conversation, so nothing after it is a re-write; and a call can't
                # re-write more than it wrote
                c['compacted'] = bool(prev) and bisect.bisect_right(cp, c['t0']) > bisect.bisect_right(cp, prev['t1'])
                c['rewritten'] = max(0, min(prev['ctx'] - c['cr'] - c['inp'], c['cw'])) if prev and not c['compacted'] else 0
                c['miss'] = c['rewritten'] > MISS_MIN
                prev = c
        self.misses = [c for c in self.real if c['miss']]

    # -- tools --------------------------------------------------------------------------------------------
    def _tools(self):
        self.tool_uses = {}
        for c in self.real:
            for b, t in c['tools']:
                tid = b.get('id')
                if not tid or tid in self.tool_uses:
                    continue
                inp = b.get('input') if isinstance(b.get('input'), dict) else {}
                name = b.get('name') or '?'
                self.tool_uses[tid] = dict(
                    id=tid, name=name, input=inp, t=t, call=c, sid=c['sid'], agent=c['agent'], proj=c['proj'],
                    thread=c['thread'], in_chars=len(json.dumps(inp, ensure_ascii=False)), res=None, latency=None,
                    phase=tool_phase(name, inp), bash=bash_class(inp.get('command', '')) if name == 'Bash' else None)
        self.denials = []
        for d in self.recs:
            if d.get('type') != 'user':
                continue
            content = (d.get('message') or {}).get('content')
            if not isinstance(content, list):
                continue
            tur = d.get('toolUseResult')
            for b in content:
                if not (isinstance(b, dict) and b.get('type') == 'tool_result'):
                    continue
                tu = self.tool_uses.get(b.get('tool_use_id'))
                if tu is None or tu['res'] is not None:
                    continue
                chars, head, images, failed = b.get('_res') or (0, '', 0, False)       # see slim()
                tu['res'] = dict(t=d['_t'], is_error=bool(b.get('is_error')), chars=chars, images=images,
                                 text=head, tur=tur if isinstance(tur, dict) else None, fail=bool(b.get('is_error')) or failed)
                if d['_t'] is not None and tu['t'] is not None:
                    tu['latency'] = max(0.0, d['_t'] - tu['t'])
                if d.get('toolDenialKind'):
                    self.denials.append(dict(t=d['_t'], sid=d.get('sessionId'), tool=tu['name'], kind=d['toolDenialKind'],
                                             feedback=clip(d.get('userFeedback') or '', 120)))
        self.tools = sorted(self.tool_uses.values(), key=lambda x: x['t'] or 0)

    # -- prompts, commands, attachments, system records ------------------------------------------------
    def _user_side(self):
        self.prompts, self.sdk_prompts, self.commands, self.notifs = [], [], [], []
        self.interrupts, self.other_user = [], []
        self.notif_result = {}
        seen_queued = set()
        tagged = {d.get('sessionId') for d in self.recs if d.get('type') == 'user' and d.get('promptSource')}   # newer versions log promptSource
        for d in self.recs:
            if d.get('type') == 'queue-operation' and isinstance(d.get('content'), str):
                self._notif_parse(d['content'])
            if d.get('type') != 'user' or d['_t'] is None:
                continue
            content = (d.get('message') or {}).get('content')
            if isinstance(content, list) and any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in content):
                continue
            text = text_of(d)
            side = bool(d.get('isSidechain'))
            rec = dict(t=d['_t'], sid=d.get('sessionId'), proj=d['_proj'], text=text, images=images_in(content),
                       mode=d.get('permissionMode'), uuid=d.get('uuid'), parent=d.get('parentUuid'), side=side,
                       thread=thread_key(d), source=d.get('promptSource'), meta=bool(d.get('isMeta')))
            s = text.lstrip()
            ps = d.get('promptSource')
            if (ps is None and d.get('sessionId') not in tagged and not side and not rec['meta'] and s
                    and not d.get('isCompactSummary') and not d.get('isVisibleInTranscriptOnly')
                    and not s.startswith(('<', '[Request interrupted', 'Caveat:'))):
                ps = 'sdk' if str(d.get('entrypoint') or '').startswith('sdk') else 'typed'     # older versions: infer it
            if ps in ('typed', 'queued') and not side:
                self.prompts.append(rec)
                if ps == 'queued':
                    seen_queued.add((rec['sid'], clip(text, 80)))
            elif ps == 'sdk':
                self.sdk_prompts.append(rec)
            elif '<command-name>' in s[:300]:
                m = re.search(r'<command-name>/?([^<]+)</command-name>', s)
                a = re.search(r'<command-args>([^<]*)</command-args>', s)
                rec['name'] = (m.group(1).strip() if m else '?')
                rec['args'] = a.group(1).strip() if a else ''
                self.commands.append(rec)
            elif s.startswith('<task-notification>'):
                rec['task'] = self._notif_parse(s)
                self.notifs.append(rec)
            elif s.startswith('[Request interrupted'):
                self.interrupts.append(rec)
            else:
                self.other_user.append(rec)
        self.atts = []
        self.hook_runs = []
        for d in self.recs:
            if d.get('type') != 'attachment' or not isinstance(d.get('attachment'), dict):
                continue
            a = d['attachment']
            typ = a.get('type') or '?'
            rec = dict(t=d['_t'], sid=d.get('sessionId'), proj=d['_proj'], thread=thread_key(d), type=typ, a=a,
                       chars=injected_chars(a), images=images_deep(a.get('prompt')) if typ == 'queued_command' else 0)
            self.atts.append(rec)
            if typ in ('hook_success', 'hook_non_blocking_error', 'hook_cancelled', 'hook_blocking_error'):
                self.hook_runs.append(dict(t=d['_t'], sid=rec['sid'], event=a.get('hookEvent') or '?',
                                           script=hook_script(a.get('command')), ms=a.get('durationMs') or 0,
                                           ok=typ == 'hook_success' and (a.get('exitCode') in (0, None)),
                                           code=a.get('exitCode'), kind=typ, stderr=clip(a.get('stderr') or '', 140)))
            if typ == 'queued_command' and a.get('commandMode') == 'prompt' and (a.get('origin') or {}).get('kind') == 'human':
                p = a.get('prompt')
                text = p if isinstance(p, str) else '\n'.join(
                    b.get('text') or '' for b in (p or []) if isinstance(b, dict) and b.get('type') == 'text')
                if (rec['sid'], clip(text, 80)) not in seen_queued and d['_t'] is not None:
                    self.prompts.append(dict(t=d['_t'], sid=rec['sid'], proj=rec['proj'], text=text, images=rec['images'],
                                             mode=None, uuid=d.get('uuid'), parent=d.get('parentUuid'), side=False,
                                             thread=rec['thread'], source='queued', meta=False))
        self.prompts.sort(key=lambda p: p['t'])
        self.sys = collections.defaultdict(list)
        self.titles, self.names, self.bridge, self.pr_links = {}, {}, {}, []
        for d in self.recs:
            typ = d.get('type')
            if typ == 'system':
                self.sys[d.get('subtype') or '?'].append(d)
            elif typ == 'ai-title' and d.get('sessionId'):
                self.titles[d['sessionId']] = d.get('aiTitle')
            elif typ == 'agent-name' and d.get('sessionId'):
                self.names[d['sessionId']] = d.get('agentName')
            elif typ == 'bridge-session' and d.get('sessionId'):
                self.bridge[d['sessionId']] = d.get('bridgeSessionId')
            elif typ == 'pr-link':
                self.pr_links.append(d)

    def _notif_parse(self, s):
        m = re.search(r'<task-id>([^<]+)</task-id>', s)
        if not m:
            return None
        tid = m.group(1).strip()
        r = re.search(r'<result>(.*?)</result>', s, re.S)
        if r:
            self.notif_result[tid] = max(self.notif_result.get(tid, 0), len(r.group(1)))
        return tid

    # -- sessions -------------------------------------------------------------------------------------------
    def _sessions(self):
        S = {}
        for d in self.recs:
            sid = d.get('sessionId')
            if not sid or d['_sub']:
                continue
            s = S.get(sid)
            if s is None:
                s = S[sid] = dict(sid=sid, proj=d['_proj'], times=[], cwd=collections.Counter(), branches=set(),
                                  entry=set(), versions=set())
            if d['_t'] is not None:
                s['times'].append(d['_t'])
            if d.get('cwd'):
                s['cwd'][d['cwd']] += 1
                if d['_t'] is not None and d['_t'] < s.get('cwd_t', float('inf')):
                    s['cwd_t'], s['cwd_first'] = d['_t'], d['cwd']      # where the session started (cd can move it later)
            if d.get('gitBranch'):
                s['branches'].add(d['gitBranch'])
            if d.get('entrypoint'):
                s['entry'].add(d['entrypoint'])
            if d.get('version'):
                s['versions'].add(d['version'])
        by_sid = collections.defaultdict(list)
        for c in self.real:
            by_sid[c['sid']].append(c)
        pr = collections.Counter(p['sid'] for p in self.prompts)
        cm = collections.Counter(c['sid'] for c in self.commands)
        sdk = collections.Counter(p['sid'] for p in self.sdk_prompts)
        first_cmd = {}
        for c in sorted(self.commands, key=lambda c: c['t']):
            first_cmd.setdefault(c['sid'], c['name'])
        self.sessions = {}
        for sid, s in S.items():
            if not s['times']:
                continue
            ts_ = sorted(s['times'])
            calls = sorted(by_sid.get(sid, []), key=lambda c: c['t0'])
            main = [c for c in calls if not c['agent']]
            s.update(start=ts_[0], end=ts_[-1], wall=ts_[-1] - ts_[0],
                     active=sum(min(b - a, IDLE_CAP) for a, b in zip(ts_, ts_[1:])),
                     calls=calls, n_calls=len(calls), main_calls=main, usd=sum(c['usd'] for c in calls),
                     prompts=pr.get(sid, 0), commands=cm.get(sid, 0), sdk=sdk.get(sid, 0),
                     title=self.titles.get(sid) or '', name=self.names.get(sid), bridge=self.bridge.get(sid),
                     cwd_main=s['cwd'].most_common(1)[0][0] if s['cwd'] else None,
                     first_cmd=first_cmd.get(sid))
            s['peak'] = max((c['ctx'] for c in main), default=0)
            s['baseline'] = main[0]['ctx'] if main else None
            fp = next((p['text'] for p in self.prompts if p['sid'] == sid and p['text'].strip()), None)
            s['label'] = s['title'] or s['name'] or (clip(fp, 60) if fp else None) or (f'/{s["first_cmd"]}' if s['first_cmd'] else sid[:8])
            if not calls:
                kind = 'Empty (no API calls)'
            elif s['entry'] & {'sdk-cli', 'sdk-ts', 'sdk-py'} or (s['sdk'] and not s['prompts']):
                kind = 'Automated (SDK)'
            elif s['first_cmd'] and s['prompts'] <= 1 and s['first_cmd'] not in ('clear', 'model', 'effort', 'resume'):
                kind = 'Started by a command/skill'
            elif s['prompts'] <= 2 and len(calls) <= 25:
                kind = 'Quick (≤2 prompts)'
            else:
                kind = 'Working session'
            s['kind'] = kind
            self.sessions[sid] = s
        self.sess_times = sorted((s['start'], sid) for sid, s in self.sessions.items())

    # -- turns ------------------------------------------------------------------------------------------------
    def _turns(self):
        starters = collections.defaultdict(list)
        for p in self.prompts:
            if p['source'] == 'typed':
                starters[p['sid']].append((p['t'], 'prompt', p))
        for c in self.commands:
            if not c['side']:
                starters[c['sid']].append((c['t'], 'command', c))
        for p in self.sdk_prompts:
            starters[p['sid']].append((p['t'], 'sdk', p))
        tds = collections.defaultdict(list)
        for d in self.sys.get('turn_duration', []):
            if d['_t'] is not None:
                tds[d.get('sessionId')].append((d['_t'], d.get('durationMs') or 0))
        ints = collections.defaultdict(list)
        for r in self.interrupts:
            ints[r['sid']].append(r['t'])
        self.turns = []
        for sid, st in starters.items():
            s = self.sessions.get(sid)
            if not s:
                continue
            st.sort(key=lambda x: x[0])
            times = [x[0] for x in st]
            turns = [dict(sid=sid, proj=s['proj'], start=t, end=(times[i + 1] if i + 1 < len(times) else float('inf')),
                          kind=k, text=r.get('text') if k != 'command' else '/' + r.get('name', '') + (' ' + r.get('args', '') if r.get('args') else ''),
                          mode=r.get('mode'), calls=[], busy_ms=0, interrupted=False, session=s)
                     for i, (t, k, r) in enumerate(st)]
            for c in s['calls']:
                i = bisect.bisect_right(times, c['t0']) - 1
                if i >= 0:
                    turns[i]['calls'].append(c)
            for t, ms in tds.get(sid, []):
                i = bisect.bisect_right(times, t) - 1
                if i >= 0:
                    turns[i]['busy_ms'] += ms
            for t in ints.get(sid, []):
                i = bisect.bisect_right(times, t) - 1
                if i >= 0:
                    turns[i]['interrupted'] = True
            for tr in turns:
                tr['usd'] = sum(c['usd'] for c in tr['calls'])
                tr['n_main'] = sum(1 for c in tr['calls'] if not c['agent'])
                tr['n_tools'] = sum(len(c['tools']) for c in tr['calls'] if not c['agent'])
                tr['subagents'] = len({c['agent'] for c in tr['calls'] if c['agent']})
                tr['last'] = max((c['t1'] for c in tr['calls']), default=tr['start'])
            self.turns.extend(turns)
        self.turns.sort(key=lambda t: t['start'])

    # -- subagents -----------------------------------------------------------------------------------------
    def _subagents(self):
        self.subs = {}
        for key, t in self.threads.items():
            if key[1] == 'main':
                continue
            aid = key[1]
            meta = self.src.metas.get(aid, {})
            launch = self.tool_uses.get(meta.get('toolUseId'))
            linp = launch['input'] if launch else {}
            typ = meta.get('agentType') or linp.get('subagent_type') or 'general-purpose'
            res = (launch or {}).get('res') or {}
            tur = res.get('tur') or {}
            is_async = bool(tur.get('isAsync') or linp.get('run_in_background') or tur.get('status') == 'async_launched')
            returned = self.notif_result.get(aid)
            if returned is None and res and not is_async:
                returned = res.get('chars')
            models = collections.Counter(c['model'] for c in t)
            self.subs[aid] = dict(
                aid=aid, sid=key[0], proj=t[0]['proj'], type=typ, desc=meta.get('description') or linp.get('description') or '',
                model=models.most_common(1)[0][0], calls=t, n_calls=len(t), start=t[0]['start'], end=t[-1]['t1'],
                usd=sum(c['usd'] for c in t), peak=max(c['ctx'] for c in t), cold=t[0]['ctx'],
                ctx_sum=sum(c['ctx'] for c in t), out=sum(c['out'] for c in t), is_async=is_async,
                depth=meta.get('spawnDepth') or 1, worktree=bool(meta.get('spawnedWithWorktree')),
                returned_chars=returned, misses=sum(1 for c in t if c['miss']),
                rewritten=sum(c['rewritten'] for c in t if c['miss']))

    # -- context items: what was added between calls, and how often it was re-read ------------------------
    def _items(self):
        cand = collections.defaultdict(list)          # thread -> [(t, source, detail, weight_tokens)]
        for tu in self.tools:
            r = tu['res']
            if r and r['t'] is not None:
                cand[tu['thread']].append((r['t'], 'Tool result: ' + tool_group(tu['name']),
                                           f"{tu['name']}: {tool_detail(tu, self._cwd(tu['sid']))}",
                                           r['chars'] / 4 + r['images'] * IMAGE_TOKENS, tu))
        for p in self.prompts:
            if p['text']:
                cand[p['thread']].append((p['t'], 'Your prompts (text)', clip(p['text'], 70), len(p['text']) / 4, None))
            if p['images']:
                cand[p['thread']].append((p['t'], 'Your prompts (images)', f"{p['images']} image(s) with: {clip(p['text'], 50)}",
                                          p['images'] * IMAGE_TOKENS, None))
        for c in self.commands:
            cand[c['thread']].append((c['t'], 'Commands & skill prompts', clip(c['text'], 70), len(c['text']) / 4, None))
        for n in self.notifs:
            cand[n['thread']].append((n['t'], 'Task notifications', clip(n['text'], 70), len(n['text']) / 4, None))
        for p in self.sdk_prompts:
            cand[p['thread']].append((p['t'], 'SDK prompts', clip(p['text'], 70), len(p['text']) / 4, None))
        for o in self.other_user:
            src_ = 'Subagent task prompt' if o['side'] else 'Commands & skill prompts'
            cand[o['thread']].append((o['t'], src_, clip(o['text'], 70), len(o['text']) / 4 + o['images'] * IMAGE_TOKENS, None))
        for a in self.atts:
            if a['t'] is None or (a['chars'] == 0 and not a['images']):
                continue
            cand[a['thread']].append((a['t'], att_source(a['type']), a['type'], a['chars'] / 4 + a['images'] * IMAGE_TOKENS, None))
        self.items = []
        for key, calls in self.threads.items():
            lst = sorted(cand.get(key, []), key=lambda x: x[0])
            times = [x[0] for x in lst]
            n = len(calls)
            for i, c in enumerate(calls):
                lo = bisect.bisect_right(times, calls[i - 1]['t1']) if i else 0
                hi = bisect.bisect_right(times, c['t0'])
                window = lst[lo:hi]
                rr = self.prices.per_token(c['model'], 'cr', c['pm'])
                reads = n - i - 1

                def add(source, detail, tokens, tu=None):
                    if tokens <= 0:
                        return
                    carry = tokens * reads
                    self.items.append(dict(thread=key, sid=key[0], source=source, detail=detail, tokens=tokens, k=i,
                                           reads=reads, carry=carry, carry_usd=carry * rr, t=c['t0'], tu=tu,
                                           model=c['model'], pm=c['pm']))
                W = sum(x[3] for x in window)
                if i == 0:
                    scale = min(1.0, c['ctx'] / W) if W else 1.0
                    for x in window:
                        add(x[1], x[2], x[3] * scale, x[4])
                    add('Start-up baseline (system prompt, tools, CLAUDE.md…)', 'first call of the thread',
                        max(0, c['ctx'] - W * scale))
                    continue
                p = calls[i - 1]
                add("Claude's own previous output", 'assistant turn', p['out'])
                resid = c['ctx'] - p['ctx'] - p['out']
                if resid <= 0:
                    continue
                if W > 0:
                    for x in window:
                        add(x[1], x[2], resid * x[3] / W, x[4])
                else:
                    add('Unattributed growth', 'no logged item between calls', resid)

    def _cwd(self, sid):
        s = self.sessions.get(sid)
        return s['cwd_main'] if s else None

    # -- shared derived values ---------------------------------------------------------------------------------
    def _between(self, kind, c):
        """Records of `kind` in c's thread that arrived after the previous call and before c."""
        if not hasattr(self, '_idx'):
            idx = collections.defaultdict(list)
            for o in self.other_user:
                if o['side']:
                    idx[('resume', o['thread'])].append(o['t'])
            for a in self.atts:
                if a['type'] in ('deferred_tools_delta', 'mcp_instructions_delta') and a['t'] is not None:
                    idx[('tools', a['thread'])].append(a['t'])
            for v in idx.values():
                v.sort()
            self._idx = idx
        lst, p = self._idx.get((kind, c['thread']), []), c['prev']
        if not p:
            return 0
        return bisect.bisect_right(lst, c['t0']) - bisect.bisect_right(lst, p['t1'])

    def miss_cause(self, c):
        p = c['prev']
        reason = ((c.get('diag') or {}).get('cache_miss_reason') or {}).get('type')
        if p and p['model'] != c['model']:
            return 'Model switch'
        if reason == 'tools_changed' or self._between('tools', c):
            return 'Tool list changed (MCP/tools)'
        if c['is_sub'] and p and not p['tools'] and self._between('resume', c):
            return 'Subagent resumed via SendMessage'
        if p and idle_of(c) > c['ttl']:
            ptools = [self.tool_uses.get(b.get('id')) for b, _ in p['tools']]
            ptools = [x for x in ptools if x]
            if any(x['name'] in WAITING_TOOLS for x in ptools):
                return 'Waited on your answer/approval'
            if c['is_sub'] and p['stop'] == 'end_turn':
                return 'Subagent idle until resumed'
            if not c['is_sub'] and p['stop'] in ('end_turn', None) and not ptools:
                return 'You came back after a break'
            slow = max((x['latency'] or 0) for x in ptools) if ptools else 0
            if slow > 0.5 * idle_of(c):
                return 'Slow tool run'
            return 'Slow or retried API response'
        if c['gen'] > 0.9 * c['ttl']:
            return 'Slow or retried API response'
        if p and p['effort'] and c['effort'] and p['effort'] != c['effort']:
            return 'Effort changed'
        if reason == 'previous_message_not_found':
            return 'Cache expired'
        if reason == 'messages_changed':
            return 'Earlier messages changed'
        return 'No logged reason'

    def miss_usd(self, c):
        r = self.prices.rate(c['model']) or {}
        write = r.get('cw1h' if c['ttl'] >= 3600 else 'cw5m') or 0
        return c['rewritten'] * (write - (r.get('cr') or 0)) / 1e6 * c['pm']

# ------------------------------------------------------------------------------------------- scope context

class G:
    """What card builders need besides the scope's model: labels, fixed colours, the scope itself."""

    def __init__(self, src, prices, scope, model_slots):
        self.src, self.prices, self.scope, self.model_slots = src, prices, scope, model_slots
        self.is_all = scope['kind'] == 'all'
        self.where = {}

    def slot(self, model):
        return self.model_slots.get(model)


def hit_rate(calls):
    tot = sum(c['ctx'] for c in calls)
    return share(sum(c['cr'] for c in calls), tot)


def thread_label(m, key):
    if key[1] == 'main':
        return 'main thread'
    s = m.subs.get(key[1])
    return f"{s['type'] if s else 'subagent'} · {key[1][:7]}"


def sess_label(m, sid, n=48):
    s = m.sessions.get(sid)
    return clip(s['label'], n) if s else sid[:8]


DUR_EDGES = [300, 900, 1800, 3600, 7200, 14400]
DUR_LABELS = ['<5m', '5–15m', '15–30m', '30–60m', '1–2h', '2–4h', '>4h']
GAP_EDGES = [60, 300, 900, 3600, 14400]
GAP_LABELS = ['<1m', '1–5m', '5–15m', '15–60m', '1–4h', '>4h']
CTX_EDGES = [25e3, 50e3, 100e3, 200e3, 400e3, 700e3]
CTX_LABELS = ['<25K', '25–50K', '50–100K', '100–200K', '200–400K', '400–700K', '>700K']


# ------------------------------------------------------------------------------------------ OV: overview

def ov1(m, g):
    sess = list(m.sessions.values())
    act = collections.Counter()
    for s in sess:
        ts_ = sorted(s['times'])
        for a, b in zip(ts_, ts_[1:]):
            act[day(a)] += min(b - a, IDLE_CAP) / 3600
    days = fill_days(act)
    tot = lambda k: sum(c[k] for c in m.real)
    m.facts.update(sessions=len(sess), prompts=len(m.prompts), calls=len(m.real), active_h=sum(act.values()),
                   days=len([d for d in days if act.get(d)]))
    parts = [('Cache read', tot('cr')), ('Cache write (1-hour)', tot('cw1')), ('Cache write (5-minute)', tot('cw5')),
             ('Output', tot('out')), ('Input (uncached)', tot('inp'))]
    pie = PIE([k for k, _ in parts], [v for _, v in parts], 'tokens', title='Tokens by type', center='tokens',
              note=f"Output includes {f_tok(tot('think'))} thinking tokens.", width='half')
    hours = BAR(days, [S('Active hours', [r1(act[d]) for d in days])], 'hours', orient='v', title='Active hours per day (hours)')
    if hours:                       # values on the bars (the axis scrolls out of view with them), without the unit to keep bars dense
        hours['width'] = 'half'
        hours['chart'].update(minBand=24, scrollX=True, labelUnit='count', scrollHint='← Scroll sideways, or use the arrows, for earlier days')
    return card('OV1', 'How much did I use Claude Code?', 'T P W', [
        K(kpi('Sessions', len(sess), 'count', f"{sum(1 for s in sess if s['n_calls'])} made API calls"),
          kpi('Your prompts', len(m.prompts), 'count', f'+ {len(m.commands)} slash commands'),
          kpi('API calls', len(m.real), 'count', f'{len(m.tools):,} tool calls'),
          kpi('Subagents', len(m.subs), 'count'),
          kpi('Active hours', sum(act.values()), 'hours', 'summed per session; gaps over 5 min not counted')),
        pie, hours],
        why='The baseline every other number is read against.')


def ov2(m, g):
    by_model, by_proj = collections.defaultdict(float), collections.defaultdict(float)
    daily = collections.defaultdict(lambda: collections.defaultdict(float))
    for c in m.real:
        by_model[c['model']] += c['usd']
        by_proj[c['proj']] += c['usd']
        daily[day(c['t0'])][c['model']] += c['usd']
    models = sorted(by_model, key=lambda k: -by_model[k])
    active = sorted(daily)
    days = fill_days(active)
    m.facts['usd'] = m.usd
    blocks = [K(kpi('Cost at API list prices', m.usd, 'usd', f'{len(active)} active days'),
                kpi('Per active day', m.usd / len(active) if active else None, 'usd'),
                kpi('Per API call', m.usd / len(m.real) if m.real else None, 'usd'),
                kpi('Per prompt', m.usd / len(m.prompts) if m.prompts else None, 'usd'))]
    two = len(by_proj) > 1
    blocks.append(PIE([model_name(x) for x in models], [r2(by_model[x]) for x in models], 'usd', title='By model', center='total',
                      slots=[g.slot(x) for x in models], width='half' if two else None, total=r2(m.usd)))
    if two:                          # the top five projects, the rest grouped
        ps = sorted(by_proj, key=lambda k: -by_proj[k])
        top, rest = ps[:5], sum(by_proj[p] for p in ps[5:])
        cats = [clip(g.src.label(p), 34) for p in top] + ([f'Other ({len(ps) - 5})'] if rest > 0 else [])
        vals = [r2(by_proj[p]) for p in top] + ([r2(rest)] if rest > 0 else [])
        blocks.append(PIE(cats, vals, 'usd', title='By project directory', center='total', width='half',
                          slots=list(range(1, len(cats) + 1)), total=r2(m.usd)))
    blocks.append(BAR(days, [S(model_name(x), [r2(daily[d].get(x, 0)) for d in days], g.slot(x)) for x in models],
                      'usd', orient='v', stacked=True, title='Per day, by model', labels=False))
    note = 'Dollar figures are tokens × prices.json (API list prices). On a subscription plan this is a yardstick, not your bill.'
    if g.prices.missing:
        note += f' No price for: {", ".join(sorted(g.prices.missing))} (counted as $0).'
    if g.prices.estimated:
        note += ' Priced like the nearest model of the same family (no exact price in prices.json): ' + \
                ', '.join(f'{model_name(k)} as {model_name(v)}' for k, v in sorted(g.prices.estimated.items())) + '.'
    return card('OV2', 'What would my usage cost at API list prices?', 'T P S W', blocks, note=note,
                why='Token types differ up to 50× in price within one model; only dollars make them comparable.')


def ov3(m, g):
    parts = {k: sum(c['cost'].get(k, 0) for c in m.real) for k, _, _ in TOKEN_TYPES}
    tot = sum(parts.values())
    if not tot:
        return card('OV3', 'Which token type dominates the bill?', 'T P S', [])
    models = sorted(uniq(c['model'] for c in m.real), key=lambda x: -sum(c['usd'] for c in m.real if c['model'] == x))
    per = {x: {k: sum(c['cost'].get(k, 0) for c in m.real if c['model'] == x) for k, _, _ in TOKEN_TYPES} for x in models}
    top = max(parts, key=parts.get)
    label = {'cache_read': 'Cache reads are', 'write_1h': '1-hour cache writes are', 'output': 'Output tokens are',
             'write_5m': '5-minute cache writes are', 'input': 'Uncached input is'}
    m.facts.update(cost_parts={k: share(v, tot) for k, v in parts.items()})
    ins = f'{label[top]} {f_pct(share(parts[top], tot))} of your cost.'
    if top == 'cache_read':
        ins += ' What Claude re-reads each call (context size) matters more than what it writes.'
    elif top == 'output':
        ins += ' Verbose output (thinking, file writes, long answers) is the main lever.'
    return card('OV3', 'Which token type dominates the bill?', 'T P S', [
        K(*[kpi(lbl, share(parts[k], tot), 'pct', f_usd(parts[k])) for k, lbl, _ in TOKEN_TYPES]),
        BAR([model_name(x) for x in models], [S(lbl, [r2(per[x][k]) for x in models], slot) for k, lbl, slot in TOKEN_TYPES],
            'usd', stacked=True, title='Cost by token type, per model', labels=False)],
        why='Shows which lever matters: smaller contexts (reads), fewer misses (writes), or less verbose work (output).',
        insight=ins)


EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max']


def ov4(m, g):
    grp = collections.defaultdict(list)
    for c in m.real:
        grp[c['effort'] or 'unset'].append(c)
    levels = sorted(grp, key=lambda e: EFFORTS.index(e) if e in EFFORTS else 9)
    name = {e: 'not set' if e == 'unset' else e for e in levels}
    slots = [EFFORTS.index(e) + 1 if e in EFFORTS else 6 for e in levels]
    usd = {e: sum(c['usd'] for c in grp[e]) for e in levels}
    med = {e: median([c['gen'] for c in grp[e]]) for e in levels}
    think = sum(c['think'] for c in m.real)
    out = sum(c['out'] for c in m.real)
    m.facts['think_share'] = share(think, out)
    ins = f'Thinking is {f_pct(share(think, out))} of all output tokens.' if think else None
    if len(levels) > 1:                              # the level whose cost share runs furthest ahead of its call share
        e = max(levels, key=lambda x: share(usd[x], m.usd) - share(len(grp[x]), len(m.real)))
        ins = (f'{name[e].capitalize()} effort is {f_pct(share(len(grp[e]), len(m.real)))} of API calls but '
               f'{f_pct(share(usd[e], m.usd))} of cost ({f_usd(usd[e] / len(grp[e]))} per call, against '
               f'{f_usd(m.usd / len(m.real))} on average).' + (f' {ins}' if ins else ''))
    return card('OV4', 'How does effort level change cost and time?', 'T P', [
        K(kpi('Thinking share of output', share(think, out), 'pct')),
        PIE([f"{name[e]} ({f_usd(usd[e] / len(grp[e]))} per call)" for e in levels], [r2(usd[e]) for e in levels], 'usd',
            title='Cost by effort level', center='total', total=r2(m.usd), width='third', slots=slots),
        PIE([name[e] for e in levels], [len(grp[e]) for e in levels], 'count', title='API calls by effort level',
            center='API calls', width='third', slots=slots),
        PIE([f"{name[e]} ({med[e]:.1f}s median call)" if med[e] < 60 else f"{name[e]} ({f_dur(med[e])} median call)" for e in levels],
            [r2(sum(c['gen'] for c in grp[e]) / 3600) for e in levels], 'hours', title='Model time by effort level',
            center='hours', width='third', slots=slots)],
        note='Effort is logged per call; every model is counted together. Tasks differ between effort levels, so treat this '
             'as descriptive, not causal. “not set” = calls without an effort level (e.g. Haiku).',
        insight=ins, empty=None if m.real else 'No API calls in this scope.')


def ov5(m, g):
    sess = sorted((s for s in m.sessions.values() if s['usd'] > 0), key=lambda s: -s['usd'])
    if not sess:
        return card('OV5', 'Where is my spend concentrated?', 'T P', [])
    tot = sum(s['usd'] for s in sess)
    xs, ys, cum = [0], [0], 0
    for i, s in enumerate(sess, 1):
        cum += s['usd']
        xs.append(r1(100 * i / len(sess)))
        ys.append(r1(100 * cum / tot))
    top10 = sum(s['usd'] for s in sess[:max(1, len(sess) // 10)])
    m.facts['top10_share'] = share(top10, tot)
    rows = [dict(s=clip(s['label'], 60), d=day(s['start']), p=g.src.label(s['proj']), n=s['prompts'], c=s['n_calls'],
                 u=r2(s['usd']), sh=r1(share(s['usd'], tot))) for s in sess[:25]]
    daily = collections.defaultdict(float)
    for c in m.real:
        daily[day(c['t0'])] += c['usd']
    drows = [dict(d=d, u=r2(v), sh=r1(share(v, m.usd))) for d, v in sorted(daily.items(), key=lambda kv: -kv[1])[:10]]
    crow = []
    for c in sorted(m.real, key=lambda c: -c['usd'])[:15]:
        what = ', '.join(sorted({b.get('name') for b, _ in c['tools']})) or ('text answer' if c['text_chars'] else '—')
        crow.append(dict(t=local(c['t0']).strftime('%m-%d %H:%M'), s=sess_label(m, c['sid'], 40), th=thread_label(m, c['thread']),
                         mo=model_name(c['model']), x=c['ctx'], w=c['rewritten'] if c['miss'] else 0, u=r2(c['usd']), what=clip(what, 40)))
    return card('OV5', 'Where is my spend concentrated?', 'T P', [
        K(kpi('Top 10% of sessions', share(top10, tot), 'pct', 'of spend'),
          kpi('Costliest session', sess[0]['usd'], 'usd', clip(sess[0]['label'], 40)),
          kpi('Median session', median([s['usd'] for s in sess]), 'usd')),
        LINE(xs, [S('Cumulative share of spend', ys)], 'pct', x_kind='num', x_unit='pct',
             title='Pareto: sessions sorted by cost (x) vs cumulative share of spend (y)'),
        TABS([('Sessions', TABLE([('s', 'Session', None), ('d', 'Date', None), ('p', 'Project', None), ('n', 'Prompts', 'count'),
                                  ('c', 'API calls', 'count'), ('u', 'Cost', 'usd'), ('sh', 'Share', 'pct')], rows, 'Costliest sessions', 10)),
              ('Days', TABLE([('d', 'Day', None), ('u', 'Cost', 'usd'), ('sh', 'Share', 'pct')], drows, 'Costliest days', 5)),
              ('Single API calls', TABLE([('t', 'When', None), ('s', 'Session', None), ('th', 'Thread', None), ('mo', 'Model', None),
                                          ('x', 'Context', 'tokens'), ('w', 'Re-written', 'tokens'), ('u', 'Cost', 'usd'), ('what', 'Tools', None)],
                                         crow, 'Costliest single API calls', 8))],
             title='The costliest sessions, days and API calls')],
        why='Fixing the top few sessions matters more than the median.',
        insight=f'The top 10% of sessions account for {f_pct(share(top10, tot))} of spend.')


def ov6(m, g):
    sess = [s for s in m.sessions.values() if s['n_calls']]
    turns = [t for t in m.turns if t['calls'] and t['kind'] == 'prompt']
    hours = sum(s['active'] for s in sess) / 3600

    def row(name, vals, extra=None):
        return dict(n=name, md=r2(median(vals)), p90=r2(pctl(vals, 90)), mean=r2(mean(vals)), mx=r2(max(vals) if vals else None),
                    k=len(vals))
    rows = [row('Per session', [s['usd'] for s in sess]), row('Per prompt (turn)', [t['usd'] for t in turns]),
            row('Per API call', [c['usd'] for c in m.real]),
            row('Per active hour (by session)', [s['usd'] / (s['active'] / 3600) for s in sess if s['active'] > 600])]
    return card('OV6', 'What does a typical session, prompt and hour cost?', 'T P W', [
        K(kpi('Median session', rows[0]['md'], 'usd'), kpi('Median prompt', rows[1]['md'], 'usd'),
          kpi('Per active hour', m.usd / hours if hours else None, 'usd', 'total cost ÷ active hours')),
        TABLE([('n', 'Unit', None), ('md', 'Median', 'usd'), ('p90', '90th pct', 'usd'), ('mean', 'Mean', 'usd'),
               ('mx', 'Max', 'usd'), ('k', 'Count', 'count')], rows)],
        why='An intuitive price for "one more prompt".')


def ov7(m, g):
    groups = []

    def add(group, part, calls):
        u = sum(c['usd'] for c in calls)
        groups.append(dict(g=group, p=part, c=len(calls), u=r2(u), sh=r1(share(u, m.usd))))
    add('Thread', 'Main thread', [c for c in m.real if not c['agent']])
    add('Thread', 'Subagents', [c for c in m.real if c['agent']])
    wt = [c for c in m.real if '--claude-worktrees-' in c['proj']]
    add('Location', 'Repo checkout', [c for c in m.real if '--claude-worktrees-' not in c['proj']])
    add('Location', 'Task worktrees', wt)
    kind = {}
    for sid, s in m.sessions.items():
        kind[sid] = 'SDK / automated' if s['kind'] == 'Automated (SDK)' else 'Named sessions' if s['name'] else 'Interactive'
    for k in ('Interactive', 'Named sessions', 'SDK / automated'):
        add('How started', k, [c for c in m.real if kind.get(c['sid']) == k])
    sub_share = share(sum(c['usd'] for c in m.real if c['agent']), m.usd)
    m.facts['sub_share'] = sub_share
    pies = []
    for grp, title in (('Thread', 'Main thread vs subagents'), ('Location', 'Checkout vs task worktrees'), ('How started', 'How sessions started')):
        parts = [r for r in groups if r['g'] == grp and r['u'] > 0]
        pies.append(PIE([f"{r['p']} ({r['c']:,} calls)" for r in parts], [r['u'] for r in parts], 'usd', title=title, center='total',
                        total=r2(m.usd), width='third', slots=list(range(1, len(parts) + 1))))
    return card('OV7', 'How is usage split: main thread vs subagents, checkout vs worktrees, interactive vs automated?', 'T P', pies,
        note='Named sessions carry an agent-name (e.g. launched by an orchestration tool); SDK sessions have an sdk-* entrypoint.',
        insight=f'Subagents are {f_pct(sub_share)} of spend.' if sub_share else None)


# ------------------------------------------------------------------------------------------ CX7–CX12: caching (part of Context & Caching)


def ov8(m, g):
    miss = sum(r['usd'] for r in _miss_rows(m))
    rep, _ = _read_seq(m)
    reread = sum(it['carry_usd'] + it['tokens'] * g.prices.per_token(it['model'], 'cw5m', it['pm']) for it in m.items
                 if it['tu'] and it['tu']['id'] in rep)
    after_err = set()
    for t in m.tools:
        if t['res'] and t['res']['is_error']:
            c = t['call']
            th = m.threads[c['thread']]
            if c['idx'] + 1 < len(th):
                after_err.add(th[c['idx'] + 1]['key'])
    recovery = sum(c['usd'] for c in m.real if c['key'] in after_err)
    inter = sum(t['usd'] for t in m.turns if t['interrupted'])
    e = _ext(m)
    parts = [('Cache-miss re-writes', miss), ('Error-recovery calls', recovery), ('Redundant file re-reads', reread),
             ('Interrupted turns', inter), ('Unused skills/MCP/agents in context', e['usd'])]
    parts.sort(key=lambda p: -p[1])
    tot = sum(p[1] for p in parts)
    m.facts.update(waste=tot, waste_share=share(tot, m.usd), waste_top=parts[0][0] if parts else None)
    ins = f'Identifiable waste: {f_save(m, tot)}, {f_pct(share(tot, m.usd))} of spend, led by {parts[0][0].lower()}.' if tot else None
    return card('OV8', "What's my total waste?", 'T P W', [
        K(kpi_save(m, 'Identifiable waste', tot, f_pct(share(tot, m.usd)) + ' of spend')),
        BAR([p[0] for p in parts], [S('All time', [r2(p[1]) for p in parts], 1), S('Per 30 days (projected)', [r2(per_month(m, p[1])) for p in parts], 2)],
            'usd', title='Waste by component: all time and per 30 days')],
        insight=ins, note='Interrupted turns are an upper bound (some of that work was kept). Re-read waste = context those repeat reads added, written and re-read.')


def cx7(m, g):
    main = [c for c in m.real if not c['agent']]
    sub = [c for c in m.real if c['agent']]
    models = sorted(uniq(c['model'] for c in m.real), key=lambda x: -sum(1 for c in m.real if c['model'] == x))
    worst = []
    for sid, s in m.sessions.items():
        if len(s['calls']) >= 20:
            worst.append(dict(s=clip(s['label'], 60), d=day(s['start']), c=len(s['calls']), h=r1(hit_rate(s['calls'])),
                              w=sum(c['rewritten'] for c in s['calls'] if c['miss']), u=r2(s['usd'])))
    worst.sort(key=lambda r: r['h'] if r['h'] is not None else 100)
    hr = hit_rate(m.real)
    m.facts['hit_rate'] = hr
    return card('CX7', "What's my cache hit rate, and which sessions are worst?", 'T P S A', [
        K(kpi('Cache hit rate', hr, 'pct', 'cache reads ÷ all input'), kpi('Main thread', hit_rate(main), 'pct'),
          kpi('Subagents', hit_rate(sub), 'pct')),
        BAR([model_name(x) for x in models], [S('Hit rate', [r1(hit_rate([c for c in m.real if c['model'] == x])) for x in models])],
            'pct', title='Hit rate by model'),
        TABS([('Sessions', TABLE([('s', 'Session', None), ('d', 'Date', None), ('c', 'API calls', 'count'), ('h', 'Hit rate', 'pct'),
                                  ('w', 'Re-written', 'tokens'), ('u', 'Cost', 'usd')], worst, 'Lowest hit rates (sessions with ≥20 calls)', 8))],
             title='Lowest hit rates (sessions with ≥20 calls)')],
        insight=(f'Cache hit rate is {f_pct(hr)}: caching is doing its job.' if hr and hr >= 95 else
                 f'Cache hit rate is {f_pct(hr)}: misses are worth investigating (see CX8).' if hr else None))


def cx9(m, g):
    out = sum(c['out'] for c in m.real)
    amp = sum(c['cr'] for c in m.real) / out if out else None
    m.facts['amp'] = amp
    models = sorted(uniq(c['model'] for c in m.real), key=lambda x: -sum(1 for c in m.real if c['model'] == x))

    def a(cs):
        o = sum(c['out'] for c in cs)
        return r1(sum(c['cr'] for c in cs) / o) if o else None
    return card('CX9', 'For every token Claude writes, how many does it re-read?', 'T P S', [
        BAR([model_name(x) for x in models], [S('Re-read per output token', [a([c for c in m.real if c['model'] == x]) for x in models])],
            'ratio', title='By model')],
        why='Read amplification = cache-read tokens ÷ output tokens. A high ratio means you mostly pay to re-send context, not for new work.',
        insight=f'Claude re-reads {amp:.0f} tokens for every token it writes.' if amp else None)


def _miss_rows(m):
    if 'miss_rows' not in m.cache:
        rows = []
        for c in m.misses:
            rows.append(dict(c=c, cause=m.miss_cause(c), usd=m.miss_usd(c)))
        m.cache['miss_rows'] = rows
    return m.cache['miss_rows']


CAUSE_HELP = {
    'Subagent resumed via SendMessage': 'The main agent sent a follow-up to a finished subagent; its history is rebuilt, so its whole context is written again.',
    'You came back after a break': 'The pause after the previous reply outlasted the cache lifetime (1 hour on a subscription, '
                                   '5 minutes on an API key).',
    'Slow or retried API response': 'The request took longer than the cache lifetime to answer (queueing/retries).',
    'Tool list changed (MCP/tools)': 'An MCP server connected or tools were added mid-session; tools sit at the front of the prompt.',
    'Slow tool run': 'A tool (often a build) ran longer than the cache lifetime.',
    'Waited on your answer/approval': 'A question or plan approval waited longer than the cache lifetime.',
    'Model switch': 'Caches are per model; switching rebuilds everything.',
    'Effort changed': 'Changing effort invalidates the message cache.',
}


def cx8(m, g):
    rows = _miss_rows(m)
    tok = sum(r['c']['rewritten'] for r in rows)
    cw = sum(c['cw'] for c in m.real)
    m.facts.update(miss_n=len(rows), miss_tok=tok)
    causes = sorted(uniq(r['cause'] for r in rows), key=lambda k: -sum(r['c']['rewritten'] for r in rows if r['cause'] == k))
    days = fill_days({day(r['c']['t0']) for r in rows})
    tab = [dict(t=local(r['c']['t0']).strftime('%m-%d %H:%M'), s=sess_label(m, r['c']['sid'], 40), th=thread_label(m, r['c']['thread']),
                mo=model_name(r['c']['model']), w=r['c']['rewritten'], gap=idle_of(r['c']), cause=r['cause'], u=r2(r['usd']))
           for r in sorted(rows, key=lambda r: -r['c']['rewritten'])]
    extra = sum(r['usd'] for r in rows)
    cache_usd = sum(c['cost'].get('cache_read', 0) + c['cost'].get('write_5m', 0) + c['cost'].get('write_1h', 0) for c in m.real)
    ins = None
    if rows and m.usd:
        ps, pc = share(extra, m.usd), share(extra, cache_usd)
        ins = (f"Misses re-wrote {f_tok(tok)} tokens, {f_pct(share(tok, cw))} of all cache writes. They cost {money(extra)} more than cache hits "
               f"would have: {f_pct(ps)} of your total spend and {f_pct(pc)} of what you paid for caching ({f_usd(cache_usd)}). "
               + ('That makes preventing them one of your bigger levers (see SV3 for which ones were avoidable).' if ps >= 5 else
                  'Worth preventing, though not where most of your money goes (see SV3 for which ones were avoidable).' if ps >= 2 else
                  'Small: your misses are not where your money goes.'))
    return card('CX8', 'How many cache misses did I have, and how many tokens did each re-write?', 'T P S A W', [
        K(kpi('Cache misses', len(rows), 'count', f'calls that re-wrote >{MISS_MIN:,} tokens'),
          kpi('Tokens re-written', tok, 'tokens'), kpi('Share of all cache writes', share(tok, cw), 'pct')),
        BAR(days, [S(k, [sum(r['c']['rewritten'] for r in rows if r['cause'] == k and day(r['c']['t0']) == d) for d in days], i + 1)
                   for i, k in enumerate(causes[:8])], 'tokens', orient='v', stacked=True, title='Re-written tokens per day, by cause',
            labels=False),
        TABS([('Every miss', TABLE([('t', 'When', None), ('s', 'Session', None), ('th', 'Thread', None), ('mo', 'Model', None),
                                    ('w', 'Re-written', 'tokens'), ('gap', 'Idle before', 'sec'), ('cause', 'Cause', None), ('u', 'Extra cost', 'usd')],
                                   tab, 'Every miss', 10))],
             title=f'Every miss ({len(rows)})', sub='when, where, how big, why, and what it cost')],
        insight=ins,
        note='Re-written = previous call’s context − this call’s cache read. Gap = time between the two requests’ starts.',
        empty=None if rows else 'No cache misses in this scope.')


def ca_miss_cost(m, g):
    rows = _miss_rows(m)
    usd = sum(r['usd'] for r in rows)
    m.facts['miss_usd'] = usd
    causes = sorted(uniq(r['cause'] for r in rows), key=lambda k: -sum(r['usd'] for r in rows if r['cause'] == k))
    return card('_CA_COST', 'What did cache misses cost compared with a cache hit?', 'T P S', [
        K(kpi('Extra cost of misses', usd, 'usd', 'write price − read price, on re-written tokens'),
          kpi('Share of spend', share(usd, m.usd), 'pct'), kpi('Per miss', usd / len(rows) if rows else None, 'usd')),
        BAR(causes, [S('Extra cost', [r2(sum(r['usd'] for r in rows if r['cause'] == k)) for k in causes])], 'usd', title='By cause')],
        empty=None if rows else 'No cache misses in this scope.')


def ca_miss_causes(m, g):
    rows = _miss_rows(m)
    agg = collections.defaultdict(lambda: [0, 0, 0.0])
    for r in rows:
        a = agg[r['cause']]
        a[0] += 1
        a[1] += r['c']['rewritten']
        a[2] += r['usd']
    causes = sorted(agg, key=lambda k: -agg[k][1])
    tot = sum(v[1] for v in agg.values())
    ins = None
    if causes:
        top = causes[0]
        m.facts.update(top_cause=top, top_cause_share=share(agg[top][1], tot))
        ins = f'“{top}” caused {f_pct(share(agg[top][1], tot))} of re-written tokens ({f_tok(agg[top][1])}).'
    return card('_CA_CAUSES', 'What caused each cache miss?', 'T P S', [
        BAR(causes, [S('Re-written tokens', [agg[k][1] for k in causes])], 'tokens', title='Re-written tokens by cause'),
        TABLE([('k', 'Cause', None), ('n', 'Misses', 'count'), ('t', 'Tokens', 'tokens'), ('u', 'Extra cost', 'usd'), ('h', 'What it means', None)],
              [dict(k=k, n=agg[k][0], t=agg[k][1], u=r2(agg[k][2]), h=CAUSE_HELP.get(k, '')) for k in causes])],
        insight=ins, note='Causes come from the gap vs the cache lifetime, the tools before the call, model/effort changes and the '
                          'logged cache_miss_reason. CX8 shows the timeline of each costly miss, step by step.',
        empty=None if rows else 'No cache misses in this scope.')


TTL_ZONES = [(60, 300, '1–5m'), (300, 600, '5–10m'), (600, 1200, '10–20m'), (1200, 2400, '20–40m'), (2400, 3600, '40–60m'),
             (3600, 14400, '1–4h'), (14400, float('inf'), '>4h')]


def cx10(m, g):
    t = _sv_ttl(m)
    title = 'Does each cache lifetime (TTL) fit my gap pattern?'
    kinds = [(k, lbl) for k, lbl in (('main', 'Main threads'), ('sub', 'Subagents')) if t[k]['calls']]
    if not kinds:
        return card('CX10', title, 'T P A', [])
    life = {300: '5 minutes', 3600: '1 hour'}
    tiles, tabs, verdict = [], [], []
    for k, lbl in kinds:
        v = t[k]
        p5, p1 = v['parts'][300], v['parts'][3600]
        cur = life[3600 if v['ttls'] and v['ttls'].most_common(1)[0][0] >= 3600 else 300]
        best = life[300] if v['c5'] <= v['c1h'] else life[3600]
        diff = abs(v['c5'] - v['c1h'])
        prem = (p1['new'] + p1['either']) - (p5['new'] + p5['either'])    # the 2× write price on what both lifetimes write
        avoid = p5['pause'] - (p1['read'] - p5['read'])                     # 5-min re-writes, net of the reads 1 hour pays instead
        n = v['n_pauses']
        pauses = f"{n} pause{'' if n == 1 else 's'} of 5–60 min"
        tiles.append(kpi(f'{lbl}: cheaper lifetime', best, 'text',
                         f'{f_usd(diff)} cheaper · ' + ('you already use it' if cur == best else f'you use {cur}')))
        tiles.append(kpi(f'{lbl}: pauses of 5–60 min', n, 'count',
                         f"{v['rate']:.1f} per 100 calls" + (f"; 1 hour pays off above {v['breakeven']:.1f}" if v['breakeven'] is not None else '')))
        mark = lambda x: x + (' · yours' if x.endswith(cur) else '')
        cheaper = lambda a5, a1: '—' if abs(a5 - a1) < 0.005 else (f'5 minutes by {f_usd(a1 - a5)}' if a5 < a1 else f'1 hour by {f_usd(a5 - a1)}')
        rows = [dict(k='Cache reads', a=r2(p5['read']), b=r2(p1['read'])),
                dict(k='Writing new content (1.25× vs 2× input)', a=r2(p5['new']), b=r2(p1['new'])),
                dict(k='Re-writes after a 5–60 min pause', a=r2(p5['pause']), b=r2(p1['pause'])),
                dict(k='Re-writes under both (pause over 1 hour, changed prompt, retry), at each write price', a=r2(p5['either']),
                     b=r2(p1['either'])),
                dict(k='Total cache cost', a=r2(v['c5']), b=r2(v['c1h']))]
        for r in rows:
            r['x'] = cheaper(r['a'], r['b'])
        rows.append(dict(k='Cache misses (count)', a=f"{v['n5']:,}", b=f"{v['n1h']:,}", x=''))
        zones, quick = [[0] * len(TTL_ZONES) for _ in range(3)], 0
        for key, th in m.threads.items():                # each pause, by what the replay found: 0 kept, 1 decided, 2 missed
            if (key[1] == 'main') != (k == 'main'):
                continue
            for c in th:
                if not c['prev']:
                    continue
                x = idle_of(c)
                if x <= 60:
                    quick += 1
                    continue
                m5, m1 = _ttl_tokens(m, c, 300)[2], _ttl_tokens(m, c, 3600)[2]
                zones[1 if m5 != m1 else 2 if m1 else 0][next(i for i, z in enumerate(TTL_ZONES) if x <= z[1])] += 1
        tabs.append((lbl, [
            BAR(['If 5 minutes', 'If 1 hour'], [S('Cache reads', [r2(p5['read']), r2(p1['read'])], 1),
                                                S('Writing new content', [r2(p5['new']), r2(p1['new'])], 2),
                                                S('Re-writes after a 5–60 min pause', [r2(p5['pause']), r2(p1['pause'])], 3),
                                                S('Re-writes under both', [r2(p5['either']), r2(p1['either'])], 4)], 'usd',
                stacked=True, title=f'{lbl}: cache cost under each lifetime (yours: {cur})'),
            TABLE([('k', 'Part of the cache cost', None), ('a', mark('If 5 minutes'), 'usd'), ('b', mark('If 1 hour'), 'usd'),
                   ('x', 'Cheaper', None)], rows),
            BAR([z[2] for z in TTL_ZONES], [S('Cached under both', zones[0], 6), S('Only 1 hour keeps it cached', zones[1], 3),
                                            S('A miss under both', zones[2], 4)], 'count', orient='v', stacked=True,
                title=f'Idle time before each request ({quick:,} more came within a minute)')]))
        head = f'{lbl}: {best} is {f_usd(diff)} cheaper' + (', and you already use it' if cur == best else f' (you use {cur})')
        verdict.append(head + (f': its 2× write price costs {f_usd(prem)} more, but it saves {f_usd(max(0.0, avoid))} of re-writes on {pauses}.'
                               if best == life[3600] else
                               f': 1 hour would cost {f_usd(prem)} more in writes to save {f_usd(max(0.0, avoid))} of re-writes on {pauses}.'))
    return card('CX10', title, 'T P A', [K(*tiles), TABS(tabs, title='Compare 5 minutes with 1 hour', collapsed=False,
                                                         sub='where each option’s cache cost goes')],
        why='The two lifetimes differ only on pauses of 5–60 minutes: shorter ones stay cached either way, longer ones expire either way. '
            'A 1-hour entry costs 2× input to write instead of 1.25×, so it pays off only when those pauses (and the context they would '
            're-write) are frequent enough.',
        insight=' '.join(verdict),
        note='Every call replayed under both lifetimes (the SV5 method, which also prices the whole bill). Input and output cost the same '
             'under both, so only the cache cost is compared. Set with promptCacheTtl (main threads) and subagentPromptCacheTtl (subagents).')


def cx11(m, g):
    main = [s['baseline'] for s in m.sessions.values() if s['baseline']]
    subs = [s for s in m.subs.values() if (s['type'] or '').lower() != 'fork']   # a fork starts from its parent's context, not cold
    by_type = collections.defaultdict(list)
    for s in subs:
        by_type[s['type']].append(s['cold'])
    types = sorted(by_type, key=lambda k: -mean(by_type[k]))
    cats = ['Session (main thread)'] + [f'Subagent: {k}' for k in types]
    vals = [mean(main)] + [mean(by_type[k]) for k in types]
    first_writes = sum(t[0]['cw'] for t in m.threads.values() if t)
    forks = len(m.subs) - len(subs)
    return card('CX11', 'How much do cold starts cost?', 'T P A', [
        K(kpi('Session start', mean(main), 'tokens', f'range {f_tok(min(main) if main else None)}–{f_tok(max(main) if main else None)}'),
          kpi('Subagent start', mean([s['cold'] for s in subs]), 'tokens', f'{len(subs)} subagents'
              + (' (forks left out: they start from the parent’s context)' if forks else '')),
          kpi('Written by first calls', first_writes, 'tokens', 'all threads')),
        BAR(cats, [S('Average first-call context', [round(v) if v else 0 for v in vals])], 'tokens', title='Average start-up context')],
        why='Every new session or subagent writes its whole starting context to the cache before doing any work.')


def cx12(m, g):
    cw = sum(c['cw'] for c in m.real)
    cold = sum(t[0]['cw'] for t in m.threads.values() if t)
    miss = sum(c['rewritten'] for c in m.misses)
    new = max(0, cw - cold - miss)
    return card('CX12', 'Where do my cache writes go?', 'T P S', [
        K(kpi('New content', share(new, cw), 'pct', f_tok(new)), kpi('Cold starts', share(cold, cw), 'pct', f_tok(cold)),
          kpi('Miss re-writes', share(miss, cw), 'pct', f_tok(miss))),
        BAR(['Cache writes'], [S('New content', [new], 1), S('Cold starts', [cold], 2), S('Miss re-writes', [miss], 3)], 'tokens',
            stacked=True, labels=False)],
        why='Only new content has to be written; cold starts and re-writes are overhead you can shape.')


# ------------------------------------------------------------------ a trace of each costly miss (a section of CX8)

TRACE_TOP = 15
TRACES = {}   # trace id -> trace; shared by every scope (a miss is the same wherever it is listed)
REASON_WORDS = {'tools_changed': 'tools changed', 'messages_changed': 'earlier messages changed',
                'previous_message_not_found': 'cached prefix not found (expired)'}
EXPIRED_CAUSES = {'You came back after a break', 'Waited on your answer/approval', 'Subagent idle until resumed',
                  'Slow tool run', 'Slow or retried API response', 'Cache expired'}


def ms(t):
    return None if t is None else int(round(t * 1000))


def trace_id(c):
    return f"{c['sid'][:8]}-{(c['agent'] or 'main')[:7]}-{c['idx']}"


def clock(t, ref=None):
    """Local wall-clock time, with the date when it falls on another day than `ref`."""
    d = local(t)
    return d.strftime('%H:%M:%S') if ref is None or day(t) == day(ref) else f'{d:%b} {d.day} {d:%H:%M:%S}'


def tz_min(t):
    off = local(t).astimezone().utcoffset()
    return int(off.total_seconds() // 60) if off else 0


def f_span(s):
    """Precise duration for the trace text: 3.8s, 4m 43s, 3h 31m."""
    if s is None:
        return '–'
    s = max(0.0, float(s))
    if s < 10:
        return f'{s:.1f}s'
    if s < 59.5:
        return f'{s:.0f}s'
    if s < 3599.5:
        mnt, sec = divmod(int(round(s)), 60)
        return f'{mnt}m {sec:02d}s' if sec else f'{mnt}m'
    h, rem = divmod(int(round(s)), 3600)
    return f'{h}h {rem // 60:02d}m'


def ttl_word(ttl):
    return '1-hour' if ttl >= 3600 else '5-minute' if ttl == 300 else f_dur(ttl)


def life_word(ttl):
    return '1 hour' if ttl >= 3600 else '5 minutes' if ttl == 300 else f_dur(ttl)


def main_ttl(m):
    """The cache lifetime most main-thread calls in this scope had: 1 hour on a subscription, 5 minutes on an API key,
    Bedrock or Vertex (detected per thread from its cache writes)."""
    n = collections.Counter(c['ttl'] for c in m.real if not c.get('is_sub') and c.get('ttl'))
    return n.most_common(1)[0][0] if n else 3600


def names_of(v):
    return [str(x) for x in v] if isinstance(v, list) else [str(v)] if v else []


def _downsample(pts, n=240):
    """Keep the context line's shape (the peak of each bucket) without shipping every call."""
    if len(pts) <= n:
        return pts
    k = math.ceil(len(pts) / n)
    out = [max(pts[j:j + k], key=lambda q: q[1]) for j in range(0, len(pts), k)]
    return out if out[-1] is pts[-1] else out + [pts[-1]]


def idle_of(c):
    """Seconds between the end of the previous response and this request. The cache clock runs from that end: counted from
    the previous request's start instead, some logged hits would have come after the cache expired."""
    return c['start'] - c['prev']['t1']


def cache_expiry(c):
    """When the cache call c depends on runs out: its lifetime after the end of the previous response."""
    return c['prev']['t1'] + c['ttl']


def cache_state(c):
    """When the cache ran out relative to the missed request: 'before' it went out, while it was 'waiting' for the API,
    or never ('alive': the prompt changed instead). A call that still read most of the old context had a live cache."""
    p = c['prev']
    expiry = cache_expiry(c)
    if c['cr'] >= 0.5 * p['ctx']:
        return 'alive'
    if expiry <= c['start']:
        return 'before'
    return 'waiting' if expiry <= c['t0'] and c['t0'] - c['start'] > 60 else 'alive'


def expired(r):
    """The cache had timed out (as opposed to the prompt changing while the cache was still alive)."""
    return cache_state(r['c']) != 'alive'


def money(x):
    """Dollars for prose: two decimals."""
    return '–' if x is None else f'${x:,.0f}' if x >= 100 else f'${x:,.2f}' if x >= 0.01 else '<$0.01'


def _resumes(m):
    if 'resumes' not in m.cache:
        m.cache['resumes'] = [c for t in m.threads.values() for c in t
                              if c['is_sub'] and c['prev'] and not c['prev']['tools'] and m._between('resume', c)]
    return m.cache['resumes']


def _trigger(m, c):
    """The first input after the previous reply: what made the missed request go out."""
    sid, key, p = c['sid'], c['thread'], c['prev']
    lo, hi = p['t1'], c['t0']
    cand = []
    for kind, lst in (('prompt', m.prompts), ('command', m.commands), ('notif', m.notifs), ('sdk', m.sdk_prompts),
                      ('message', m.other_user)):
        cand += [(x['t'], kind, x) for x in lst if x['sid'] == sid and x['thread'] == key and lo <= x['t'] <= hi]
    for b, _ in p['tools']:
        tu = m.tool_uses.get(b.get('id'))
        if tu and tu['res'] and tu['res']['t'] is not None and lo <= tu['res']['t'] <= hi:
            cand.append((tu['res']['t'], 'answer' if tu['name'] in WAITING_TOOLS else 'tool', tu))
    return min(cand, key=lambda x: x[0]) if cand else None


def _concurrent(m, c):
    """Other threads that were calling the API while this request waited for its first token."""
    a, b = c['start'], c['t0']
    return len({x['thread'] for x in m.real if x['sid'] == c['sid'] and x['thread'] != c['thread']
                and x['start'] < b and x['t1'] > a})


def _trace_events(m, c, w0, w1):
    sid, key, p, aid = c['sid'], c['thread'], c['prev'], c['agent']
    mkey, sub = (sid, 'main'), c['is_sub']
    cwd = m._cwd(sid)
    ev = []

    def add(lane, k, label, t, e=None, **kw):
        if t is None:
            return
        end = t if e is None else max(e, t)
        if end < w0 or t > w1:
            return
        x = {'lane': lane, 'k': k, 'l': label, 't': ms(max(t, w0))}
        if e is not None:
            x['e'], x['dur'] = ms(min(end, w1)), r1(end - t)
        x.update({a: b for a, b in kw.items() if b not in (None, '', 0)})
        ev.append(x)

    def api(cc, lane):
        k = 'miss' if cc is c else 'miss2' if cc['miss'] else 'mcall' if lane == 'main' else 'call'
        label = ('Cache miss' if cc is c else 'Another cache miss' if cc['miss'] else 'Previous reply' if cc is p else
                 'Main agent: API call' if lane == 'main' else 'API call')
        why = ((cc.get('diag') or {}).get('cache_miss_reason') or {}).get('type') if cc['miss'] else None
        add(lane, k, label, cc['start'], cc['t1'], first=ms(cc['t0']), ctx=cc['ctx'], cr=cc['cr'], cw=cc['cw'], out=cc['out'],
            usd=r2(cc['usd']), rw=cc['rewritten'] if cc['miss'] else None, xusd=r2(m.miss_usd(cc)) if cc['miss'] else None,
            model=model_name(cc['model']), stop=cc['stop'], why=REASON_WORDS.get(why, why),
            tag='prev' if cc is p else 'miss' if cc is c else None)

    for cc in m.threads.get(key, []):
        if cc['t1'] >= w0 and cc['start'] <= w1:
            api(cc, 'claude')
    if sub:
        for cc in m.threads.get(mkey, []):
            if cc['t1'] >= w0 and cc['start'] <= w1:
                api(cc, 'main')
    for cc in m.errors:
        th = (cc['sid'], cc['agent'] or 'main')
        if cc['sid'] == sid and th in (key, mkey):
            add('claude' if th == key else 'main', 'err', 'API error', cc['t0'], d=cc['errtext'] or cc['err'])
    meta = m.src.metas.get(aid) or {}
    for tu in m.tools:
        if tu['sid'] != sid or tu['t'] is None:
            continue
        res = tu['res'] or {}
        end = res.get('t') if res.get('t') is not None else tu['t']
        if tu['thread'] == key and tu['call']['t0'] >= c['t0']:
            continue
        if tu['thread'] == key:
            if tu['name'] in WAITING_TOOLS:
                add('you', 'wait', f"Waiting for your answer ({tu['name']})", tu['t'], end)
            else:
                add('tools', 'tool', tu['name'], tu['t'], end, d=tool_detail(tu, cwd), err=1 if res.get('is_error') else None,
                    chars=res.get('chars'))
        elif sub and tu['thread'] == mkey:
            inp = tu['input']
            if tu['name'] == 'SendMessage' and inp.get('to') == aid:
                add('main', 'send', 'SendMessage to this subagent', tu['t'], d=clip(inp.get('summary') or inp.get('message') or '', 160))
            elif tu['id'] == meta.get('toolUseId'):
                add('main', 'launch', 'Launched this subagent', tu['t'], d=clip(inp.get('description') or '', 120))
    busy = []
    for s in m.subs.values():
        if s['sid'] != sid or s['aid'] == aid or s['end'] < w0 or s['start'] > w1:
            continue
        runs = []
        for x in s['calls']:                                  # its working stretches (calls less than a minute apart)
            if runs and x['start'] - runs[-1][1] <= 60:
                runs[-1][1] = max(runs[-1][1], x['t1'])
            else:
                runs.append([x['start'], x['t1']])
        runs = [r_ for r_ in runs if r_[1] >= w0 and r_[0] <= w1]
        if runs:
            busy.append((sum(min(b, w1) - max(a, w0) for a, b in runs), s, runs))
    for _, s, runs in sorted(busy, key=lambda x: -x[0])[:6]:
        for a, b in runs:
            add('subs', 'sub', f"{s['type']}: {clip(s['desc'], 48)}", a, b, d=f"subagent {s['aid'][:7]} · peak context {f_tok(s['peak'])}")
    for pr in m.prompts:
        if pr['sid'] == sid and not pr['side']:
            add('you', 'prompt', 'You: ' + (clip(pr['text'], 60) or f"{pr['images']} image(s)"), pr['t'], d=clip(pr['text'], 300),
                img=pr['images'])
    for x in m.sdk_prompts:
        if x['sid'] == sid and x['thread'] in (key, mkey):
            add('you', 'prompt', 'SDK prompt: ' + clip(x['text'], 50), x['t'], d=clip(x['text'], 300))
    seen_cmd = set()
    for cm in m.commands:
        if cm['sid'] == sid and cm['thread'] in (key, mkey):
            seen_cmd.add((cm['name'], round(cm['t'])))
            add('you', 'cmd', f"You ran /{cm['name']}", cm['t'], d=clip(cm.get('args') or '', 200))
    for d in m.sys.get('local_command', []):
        mm = re.search(r'<command-name>/?([^<]+)</command-name>', d.get('content') or '')
        if mm and d.get('sessionId') == sid and d['_t'] is not None and \
                not any((mm.group(1).strip(), round(d['_t']) + k) in seen_cmd for k in (-2, -1, 0, 1, 2)):
            add('you', 'cmd', f"You ran /{mm.group(1).strip()}", d['_t'])
    for x in m.interrupts:
        if x['sid'] == sid and x['thread'] in (key, mkey):
            add('you', 'int', 'You interrupted Claude', x['t'])
    for n in m.notifs:
        if n['sid'] == sid and n['thread'] == mkey:
            tk = n.get('task')
            lab = ("This subagent's result reached the main agent" if sub and tk == aid else
                   f"Subagent finished ({m.subs[tk]['type']})" if tk in m.subs else 'Background task finished')
            add('main' if sub else 'events', 'notif', lab, n['t'], d=tk)
    for o in m.other_user:
        if o['thread'] == key and o['side'] and o['t'] > (m.threads[key][0]['t0'] if m.threads.get(key) else 0):
            add('claude', 'msg', 'Message from the main agent', o['t'], d=clip(o['text'], 300))
    for d in m.sys.get('compact_boundary', []):
        if d.get('sessionId') == sid and thread_key(d) in (key, mkey):
            cm_ = d.get('compactMetadata') or {}
            add('events', 'mark', 'Conversation compacted', d['_t'],
                d=f"{f_tok(cm_.get('preTokens'))} → {f_tok(cm_.get('postTokens'))} tokens" if cm_.get('preTokens') else None)
    for d in m.sys.get('away_summary', []):
        if d.get('sessionId') == sid and thread_key(d) == mkey:
            add('events', 'mark', 'Away recap written', d['_t'], d=clip(d.get('content') or '', 240))
    for a in m.atts:
        if a['sid'] != sid or a['thread'] not in (key, mkey) or a['t'] is None:
            continue
        typ, at = a['type'], a['a']
        if typ in ('deferred_tools_delta', 'mcp_instructions_delta'):
            add_, rem = names_of(at.get('addedNames')), names_of(at.get('removedNames'))
            if not (add_ or rem):
                continue
            what = 'MCP servers' if typ == 'mcp_instructions_delta' else 'Tools'
            parts = ([f'+{len(add_)} added'] if add_ else []) + ([f'−{len(rem)} removed'] if rem else [])
            add('events', 'tools', f"{what} changed: {', '.join(parts)}", a['t'],
                d=clip(', '.join(['+' + x for x in add_] + ['−' + x for x in rem]), 300))
        elif typ == 'date_change':
            add('events', 'mark', 'Date changed', a['t'], d=at.get('newDate'))
        elif typ == 'thinking_drop':
            add('events', 'mark', 'Earlier thinking dropped', a['t'], d=((at.get('newlyDropped') or {}).get('reason')))
        elif typ in ('hook_non_blocking_error', 'hook_blocking_error', 'hook_cancelled'):
            add('events', 'err', f"Hook failed: {hook_script(at.get('command'))}", a['t'], d=clip(at.get('stderr') or '', 200))
    if p['model'] != c['model']:
        add('events', 'tools', f"Model: {model_name(p['model'])} → {model_name(c['model'])}", c['start'])
    if p['effort'] and c['effort'] and p['effort'] != c['effort']:
        add('events', 'tools', f"Effort: {p['effort']} → {c['effort']}", c['start'])
    return sorted(ev, key=lambda e: (e['t'], e.get('e') or e['t']))


def _trace_text(m, r, trig):
    """Plain-language account of the miss, and what would have avoided it."""
    c, cause, usd = r['c'], r['cause'], r['usd']
    p, rw, ttl = c['prev'], c['rewritten'], c['ttl']
    T = lambda t: clock(t, c['start'])
    ttlw, expiry, idle = ttl_word(ttl), cache_expiry(c), idle_of(c)
    who = 'The subagent' if c['is_sub'] else 'Claude'
    kind = trig[1] if trig else None
    tp = {'prompt': 'you sent a prompt', 'sdk': 'an SDK prompt arrived', 'message': "the main agent's message arrived",
          'answer': 'your answer came back'}.get(kind, 'the next request went out')
    if kind == 'command':
        tp = f"you ran /{trig[2]['name']}"
    elif kind == 'notif':
        tp = 'a subagent reported back' if trig[2].get('task') in m.subs else 'a background task reported back'
    elif kind == 'tool':
        tp = f"the {trig[2]['name']} result came back"
    hit = f"{money(usd)} more than reading them from the cache"
    story = [f"{who}'s previous reply ended at {T(p['t1'])}, with {f_tok(p['ctx'])} tokens of context in the cache."]
    advice = []
    tools_p = [m.tool_uses.get(b.get('id')) for b, _ in p['tools']]
    tools_p = [x for x in tools_p if x and x['res'] and x['res']['t'] is not None]
    errs = [x for x in m.errors if x['sid'] == c['sid'] and (x['agent'] or 'main') == c['thread'][1] and p['t1'] <= x['t0'] <= c['t0']]
    for e in errs[:1]:
        story.append(f"At {T(e['t0'])} Claude Code logged an API error for that reply: “{clip(e['errtext'] or e['err'] or '', 100)}”")
    from_cache = f"only the first {f_tok(c['cr'])} came from the cache" if c['cr'] else 'nothing came from the cache'
    if cause == 'You came back after a break':
        story.append(f"Nothing happened for {f_span(idle)}; the {ttlw} cache expired at {T(expiry)}.")
        story.append(f"At {T(c['start'])} {tp}, and the request re-wrote {f_tok(rw)} tokens" +
                     (f" (only the first {f_tok(c['cr'])} still came from the cache)" if c['cr'] else '') + f": {hit}.")
        if kind == 'notif':
            advice.append(f"It wasn't you who came back: a background task finished at {T(trig[0])} and woke the session after the "
                          f"cache had expired. A task that outlives the {ttlw} cache brings the whole context back at the write price.")
        base = median([s['baseline'] for s in m.sessions.values() if s['baseline']])
        rate = m.prices.per_token(c['model'], 'cw1h' if ttl >= 3600 else 'cw5m', c['pm'])
        if base and base < p['ctx']:
            advice.append(f"A fresh session starts at about {f_tok(base)} tokens (about {money(base * rate)} to write). When the next "
                          f"task is new, /clear or a new session with a short summary is cheaper than bringing back {f_tok(p['ctx'])} tokens.")
        advice.append(f"If you know you'll be away for more than {'an hour' if ttl >= 3600 else 'a few minutes'}, run /compact before "
                      f"leaving so there's less to re-write when you return.")
    elif cause == 'Waited on your answer/approval':
        w = next((x for x in tools_p if x['name'] in WAITING_TOOLS), None)
        if w:
            story.append(f"It asked for your answer ({w['name']}) at {T(w['t'])}; the {ttlw} cache expired at {T(expiry)}, and your "
                         f"answer came at {T(w['res']['t'])}, {f_span(w['res']['t'] - w['t'])} after the question.")
        story.append(f"The next request re-wrote {f_tok(rw)} tokens: {hit}.")
        advice.append(f"Answering within {'the hour' if ttl >= 3600 else '5 minutes'} would have kept the cache and saved {money(usd)}.")
    elif cause == 'Subagent resumed via SendMessage':
        story.append(f"It then sat idle for {f_span(idle)}; its {ttlw} cache " +
                     (f"was still alive (it would expire at {T(expiry)})." if c['start'] <= expiry else f"expired at {T(expiry)}."))
        send = next((tu for tu in m.tools if tu['name'] == 'SendMessage' and tu['sid'] == c['sid'] and tu['input'].get('to') == c['agent']
                     and tu['t'] is not None and p['t1'] - 5 <= tu['t'] <= c['t0']), None)
        if send:
            story.append(f"At {T(send['t'])} the main agent resumed it with SendMessage (“{clip(send['input'].get('summary') or '', 70)}”).")
        story.append(f"Its first request re-wrote {f_tok(rw)} tokens ({from_cache}): {hit}.")
        res = _resumes(m)
        mine = [x for x in res if x['thread'] == c['thread'] and x['miss']]
        n_miss = sum(1 for x in res if x['miss'])
        if res:
            advice.append((f"All {len(res)} subagent resumes in this scope missed the cache" if n_miss == len(res) else
                           f"{n_miss} of {len(res)} subagent resumes in this scope missed the cache") +
                          ", including ones idle for less than the cache lifetime: resuming rebuilds the subagent's history, so its "
                          "whole context is written again.")
        if len(mine) > 1:
            advice.append(f"This subagent was resumed {len(mine)} times; those re-writes cost {money(sum(m.miss_usd(x) for x in mine))}.")
        advice.append(f"For implement → review → fix loops, a fresh subagent with a short brief of what changed is usually cheaper "
                      f"than resuming one that carries {f_tok(p['ctx'])} tokens.")
    elif cause == 'Slow tool run':
        slow = max(tools_p, key=lambda x: x['latency'] or 0) if tools_p else None
        if slow:
            story.append(f"It started {slow['name']} ({clip(tool_detail(slow, m._cwd(c['sid'])), 60)}) at {T(slow['t'])}; the tool ran "
                         f"for {f_span(slow['latency'])}, and the {ttlw} cache expired at {T(expiry)} while it was still running.")
        story.append(f"When {tp.replace('the next request went out', 'the result came back')} at {T(c['start'])}, the next request "
                     f"re-wrote {f_tok(rw)} tokens: {hit}.")
        if c['is_sub']:
            mt = main_ttl(m)
            advice.append((f"This subagent caches for {life_word(ttl)}. Run long builds and tests from the main thread "
                           f"({ttl_word(mt)} cache), or start them " if mt > ttl else "Start long builds and tests ") +
                          "in the background and check back every few minutes: each check is a cheap cache read that keeps the cache alive.")
        else:
            advice.append(f"The command outlived the {ttlw} cache. Running it in the background and checking back before the cache "
                          f"expires keeps it alive for the price of a cache read.")
    elif cause == 'Slow or retried API response':
        wait, state = c['t0'] - c['start'], cache_state(c)
        if state == 'waiting':
            story.append(f"The next request went out at {T(c['start'])}, but the first response only arrived at {T(c['t0'])}, "
                         f"{f_span(wait)} later; the {ttlw} cache expired at {T(expiry)} while it waited.")
            advice.append(f"The API took {f_span(wait)} to start answering, longer than the {ttlw} cache. The transcript doesn't "
                          f"say why; rate-limit retries, queueing or the computer sleeping mid-request are the usual causes, not your prompt.")
        else:
            story.append(f"The next request went out at {T(c['start'])}, {f_span(idle)} after the previous reply ended" +
                         (f"; the {ttlw} cache had expired at {T(expiry)}." if state == 'before' else '.'))
        story.append(f"That request re-wrote {f_tok(rw)} tokens" +
                     (f"; the other {f_tok(c['cr'])} still came from the cache, so it was only a partial miss" if state == 'alive' and c['cr'] else '') +
                     f": {hit}.")
        n = _concurrent(m, c) if state == 'waiting' else 0
        if n >= 2:
            advice.append(f"{n} other threads of this session were calling the API at the same time. Running fewer subagents in "
                          f"parallel makes these waits, and the misses they cause, less likely.")
    elif cause == 'Tool list changed (MCP/tools)':
        ch = [a for a in m.atts if a['sid'] == c['sid'] and a['thread'] == c['thread'] and a['t'] is not None and p['t1'] <= a['t'] <= c['t0']
              and a['type'] in ('deferred_tools_delta', 'mcp_instructions_delta')]
        srv = [n for a in ch if a['type'] == 'mcp_instructions_delta' for n in names_of(a['a'].get('addedNames'))]
        gone = [n for a in ch if a['type'] == 'mcp_instructions_delta' for n in names_of(a['a'].get('removedNames'))]
        ntools = sum(len(names_of(a['a'].get('addedNames'))) + len(names_of(a['a'].get('removedNames'))) for a in ch
                     if a['type'] == 'deferred_tools_delta')
        if ch:
            what = ', '.join(([f"MCP server{'s' if len(srv) > 1 else ''} {', '.join(srv)} connected"] if srv else []) +
                             ([f"{', '.join(gone)} disconnected"] if gone else []) +
                             ([f"{ntools} tool definition{'s' if ntools != 1 else ''} changed"] if ntools and not srv else []))
            story.append(f"Between the two requests the tool list changed at {T(ch[0]['t'])}: {what or 'tools were added'}.")
        else:
            story.append(f"{f_span(idle)} later Claude Code reported that the tool definitions had changed; the transcript doesn't say which.")
        story.append(f"Tools sit at the start of the prompt, so the request re-wrote all {f_tok(rw)} tokens: {hit}.")
        if srv and c['idx'] <= 2:
            advice.append("The MCP servers were still connecting when the first request went out. Give them a few seconds after "
                          "start-up, or disable servers this project doesn't use (/mcp), so the tool list is final before you start.")
        elif ch:
            advice.append("Connecting MCP servers or loading new tools mid-session re-writes the whole context. Set them up before "
                          "you start, or in a fresh session.")
        else:
            advice.append("The transcript doesn't record which definitions changed; an MCP server dropping out or reconnecting is "
                          "one possible cause. If it recurs, /mcp shows which servers are connected.")
    elif cause in ('Model switch', 'Effort changed'):
        what = (f"the model changed from {model_name(p['model'])} to {model_name(c['model'])}" if cause == 'Model switch' else
                f"effort changed from {p['effort']} to {c['effort']}")
        story.append(f"At {T(c['start'])} {what}, and the request re-wrote {f_tok(rw)} tokens: {hit}.")
        advice.append("Caches belong to one model, so pick the model at the start of a session, or /clear before switching."
                      if cause == 'Model switch' else "Set effort at the start of a session; changing it mid-session re-writes the conversation.")
    else:
        reason = ((c.get('diag') or {}).get('cache_miss_reason') or {}).get('type')
        story.append(f"The next request went out {f_span(idle)} later, at {T(c['start'])}" +
                     (f", after the {ttlw} cache had expired at {T(expiry)}." if c['start'] > expiry else '.'))
        if c['cr']:
            story.append(f"The first {f_tok(c['cr'])} tokens still came from the cache, but the {f_tok(rw)} after them were re-written "
                         f"({money(usd)} extra): something earlier in the conversation changed" +
                         (f" (Claude Code logged “{reason}”)." if reason else '.'))
            advice.append("A partial miss like this has no clear cause in the transcript; it's worth a look only if it repeats.")
        else:
            story.append(f"The request re-wrote {f_tok(rw)} tokens ({money(usd)} extra)" +
                         (f"; Claude Code logged “{reason}”." if reason else '.'))
            if cause == 'Subagent idle until resumed':
                advice.append("A subagent that is needed again after its 5-minute cache has expired re-writes its whole context; a "
                              "fresh subagent with a short brief is often cheaper.")
    if any('sleep' in (e['errtext'] or '').lower() for e in errs):
        advice.insert(0, "The computer went to sleep mid-response. Keeping the Mac awake during long runs (for example by starting "
                         "Claude Code under caffeinate -i) avoids both the cut-off reply and this re-write.")
    return story, advice


def _trace_overview(m, c, w0, w1):
    """The miss in its session: context size over time, every miss on the same threads, and your prompts."""
    sid, key = c['sid'], c['thread']
    thr = [(sid, 'main')] + ([key] if c['is_sub'] else [])
    ov = {'main': _downsample([[ms(x['start']), x['ctx']] for x in m.threads.get((sid, 'main'), [])]), 'win': [ms(w0), ms(w1)]}
    if c['is_sub']:
        ov['sub'] = _downsample([[ms(x['start']), x['ctx']] for x in m.threads[key]])
        ov['subLabel'] = thread_label(m, key)
    ov['misses'] = [{'t': ms(x['start']), 'y': x['ctx'], 'rw': x['rewritten'], 'usd': r2(m.miss_usd(x)), 'id': trace_id(x),
                     'sub': 1 if x['is_sub'] else 0, 'cause': m.miss_cause(x)}
                    for tk in thr for x in m.threads.get(tk, []) if x['miss']]
    others = sum(1 for x in m.misses if x['sid'] == sid and x['thread'] not in thr)
    if others:
        ov['others'] = others
    ov['prompts'] = [ms(x['t']) for x in m.prompts if x['sid'] == sid]
    return ov


def trace_for(m, r):
    c = r['c']
    tid = trace_id(c)
    if tid in TRACES:
        return tid
    p, key, i = c['prev'], c['thread'], c['idx']
    calls = m.threads[key]
    lead = [x for x in calls[max(0, i - 3):i] if x['start'] >= p['start'] - 120]   # a little of what came before
    w0 = min(x['start'] for x in lead)
    expiry = cache_expiry(c)
    w1 = max(c['t1'], expiry + 1) if c['start'] <= expiry <= c['t1'] + 600 else c['t1']
    trig = _trigger(m, c)
    story, advice = _trace_text(m, r, trig)
    rate = {k: v * c['pm'] for k, v in (m.prices.rate(c['model']) or {}).items() if isinstance(v, (int, float))}
    sub = c['is_sub']
    lanes = [('you', 'You'), ('main', 'Main agent'), ('claude', 'This subagent' if sub else 'Claude'),
             ('tools', 'Its tools' if sub else 'Tools'), ('subs', 'Other subagents' if sub else 'Subagents'), ('events', 'Session')]
    TRACES[tid] = clean({
        'id': tid, 'cause': r['cause'], 'help': CAUSE_HELP.get(r['cause']), 'expired': expired(r),
        'usd': r['usd'], 'rw': c['rewritten'], 'ctx': p['ctx'], 'cr': c['cr'], 'model': model_name(c['model']),
        'thread': thread_label(m, key), 'sub': sub, 'session': sess_label(m, c['sid'], 70), 'project': m.src.label(c['proj']),
        'at': ms(c['start']), 'tz': tz_min(c['start']), 'ttl': c['ttl'], 'idle': idle_of(c),
        'state': cache_state(c),
        'wait': c['t0'] - c['start'], 'trigger': trig[1] if trig else None,
        'price': {'write': rate.get('cw1h' if c['ttl'] >= 3600 else 'cw5m'), 'read': rate.get('cr')},
        'marks': {'prevEnd': ms(p['t1']), 'lastUse': ms(p['t1']), 'expiry': ms(expiry), 'start': ms(c['start']),
                  'first': ms(c['t0'])},
        'win': [ms(w0), ms(w1)], 'lanes': [{'id': a, 'label': b} for a, b in lanes],
        'events': _trace_events(m, c, w0, w1), 'story': story, 'advice': advice,
        'ov': _trace_overview(m, c, w0, w1),
    })
    return tid


def ca_miss_traces(m, g):
    q = 'What happened before each costly cache miss, step by step?'
    rows = sorted(_miss_rows(m), key=lambda r: -r['usd'])
    if not rows:
        return card('_CA_TRACE', q, 'T P S A', [], empty='No cache misses in this scope.')
    top = rows[:TRACE_TOP]
    ids = [trace_for(m, r) for r in top]
    tot = sum(r['usd'] for r in rows)
    exp = [r for r in rows if expired(r)]
    alive = [r for r in rows if not expired(r)]
    b = top[0]
    bc = b['c']
    what = (f"coming back after {f_span(bc['start'] - bc['prev']['t1'])} to a {f_tok(bc['prev']['ctx'])}-token context"
            if b['cause'] == 'You came back after a break' else b['cause'].lower())
    ins = f"The costliest miss cost {money(b['usd'])}: {what}."
    if alive:
        ins += (f" {len(alive)} of {len(rows)} misses ({money(sum(r['usd'] for r in alive))}) happened while the cache was still "
                f"alive: the prompt changed, so waiting less would not have helped.")
    m.facts.update(alive_misses=len(alive))
    return card('_CA_TRACE', q, 'T P S A', [
        K(kpi('Misses traced', len(top), 'count', f'the costliest {len(top)} of {len(rows)}'),
          kpi('Their extra cost', sum(r['usd'] for r in top), 'usd', f"{f_pct(share(sum(r['usd'] for r in top), tot))} of all miss cost"),
          kpi('Cache had expired', len(exp), 'count', f"{f_usd(sum(r['usd'] for r in exp))} · idle past its lifetime"),
          kpi('Cache still alive', len(alive), 'count', f"{f_usd(sum(r['usd'] for r in alive))} · the prompt changed")),
        {'kind': 'traces', 'ids': ids}],
        why='Pick a miss to see its timeline: the previous reply, what happened in between (tools, your input, idle time), '
            'when the cache expired, and the request that paid to re-write it.',
        insight=ins,
        note='Times are local. Idle = from the end of the previous reply to the next request; the cache lifetime counts from '
             'there too. Extra cost = re-written tokens × (write price − read price).')


# ------------------------------------------------------------------------------ SV: what would it have saved?
# Each scenario re-prices the logged calls as if one habit or setting had been different from the first day.
# They are theoretical: they assume the same work would still have been done, and several levers overlap.

SUMMARY_TOK = 20_000     # context a /compact leaves behind (the one /compact in these logs left about 17K)
SUMMARY_OUT = 5_000      # output tokens to write that summary
REREAD_TOK = 10_000      # detail re-read after a compaction or a fresh start
BRIEF_TOK = 5_000        # a short summary pasted into a fresh session
RANGE_KEEP = 0.5         # share of a large file that a targeted read would still load
BIG_READ = 8_000         # tokens: a large Read result
COMPACT_AT = [60e3, 80e3, 100e3, 125e3, 150e3, 200e3, 250e3, 300e3, 400e3]
PROMPT_CHANGED = {'Subagent resumed via SendMessage', 'Tool list changed (MCP/tools)', 'Model switch', 'Effort changed',
                  'Earlier messages changed'}          # these miss whatever the cache lifetime
FIX = {  # cause -> (who can fix it, how)
    'You came back after a break': ('your habits', 'start fresh (or /compact) instead of returning to a big, expired session'),
    'Waited on your answer/approval': ('your habits', 'answer within the cache lifetime'),
    'Model switch': ('your habits', 'switch models only at the start of a session'),
    'Effort changed': ('your habits', 'set effort at the start of a session'),
    'Subagent resumed via SendMessage': ('how Claude delegates', 'a fresh subagent with a short brief instead of a resume'),
    'Subagent idle until resumed': ('how Claude delegates', 'a fresh subagent with a short brief instead of a late resume'),
    'Tool list changed (MCP/tools)': ('your setup', 'let MCP servers finish connecting; disable unused ones'),
    'Slow tool run': ('your setup', 'run long builds from the main thread or in the background'),
    'Computer went to sleep': ('your setup', 'keep the computer awake during long runs'),
    'Slow or retried API response': ('not in your control', 'fewer parallel subagents may help'),
}


def w_rate(m, c, ttl=None):
    return m.prices.per_token(c['model'], 'cw1h' if (ttl or c['ttl']) >= 3600 else 'cw5m', c['pm'])


def r_rate(m, c):
    return m.prices.per_token(c['model'], 'cr', c['pm'])


def span_days(m):
    """The days “all time” covers: the whole analysed period, the same for every project scope (a project used on one day of
    a three-week period is projected at that period's pace, not ×30)."""
    if getattr(m, 'window', None):
        return m.window
    return max(1.0, (max(c['t1'] for c in m.real) - min(c['t0'] for c in m.real)) / 86400) if m.real else 1.0


def per_month(m, usd):
    return usd * 30 / span_days(m)


def kpi_save(m, label, usd, sub=None):
    """A saving: the figure over all the analysed days, with its 30-day projection beside it."""
    d = kpi(label, usd, 'usd', sub)
    if usd is not None:
        d['month'] = r2(per_month(m, usd))
    return d


def f_save(m, usd):
    return f'{f_usd(usd)} all time (≈ {f_usd(per_month(m, usd))} per 30 days)'


def _slept(m, c):
    """The missed request followed an API error saying the computer went to sleep."""
    p = c['prev']
    return any('sleep' in (e['errtext'] or '').lower() for e in m.errors
               if e['sid'] == c['sid'] and (e['agent'] or 'main') == c['thread'][1] and p['t1'] <= e['t0'] <= c['t0'])


def _sv_unused(m):
    if 'sv_unused' in m.cache:
        return m.cache['sv_unused']
    e = _ext(m)
    tok, usd, n = e['per_sess'], 0.0, 0
    sess = uniq(a['sid'] for a in m.atts if a['type'] in ('skill_listing', 'mcp_instructions_delta', 'agent_listing_delta'))
    for sid in sess:
        for c in (m.sessions.get(sid) or {}).get('main_calls', []):
            usd += tok * (w_rate(m, c) if c['idx'] == 0 or c['miss'] else r_rate(m, c))
            n += 1
    m.cache['sv_unused'] = out = dict(tok=tok, sessions=len(sess), calls=n, usd=usd)
    return out


def _sv_misses(m):
    if 'sv_misses' not in m.cache:
        rows = []
        for r in _miss_rows(m):
            cause = 'Computer went to sleep' if _slept(m, r['c']) else r['cause']
            who, how = FIX.get(cause, ('unclear', 'no clear pattern; worth a look only if it repeats'))
            rows.append(dict(r, fix_cause=cause, who=who, how=how))
        m.cache['sv_misses'] = rows
    return m.cache['sv_misses']


def _sv_compact(m):
    """Replay every main thread as if you had run /compact whenever the context was about to pass a threshold."""
    if 'sv_compact' in m.cache:
        return m.cache['sv_compact']
    threads = [t for k, t in m.threads.items() if k[1] == 'main' and len(t) > 1]
    res = []
    for T in COMPACT_AT:
        saved = cost = 0.0
        n = aff = 0
        for t in threads:
            base = t[0]['ctx']
            after = base + SUMMARY_TOK + REREAD_TOK
            sim = t[0]['ctx']
            for i in range(1, len(t)):
                c, p = t[i], t[i - 1]
                if c['compacted']:
                    sim = c['ctx']
                    continue
                growth = c['ctx'] - p['ctx']
                nxt = sim + growth
                if nxt > T and sim > after:
                    n += 1
                    cost += sim * r_rate(m, p) + SUMMARY_OUT * m.prices.per_token(p['model'], 'out', p['pm']) + (after - base) * w_rate(m, p)
                    nxt = after + max(0, growth)
                sim = min(nxt, c['ctx'])
                d = c['ctx'] - sim
                if d > 0:
                    aff += 1
                    saved += d * (w_rate(m, c) if c['miss'] else r_rate(m, c))
        res.append(dict(T=T, n=n, calls=aff, saved=saved, cost=cost, net=saved - cost))
    m.cache['sv_compact'] = res
    return res


TTL_PAUSES = [(300, 600, '5–10 min'), (600, 1200, '10–20 min'), (1200, 2400, '20–40 min'), (2400, 3600, '40–60 min')]


def ttl_fixed_miss(m, c):
    """A miss that happens whatever the lifetime: a changed prompt, or one within 5 minutes (retries, parallel requests)."""
    return c['miss'] and (m.miss_cause(c) in PROMPT_CHANGED or cache_state(c) == 'alive' or idle_of(c) <= 300)


def _ttl_tokens(m, c, tau):
    """Cache tokens of call c had its thread used lifetime tau: (read, written, miss, flipped).

    Anchored on what actually happened: a longer lifetime than the actual one never adds a miss, a shorter one never
    removes one, so only the other direction is predicted (from the idle time). Replaying the actual lifetime therefore
    reproduces the actual cost exactly."""
    rd, wr, miss, flip = c['cr'], c['cw'], c['miss'], False
    if c['prev']:
        if c['miss'] and tau > c['ttl'] and not ttl_fixed_miss(m, c) and idle_of(c) <= tau:
            rw = c['rewritten']
            rd, wr, miss, flip = c['cr'] + rw, c['cw'] - rw, False, True
        elif not c['miss'] and tau < c['ttl'] and idle_of(c) > tau:
            rd, wr, miss, flip = 0, c['cw'] + c['cr'], True, True
    return rd, wr, miss, flip


def _ttl_price(m, c, tau):
    """Cache cost of call c had its thread used lifetime tau. Returns (usd, miss, flipped)."""
    rd, wr, miss, flip = _ttl_tokens(m, c, tau)
    return rd * r_rate(m, c) + wr * w_rate(m, c, tau), miss, flip


def _sv_ttl(m):
    """Replay the whole history with every cache entry living 5 minutes, then 1 hour (main threads and subagents apart)."""
    if 'sv_ttl' in m.cache:
        return m.cache['sv_ttl']
    cache_usd = lambda c: c['cost'].get('cache_read', 0) + c['cost'].get('write_5m', 0) + c['cost'].get('write_1h', 0)
    out, daily, types = {}, {}, collections.defaultdict(lambda: dict(actual=0.0, c5=0.0, c1h=0.0, calls=0))
    for kind in ('main', 'sub'):
        v = dict(actual=0.0, c5=0.0, c1h=0.0, n5=0, n1h=0, calls=0, replay=0.0, premium=0.0, n_calls_prev=0,
                 pauses=[dict(label=lbl, n=0, ctx=0, d=0.0) for _, _, lbl in TTL_PAUSES], long=0,
                 parts={tau: dict(read=0.0, new=0.0, pause=0.0, either=0.0) for tau in (300, 3600)}, ttls=collections.Counter())
        day = collections.defaultdict(float)
        for key, t in m.threads.items():
            if (key[1] == 'main') != (kind == 'main'):
                continue
            for c in t:
                a = cache_usd(c)
                (u5, x5, f5), (u1, x1, f1) = _ttl_price(m, c, 300), _ttl_price(m, c, 3600)
                v['actual'] += a
                v['replay'] += _ttl_price(m, c, c['ttl'])[0]
                v['c5'] += u5
                v['c1h'] += u1
                v['calls'] += 1
                v['n5'] += x5
                v['n1h'] += x1
                v['ttls'][c['ttl']] += 1
                tk = {tau: _ttl_tokens(m, c, tau) for tau in (300, 3600)}
                for tau, (rd, wr, miss, _) in tk.items():     # split each lifetime's cache cost into what it pays for
                    pt, wr_ = v['parts'][tau], w_rate(m, c, tau)
                    pt['read'] += rd * r_rate(m, c)
                    rw = 0
                    if miss and c['prev']:
                        if x5 != x1:                            # re-written only because the entry expired after 5 minutes
                            rw = min(wr, tk[3600][0] - rd)
                            pt['pause'] += rw * wr_
                        else:                                   # a miss under both lifetimes
                            rw = min(wr, c['rewritten'] if c['miss'] else c['cr'])
                            pt['either'] += rw * wr_
                    pt['new'] += (wr - rw) * wr_
                day[_period(c['start'], False)] += u5 - u1
                if kind == 'sub':
                    ty = types[c.get('agt') or 'unnamed']
                    ty['actual'] += a
                    ty['c5'] += u5
                    ty['c1h'] += u1
                    ty['calls'] += 1
                if x5 != x1:        # a pause the lifetime decides: missed on 5 minutes, kept on 1 hour
                    idle = idle_of(c)
                    b = next(i for i, (lo, hi, _) in enumerate(TTL_PAUSES) if lo < idle <= hi or i == len(TTL_PAUSES) - 1)
                    p = v['pauses'][b]
                    p['n'] += 1
                    p['ctx'] += c['ctx']
                    p['d'] += u5 - u1
                else:               # same outcome under both: only the write rate differs
                    v['premium'] += u1 - u5
                    if c['prev'] and x5 and not ttl_fixed_miss(m, c) and idle_of(c) > 3600:
                        v['long'] += 1
        for p in v['pauses']:
            p['ctx'] = p['ctx'] / p['n'] if p['n'] else 0
        n_p = sum(p['n'] for p in v['pauses'])
        penalty = sum(p['d'] for p in v['pauses'])
        v.update(n_pauses=n_p, penalty=penalty, rate=100 * n_p / v['calls'] if v['calls'] else 0.0,
                 breakeven=100 * n_p * v['premium'] / (penalty * v['calls']) if penalty > 0 and v['calls'] else None,
                 calib=(v['replay'] - v['actual']) / v['actual'] if v['actual'] else 0.0)
        out[kind] = v
        acc, cum = 0.0, []
        for d in sorted(day):
            acc += day[d]
            cum.append((d, acc))
        daily[kind] = cum
    other = m.usd - out['main']['actual'] - out['sub']['actual']
    combos = []
    for mt, mk in ((300, 'c5'), (3600, 'c1h')):
        for st_, sk in ((300, 'c5'), (3600, 'c1h')):
            combos.append(dict(main=mt, sub=st_, usd=other + out['main'][mk] + out['sub'][sk]))
    out.update(combos=combos, daily=daily, other=other,
               by_type={k: v for k, v in sorted(types.items(), key=lambda kv: -kv[1]['actual'])})
    m.cache['sv_ttl'] = out
    return out


def _sv_models(m):
    """The same calls at another model's list prices."""
    if 'sv_models' in m.cache:
        return m.cache['sv_models']
    main_calls = sorted((c for c in m.real if not c['agent']), key=lambda c: c['t0'])
    recent = collections.Counter(c['model'] for c in main_calls[-200:])
    current = next((k for k, _ in recent.most_common() if m.prices.rate(k)), None)     # the model you use now
    names = list(m.prices.compare) + ([current] if current and current not in m.prices.compare else [])
    rows = []
    for mdl in names:
        main = sum(sum(m.prices.cost(mdl, c['u'], m.prices.mult(mdl, c['where'])).values()) for c in m.real if not c['agent'])
        sub = sum(sum(m.prices.cost(mdl, c['u'], m.prices.mult(mdl, c['where'])).values()) for c in m.real if c['agent'])
        rows.append(dict(model=mdl, main=main, sub=sub))
    act_main = sum(c['usd'] for c in m.real if not c['agent'])
    act_sub = sum(c['usd'] for c in m.real if c['agent'])
    explore = [c for c in m.real if c['agent'] and (m.subs.get(c['agent']) or {}).get('type') == 'Explore']
    haiku = m.prices.pick('haiku')

    def cheaper(calls, mdl):
        """What moving the calls on pricier models to `mdl` would have saved (cheaper calls stay as they were)."""
        if not mdl:
            return 0.0
        top = m.prices.rate(mdl)['out']
        return sum(c['usd'] - sum(m.prices.cost(mdl, c['u'], m.prices.mult(mdl, c['where'])).values()) for c in calls
                   if (m.prices.rate(c['model']) or {}).get('out', 0) > top)
    sonnet = m.prices.pick('sonnet')
    m.cache['sv_models'] = out = dict(rows=rows, act_main=act_main, act_sub=act_sub, explore=sum(c['usd'] for c in explore),
                                      explore_haiku=sum(sum(m.prices.cost(haiku, c['u'], m.prices.mult(haiku, c['where'])).values()) for c in explore)
                                      if haiku else None,
                                      current=current, sonnet=sonnet, haiku=haiku, main_saving=cheaper(main_calls, current),
                                      sub_saving=cheaper([c for c in m.real if c['agent']], sonnet))
    return out


def _sv_fresh(m):
    """Returns to an expired, big session priced as a fresh session that starts from a short summary instead."""
    if 'sv_fresh' in m.cache:
        return m.cache['sv_fresh']
    base = median([s['baseline'] for s in m.sessions.values() if s['baseline']]) or 30_000
    rets = sorted((r['c'] for r in _miss_rows(m) if r['cause'] == 'You came back after a break'), key=lambda c: c['t0'])
    firsts = {(c['thread'], c['idx']) for c in rets}
    rows = []
    for c in rets:
        start = base + BRIEF_TOK + REREAD_TOK
        d = c['prev']['ctx'] - start
        if d <= 0:
            continue
        first = d * w_rate(m, c)
        later, n = 0.0, 0
        for x in m.threads[c['thread']][c['idx'] + 1:]:
            if x['compacted'] or (x['thread'], x['idx']) in firsts:
                break
            later += d * (w_rate(m, x) if x['miss'] else r_rate(m, x))
            n += 1
        rows.append(dict(c=c, d=d, first=first, later=later, n=n, usd=first + later))
    m.cache['sv_fresh'] = out = dict(base=base, rows=rows, usd=sum(r['usd'] for r in rows))
    return out


def _sv_reads(m):
    """Whole-file reads of large files, and what reading only the needed part would have saved."""
    if 'sv_reads' in m.cache:
        return m.cache['sv_reads']
    items = [it for it in m.items if it['tu'] is not None and it['tu']['name'] == 'Read' and it['tokens'] >= BIG_READ
             and not ({'offset', 'limit'} & set(it['tu']['input']))]
    ttl = lambda it: m.ttl.get(it['thread'], 3600 if it['thread'][1] == 'main' else 300)
    stake = sum(it['carry_usd'] + it['tokens'] * m.prices.per_token(it['model'], 'cw1h' if ttl(it) >= 3600 else 'cw5m', it['pm'])
                for it in items)
    m.cache['sv_reads'] = out = dict(items=items, tokens=sum(it['tokens'] for it in items), stake=stake, usd=stake * (1 - RANGE_KEEP))
    return out


def sv_levers(m):
    """Every lever with its standalone saving so far; SV1 and the insights read this."""
    if 'sv_levers' in m.cache:
        return m.cache['sv_levers']
    un, ms_, comp, ttl, mod, fr, rd = (_sv_unused(m), _sv_misses(m), _sv_compact(m), _sv_ttl(m), _sv_models(m), _sv_fresh(m),
                                       _sv_reads(m))
    best = max(comp, key=lambda r: r['net']) if comp else None
    avoid = sum(r['usd'] for r in ms_ if r['who'] not in ('not in your control', 'unclear'))
    ttl_gain = max(0.0, m.usd - min(x['usd'] for x in ttl['combos']))
    post = _post_stop(m)[0]
    levers = [
        dict(id='unused', label='Remove unused skills, MCP servers and agent types', usd=un['usd'], card='SV2'),
        dict(id='misses', label='Avoid the avoidable cache misses', usd=avoid, card='SV3'),
        dict(id='compact', label=f"/compact at about {f_tok(best['T'])} tokens" if best else '/compact earlier', usd=max(0.0, best['net']) if best else 0.0,
             card='SV4'),
        dict(id='ttl', label='Cache lifetime that fits each thread kind', usd=ttl_gain, card='SV5'),
        dict(id='sub_model', label=f"Run subagents on {model_name(mod['sonnet'])}" if mod['sonnet'] else 'Run subagents on a cheaper model', usd=mod['sub_saving'], card='SV6'),
        dict(id='main_model', label=f"Main threads on {(model_name(mod['current']) if mod['current'] else 'one model')} (your current model)", usd=mod['main_saving'], card='SV6'),
        dict(id='fresh', label='Start fresh after long breaks', usd=fr['usd'], card='SV7'),
        dict(id='reads', label='Read large files in ranges', usd=rd['usd'], card='SV8'),
        dict(id='stop_hook', label='Stop-hook follow-up work (upper bound)', usd=sum(c['usd'] for c in post), card='EX5'),
    ]
    for lv in levers:
        lv['share'] = share(lv['usd'], m.usd)
        lv['month'] = per_month(m, lv['usd'])
    levers.sort(key=lambda lv: -lv['usd'])
    m.cache['sv_levers'] = levers
    return levers


def sv1(m, g):
    lv = [x for x in sv_levers(m) if x['usd'] > 0.005]
    if not lv:
        return card('SV1', 'How much could I have saved so far, lever by lever?', 'T P', [], empty='No savings scenario applies here.')
    top = lv[0]
    m.facts.update(sv_top=top['label'], sv_top_usd=top['usd'])
    return card('SV1', 'How much could I have saved so far, lever by lever?', 'T P', [
        K(kpi_save(m, 'Biggest lever', top['usd'], clip(top['label'], 48)), kpi('Its share of spend', top['share'], 'pct'),
          kpi('Days covered', span_days(m), 'count', '“all time” is these days; “per 30 days” projects them to 30')),
        BAR([x['label'] for x in lv], [S('All time', [r2(x['usd']) for x in lv], 1), S('Per 30 days (projected)', [r2(x['month']) for x in lv], 2)],
            'usd', title='Theoretical saving by lever: all time and per 30 days'),
        TABLE([('l', 'Lever', None), ('u', 'Saved, all time', 'usd'), ('mo', 'Per 30 days', 'usd'), ('s', 'Share of spend', 'pct'), ('c', 'Details', None)],
              [dict(l=x['label'], u=r2(x['usd']), s=r1(x['share']), mo=r2(x['month']), c=x['card']) for x in lv])],
        why='The cost questions say where the money went; this says which change would have kept the most of it.',
        insight=f"The biggest lever is “{top['label']}”: about {f_save(m, top['usd'])} saved ({f_pct(top['share'])} of spend).",
        note='Each lever is priced on its own, as if applied from the first day with the same work done. Levers overlap '
             '(a smaller context also makes misses and re-reads cheaper), so they do not add up.')


def places(src, used, n=None):
    """The projects that use an item (where_used()'s used), most uses first: '~/Dev/app and ~/Dev/web'; with n, at most n
    of them and '… and 3 more' (for display only: advice names every project, or following it breaks the others)."""
    ks = sorted(used, key=lambda k: (-used[k], k))
    n = len(ks) if n is None else n
    names = [src.label(k) for k in ks[:n]] + ([f'{len(ks) - n} more'] if len(ks) > n else [])
    return names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]


def scope_how(src, kind, name, w):
    """How to load an item only in the projects that use it (w: its where_used() entry), instead of switching it off and on."""
    there = places(src, w['used'])
    if kind == 'MCP server':
        return f"add it with claude mcp add … -s local (or to the project's .mcp.json) in {there}, then claude mcp remove {name} -s user"
    if kind == 'plugin':
        return (f'enabledPlugins "{w["pid"] or name}": true in .claude/settings.local.json of {there}, and false in '
                f'{tilde(os.path.join(src.home, "settings.json"))}')
    cmd = os.path.join(src.home, 'commands', name + '.md')         # a personal slash command rather than a skill folder
    if not os.path.isdir(os.path.join(src.home, 'skills', name)) and os.path.exists(cmd):
        return f'move {tilde(cmd)} into .claude/commands/ of {there}'
    return f'move {tilde(os.path.join(src.home, "skills", name))} into .claude/skills/ of {there}'


def item_origin(src, kind, name, where=None):
    """Where an unused item comes from, and how to stop loading it: only in the projects that use it when another project
    does (where: where_used()), else off or removed. Never "switch it off in each project": that is the toggling it avoids."""
    home, where = src.home, where or {}
    if kind == 'skill':
        if name.startswith('anthropic-skills:'):
            return 'synced from your account', f'skillOverrides "{name}": "off"'
        if ':' in name:
            w = where.get(('plugin', name.split(':')[0]))
            return f"plugin {name.split(':')[0]}", scope_how(src, 'plugin', name.split(':')[0], w) if w else 'disable the plugin (enabledPlugins)'
        if os.path.exists(os.path.join(home, 'skills', name, 'SKILL.md')) or os.path.exists(os.path.join(home, 'commands', name + '.md')):
            w = where.get(('skill', name))
            return f'personal ({tilde(home)})', scope_how(src, 'skill', name, w) if w else f'skillOverrides "{name}": "off"'
        for p_, cwd in src.cwd.items():
            if cwd and (os.path.exists(os.path.join(cwd, '.claude', 'skills', name, 'SKILL.md'))
                        or os.path.exists(os.path.join(cwd, '.claude', 'commands', name + '.md'))):
                return f'project {src.label(p_)}', f'skillOverrides "{name}": "off" in that project’s .claude/settings.local.json'
        return 'built in, or no longer on disk', 'leave it'
    if kind == 'MCP server':
        if name in (src.mcp.get('user') or {}):
            w = where.get(('MCP server', name))
            return 'your MCP config (user scope)', (scope_how(src, 'MCP server', name, w) if w else
                                                    f'claude mcp remove {name} -s user (add it with -s local where a project needs it)')
        if name.startswith('claude.ai'):
            return 'claude.ai connector', '"disableClaudeAiConnectors": true'
        if 'chrome' in name:
            return 'Claude in Chrome extension', '/chrome settings'
        return 'connected server', f'/mcp disable {name}'
    if os.path.exists(os.path.join(home, 'agents', name + '.md')):
        return f'personal agent ({tilde(os.path.join(home, "agents"))})', f'move the agent file out of {tilde(os.path.join(home, "agents"))}'
    for p_, cwd in src.cwd.items():
        if cwd and os.path.exists(os.path.join(cwd, '.claude', 'agents', name + '.md')):
            return f'project agent ({src.label(p_)})', "move it out of that project's .claude/agents"
    return 'built in', 'leave it'


def sv2(m, g):
    u, e = _sv_unused(m), _ext(m)
    wt = {group_of(p) for p in g.src.cwd if group_of(p) != p}                   # projects with task worktrees
    scoped = [dict(n=(o['pid'] or name) if kind == 'plugin' else name, k=kind, o=o['origin'], w=places(g.src, o['used'], 3),
                   wa=places(g.src, o['used']), x='yes' if set(o['used']) & wt else 'no', i=len(o['idle']), s=o['sessions'], t=round(o['tok']),
                   u=r2(sum(o['idle'].values())), mo=r2(per_month(m, sum(o['idle'].values()))), h=scope_how(g.src, kind, name, o))
              for (kind, name), o in (g.where if g.is_all else {}).items() if sum(o['idle'].values()) >= 0.005]
    if not u['tok'] and not scoped:
        return card('SV2', 'What do unused skills, MCP servers and agent types cost me?', 'T P', [], empty='Nothing loaded went unused.')
    tot = sum(t for _, _, t in e['items']) or 1
    rows = []
    for kind, name, tok_ in sorted(e['items'], key=lambda x: -x[2]):
        origin, how = item_origin(g.src, kind, name, g.where)
        rows.append(dict(n=name, k=kind, t=round(tok_), u=r2(u['usd'] * tok_ / tot), mo=r2(per_month(m, u['usd'] * tok_ / tot)), o=origin, h=how))
    removable = [r for r in rows if r['h'] != 'leave it']
    return card('SV2', 'What do unused skills, MCP servers and agent types cost me?', 'T P', ([
        K(kpi('Unused tokens per session', u['tok'], 'tokens', f"{len(e['un_sk'])} skills · {len(e['un_mcp'])} MCP · {len(e['un_ag'])} agent types"),
          kpi_save(m, 'Saved if removed', u['usd'], f"{u['calls']:,} calls in {u['sessions']} sessions re-read them"),
          kpi_save(m, 'Of which you can switch off', sum(r['u'] for r in removable), f"{len(removable)} items; the rest are built in")),
        TABLE([('n', 'Item', None), ('k', 'Kind', None), ('t', 'Tokens per session', 'tokens'), ('u', 'Saved, all time', 'usd'),
               ('mo', 'Per 30 days', 'usd'), ('o', 'Comes from', None), ('h', 'How to switch it off', None)], rows, 'Every unused item, largest first', 12)]
        if u['tok'] else []) + ([
        TABLE([('n', 'Item', None), ('k', 'Kind', None), ('w', 'Used in', None), ('i', 'Other projects loading it', 'count'),
               ('s', 'Their sessions', 'count'), ('u', 'Saved, all time', 'usd'), ('mo', 'Per 30 days', 'usd'),
               ('h', 'Set it up only there', None)],        # rows also keep wa (every project using it), x (task worktrees
                                                             # there), t (tokens) and o (origin)
              scoped, 'Used in some projects, loaded in every one: set up only where used', 12)] if scoped else []),
        why='Everything listed at session start is re-read on every later call of that session, used or not. Something one project '
            'needs can be set up in that project alone, so the others never load it and nothing has to be switched on and off.',
        insight=f"Unused listings add about {f_tok(u['tok'])} tokens to every session; removing them would have saved {f_save(m, u['usd'])}."
                if u['tok'] else None,
        note='Priced per main-thread call: re-read at the cache-read price, written at the write price on first calls and misses. '
             'Some built-in skills and agent types cannot be switched off.')


def sv3(m, g):
    rows = _sv_misses(m)
    if not rows:
        return card('SV3', 'Which cache misses were avoidable, and what would avoiding them have saved?', 'T P S', [],
                    empty='No cache misses in this scope.')
    agg = collections.defaultdict(lambda: dict(n=0, usd=0.0, who='', how=''))
    for r in rows:
        a = agg[r['fix_cause']]
        a['n'] += 1
        a['usd'] += r['usd']
        a['who'], a['how'] = r['who'], r['how']
    order = sorted(agg, key=lambda k: -agg[k]['usd'])
    avoid = sum(a['usd'] for a in agg.values() if a['who'] not in ('not in your control', 'unclear'))
    who = collections.defaultdict(float)
    for a in agg.values():
        who[a['who']] += a['usd']
    wk = sorted(who, key=lambda k: -who[k])
    return card('SV3', 'Which cache misses were avoidable, and what would avoiding them have saved?', 'T P S', [
        K(kpi_save(m, 'Avoidable', avoid, f"{f_pct(share(avoid, sum(r['usd'] for r in rows)))} of all miss cost"),
          kpi('Not in your control', who.get('not in your control', 0.0), 'usd')),
        BAR(wk, [S('All time', [r2(who[k]) for k in wk], 1), S('Per 30 days (projected)', [r2(per_month(m, who[k])) for k in wk], 2)], 'usd',
            title='Miss cost by who can fix it: all time and per 30 days'),
        TABLE([('k', 'Cause', None), ('n', 'Misses', 'count'), ('u', 'Extra cost, all time', 'usd'), ('mo', 'Per 30 days', 'usd'),
               ('w', 'Who can fix it', None), ('h', 'How', None)],
              [dict(k=k, n=agg[k]['n'], u=r2(agg[k]['usd']), mo=r2(per_month(m, agg[k]['usd'])), w=agg[k]['who'], h=agg[k]['how']) for k in order])],
        why='Separates the misses a habit or a setting would have prevented from the ones you could not have.',
        insight=f"Avoidable cache-miss cost: {f_save(m, avoid)}, preventable by a habit or a setting." if avoid else None,
        note='Misses that followed a “computer went to sleep” API error are grouped as their own cause. CX8 traces each one, step by step.')


def sv4(m, g):
    res = _sv_compact(m)
    if not res or not any(r['n'] for r in res):
        return card('SV4', 'At what context size should I /compact, and what would it have saved?', 'T P', [],
                    empty='No main thread grew past the smallest threshold.')
    best = max(res, key=lambda r: r['net'])
    m.facts.update(sv_compact_T=best['T'], sv_compact_usd=best['net'])
    shown = [r for r in res if r['net'] >= -2 * best['net']] if best['net'] > 0 else res   # keep the optimum readable
    return card('SV4', 'At what context size should I /compact, and what would it have saved?', 'T P', [
        K(kpi('Best threshold', best['T'], 'tokens', f"{best['n']} compactions"),
          kpi_save(m, 'Net saving', best['net'], f"after {f_usd(best['cost'])} spent compacting")),
        LINE([int(r['T']) for r in shown], [S('All time', [r2(r['net']) for r in shown], 1),
                                            S('Per 30 days (projected)', [r2(per_month(m, r['net'])) for r in shown], 2)], 'usd', x_kind='num', x_unit='tokens',
             title='Net saving if you had compacted at each threshold' + (' (thresholds that lose much more are in the table)'
                                                                          if len(shown) < len(res) else '')),
        TABLE([('t', 'Compact at', 'tokens'), ('n', 'Compactions', 'count'), ('a', 'Calls with a smaller context', 'count'),
               ('s', 'Read/write saved', 'usd'), ('c', 'Compaction cost', 'usd'), ('u', 'Net, all time', 'usd'), ('mo', 'Net per 30 days', 'usd')],
              [dict(t=r['T'], n=r['n'], a=r['calls'], s=r2(r['saved']), c=r2(r['cost']), u=r2(r['net']), mo=r2(per_month(m, r['net']))) for r in res])],
        why='A smaller context is cheaper on every call; a compaction costs one summary. This finds where the two balance for your sessions.',
        insight=(f"Compacting at about {f_tok(best['T'])} tokens would have saved {f_save(m, best['net'])} ({best['n']} compactions)."
                 if best['net'] > 0 else 'Compacting earlier would not have paid off for your sessions.'),
        note=f'Replays every main thread: when the context would pass the threshold, it drops to the start-up size + {f_tok(SUMMARY_TOK)} '
             f'summary + {f_tok(REREAD_TOK)} re-read detail, and the compaction costs one full read plus {f_tok(SUMMARY_OUT)} output tokens. '
             'Detail lost in a summary can cost extra work that this does not see.')


def sv5(m, g):
    t = _sv_ttl(m)
    title = '5-minute or 1-hour cache: which fits my sessions?'
    kinds = [(k, lbl) for k, lbl in (('main', 'Main threads'), ('sub', 'Subagents')) if t[k]['calls']]
    if not kinds:
        return card('SV5', title, 'T P A', [])
    life = {300: '5 min', 3600: '1 hour'}
    combo = lambda x: f"main {life[x['main']]} · subagents {life[x['sub']]}"
    best = min(t['combos'], key=lambda x: x['usd'])
    c55 = next(x for x in t['combos'] if x['main'] == 300 and x['sub'] == 300)
    c11 = next(x for x in t['combos'] if x['main'] == 3600 and x['sub'] == 3600)
    rows, verdict = [], []
    for k, lbl in kinds:
        v = t[k]
        cheap = '5 minutes' if v['c5'] <= v['c1h'] else '1 hour'
        rows.append(dict(k=lbl, a=r2(v['actual']), c5=r2(v['c5']), n5=v['n5'], c1=r2(v['c1h']), n1=v['n1h'], b=cheap,
                         d=r2(abs(v['c5'] - v['c1h'])), dm=r2(per_month(m, abs(v['c5'] - v['c1h'])))))
        margin = abs(v['c5'] - v['c1h']) / max(v['c5'], v['c1h'], 1e-9)
        verdict.append(f"{lbl.lower()} {cheap}, cheaper by {f_save(m, abs(v['c5'] - v['c1h']))}{', a close call' if margin < 0.05 else ''}")
    if len(t['by_type']) > 1:
        for name, v in list(t['by_type'].items())[:8]:
            rows.append(dict(k=f'· subagent {name}', a=r2(v['actual']), c5=r2(v['c5']), c1=r2(v['c1h']),
                             b='5 minutes' if v['c5'] <= v['c1h'] else '1 hour', d=r2(abs(v['c5'] - v['c1h'])),
                             dm=r2(per_month(m, abs(v['c5'] - v['c1h'])))))
    why = []
    for k, lbl in kinds:
        v = t[k]
        for p in v['pauses']:
            if p['n']:
                why.append(dict(k=lbl, w=f"Pauses of {p['label']}: expire on 5 min, kept on 1 hour", n=p['n'], x=int(p['ctx']),
                                d=r2(p['d'])))
        why.append(dict(k=lbl, w='Writing every entry at 2× instead of 1.25× input (1-hour premium)'
                        + (f"; includes {v['long']} pauses over 1 hour, which miss either way" if v['long'] else ''),
                        n=v['calls'], d=r2(-v['premium'])))
        why.append(dict(k=lbl, w='= 5 minutes minus 1 hour', d=r2(v['c5'] - v['c1h'])))
    days = sorted({d for k, _ in kinds for d, _ in t['daily'][k]})
    series = []
    for k, lbl in kinds:
        cum, acc, j = dict(t['daily'][k]), 0.0, []
        for d in days:
            acc = cum.get(d, acc)
            j.append(r2(acc))
        series.append(S(lbl, j, 1 if k == 'main' else 2))
    tip = []
    for k, lbl in kinds:
        v = t[k]
        if v['breakeven'] is not None:
            tip.append(f"{lbl.lower()}: 1 hour pays off above {v['breakeven']:.1f} pauses of 5–60 min per 100 calls; yours is {v['rate']:.1f}")
    calib = max(abs(t[k]['calib']) for k, _ in kinds)
    recent = []
    if days:
        cut = (dt.datetime.strptime(days[-1], '%Y-%m-%d') - dt.timedelta(days=7)).strftime('%Y-%m-%d')
        for k, lbl in kinds:
            before = [a for d, a in t['daily'][k] if d <= cut]
            delta = t['daily'][k][-1][1] - (before[-1] if before else 0.0) if t['daily'][k] else 0.0
            if abs(delta) >= 1 and (before or len(days) > 1):
                recent.append(f"{lbl.lower()} {'1 hour' if delta > 0 else '5 minutes'} by {f_usd(abs(delta))}")
    return card('SV5', title, 'T P A', [
        K(kpi('Total if every entry lived 5 min', c55['usd'], 'usd', f"{'+' if c55['usd'] >= m.usd else '−'}{f_usd(abs(c55['usd'] - m.usd))} vs actual"),
          kpi('Total if every entry lived 1 hour', c11['usd'], 'usd', f"{'+' if c11['usd'] >= m.usd else '−'}{f_usd(abs(c11['usd'] - m.usd))} vs actual"),
          kpi('Actual total', m.usd, 'usd', 'your real mix'),
          kpi_save(m, 'Cheapest mix saves', max(0.0, m.usd - best['usd']), f"{combo(best)}: {f_usd(best['usd'])} in total")),
        BAR([combo(x) for x in t['combos']], [S('Total cost', [r2(x['usd']) for x in t['combos']], 1)], 'usd',
            title='Whole history, re-priced under each lifetime (total bill)'),
        TABLE([('k', 'Threads', None), ('a', 'Actual cache cost', 'usd'), ('c5', 'If 5 min', 'usd'), ('n5', 'Misses (5 min)', 'count'),
               ('c1', 'If 1 hour', 'usd'), ('n1', 'Misses (1 hour)', 'count'), ('b', 'Cheaper', None), ('d', 'Difference, all time', 'usd'),
               ('dm', 'Per 30 days', 'usd')], rows,
              'Cache read + write cost per thread kind'),
        TABLE([('k', 'Threads', None), ('w', 'Where the difference comes from', None), ('n', 'Calls', 'count'),
               ('x', 'Avg context', 'tokens'), ('d', '5 min minus 1 hour', 'usd')], why, 'Why: pauses that expire vs the 1-hour write premium'),
        LINE(days, series, 'usd', title='Running total: 5 minutes minus 1 hour (above 0 = 1 hour cheaper so far)',
             ref_lines=[{'axis': 'y', 'value': 0, 'label': 'break-even'}]) if len(days) > 1 else None],
        why='A 1-hour entry costs 2× input to write instead of 1.25×, but survives pauses of 5–60 minutes that a 5-minute entry '
            'would have to rewrite in full. Which wins depends on how often you pause, and how big the context is when you do.',
        insight='Cheapest lifetime: ' + '; '.join(verdict) + f". Cheapest mix: {combo(best)}, {f_usd(best['usd'])} in total"
                + (f", saving {f_save(m, m.usd - best['usd'])} against your actual {f_usd(m.usd)}" if m.usd - best['usd'] >= 0.01 else ', which is your actual mix')
                + f"; all 5 min would be {f_usd(c55['usd'])}, all 1 hour {f_usd(c11['usd'])}."
                + (' Break-even: ' + '; '.join(tip) + '.' if tip else '')
                + (' Last 7 days alone favour ' + '; '.join(recent) + '.' if recent else ''),
        note='Replays every call under both lifetimes. The cache clock runs from the end of the previous response (the '
             'logged hits fit that; counted from the request\'s start, some would have come after the cache expired). A longer lifetime never adds a miss and a shorter one never removes '
             'one, so only the other direction is predicted from the idle time; misses from a changed prompt, retries or '
             f'parallel requests stay misses under both. Replaying your actual lifetimes reproduces your cache cost '
             + ('exactly' if calib < 0.0005 else f'within {calib * 100:.1f}%') + '. A simulated miss rewrites the whole context '
             '(a small shared prefix could survive: under $1 here). '
             'Input, output and other costs are unchanged. Set with promptCacheTtl and subagentPromptCacheTtl (see the optimizations).')


def sv6(m, g):
    d = _sv_models(m)
    act = d['act_main'] + d['act_sub']
    rows = [dict(k=model_name(r['model']), mn=r2(r['main']), sb=r2(r['sub']), t=r2(r['main'] + r['sub']), v=r2(act - r['main'] - r['sub']),
                 vm=r2(per_month(m, act - r['main'] - r['sub']))) for r in sorted(d['rows'], key=lambda r: r['main'] + r['sub'])]
    rows.append(dict(k='Actual mix', mn=r2(d['act_main']), sb=r2(d['act_sub']), t=r2(act), v=0, vm=0))
    ins = None
    if d['main_saving'] > 0.01 or d['sub_saving'] > 0.01:
        ins = (f"At list prices, the same main-thread work on {(model_name(d['current']) if d['current'] else 'one model')} (the model you use now) would have saved "
               f"{f_save(m, d['main_saving'])}" + (f", and subagents on {model_name(d['sonnet'])} {f_save(m, d['sub_saving'])}." if d['sonnet'] else '.'))
    return card('SV6', 'What if another model had done the same work?', 'T P A', [
        K(kpi_save(m, f"Main threads on {(model_name(d['current']) if d['current'] else 'one model')}", d['main_saving'], 'your current model; pricier calls only'),
          kpi_save(m, f"Subagents on {model_name(d['sonnet'])}", d['sub_saving'], 'pricier calls only') if d['sonnet'] else None,
          kpi_save(m, f"Explore subagents on {model_name(d['haiku'])}", max(0.0, d['explore'] - d['explore_haiku']) if d['explore_haiku'] is not None else None,
                   f"of {f_usd(d['explore'])} they cost") if d['haiku'] else None),
        BAR([r['k'] for r in rows], [S('Main threads', [r['mn'] for r in rows], 1), S('Subagents', [r['sb'] for r in rows], 2)], 'usd',
            stacked=True, title='The same tokens at each model’s list prices'),
        TABLE([('k', 'Priced as', None), ('mn', 'Main threads', 'usd'), ('sb', 'Subagents', 'usd'), ('t', 'Total', 'usd'), ('v', 'Saved, all time', 'usd'),
               ('vm', 'Per 30 days', 'usd')], rows)],
        why='Model choice multiplies everything else. This shows the price side only.',
        insight=ins,
        note='Same token counts at each model’s list price. A different model would use a different number of tokens and may do '
             'the work better or worse; treat this as the price ceiling of switching, not a forecast.')


def sv7(m, g):
    f = _sv_fresh(m)
    if not f['rows']:
        return card('SV7', 'What would starting a fresh session after long breaks have saved?', 'T P S', [],
                    empty='You never came back to a big session after its cache expired.')
    rows = [dict(t=local(r['c']['start']).strftime('%m-%d %H:%M'), s=sess_label(m, r['c']['sid'], 44), x=r['c']['prev']['ctx'],
                 d=r['d'], n=r['n'], u=r2(r['usd'])) for r in sorted(f['rows'], key=lambda r: -r['usd'])]
    return card('SV7', 'What would starting a fresh session after long breaks have saved?', 'T P S', [
        K(kpi_save(m, 'Saved by starting fresh', f['usd'], f"{len(f['rows'])} returns after the cache expired"),
          kpi('A fresh session starts at', f['base'], 'tokens', 'median start-up context')),
        TABLE([('t', 'Came back', None), ('s', 'Session', None), ('x', 'Context', 'tokens'), ('d', 'Not carried', 'tokens'),
               ('n', 'Later calls', 'count'), ('u', 'Saved on this return', 'usd')], rows, limit=10)],
        why='After the cache expires, the whole context is written again and then re-read on every later call. A fresh start carries only a summary.',
        insight=f"Starting fresh instead of returning to an expired session would have saved {f_save(m, f['usd'])}.",
        note=f"The fresh session starts at the median start-up size + {f_tok(BRIEF_TOK)} summary + {f_tok(REREAD_TOK)} re-read detail; "
             'every later call until the next break or compaction reads that much less. An upper bound when the old context was still useful.')


def sv8(m, g):
    r = _sv_reads(m)
    if not r['items']:
        return card('SV8', 'What would reading large files in ranges have saved?', 'T P S', [], empty='No large whole-file reads.')
    top = sorted(r['items'], key=lambda it: -it['carry_usd'])[:12]
    return card('SV8', 'What would reading large files in ranges have saved?', 'T P S', [
        K(kpi('Whole-file reads over ' + f_tok(BIG_READ), len(r['items']), 'count', f"{f_tok(r['tokens'])} tokens"),
          kpi('What they cost', r['stake'], 'usd', 'written once, then re-read'),
          kpi_save(m, 'Saved if read in ranges', r['usd'], f'if a range kept {int(RANGE_KEEP * 100)}%')),
        TABLE([('d', 'File', None), ('tok', 'Size', 'tokens'), ('n', 'Re-reads', 'count'), ('u', 'Cost', 'usd')],
              [dict(d=clip(it['detail'], 70), tok=round(it['tokens']), n=it['reads'], u=r2(it['carry_usd'])) for it in top], 'Largest reads')],
        why='A large file read once is paid for again on every later call of the thread.',
        insight=f"Reading large files in ranges could have saved about {f_save(m, r['usd'])}.",
        note='Counts Read calls without offset/limit whose result was at least ' + f_tok(BIG_READ) + ' tokens. The saving assumes a targeted read '
             f'(or a grep first) would have loaded {int(RANGE_KEEP * 100)}% of the file.')


# ------------------------------------------------------------------------------------------ CX: context & caching
# CX1–CX5 are about context, CX6–CX12 about caching (CX7–CX12 are defined with the caching helpers further up).

def cx1(m, g):
    peaks = [s['peak'] for s in m.sessions.values() if s['peak']]
    ctxs = [c['ctx'] for c in m.real]
    m.facts['peak_med'] = median(peaks)
    return card('CX1', 'How big does my context get?', 'T P S', [
        K(kpi('Peak per session (median)', median(peaks), 'tokens'), kpi('Peak (90th pct)', pctl(peaks, 90), 'tokens'),
          kpi('Largest ever', max(peaks) if peaks else None, 'tokens'),
          kpi('Calls above 100K', share(sum(1 for x in ctxs if x > 1e5), len(ctxs)), 'pct'),
          kpi('Calls above 200K', share(sum(1 for x in ctxs if x > 2e5), len(ctxs)), 'pct')),
        BAR(CTX_LABELS, [S('API calls', bucketize(ctxs, CTX_EDGES, CTX_LABELS))], 'count', orient='v', title='API calls by context size')])


def cx2(m, g):
    sess = sorted((s for s in m.sessions.values() if len(s['main_calls']) > 5), key=lambda s: -s['peak'])[:4]
    n = max((len(s['main_calls']) for s in sess), default=0)
    series = [S(clip(s['label'], 36), [c['ctx'] for c in s['main_calls']] + [None] * (n - len(s['main_calls'])), i + 1)
              for i, s in enumerate(sess)]
    return card('CX2', 'How fast does context grow in my biggest sessions?', 'S', [
        LINE(list(range(1, n + 1)), series, 'tokens', x_kind='num', title='Context per call, four largest sessions (main thread)')])


def _by_source(m, key):
    agg = collections.defaultdict(float)
    for it in m.items:
        agg[it['source']] += it[key]
    return agg


def short_source(s):
    """A context source as a short axis label."""
    if s.startswith('Start-up baseline'):
        return 'Start-up baseline'
    if s == "Claude's own previous output":
        return "Claude's own output"
    if s.startswith('Tool result: '):
        return s[len('Tool result: '):] + ' results'
    return s


def cx3(m, g):
    add, carry, usd = _by_source(m, 'tokens'), _by_source(m, 'carry'), _by_source(m, 'carry_usd')
    cats, (tok, cost) = _top_other(sorted(add, key=lambda k: -add[k]), 11, add, usd, name=short_source)
    chart = BAR(cats, [S('Tokens added (left axis)', [round(v) for v in tok], 1),
                       S('Re-read cost (right axis)', [r2(v) for v in cost], 2, axis=2, unit='usd')], 'tokens', orient='v',
                title='Tokens added and what re-reading them cost, by source', labels=False, wrap_labels=True)
    items = sorted((it for it in m.items if not it['source'].startswith(('Start-up', "Claude's"))), key=lambda it: -it['carry'])
    ctl = [k for k in sorted(carry, key=lambda k: -carry[k]) if not k.startswith(('Start-up', "Claude's"))]
    ins = None
    if items:
        it = items[0]
        m.facts.update(top_item=it, top_ctl=ctl[0] if ctl else None, top_ctl_share=share(carry[ctl[0]], sum(carry.values())) if ctl else None)
        ins = (f'Costliest single item: {clip(it["detail"], 90)} — {f_tok(it["tokens"])} tokens re-read {it["reads"]} times '
               f'({f_tok(it["carry"])} tokens, {f_usd(it["carry_usd"])}).')
    return card('CX3', 'What is my context made of, and what does each part cost?', 'T P S', [
        K(kpi('Tokens added to context', sum(add.values()), 'tokens', 'each counted once, when it arrived'),
          kpi('Re-read tokens', sum(carry.values()), 'tokens', 'Σ size × later calls'), kpi('Re-read cost', sum(usd.values()), 'usd')),
        chart],
        why='A big early tool output is paid for again on every later call; trimming it is the most direct saving.',
        insight=ins,
        note='Each call’s context growth (minus Claude’s previous output) is split across the items that arrived since the previous call, '
             'by their size; first calls split their context into the logged items plus the start-up baseline. Re-read cost = size × '
             'later calls × the cache-read price. The re-read total matches the logged cache-read tokens within a few percent.')


def _clears(m):
    if 'clears' in m.cache:
        return m.cache['clears']
    out = []
    for c in m.commands:
        if c['name'] != 'clear' or c['side']:
            continue
        s = m.sessions.get(c['sid'])
        if not s:
            continue
        cands = []
        b = s.get('bridge')
        for sid2, s2 in m.sessions.items():
            if sid2 == c['sid'] or s2['end'] > c['t'] + 1 or not s2['main_calls']:
                continue
            if b and s2.get('bridge') == b:
                cands.append((s2['end'], sid2, 'chain'))
            elif not b and s2['proj'] == s['proj'] and c['t'] - s2['end'] < 7200:
                cands.append((s2['end'], sid2, 'time'))
        if cands:
            _, sid2, how = max(cands)
            p = m.sessions[sid2]
            out.append(dict(t=c['t'], prev=p, ctx=p['main_calls'][-1]['ctx'], how=how))
    m.cache['clears'] = out
    return out


def cx4(m, g):
    cl = _clears(m)
    ctx = [x['ctx'] for x in cl]
    m.facts['clear_med'] = median(ctx)
    return card('CX4', 'How big is my context when I /clear?', 'T P', [
        K(kpi('/clear commands linked', len(cl), 'count', 'to the session they cleared'),
          kpi('Median context at /clear', median(ctx), 'tokens')),
        BAR(CTX_LABELS, [S('Clears', bucketize(ctx, CTX_EDGES, CTX_LABELS))], 'count', orient='v', title='Context size when you cleared')],
        note='Each /clear starts a new session; the one it cleared is found through the shared bridgeSessionId (same terminal), else the latest session in the same project.',
        insight=f'You /clear at a median context of {f_tok(median(ctx))} tokens.' if cl else None,
        empty=None if cl else 'No /clear commands in this scope.')


# What a new session carries before any work: /context's categories, in plain words. (label, what it is, how to make it smaller)
START_PARTS = {
    'Built-in tools': ('The definitions of the tools Claude can call (Bash, Read, Edit, Agent…), sent with every request.',
                       'Set by Claude Code and the features you have on.'),
    'Claude Code system prompt': ("Claude Code's own instructions.", 'Fixed by Claude Code.'),
    'MCP tools': ('Tools from MCP servers that load up front.', 'Disconnect servers you don’t use (/mcp).'),
    'Memory files': ('Your CLAUDE.md files and the auto-memory index (MEMORY.md), loaded into every session.',
                     'Keep them short; move detail into files Claude reads when needed.'),
    'Skills list': ('One line per skill, so Claude knows what it can invoke.', 'Turn off skills you never use (skillOverrides; see SV2).'),
    'Agent types list': ('One line per subagent type.', 'Remove agent definitions you never use.'),
    'MCP server instructions': ('Usage notes each connected MCP server adds.', 'Disconnect servers you don’t use (/mcp).'),
    'Deferred tools list': ('Names of tools that load only when needed (ToolSearch), mostly from MCP servers.',
                            'Shrinks with fewer MCP servers.'),
    'Hook output': ('Text your hooks add at session start or on the first prompt.', 'Trim what your hooks print.'),
    'Session reminders': ('Date, model, environment and similar notes.', 'Fixed by Claude Code.'),
    'Your first message': ('The prompt or command that started the session.', '–'),
    'Other notices': ('Mode notices and other small attachments.', '–'),
    'Other (not logged)': ('The rest of the first request: parts the transcript doesn’t itemise.', '–'),
    'System prompt, tools & other (not logged)': ('Claude Code’s instructions and tool definitions, which this Claude Code version '
                                                  'doesn’t log (newer versions do).', 'Mostly fixed by Claude Code.'),
}
START_GROUP = {
    'Skill listing': 'Skills list', 'Agent listing': 'Agent types list', 'MCP instructions': 'MCP server instructions',
    'Deferred-tool list': 'Deferred tools list', 'Hook context': 'Hook output', 'Reminders & session setup': 'Session reminders',
    'Your prompts (text)': 'Your first message', 'Your prompts (images)': 'Your first message',
    'Commands & skill prompts': 'Your first message', 'SDK prompts': 'Your first message', 'Task notifications': 'Your first message',
    'Queued prompts & notifications': 'Your first message', 'Subagent task prompt': 'Your first message',
}


def memory_files(src, cwd, proj):
    """The memory files a session in `cwd` loads at start, as they are on disk now: (path, tokens)."""
    out, seen = [], set()

    def add(path, max_lines=None):
        rp = os.path.realpath(path)
        if rp in seen or not os.path.isfile(rp):
            return
        seen.add(rp)
        try:
            with open(rp, encoding='utf-8', errors='replace') as fh:
                text = ''.join(fh.readlines()[:max_lines]) if max_lines else fh.read()
        except OSError:
            return
        out.append((tilde(path), len(text) / 4))
    add(os.path.join(src.home, 'CLAUDE.md'))
    for p in sorted(glob.glob(os.path.join(src.home, 'rules', '*.md'))):
        add(p)
    d = cwd
    while d and os.path.dirname(d) != d:                  # the working folder and each parent up to the root
        for name in ('CLAUDE.md', 'CLAUDE.local.md', os.path.join('.claude', 'CLAUDE.md')):
            add(os.path.join(d, name))
        if d == cwd:
            for p in sorted(glob.glob(os.path.join(d, '.claude', 'rules', '*.md'))):
                add(p)
        d = os.path.dirname(d)
    add(os.path.join(src.dir, proj, 'memory', 'MEMORY.md'), max_lines=200)     # the auto-memory index (first 200 lines)
    return out


def cx5(m, g):
    q = 'What does a new session start with right now?'
    pool = [(sid, s) for sid, s in m.sessions.items() if s['main_calls']]
    pool = [x for x in pool if x[1]['kind'] != 'Automated (SDK)'] or pool
    if not pool:
        return card('CX5', q, 'T P', [], empty='No sessions in this scope.')
    sid, s = max(pool, key=lambda x: x[1]['start'])
    c = s['main_calls'][0]
    parts = collections.defaultdict(float)
    rest = 0.0
    for it in m.items:
        if it['thread'] == c['thread'] and it['k'] == 0:
            if it['source'].startswith('Start-up baseline'):
                rest += it['tokens']
            else:
                parts[START_GROUP.get(it['source'], 'Other notices')] += it['tokens']
    snaps = getattr(g, 'snaps', None) or [dict(a, ver=s['versions']) for a in m.atts if a['type'] == 'prompt_snapshot']
    mine = [a for a in snaps if a['sid'] == sid]
    if not mine:                                     # another session on the same Claude Code version sends the same prompt and tools
        same = [a for a in snaps if a['ver'] & s['versions'] and isinstance(a['a'].get('tools'), list) and a['a']['tools']]
        near = min(same, key=lambda a: abs((a['t'] or 0) - c['t0']), default=None)
        mine = [a for a in same if near and a['sid'] == near['sid']]
    sp = max((str_len(a['a'].get('systemPrompt')) for a in mine), default=0) / 4
    tools = next((a['a'].get('tools') for a in mine if isinstance(a['a'].get('tools'), list) and a['a'].get('tools')), [])
    tool_tok = [(t.get('name') or '?', len(json.dumps(t, ensure_ascii=False)) / 4) for t in tools if isinstance(t, dict)]
    mem = memory_files(g.src, s.get('cwd_first') or s['cwd_main'], s['proj'])
    est = {'Claude Code system prompt': sp, 'Built-in tools': sum(v for k, v in tool_tok if not k.startswith('mcp__')),
           'MCP tools': sum(v for k, v in tool_tok if k.startswith('mcp__')), 'Memory files': sum(v for _, v in mem)}
    scale = min(1.0, rest / sum(est.values())) if sum(est.values()) else 1.0       # estimates never exceed what was measured
    for k, v in est.items():
        parts[k] += v * scale
    other = 'Other (not logged)' if mine else 'System prompt, tools & other (not logged)'
    parts[other] += max(0.0, rest - sum(est.values()) * scale)
    keys = [k for k in sorted(parts, key=lambda k: -parts[k]) if parts[k] >= 1]
    ctx = c['ctx']
    mdl = c['model']
    fam = next((f for f in ('opus', 'sonnet', 'haiku', 'fable') if f in mdl), None)
    big = any(x['ctx'] > 200_000 for x in m.real if x['model'] == mdl) or (
        '[1m]' in str(g.src.settings.get('model') or '') and fam and fam in str(g.src.settings.get('model')))
    window = 1_000_000 if big else 200_000
    wlab = '1M' if big else '200K'
    top_tool = max(tool_tok, key=lambda x: x[1]) if tool_tok else None
    rows = [dict(k=k, t=round(parts[k]), s=r1(share(parts[k], ctx)), w=START_PARTS[k][0],
                 h=START_PARTS[k][1] + (f' The biggest is {top_tool[0]} ({f_tok(top_tool[1] * scale)}).'
                                        if k == 'Built-in tools' and top_tool else '')) for k in keys]
    top = keys[0] if keys else None
    of200 = f'{f_pct(share(ctx, 200_000))} of a 200K window' if big else None       # on a 1M model, how much of a standard window it would fill
    ins = (f'A new session starts with {f_tok(ctx)} tokens ({f_pct(share(ctx, window))} of the {wlab} window'
           + (f', {f_pct(share(ctx, 200_000))} of a 200K one' if big else '') + '). '
           f'The biggest part is {top.lower() if top else "–"} ({f_tok(parts[top]) if top else "–"})'
           + (f', led by the {top_tool[0]} tool ({f_tok(top_tool[1] * scale)}).' if top == 'Built-in tools' and top_tool else '.'))
    m.facts.update(start_ctx=ctx, start_top=top)
    lt = local(c['t0'])
    when = f"{lt:%b} {lt.day} {lt:%H:%M}"
    pcats, (pvals,) = _top_other(keys, 8, parts, other='Smaller parts')       # at most 8 slices (one colour each), the rest grouped
    return card('CX5', q, 'T P', [
        K(kpi('A new session starts with', ctx, 'tokens', f'{model_name(mdl)} · {when}'),
          kpi('Of the context window', share(ctx, window), 'pct',
              f'{wlab} window · {f_tok(window - ctx)} free' + (f'\n{of200}' if of200 else '')),
          kpi('Cost to load it', ctx * g.prices.per_token(mdl, 'cw1h' if c.get('ttl', 3600) >= 3600 else 'cw5m', c.get('pm', 1.0)),
              'usd', 'written to the cache once'),
          kpi('Carried per 100 calls', ctx * g.prices.per_token(mdl, 'cr', c.get('pm', 1.0)) * 100, 'usd', 're-read on every later call')),
        PIE(pcats, [round(v) for v in pvals], 'tokens',
            title='What the first request of your latest session carried', center='tokens', total=ctx, width='half',
            slots=list(range(1, len(pcats) + 1))),
        TABLE([('k', 'Part', None), ('t', 'Tokens', 'tokens'), ('s', 'Share', 'pct'), ('w', 'What it is', None),
               ('h', 'How to make it smaller', None)], rows, 'In plain words', width='half'),
        TABS([('Tools by size', TABLE([('n', 'Tool', None), ('t', 'Tokens', 'tokens')],
                                      [dict(n=k, t=round(v * scale)) for k, v in sorted(tool_tok, key=lambda x: -x[1])])),
              ('Memory files', TABLE([('f', 'File', None), ('t', 'Tokens', 'tokens')],
                                     [dict(f=p, t=round(v * scale)) for p, v in mem]))], title='Details')],
        why='This is paid on every session before you type anything, and re-read on every call after.',
        insight=ins,
        note=f'From the first request of your latest session in this scope ({clip(s["label"], 50)}, {when}). Logged parts are measured. '
             'The system prompt and tool definitions come from the prompt snapshot Claude Code logs, and memory files are read from disk '
             'now; those are estimated at about 4 characters per token and scaled so all parts add up to the measured request. '
             + ('' if any(a['sid'] == sid for a in mine) else
                'This session logged no prompt snapshot, so one from another session on the same Claude Code version is used. ' if mine else
                'No prompt snapshot exists for this Claude Code version, so the system prompt and tools are not split out. ')
             + f'Window: {wlab}' + (' (calls on this model went past 200K, or settings ask for [1m])' if big else '') + '.')


def cx6(m, g):
    cr = [0] * len(CTX_LABELS)
    usd = [0.0] * len(CTX_LABELS)
    for c in m.real:
        i = min(bisect.bisect_right(CTX_EDGES, c['ctx']), len(CTX_LABELS) - 1)
        cr[i] += c['cr']
        usd[i] += c['cost'].get('cache_read', 0)
    tot = sum(cr)
    big = sum(v for v, lo in zip(cr, [0] + CTX_EDGES) if lo >= 1e5)
    big2 = sum(v for v, lo in zip(cr, [0] + CTX_EDGES) if lo >= 2e5)
    m.facts['cr_big'] = share(big, tot)
    return card('CX6', 'How much of my cache-read spend comes from big contexts?', 'T P', [
        K(kpi('From calls above 100K', share(big, tot), 'pct'), kpi('From calls above 200K', share(big2, tot), 'pct'),
          kpi('Cache-read cost', sum(usd), 'usd')),
        BAR(CTX_LABELS, [S('Cache-read cost', [r2(v) for v in usd])], 'usd', orient='v', title='Cache-read cost by context size of the call')],
        why='The price of long sessions: every extra call re-reads everything so far.')


# ------------------------------------------------------------------------------------------ SE: sessions (SE5–SE6, subagents, are further down)

def se1(m, g):
    sess = [s for s in m.sessions.values() if s['n_calls']]
    return card('SE1', 'How long are my sessions?', 'T P', [
        K(kpi('Median wall time', median([s['wall'] for s in sess]), 'sec'),
          kpi('Median active time', median([s['active'] for s in sess]), 'sec'),
          kpi('Median prompts', median([s['prompts'] for s in sess]), 'count'),
          kpi('Median API calls', median([s['n_calls'] for s in sess]), 'count')),
        BAR(DUR_LABELS, [S('Wall time', bucketize([s['wall'] for s in sess], DUR_EDGES, DUR_LABELS), 1),
                         S('Active time', bucketize([s['active'] for s in sess], DUR_EDGES, DUR_LABELS), 2)], 'count',
            orient='v', title='Sessions by length')],
        note='Sessions that made at least one API call. Active time ignores gaps over 5 minutes.')


def se2(m, g):
    pt = [t for t in m.turns if t['kind'] == 'prompt']
    calls = [t['n_main'] for t in pt]
    busy = [d.get('durationMs', 0) / 1000 for d in m.sys.get('turn_duration', [])]
    m.facts['calls_per_prompt'] = median(calls)
    edges, labels = [0.5, 5.5, 15.5, 30.5, 60.5, 120.5], ['0', '1–5', '6–15', '16–30', '31–60', '61–120', '>120']
    return card('SE2', 'How much work does one prompt set off?', 'T P S', [
        K(kpi('API calls per prompt', median(calls), 'count', f'90th pct {f_int(pctl(calls, 90))}, max {f_int(max(calls) if calls else None)}'),
          kpi('Tool calls per prompt', median([t['n_tools'] for t in pt]), 'count'),
          kpi('Turn time (median)', median(busy), 'sec', f'90th pct {f_dur(pctl(busy, 90))}'),
          kpi('Longest turn', max(busy) if busy else None, 'sec')),
        BAR(labels, [S('Prompts', bucketize(calls, edges, labels))], 'count', orient='v', title='Main-thread API calls per prompt')],
        why='Measures how much Claude does on its own per prompt, and how long you wait.')


def se3(m, g):
    turns = sorted((t for t in m.turns if t['kind'] in ('prompt', 'command') and t['usd'] > 0), key=lambda t: -t['usd'])[:20]
    rows = [dict(u=r2(t['usd']), c=t['n_main'], sa=t['subagents'], b=t['busy_ms'] / 1000 if t['busy_ms'] else None,
                 d=local(t['start']).strftime('%m-%d %H:%M'), txt=clip(t['text'], 110), sid=t['sid']) for t in turns]
    if turns:
        m.facts['top_turn'] = turns[0]
    return card('SE3', 'Which of my prompts were most expensive, and what did they ask?', 'T P', [
        TABLE([('u', 'Cost', 'usd'), ('c', 'Calls', 'count'), ('sa', 'Subagents', 'count'), ('b', 'Claude busy', 'sec'),
               ('d', 'When', None), ('txt', 'Prompt', None), ('sid', 'Session ID', None, {'code': True})], rows, limit=10)],
        note='A turn runs from your prompt (or slash command) to the next one and includes the subagents it launched. '
             'Pick a session up again with claude --resume <session ID>.')


def se4(m, g):
    iv = []
    for s in m.sessions.values():
        ts_ = sorted(s['times'])
        if not ts_:
            continue
        a = p = ts_[0]
        for x in ts_[1:]:
            if x - p > IDLE_CAP:
                iv.append((a, p))
                a = x
            p = x
        iv.append((a, p))
    pts = sorted([(a, 1) for a, b in iv] + [(b, -1) for a, b in iv])
    cur = peak = 0
    tim = collections.Counter()
    last = None
    for x, dlt in pts:
        if last is not None and cur > 0:
            tim[min(cur, 4)] += x - last
        cur += dlt
        peak = max(peak, cur)
        last = x
    tot = sum(tim.values())
    labels = ['1 session', '2 sessions', '3 sessions', '4+ sessions']
    return card('SE4', 'How often do I run sessions in parallel?', 'T W', [
        K(kpi('Most at once', peak, 'count'), kpi('Active time with ≥2 sessions', share(sum(v for k, v in tim.items() if k >= 2), tot), 'pct')),
        BAR(labels, [S('Hours', [r2(tim.get(i + 1, 0) / 3600) for i in range(4)])], 'hours', orient='v', title='Active hours by number of sessions running')])

# ------------------------------------------------------------------------------------------ EX6–EX15: tools (EX1–EX5 are further down)

def ex6(m, g):
    cnt = collections.Counter(tool_group(t['name']) for t in m.tools)
    tool_tok, _ = _out_alloc(m)
    inp = collections.Counter()
    for t in m.tools:
        inp[tool_group(t['name'])] += tool_tok.get(t['id'], 0)
    added = collections.Counter()
    for it in m.items:
        if it['source'].startswith('Tool result: '):
            added[it['source'][len('Tool result: '):]] += it['tokens']
    keys = sorted(cnt, key=lambda k: -cnt[k])
    cats, (calls, toks) = _top_other(keys, 12, cnt, inp)
    chart = BAR(cats, [S('Tool calls (left axis)', calls, 1),
                       S('Output tokens for their inputs (right axis)', [round(v) for v in toks], 2, axis=2, unit='tokens')],
                'count', orient='v', title='Tool calls, and the output tokens Claude wrote as their inputs, by tool', labels=False,
                wrap_labels=True)
    c2, (v2,) = _top_other(sorted(added, key=lambda k: -added[k]), 10, added)
    pt = [t['n_tools'] for t in m.turns if t['kind'] == 'prompt']
    first = keys[0] if keys else None
    out_tok = sum(inp.values())
    return card('EX6', 'Which tools does Claude use, how often, and what do they cost in tokens?', 'T P S', [
        K(kpi('Tool calls', len(m.tools), 'count'), kpi('Per prompt (median)', median(pt), 'count'),
          kpi('Distinct tools', len(cnt), 'count'),
          kpi(f'{first} share' if first else 'Top tool', share(cnt[first], len(m.tools)) if first else None, 'pct'),
          kpi('Output tokens spent on tool inputs', out_tok, 'tokens', f"{f_pct(share(out_tok, sum(c['out'] for c in m.real)))} of all output"),
          kpi('Context added by tool results', sum(added.values()), 'tokens')),
        chart,
        hide(BAR(c2, [S('Tokens', [round(v) for v in v2])], 'tokens', title='Tool results added to context'))])


def _out_alloc(m):
    """Split each call's non-thinking output tokens between its text and its tool inputs by character share."""
    if 'out_alloc' not in m.cache:
        tool_tok, text_tok = {}, 0.0
        for c in m.real:
            rest = max(0, c['out'] - c['think'])
            ws = [(b.get('id'), len(json.dumps(b.get('input') or {}, ensure_ascii=False))) for b, _ in c['tools']]
            W = c['text_chars'] + sum(w for _, w in ws)
            if W <= 0:
                text_tok += rest
                continue
            text_tok += rest * c['text_chars'] / W
            for tid, w in ws:
                tool_tok[tid] = rest * w / W
        m.cache['out_alloc'] = (tool_tok, text_tok)
    return m.cache['out_alloc']


def ex7(m, g):
    tot, err = collections.Counter(), collections.Counter()
    msgs = collections.Counter()
    after_err = set()
    for t in m.tools:
        k = tool_group(t['name'])
        tot[k] += 1
        r = t['res']
        if r and r['is_error']:
            err[k] += 1
            first = next((ln for ln in r['text'].splitlines() if ln.strip() and not ln.startswith('<')), r['text'][:80])
            msgs[(k, re.sub(r'\d+', 'N', clip(first.replace('<tool_use_error>', ''), 90)))] += 1
            c = t['call']
            nxt = m.threads[c['thread']][c['idx'] + 1] if c['idx'] + 1 < len(m.threads[c['thread']]) else None
            if nxt:
                after_err.add(nxt['key'])
    rec_usd = sum(c['usd'] for c in m.real if c['key'] in after_err)
    m.facts.update(err_rate=share(sum(err.values()), len(m.tools)), recovery_usd=rec_usd)
    ek = sorted(err, key=lambda k: -err[k])
    rows = [dict(k=k, n=tot[k], e=err[k], r=r1(share(err[k], tot[k]))) for k in ek]
    mrows = [dict(k=k, msg=msg, n=n) for (k, msg), n in msgs.most_common(15)]
    pcats, (pvals,) = _top_other(ek, 8, err)                                   # at most 8 slices (one colour each), the rest grouped
    return card('EX7', 'What fails, how often, and what did recovery take?', 'T P', [
        K(kpi('Failed tool calls', sum(err.values()), 'count', f_pct(share(sum(err.values()), len(m.tools))) + ' of all'),
          kpi('Recovery calls', len(after_err), 'count', 'the call right after a failure'),
          kpi('Recovery cost', rec_usd, 'usd')),
        PIE(pcats, pvals, 'count', title='Failed tool calls by tool', center='failures', width='half', slots=list(range(1, len(pcats) + 1))),
        TABLE([('k', 'Tool', None), ('msg', 'Error (first line)', None), ('n', 'Times', 'count')], mrows, 'Most common errors', 8, width='half'),
        hide(TABLE([('k', 'Tool', None), ('n', 'Calls', 'count'), ('e', 'Errors', 'count'), ('r', 'Error rate', 'pct')], rows, 'Error rate by tool'))],
        note='Plan rejections and permission denials count as errors of the tool that was refused.')


def _read_span(inp):
    """The lines a Read covers: (first, last), last None for the rest of the file."""
    try:
        lo = max(1, int(inp.get('offset') or 1))
        n = int(inp['limit']) if inp.get('limit') else None
    except (TypeError, ValueError):
        return 1, None
    return lo, (lo + n - 1 if n else None)


def _covered(span, spans):
    """Whether every line of span was in one of the earlier reads (spans)."""
    lo, hi = span
    for a, b in sorted(spans, key=lambda x: x[0]):
        if a > lo:
            return False
        if b is None:
            return True
        if b >= lo:
            lo = b + 1
            if hi is not None and lo > hi:
                return True
    return False


def _read_seq(m):
    """Reads of lines Claude already had: the same thread read them since the last edit of the file. Reading a big file
    range by range (what big_read_guard asks for) is not a re-read."""
    if 'reads' in m.cache:
        return m.cache['reads']
    seq = collections.defaultdict(list)
    for t in m.tools:
        if t['name'] in ('Read', 'Edit', 'Write', 'MultiEdit', 'NotebookEdit'):
            fp = t['input'].get('file_path') or t['input'].get('notebook_path')
            if fp:
                seq[(t['thread'], fp)].append(t)
    rep = set()
    stats = collections.defaultdict(lambda: dict(reads=0, rep=0, sess={}, lines=None))    # sess: a dict, ordered (EX8 uses the first)
    for (th, fp), ts_ in seq.items():
        spans, pages = [], set()                        # what this thread has read of the file since its last edit
        for t in ts_:
            if t['name'] == 'Read':
                st = stats[fp]
                st['reads'] += 1
                st['sess'][th[0]] = True
                f = ((t['res'] or {}).get('tur') or {}).get('file')
                if isinstance(f, dict) and f.get('totalLines') is not None:
                    st['lines'] = f['totalLines']
                pg = str(t['input']['pages']) if t['input'].get('pages') else None
                span = _read_span(t['input'])
                if (pg in pages) if pg else _covered(span, spans):
                    st['rep'] += 1
                    rep.add(t['id'])
                if pg:
                    pages.add(pg)
                else:
                    spans.append(span)
            else:
                spans, pages = [], set()
    m.cache['reads'] = (rep, stats)
    return rep, stats


def ex8(m, g):
    rep, stats = _read_seq(m)
    reads = sum(1 for t in m.tools if t['name'] == 'Read')
    rows = [dict(f=short_path(fp, m.sessions.get(next(iter(v['sess'])), {}).get('cwd_main')), r=v['reads'], rep=v['rep'], s=len(v['sess']),
                 ln=v['lines']) for fp, v in sorted(stats.items(), key=lambda kv: (-kv[1]['reads'], -kv[1]['rep']))[:30]]
    red_tok = sum(it['tokens'] for it in m.items if it['tu'] and it['tu']['id'] in rep)
    red_usd = sum(it['carry_usd'] for it in m.items if it['tu'] and it['tu']['id'] in rep)
    m.facts.update(reread_share=share(len(rep), reads), reread_usd=red_usd)
    return card('EX8', 'Which files does Claude read most, and how often does it re-read one it already has?', 'T P S', [
        K(kpi('File reads', reads, 'count'), kpi('Re-reads with no edit in between', share(len(rep), reads), 'pct', f'{len(rep)} reads'),
          kpi('Context they added', red_tok, 'tokens', f'{f_usd(red_usd)} in re-reads')),
        TABLE([('f', 'File', None), ('r', 'Reads', 'count'), ('rep', 'Repeat reads (no edit between)', 'count'), ('s', 'Sessions', 'count'),
               ('ln', 'Lines', 'count')], rows, 'Files read most', limit=12)],
        why='Where Claude spends its attention. Files it keeps re-reading are candidates for CLAUDE.md notes.',
        note='A repeat read is the same file read again in the same thread with no Edit/Write to it in between.',
        insight=f'{f_pct(share(len(rep), reads))} of file reads re-read a file already in context with no edit in between.' if rep else None)


def ex9(m, g):
    first_edit = {}
    for t in m.tools:
        if t['name'] in EDIT_TOOLS and t['t'] and (t['sid'] not in first_edit or t['t'] < first_edit[t['sid']]):
            first_edit[t['sid']] = t['t']
    shares = []
    for s in m.sessions.values():
        first = first_edit.get(s['sid']) if s['n_calls'] else None
        if not first or not s['usd']:
            continue
        shares.append(100 * sum(c['usd'] for c in s['calls'] if c['t0'] < first) / s['usd'])
    edges = [10, 20, 30, 40, 50, 60, 70, 80, 90]
    labels = ['0–10%', '10–20%', '20–30%', '30–40%', '40–50%', '50–60%', '60–70%', '70–80%', '80–90%', '90–100%']
    m.facts['explore_first'] = median(shares)
    return card('EX9', 'How much does Claude spend exploring before its first edit?', 'T P S', [
        K(kpi('Median share before first edit', median(shares), 'pct', f'{len(shares)} sessions with edits'),
          kpi('90th percentile', pctl(shares, 90), 'pct')),
        BAR(labels, [S('Sessions', bucketize(shares, edges, labels))], 'count', orient='v', title="Share of a session's cost spent before its first edit")],
        why='High numbers suggest CLAUDE.md, memory or the task spec could say more up front.',
        insight=f'Claude spends a median {f_pct(median(shares))} of a session’s cost exploring before its first edit.' if shares else None,
        empty=None if shares else 'No sessions with edits in this scope.')


# EX10–EX15 are computed for metrics.json and the digest, but not shown in the report (HIDDEN_CARDS).

def ex10(m, g):
    whole = ranged = big = trunc = 0
    for t in m.tools:
        f = ((t['res'] or {}).get('tur') or {}).get('file')
        if t['name'] == 'Read' and isinstance(f, dict) and 'numLines' in f:
            if f.get('numLines') == f.get('totalLines') and not (t['input'].get('offset') or t['input'].get('limit')):
                whole += 1
                big += (f.get('totalLines') or 0) > 800
            else:
                ranged += 1
            trunc += bool(f.get('truncatedByTokenCap'))
    persisted = sum(1 for t in m.tools if ((t['res'] or {}).get('tur') or {}).get('persistedOutputPath'))
    items = sorted((it for it in m.items if it['tu']), key=lambda it: -it['tokens'])[:20]
    rows = [dict(tool=it['tu']['name'], d=clip(it['detail'].split(': ', 1)[-1], 70), s=sess_label(m, it['sid'], 30),
                 tok=round(it['tokens']), r=it['reads'], u=r2(it['carry_usd'])) for it in items]
    return card('EX10', 'Which tool calls return the biggest outputs?', 'T P S', [
        K(kpi('Whole-file reads', whole, 'count', f'{big} of files over 800 lines'), kpi('Ranged reads', ranged, 'count', 'offset/limit or partial'),
          kpi('Reads cut by token cap', trunc, 'count'), kpi('Outputs saved to disk', persisted, 'count', 'too large to inline')),
        TABLE([('tool', 'Tool', None), ('d', 'Detail', None), ('s', 'Session', None), ('tok', 'Size', 'tokens'),
               ('r', 'Re-reads', 'count'), ('u', 'Re-read cost', 'usd')], rows, 'Largest single tool results', 10)])


def _tool_key(t):
    return f"Bash: {t['bash']}" if t['name'] == 'Bash' else tool_group(t['name'])


def ex11(m, g):
    lat = collections.defaultdict(list)
    for t in m.tools:
        if t['latency'] is not None and t['name'] not in WAITING_TOOLS:
            lat[_tool_key(t)].append(t['latency'])
    keys = sorted(lat, key=lambda k: -sum(lat[k]))
    rows = [dict(k=k, n=len(lat[k]), tot=sum(lat[k]), p50=median(lat[k]), p90=pctl(lat[k], 90), mx=max(lat[k])) for k in keys]
    top = keys[:10]
    return card('EX11', 'Which tools are slowest, and how much time did each take?', 'T P', [
        BAR(top, [S('Minutes', [r1(sum(lat[k]) / 60) for k in top])], 'min', title='Total wall time by tool (Bash split by command type)'),
        TABLE([('k', 'Tool', None), ('n', 'Runs', 'count'), ('tot', 'Total', 'sec'), ('p50', 'Median', 'sec'),
               ('p90', '90th pct', 'sec'), ('mx', 'Max', 'sec')], rows, limit=12)],
        note='AskUserQuestion and plan approvals are excluded — their latency is your reply time (see ME3).')


def ex12(m, g):
    n = collections.Counter(min(len(c['tools']), 5) for c in m.real if c['tools'])
    labels = ['1 tool', '2 tools', '3 tools', '4 tools', '5+ tools']
    tot = sum(n.values())
    m.facts['single_tool'] = share(n[1], tot)
    return card('EX12', 'How often does Claude batch several tool calls in one round-trip?', 'T P', [
        K(kpi('Single-tool calls', share(n[1], tot), 'pct', 'of calls that used a tool')),
        BAR(labels, [S('API calls', [n[i + 1] for i in range(5)])], 'count', orient='v', title='Tool calls per API call')],
        why='Every extra round-trip re-reads the whole context; independent reads can go in one call.',
        insight=(f'{f_pct(share(n[1], tot))} of tool-using calls run a single tool; batching independent calls saves a full context re-read each.'
                 if tot and share(n[1], tot) >= 70 else None))


def ex13(m, g):
    b = [t for t in m.tools if t['name'] == 'Bash']
    cnt = collections.Counter(t['bash'] for t in b)
    mins = collections.defaultdict(float)
    for t in b:
        mins[t['bash']] += (t['latency'] or 0) / 60
    heads = collections.Counter()
    for t in b:
        cmd = re.sub(r'^(cd \S+\s*(&&|;)\s*)+', '', (t['input'].get('command') or '').strip())
        w = re.split(r'[\s|;&]+', cmd, maxsplit=1)[0] if cmd else ''
        if w:
            heads[os.path.basename(w)] += 1
    cats = sorted(cnt, key=lambda k: -cnt[k])
    return card('EX13', 'What does Claude run in Bash?', 'T P', [
        K(kpi('Bash calls', len(b), 'count', f_pct(share(len(b), len(m.tools))) + ' of tool calls'),
          kpi('Heredoc/sed file writes', cnt.get('write file', 0), 'count', 'file changes made through Bash')),
        BAR(cats, [S('Calls', [cnt[k] for k in cats])], 'count', title='Bash calls by command type'),
        TABLE([('k', 'Type', None), ('n', 'Calls', 'count'), ('m', 'Minutes', 'min')],
              [dict(k=k, n=cnt[k], m=r1(mins[k])) for k in cats]),
        TABLE([('w', 'First word', None), ('n', 'Calls', 'count')], [dict(w=w, n=n) for w, n in heads.most_common(15)], 'Most common commands', 8)])


def ex14(m, g):
    cnt = collections.Counter(t['phase'] for t in m.tools)
    order = ['explore', 'build', 'verify', 'git', 'coordinate']
    tot = sum(cnt.values())
    return card('EX14', "How is Claude's effort split between exploring, building, verifying, git and coordinating?", 'T P S', [
        K(*[kpi(p.title(), share(cnt[p], tot), 'pct', f'{cnt[p]:,} calls') for p in order]),
        BAR([p.title() for p in order], [S('Share of tool calls', [r1(share(cnt[p], tot)) for p in order])], 'pct')],
        note='Explore = Read/search/inspect/scripts · Build = Edit/Write and Bash file writes · Verify = build and test commands · Coordinate = agents, questions, plans, skills.')


def ex15(m, g):
    seq = collections.defaultdict(list)
    runs = fails = 0
    mins = 0.0
    for t in m.tools:
        if t['name'] == 'Bash' and t['bash'] in ('build', 'test'):
            runs += 1
            f = bool(t['res'] and t['res']['fail'])
            fails += f
            mins += (t['latency'] or 0) / 60
            seq[t['thread']].append('F' if f else 'P')
        elif t['name'] in EDIT_TOOLS:
            seq[t['thread']].append('E')
    loops = sum(len(re.findall(r'FE+(?=[FP])', ''.join(s))) for s in seq.values())
    return card('EX15', 'How often do build or test failures turn into fix-and-retry loops?', 'T P S', [
        K(kpi('Build/test runs', runs, 'count', f'{f_dur(mins * 60)} in total'), kpi('Failed runs', fails, 'count', f_pct(share(fails, runs))),
          kpi('Fail → edit → re-run loops', loops, 'count'))],
        note='A run counts as failed on a tool error or when its output contains “error:”, “FAILED” or a non-zero exit code.',
        empty=None if runs else 'No build or test commands in this scope.')


# ------------------------------------------------------------------------------------------ SE5–SE6: subagents (in the Sessions section)

def _sub_group(t):
    return 0 if t == 'Explore' else 1 if t == 'general-purpose' else 2


def se5(m, g):
    subs = list(m.subs.values())
    cnt = collections.Counter(s['type'] for s in subs)
    usd = collections.defaultdict(float)
    by_model = collections.defaultdict(float)
    for s in subs:
        usd[s['type']] += s['usd']
        by_model[s['model']] += s['usd']
    types = sorted(cnt, key=lambda k: -cnt[k])
    mods = sorted(by_model, key=lambda k: -by_model[k])
    tot = sum(s['usd'] for s in subs)
    slots = [i % 8 + 1 for i in range(len(types))]      # the same colour per type in both type pies
    return card('SE5', 'How many subagents do I spawn, of which types, and what do they cost?', 'T P S', [
        K(kpi('Subagents', len(subs), 'count', f"{sum(1 for s in subs if s['is_async'])} in the background"),
          kpi('Share of spend', share(tot, m.usd), 'pct', f_usd(tot)), kpi('Average cost', tot / len(subs) if subs else None, 'usd')),
        PIE(types, [cnt[k] for k in types], 'count', title='Subagents by type', center='subagents', width='third', slots=slots),
        PIE(types, [r2(usd[k]) for k in types], 'usd', title='Cost by type', center='total', total=r2(tot), width='third', slots=slots),
        PIE([model_name(x) for x in mods], [r2(by_model[x]) for x in mods], 'usd', title='Cost by model', center='total',
            total=r2(tot), width='third', slots=[g.slot(x) for x in mods])],
        empty=None if subs else 'No subagents in this scope.')


def se6(m, g):
    pts, ratios = [], []
    for s in m.subs.values():
        if s['returned_chars']:
            ret = max(1, s['returned_chars'] / 4)
            pts.append(dict(x=round(ret), y=s['peak'], g=_sub_group(s['type']), label=f"{s['type']}: {clip(s['desc'], 40)}"))
            ratios.append(s['peak'] / ret)
    rd = collections.defaultdict(lambda: collections.defaultdict(set))       # who read each file, per session
    for t in m.tools:
        if t['name'] == 'Read' and t['input'].get('file_path'):
            rd[t['sid']][t['input']['file_path']].add(t['agent'] or 'main')
    sub_files = both = sib = 0
    for files in rd.values():
        for who in files.values():
            subs = {w for w in who if w != 'main'}
            if subs:
                sub_files += 1
                both += 'main' in who
                sib += len(subs) >= 2
    return card('SE6', 'Do subagents pay off by keeping work out of the main context?', 'T A', [
        K(kpi('Median payoff', median(ratios), 'ratio', 'peak subagent context ÷ tokens handed back'),
          kpi('Subagents measured', len(pts), 'count'),
          kpi('Files read by subagents', sub_files, 'count', 'each file counted once per session'),
          kpi('Also read by the main thread', share(both, sub_files), 'pct', f'{both} files'),
          kpi('Read by 2+ sibling subagents', sib, 'count')),
        SCATTER(pts, 'tokens', 'tokens', groups=['Explore', 'general-purpose', 'Other types'], log_x=True, log_y=True,
                x_label='Tokens handed back to the main thread', y_label='Peak context inside the subagent')],
        why='A subagent is worth it when it reads far more than it reports back.',
        empty=None if pts or sub_files else 'No subagent results found in this scope.')


# ------------------------------------------------------------------------------------------ EX: plugins, MCP, tools & hooks (EX6–EX15, tools, are further up)

def mcp_is(listed, used):
    """Whether a tool call's server key (mcp__<key>__tool) is the server a listing names (claude.ai connectors differ in spelling)."""
    return (used == listed or used.replace('_', ' ') == listed or listed.endswith(used)
            or used.endswith(listed.replace('claude.ai ', '').replace(' ', '_')))


def group_of(proj):
    """The project a transcripts folder belongs to: a task worktree's counts as its checkout's (as the scopes group them)."""
    return re.sub(r'--claude-worktrees-.*$', '', proj or '')


def where_used(m):
    """Skills, MCP servers and plugins that a user-level setting loads into every project, used in some projects and only
    loaded in others: {(kind, name): dict(used={project: uses}, idle={project: usd}, sessions=idle sessions, tok=tokens per
    session, origin=…, pid=the plugin's id)}, costliest idle first. Computed once, on all projects (build() hands it to every
    scope): SV2 lists it and item_origin words its advice from it. Loading an item costs its listing on every main-thread call
    of the session: written on the first call and after a miss, re-read on the others (as SV2 prices unused items)."""
    if 'where_used' in m.cache:
        return m.cache['where_used']
    src, e = m.src, _ext(m)
    home = src.home
    enabled = {k.split('@')[0]: k for k, v in (src.settings.get('enabledPlugins') or {}).items() if v is True and isinstance(k, str)}
    personal = lambda n: (os.path.exists(os.path.join(home, 'skills', n, 'SKILL.md'))
                          or os.path.exists(os.path.join(home, 'commands', n + '.md')))

    def item(kind, name):                     # the movable item a listed name belongs to, or None
        if kind == 'skill':
            ns = name.split(':')[0] if ':' in name else None
            if ns and ns in enabled and not name.startswith('anthropic-skills:'):
                return ('plugin', ns)
            return ('skill', name) if not ns and personal(name) else None
        return ('MCP server', name) if name in (src.mcp.get('user') or {}) else None
    listed = collections.defaultdict(lambda: collections.defaultdict(set))    # session → item → the names it listed
    for a in m.atts:
        x = a['a']
        kind = 'skill' if a['type'] == 'skill_listing' else 'MCP server' if a['type'] == 'mcp_instructions_delta' else None
        for n in (x.get('names') if kind == 'skill' else x.get('addedNames') if kind else None) or []:
            it = item(kind, n)
            if it:
                listed[a['sid']][it].add(n)
    size = lambda it, names: sum(e['sk_tok'].get(n, 15) if it[0] != 'MCP server' else e['mcp_tok'].get(n, 0) for n in names)
    used = collections.defaultdict(collections.Counter)                      # item → project → uses
    for t in m.tools:
        s = m.sessions.get(t['sid'])
        if not s:
            continue
        names = []
        if t['name'] == 'Skill':
            names.append(('skill', t['input'].get('skill') or ''))
        elif t['name'].startswith('mcp__'):
            key = t['name'].split('__')[1]
            names += [('MCP server', n) for n in (src.mcp.get('user') or {}) if mcp_is(n, key)]
        for kind, n in names:
            it = item(kind, n)
            if it:
                used[it][group_of(s['proj'])] += 1
    for c in m.commands:
        s = m.sessions.get(c['sid'])
        it = item('skill', c['name']) if s else None
        if it:
            used[it][group_of(s['proj'])] += 1
    out = {}
    for sid, items in listed.items():
        s = m.sessions.get(sid)
        if not s:
            continue
        g_ = group_of(s['proj'])
        rate = sum(w_rate(m, c) if c['idx'] == 0 or c['miss'] else r_rate(m, c) for c in s['main_calls'])
        for it, names in sorted(items.items()):
            tok = size(it, names)
            if not used.get(it) or used[it].get(g_):
                continue                              # used nowhere (SV2's unused rows) or used in this very project
            o = out.setdefault(it, dict(used=dict(used[it]), idle=collections.defaultdict(float), sessions=0, tok=[]))
            o['idle'][g_] += tok * rate
            o['sessions'] += 1
            o['tok'].append(tok)
    for (kind, name), o in out.items():
        o['idle'], o['tok'] = dict(o['idle']), mean(o['tok']) or 0
        o['origin'] = ('your MCP config (user scope)' if kind == 'MCP server' else f'personal ({tilde(home)})' if kind == 'skill'
                       else f'plugin, enabled in {tilde(os.path.join(home, "settings.json"))}')
        o['pid'] = enabled.get(name) if kind == 'plugin' else None
    m.cache['where_used'] = out = dict(sorted(out.items(), key=lambda kv: (-sum(kv[1]['idle'].values()), kv[0])))
    return out


def _ext(m):
    if 'ext' in m.cache:
        return m.cache['ext']
    listed_sk, listing_line = collections.Counter(), {}
    listed_mcp, mcp_chars = collections.Counter(), collections.defaultdict(list)
    listed_ag, ag_chars = collections.Counter(), collections.defaultdict(list)
    sess_calls = collections.Counter(c['sid'] for c in m.real)
    for a in m.atts:
        x = a['a']
        if a['type'] == 'skill_listing':
            for n in x.get('names') or []:
                listed_sk[n] += 1
            for ln in (x.get('content') or '').splitlines():
                mm = re.match(r'-\s+(.+?)(?::\s|:?$)', ln)         # "- plugin:skill: description", or a bare "- name"
                if mm:
                    listing_line[mm.group(1).strip()] = len(ln)
        elif a['type'] == 'mcp_instructions_delta':
            for n, b in zip(x.get('addedNames') or [], x.get('addedBlocks') or []):
                listed_mcp[n] += 1
                mcp_chars[n].append(len(b))
        elif a['type'] == 'deferred_tools_delta':
            for n in x.get('addedNames') or []:
                if n.startswith('mcp__'):
                    srv = n.split('__')[1]
                    mcp_chars[srv].append(len(n) + 2)
        elif a['type'] == 'agent_listing_delta':
            for n, ln in zip(x.get('addedTypes') or [], x.get('addedLines') or []):
                listed_ag[n] += 1
                ag_chars[n].append(len(ln))
    used_sk = collections.Counter()
    for t in m.tools:
        if t['name'] == 'Skill':
            used_sk[t['input'].get('skill')] += 1
    for c in m.commands:
        used_sk[c['name']] += 1
    for c in m.real:
        if c['skill']:
            used_sk[c['skill']] += 0
    used_mcp = collections.Counter(t['name'].split('__')[1] for t in m.tools if t['name'].startswith('mcp__'))
    used_ag = collections.Counter((t['input'].get('subagent_type') or 'general-purpose') for t in m.tools if t['name'] in ('Agent', 'Task'))
    un_sk = [n for n in listed_sk if n not in used_sk]
    un_mcp = [n for n in listed_mcp if not any(mcp_is(n, k) for k in used_mcp)]
    un_ag = [n for n in listed_ag if n not in used_ag]
    per_sess = (sum(listing_line.get(n, 60) for n in un_sk) + sum(sum(mcp_chars[n]) / max(1, listed_mcp[n]) for n in un_mcp)
                + sum(mean(ag_chars[n]) or 0 for n in un_ag)) / 4
    sess_with = uniq(a['sid'] for a in m.atts if a['type'] in ('skill_listing', 'mcp_instructions_delta', 'agent_listing_delta'))
    reread = sum(per_sess * max(0, sess_calls[s] - 1) for s in sess_with)
    avg_cr = (sum(c['cost'].get('cache_read', 0) for c in m.real) / sum(c['cr'] for c in m.real)) if sum(c['cr'] for c in m.real) else 0
    item_tok = ([('skill', n, listing_line.get(n, 60) / 4) for n in un_sk] +
                [('MCP server', n, (sum(mcp_chars[n]) / max(1, listed_mcp[n])) / 4) for n in un_mcp] +
                [('agent type', n, (mean(ag_chars[n]) or 0) / 4) for n in un_ag])
    out = dict(listed_sk=listed_sk, used_sk=used_sk, un_sk=un_sk, listed_mcp=listed_mcp, used_mcp=used_mcp, un_mcp=un_mcp,
               listed_ag=listed_ag, used_ag=used_ag, un_ag=un_ag, per_sess=per_sess, reread=reread, usd=reread * avg_cr,
               n_sess=len(sess_with), items=item_tok, sk_tok={n: listing_line.get(n, 60) / 4 for n in listed_sk},
               mcp_tok={n: (sum(mcp_chars[n]) / max(1, listed_mcp[n])) / 4 for n in listed_mcp})
    m.cache['ext'] = out
    return out


def ex1(m, g):
    e = _ext(m)
    att = collections.defaultdict(lambda: [0, 0.0])
    for c in m.real:
        if c['skill']:
            att[c['skill']][0] += 1
            att[c['skill']][1] += c['usd']
    tool_runs = collections.Counter(t['input'].get('skill') for t in m.tools if t['name'] == 'Skill')
    slash = collections.Counter(c['name'] for c in m.commands)
    names = sorted(uniq(list(att) + list(tool_runs)), key=lambda k: -(att[k][1] if k in att else 0))
    rows = [dict(k=k, tr=tool_runs.get(k, 0), sl=slash.get(k, 0), c=att[k][0] if k in att else 0, u=r2(att[k][1]) if k in att else 0)
            for k in names]
    top = [r for r in rows if r['u']][:10]
    return card('EX1', 'Which skills and slash commands do I use, and what does each cost?', 'T P', [
        BAR([r['k'] for r in top], [S('Cost', [r['u'] for r in top])], 'usd', title='Cost attributed to each skill'),
        TABLE([('k', 'Skill', None), ('tr', 'Skill-tool runs', 'count'), ('sl', 'Slash runs', 'count'), ('c', 'Attributed calls', 'count'),
               ('u', 'Attributed cost', 'usd')], rows, limit=12),
        TABLE([('k', 'Slash command', None), ('n', 'Uses', 'count')], [dict(k='/' + k, n=v) for k, v in slash.most_common(20)], 'Slash commands', 10)],
        note='Attributed cost = API calls tagged with that skill (attributionSkill) while it ran.')


def ex2(m, g):
    e = _ext(m)
    m.facts.update(unused=(len(e['un_sk']), len(e['listed_sk']), len(e['un_mcp']), len(e['un_ag'])), unused_tok=e['per_sess'],
                   unused_usd=e['usd'])
    ins = None
    if e['un_sk'] or e['un_mcp']:
        ins = (f"{len(e['un_sk'])} of {len(e['listed_sk'])} skills, {len(e['un_mcp'])} MCP server(s) and {len(e['un_ag'])} agent type(s) "
               f"were loaded but never used: about {f_tok(e['per_sess'])} tokens in every session.")
    mcp_rows = [dict(k=k, s=v, u=sum(n for kk, n in e['used_mcp'].items() if kk == k)) for k, v in e['listed_mcp'].most_common()]
    return card('EX2', 'What loads into every session but never gets used?', 'T P', [
        K(kpi('Unused skills', len(e['un_sk']), 'count', f"of {len(e['listed_sk'])} listed"),
          kpi('Unused MCP servers', len(e['un_mcp']), 'count', f"of {len(e['listed_mcp'])} connected"),
          kpi('Unused agent types', len(e['un_ag']), 'count', f"of {len(e['listed_ag'])} listed"),
          kpi('Their size per session', e['per_sess'], 'tokens', f"≈ {f_usd(e['usd'])} in re-reads")),
        TABLE([('k', 'MCP server', None), ('s', 'Sessions loaded', 'count'), ('u', 'Tool calls', 'count')], mcp_rows, 'MCP servers'),
        LIST(sorted(e['un_sk']), 'Skills never used'), LIST(sorted(e['un_ag']), 'Agent types never used')],
        insight=ins, note='Skills count as used when invoked through the Skill tool or as a slash command. Size uses each item’s own line in the listing.',
        empty=None if e['listed_sk'] or e['listed_mcp'] else 'No skill or MCP listings in this scope.')


def ex3(m, g):
    rows = []
    for a in m.atts:
        if a['type'] not in ('deferred_tools_delta', 'mcp_instructions_delta') or a['t'] is None:
            continue
        calls = m.threads.get(a['thread'], [])
        i = bisect.bisect_left([c['t0'] for c in calls], a['t'])
        if i == 0 or i >= len(calls):
            continue
        nxt = calls[i]
        x = a['a']
        names = x.get('addedNames') or []
        mcp = sorted({n.split('__')[1] for n in names if n.startswith('mcp__')}) if a['type'] == 'deferred_tools_delta' else names
        rows.append(dict(t=local(a['t']).strftime('%m-%d %H:%M'), s=sess_label(m, a['sid'], 40), k=a['type'].replace('_delta', ''),
                         w=clip(', '.join(mcp) if mcp else f'{len(names)} tools', 60), rw=nxt['rewritten'] if nxt['miss'] else 0))
    loads = collections.Counter()
    for t in m.tools:
        if t['name'] == 'ToolSearch':
            for n in ((t['res'] or {}).get('tur') or {}).get('matches') or []:
                loads[n] += 1
    return card('EX3', 'How often does the tool list change mid-session, and what does it cost?', 'T S', [
        K(kpi('Mid-session tool-list changes', len(rows), 'count'), kpi('Tokens re-written after them', sum(r['rw'] for r in rows), 'tokens'),
          kpi('ToolSearch loads', sum(1 for t in m.tools if t['name'] == 'ToolSearch'), 'count')),
        TABLE([('t', 'When', None), ('s', 'Session', None), ('k', 'Change', None), ('w', 'What', None), ('rw', 'Re-written next call', 'tokens')],
              rows, limit=10),
        TABLE([('k', 'Tool loaded via ToolSearch', None), ('n', 'Times', 'count')], [dict(k=k, n=v) for k, v in loads.most_common(12)], limit=8)],
        why='Tools sit at the very front of the prompt, so a change there invalidates the whole cache.')


def ex4(m, g):
    dg = [a for a in m.atts if a['type'] == 'diagnostics']
    issues = sum(len(f.get('diagnostics') or []) for a in dg for f in (a['a'].get('files') or []))
    sev = collections.Counter(d.get('severity') for a in dg for f in (a['a'].get('files') or []) for d in (f.get('diagnostics') or []))
    enabled = g.src.settings.get('enabledPlugins') or {}
    prow = []
    for name, inst in g.src.plugins.items():
        i = inst[0] if isinstance(inst, list) and inst else inst if isinstance(inst, dict) else {}
        prow.append(dict(k=name, v=i.get('version'), e='yes' if enabled.get(name) else 'no', d=(i.get('installedAt') or '')[:10]))
    e = _ext(m)
    ns = collections.defaultdict(lambda: [0, 0])
    for n in e['listed_sk']:
        if ':' in n:
            ns[n.split(':')[0]][0] += 1
            ns[n.split(':')[0]][1] += bool(e['used_sk'].get(n))
    return card('EX4', 'What do plugins add, in value and overhead?', 'T P', [
        K(kpi('LSP diagnostics injected', len(dg), 'count', f'{issues} issues ({", ".join(f"{v} {k}" for k, v in sev.most_common(3))})' if issues else None),
          kpi('Their context size', sum(a['chars'] for a in dg) / 4, 'tokens')),
        TABLE([('k', 'Installed plugin', None), ('v', 'Version', None), ('e', 'Enabled', None), ('d', 'Installed', None)], prow),
        TABLE([('k', 'Skill namespace', None), ('l', 'Skills listed', 'count'), ('u', 'Used', 'count')],
              [dict(k=k, l=v[0], u=v[1]) for k, v in sorted(ns.items())], 'Plugin-provided skills')],
        note=f'Installed plugins come from {tilde(os.path.join(g.src.home, "plugins", "installed_plugins.json"))} and settings.json (global, not per project).')


def ex5(m, g):
    agg = collections.defaultdict(lambda: dict(n=0, ms=[], f=0))
    for h in m.hook_runs:
        a = agg[(h['event'], h['script'])]
        a['n'] += 1
        a['ms'].append(h['ms'])
        a['f'] += not h['ok']
    inj = collections.defaultdict(float)
    for a in m.atts:
        if a['type'] in ('hook_additional_context', 'hook_system_message'):
            inj[(a['a'].get('hookEvent') or a['a'].get('hookName') or '?').split(':')[0]] += a['chars'] / 4
    # Stop-hook context can be logged twice (an attachment and the stop_hook_summary): count a session's summaries only
    # when it has no Stop attachment
    stop_att = {a['sid'] for a in m.atts if a['type'] == 'hook_additional_context' and a['a'].get('hookEvent') == 'Stop'}
    for d in m.sys.get('stop_hook_summary', []):
        if d.get('sessionId') not in stop_att:
            inj['Stop'] += str_len(d.get('hookAdditionalContext')) / 4
    carry = sum(it['carry'] for it in m.items if it['source'] == 'Hook context')
    rows = [dict(e=k[0], s=k[1], n=v['n'], tot=sum(v['ms']) / 1000, p50=median(v['ms']), mx=max(v['ms']), f=v['f'])
            for k, v in sorted(agg.items(), key=lambda kv: -kv[1]['n'])]
    fails = [dict(t=local(h['t']).strftime('%m-%d %H:%M') if h['t'] else '', e=h['event'], s=h['script'], c=h['code'], err=h['stderr'] or h['kind'])
             for h in m.hook_runs if not h['ok']]
    by_script = collections.Counter(h['script'] for h in m.hook_runs if not h['ok'])
    ins = None
    if by_script:
        s, n = by_script.most_common(1)[0]
        mine = [h for h in m.hook_runs if not h['ok'] and h['script'] == s]
        kinds = collections.Counter('cancelled' if h['kind'] == 'hook_cancelled' else 'errors' for h in mine)
        err = max((h['stderr'] for h in mine), key=len, default='').replace('Failed with non-blocking status code: ', '')
        ins = (f'{s} failed {n} time{"s" if n > 1 else ""} ({", ".join(f"{v} {k}" for k, v in kinds.items())})'
               + (f': {clip(err, 110)}' if err else '.'))
        missing = re.search(r'(/[^\s:\'"]+): No such file', err or '')
        if missing:                                   # is the broken path still configured anywhere?
            settings = json.dumps([g.src.settings, g.src.local_settings] + [js for f in g.src.project_settings.values() for js in f.values()])
            last = max((h['t'] for h in mine if h['t']), default=None)
            if missing.group(1) not in settings:
                ins = ins.rstrip('.') + ('. No settings file uses that path any more' + (f' (last failure {day(last)})' if last else '') +
                                         ', so it looks fixed.')
        m.facts['hook_fail'] = ins
    stops = _post_stop(m)[2]
    return card('EX5', 'What do my hooks cost: runs, time, failures and context injected?', 'T P', [
        K(kpi('Hook runs', len(m.hook_runs), 'count'), kpi('Time added', sum(h['ms'] for h in m.hook_runs) / 1000, 'sec'),
          kpi('Failures', len(fails), 'count'), kpi('Context injected', sum(inj.values()), 'tokens', f'{f_tok(carry)} tokens re-read later')),
        TABLE([('e', 'Event', None), ('s', 'Hook', None), ('n', 'Runs', 'count'), ('tot', 'Total time', 'sec'), ('p50', 'Median', 'ms'),
               ('mx', 'Max', 'ms'), ('f', 'Failures', 'count')], rows),
        TABLE([('e', 'Event', None), ('k', 'Context injected', 'tokens')], [dict(e=k, k=round(v)) for k, v in inj.items() if v], 'Injected context by event'),
        TABLE([('h', 'Stop hook', None), ('sr', 'Stop runs', 'count'), ('go', 'Claude went on working after', 'count'),
               ('fc', 'API calls that set off', 'count'), ('fu', 'Their cost', 'usd')],
              [dict(h=k, sr=v[0], go=v[1], fc=v[2], fu=r2(v[3])) for k, v in sorted(stops.items(), key=lambda kv: (-kv[1][3], -kv[1][0], kv[0]))],
              'What Stop hooks set off: work Claude did after a hook ran, before any new prompt'),
        TABLE([('t', 'When', None), ('e', 'Event', None), ('s', 'Hook', None), ('c', 'Exit code', None), ('err', 'Error', None)], fails, 'Failures', 8)],
        insight=ins, empty=None if m.hook_runs or inj else 'No hook activity in this scope.')


def new_input(d):
    """A record that starts new work on its thread: a prompt, command or queued message, a task notification or a message
    from another session (anything with a promptSource), a prompt or task notification delivered as a queued_command
    attachment, or a plain user message. Not a tool result (with or without an image beside it), not the text a hook or a
    skill adds (isMeta without a promptSource), not a compaction summary."""
    if d.get('type') == 'attachment':
        return isinstance(d.get('attachment'), dict) and d['attachment'].get('type') == 'queued_command'
    if d.get('type') != 'user' or d.get('isCompactSummary'):
        return False
    content = (d.get('message') or {}).get('content')
    if isinstance(content, list) and any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in content):
        return False
    return bool(d.get('promptSource')) or not d.get('isMeta')


GOAL = 'Goal check (/goal)'       # a /goal condition, which Claude Code checks as a prompt-based Stop hook


def _post_stop(m):
    """What Stop hooks set off. Each Stop run on a main thread is logged as a stop_hook_summary record; the API calls that
    descend from it (parentUuid links) before any new input are work Claude did because the hook sent something back (added
    context, or blocked the stop). A hook that sends nothing back, such as a notification, sets off no call and costs
    nothing here. Returns (the follow-up calls, except a /goal check's, the Stop runs, and per hook: [runs, runs that set off
    work, calls, usd])."""
    if 'post_stop' in m.cache:
        return m.cache['post_stop']
    kids = collections.defaultdict(list)
    for d in m.recs:
        if not d.get('isSidechain') and d.get('parentUuid'):
            kids[d['parentUuid']].append(d)
    main = {c['key']: c for c in m.real if not c['agent']}
    post, got, runs = [], set(), 0
    per = collections.defaultdict(lambda: [0, 0, 0, 0.0])
    for s in m.sys.get('stop_hook_summary', []):
        if s.get('isSidechain') or s['_t'] is None:
            continue
        runs += 1
        who = ' + '.join(uniq(GOAL if h.get('promptText') is not None else hook_script(h.get('command'))
                              for h in s.get('hookInfos') or [] if isinstance(h, dict))) or 'Stop hook'
        mine, stack, seen = [], list(kids.get(s.get('uuid'), [])), set()
        while stack:
            d = stack.pop()
            if id(d) in seen or new_input(d) or d.get('subtype') == 'stop_hook_summary':
                continue                                   # the next Stop run's own follow-up is counted from its record
            seen.add(id(d))
            if d.get('type') == 'assistant':
                c = main.get(((d.get('message') or {}).get('id'), d.get('requestId')))
                if c is not None and c['key'] not in got:
                    got.add(c['key'])
                    mine.append(c)
            stack.extend(kids.get(d.get('uuid'), []) if d.get('uuid') else [])
        if who != GOAL:
            post += mine                                   # a /goal check keeps Claude working on purpose: shown, not a saving
        row = per[who]
        row[0] += 1
        row[1] += bool(mine)
        row[2] += len(mine)
        row[3] += sum(c['usd'] for c in mine)
    m.cache['post_stop'] = (post, runs, dict(per))
    return m.cache['post_stop']


# ------------------------------------------------------------------------------------------ OUT: output & outcomes

def out1(m, g):
    think = sum(c['think'] for c in m.real)
    out = sum(c['out'] for c in m.real)
    tool_tok, text_tok = _out_alloc(m)
    tok = collections.Counter({'Text shown to you': text_tok})
    for t in m.tools:
        n = t['name']
        k = ('Bash commands & scripts' if n == 'Bash' else 'File contents (Write/Edit)' if n in EDIT_TOOLS
             else 'Plans' if n == 'ExitPlanMode' else 'Subagent prompts' if n in ('Agent', 'Task', 'SendMessage') else 'Other tool inputs')
        tok[k] += tool_tok.get(t['id'], 0)
    parts = [('Thinking', think)] + tok.most_common()
    return card('OUT1', 'Where do output tokens go?', 'T P S', [
        K(kpi('Output tokens', out, 'tokens'), kpi('Thinking', share(think, out), 'pct'),
          kpi('Text shown to you', share(text_tok, out), 'pct'),
          kpi('File contents & scripts', share(tok['File contents (Write/Edit)'] + tok['Bash commands & scripts'], out), 'pct')),
        BAR([p[0] for p in parts], [S('Output tokens', [round(p[1]) for p in parts])], 'tokens', title='Output tokens by what they produced')],
        note='Thinking tokens are logged exactly; each call’s remaining output is split between its text and tool inputs by their length.')


def _lines(m):
    if 'lines' in m.cache:
        return m.cache['lines']
    per_file = collections.defaultdict(lambda: [0, 0, 0])
    per_day = collections.defaultdict(lambda: [0, 0])
    per_sf = collections.defaultdict(lambda: [0, 0, 0])
    for t in m.tools:
        if t['name'] not in EDIT_TOOLS or not t['res'] or t['res']['is_error']:
            continue
        tur = t['res'].get('tur') or {}
        sp = tur.get('structuredPatch') or []
        add = sum(1 for h in sp for ln in (h.get('lines') or []) if ln.startswith('+'))
        rem = sum(1 for h in sp for ln in (h.get('lines') or []) if ln.startswith('-'))
        if not sp and tur.get('type') == 'create':
            add = len((tur.get('content') or '').splitlines())
        fp = t['input'].get('file_path') or tur.get('filePath') or '?'
        for d, v in ((per_file[fp], None), (per_sf[(t['sid'], fp)], None)):
            d[0] += 1
            d[1] += add
            d[2] += rem
        if t['t']:
            per_day[day(t['t'])][0] += add
            per_day[day(t['t'])][1] += rem
    m.cache['lines'] = (per_file, per_day, per_sf)
    return m.cache['lines']


def out2(m, g):
    per_file, per_day, _ = _lines(m)
    add = sum(v[1] for v in per_file.values())
    rem = sum(v[2] for v in per_file.values())
    m.facts['lines'] = add + rem
    days = fill_days(per_day)
    rows = [dict(f=short_path(fp), e=v[0], a=v[1], r=v[2]) for fp, v in sorted(per_file.items(), key=lambda kv: -(kv[1][1] + kv[1][2]))[:30]]
    return card('OUT2', 'How much code did Claude change, and what does 100 changed lines cost?', 'T P S', [
        K(kpi('Lines added', add, 'count'), kpi('Lines removed', rem, 'count'), kpi('Files touched', len(per_file), 'count'),
          kpi('Cost per 100 changed lines', 100 * m.usd / (add + rem) if add + rem else None, 'usd', 'all spend ÷ lines changed')),
        BAR(days, [S('Added', [per_day[d][0] if d in per_day else 0 for d in days], 3),
                   S('Removed', [per_day[d][1] if d in per_day else 0 for d in days], 8)], 'count', orient='v',
            title='Lines changed per day', labels=False),
        TABS([('Most-changed files', TABLE([('f', 'File', None), ('e', 'Edits', 'count'), ('a', 'Added', 'count'), ('r', 'Removed', 'count')],
                                           rows, limit=10))], title='Most-changed files', sub=f'top {len(rows)} by lines changed')],
        note='From the diffs Claude Code records for Edit/Write (new files count all their lines as added). Bash heredoc writes are not included.',
        empty=None if per_file else 'No file edits in this scope.')


def path_kind(p):
    pl = (p or '').lower()
    if '/memory/' in pl or pl.endswith('memory.md'):
        return 'Memory'
    if '/plans/' in pl:
        return 'Plans'
    if re.search(r'(^|/)(tests?|__tests__|spec)/|[._-]test\.|tests?\.\w+$|checks?\.\w+$', pl):
        return 'Tests'
    if pl.endswith(('.md', '.txt', '.rst')):
        return 'Docs'
    if pl.endswith(('.json', '.yaml', '.yml', '.toml', '.plist', '.ini', '.cfg', '.env', '.xcconfig')):
        return 'Config'
    if re.search(r'\.(swift|py|ts|tsx|js|jsx|go|rs|rb|java|kt|c|cc|cpp|h|hpp|m|mm|cs|php|sh|css|scss|html|vue|svelte|sql)$', pl):
        return 'Code'
    return 'Other'


def out3(m, g):
    agg = collections.defaultdict(lambda: [0, 0, 0.0])
    per_file, _, _ = _lines(m)
    tool_tok, _ = _out_alloc(m)
    for t in m.tools:
        if t['name'] in EDIT_TOOLS:
            k = path_kind(t['input'].get('file_path'))
            agg[k][0] += 1
            agg[k][2] += tool_tok.get(t['id'], 0)
    for fp, v in per_file.items():
        agg[path_kind(fp)][1] += v[1]
    kinds = sorted(agg, key=lambda k: -agg[k][0])
    tot = [sum(agg[k][j] for k in kinds) for j in range(3)]
    ser = []
    for j, (name, unit, slot) in enumerate((('Edit/Write calls', 'count', 1), ('Lines added', 'count', 2), ('Output tokens', 'tokens', 3))):
        raw = [round(agg[k][j]) for k in kinds]
        ser.append(dict(S(f"{name} ({f_tok(tot[j]) if unit == 'tokens' else f'{round(tot[j]):,}'})", [r1(share(v, tot[j])) for v in raw], slot), raw=raw, rawUnit=unit))
    return card('OUT3', 'What does Claude write: code, tests, plans, memory, docs or config?', 'T P', [
        BAR(kinds, ser, 'pct', orient='v', title='Each kind of file’s share of edits, lines added and output tokens', labels=False),
        hide(TABLE([('k', 'Kind', None), ('n', 'Edits', 'count'), ('a', 'Lines added', 'count'), ('t', 'Output tokens', 'tokens')],
                   [dict(k=k, n=agg[k][0], a=agg[k][1], t=round(agg[k][2])) for k in kinds]))],   # the digest's numbers
        why='Shares put three different measures on one scale: a kind with more tokens than edits gets long, heavy writes.',
        empty=None if agg else 'No file edits in this scope.')


LANGS = {  # file extension -> language
    'py': 'Python', 'pyi': 'Python', 'ipynb': 'Jupyter', 'ts': 'TypeScript', 'tsx': 'TypeScript', 'mts': 'TypeScript', 'cts': 'TypeScript',
    'js': 'JavaScript', 'jsx': 'JavaScript', 'mjs': 'JavaScript', 'cjs': 'JavaScript', 'swift': 'Swift', 'go': 'Go', 'rs': 'Rust',
    'rb': 'Ruby', 'java': 'Java', 'kt': 'Kotlin', 'kts': 'Kotlin', 'scala': 'Scala', 'c': 'C', 'h': 'C', 'cc': 'C++', 'cpp': 'C++',
    'cxx': 'C++', 'hpp': 'C++', 'hh': 'C++', 'm': 'Objective-C', 'mm': 'Objective-C', 'cs': 'C#', 'fs': 'F#', 'php': 'PHP',
    'sh': 'Shell', 'bash': 'Shell', 'zsh': 'Shell', 'fish': 'Shell', 'ps1': 'PowerShell', 'css': 'CSS', 'scss': 'CSS', 'sass': 'CSS',
    'less': 'CSS', 'html': 'HTML', 'htm': 'HTML', 'vue': 'Vue', 'svelte': 'Svelte', 'astro': 'Astro', 'sql': 'SQL', 'lua': 'Lua',
    'dart': 'Dart', 'r': 'R', 'ex': 'Elixir', 'exs': 'Elixir', 'erl': 'Erlang', 'hs': 'Haskell', 'clj': 'Clojure', 'pl': 'Perl',
    'zig': 'Zig', 'nim': 'Nim', 'jl': 'Julia', 'tf': 'Terraform', 'hcl': 'Terraform', 'proto': 'Protobuf', 'graphql': 'GraphQL',
    'gql': 'GraphQL', 'gradle': 'Gradle', 'md': 'Markdown', 'mdx': 'Markdown', 'markdown': 'Markdown', 'rst': 'reStructuredText',
    'txt': 'Plain text', 'json': 'JSON', 'jsonc': 'JSON', 'jsonl': 'JSON', 'yaml': 'YAML', 'yml': 'YAML', 'toml': 'TOML',
    'xml': 'XML', 'plist': 'XML', 'xcconfig': 'Xcode config', 'ini': 'INI', 'cfg': 'INI', 'conf': 'INI', 'env': 'Env', 'csv': 'CSV',
    'log': 'Log',
}
DIR_EXTS = {'app', 'xcodeproj', 'xcworkspace', 'framework', 'bundle', 'xcassets', 'lproj', 'appex', 'xcframework', 'git', 'd'}   # folders
LANG_NAMES = {'dockerfile': 'Dockerfile', 'makefile': 'Makefile', 'gnumakefile': 'Makefile', 'cmakelists.txt': 'CMake',
              'gemfile': 'Ruby', 'podfile': 'Ruby', 'rakefile': 'Ruby', '.gitignore': 'Git config', '.gitattributes': 'Git config',
              '.env': 'Env', '.zshrc': 'Shell', '.bashrc': 'Shell', '.bash_profile': 'Shell'}
NOT_CODE = {'Markdown', 'reStructuredText', 'Plain text', 'JSON', 'YAML', 'TOML', 'XML', 'Xcode config', 'INI', 'Env', 'CSV', 'Git config', 'Log'}


def lang_of(path, known=False):
    """The language of a file, from its name or extension: None for a folder or a name without one. Other short extensions
    come back as themselves ('.pen'), unless known=True (paths taken from shell commands, where they are mostly folders)."""
    name = os.path.basename((path or '').rstrip('/')).lower()
    if name in LANG_NAMES:
        return LANG_NAMES[name]
    if name.startswith('.env.'):
        return 'Env'
    ext = name.rsplit('.', 1)[1] if '.' in name.strip('.') else ''
    if not ext or ext in DIR_EXTS:
        return None
    if ext in LANGS:
        return LANGS[ext]
    return None if known or not re.fullmatch(r'[a-z0-9]{1,6}', ext) else '.' + ext


FILE_OP_WORDS = re.compile(r'rm|mv|unlink|trash')        # every command bash_file_ops looks for (git rm and git mv too)
UNQUOTE = str.maketrans('', '', '\'"\\')                  # all that shlex can take out of a word


def bash_file_ops(cmd):
    """(op, path) for the files a shell command deletes or moves: rm, git rm, unlink, trash, mv, git mv. Flags, globs and
    variables are skipped (their files can't be known), and so are heredoc bodies (text being written, not run)."""
    return _bash_file_ops(cmd if isinstance(cmd, str) else '')


@functools.lru_cache(maxsize=None)
def _bash_file_ops(cmd):
    """bash_file_ops for a command string. Cached (so a tuple): every scope that holds a command parses it again, and
    shlex is slow."""
    cmd = re.sub(r"<<-?\s*(['\"]?)(\w+)\1.*?\n\2\b", '', cmd, flags=re.S)
    out = []
    for seg in re.split(r'&&|\|\||[;\n|]', cmd):
        if not FILE_OP_WORDS.search(seg.translate(UNQUOTE)):
            continue                # none of these commands in it: quotes and backslashes are all shlex could remove
        try:
            tok = shlex.split(seg, comments=True)
        except ValueError:
            continue
        while tok and (re.match(r'^\w+=', tok[0]) or tok[0] in ('sudo', 'command', 'nohup', 'time')):
            tok = tok[1:]
        if len(tok) > 1 and tok[0] == 'git' and tok[1] in ('rm', 'mv'):
            op, args = tok[1], tok[2:]
        elif tok and tok[0] in ('rm', 'unlink', 'trash', 'mv'):
            op, args = tok[0], tok[1:]
        else:
            continue
        args = [a for a in args if not a.startswith('-') and not re.search(r'[*?\[\]{}$`~]', a)]
        if op == 'mv':
            out += [('moved', a) for a in args[:-1]] if len(args) >= 2 else []
        else:
            out += [('deleted', a) for a in args]
    return tuple(out)


def _langs(m):
    """Per language: the distinct files Claude created, edited, deleted, moved and read, lines changed, and the tokens (and their
    cost) it wrote into and read out of those files."""
    if 'langs' in m.cache:
        return m.cache['langs']
    per_file, _, _ = _lines(m)
    tool_tok, _ = _out_alloc(m)
    L = collections.defaultdict(lambda: dict(created=set(), edited=set(), deleted=set(), moved=set(), read=set(), add=0, rem=0,
                                             tw=0.0, tr=0.0, usd=0.0))
    norm = lambda p, sid: os.path.normpath(p if os.path.isabs(p) else os.path.join(m._cwd(sid) or '', p))
    for t in m.tools:
        if not t['res'] or t['res']['is_error']:
            continue
        if t['name'] in EDIT_TOOLS or t['name'] == 'Read':
            fp = t['input'].get('file_path') or t['input'].get('notebook_path') or ((t['res'].get('tur') or {}).get('filePath'))
            lg = lang_of(fp)
            if not lg:
                continue
            fp = norm(fp, t['sid'])
            a = L[lg]
            if t['name'] == 'Read':
                a['read'].add(fp)
                continue
            if t['name'] == 'Write' and (t['res'].get('tur') or {}).get('type') == 'create':
                a['created'].add(fp)
            else:
                a['edited'].add(fp)
            tok = tool_tok.get(t['id'], 0.0)
            a['tw'] += tok
            a['usd'] += tok * m.prices.per_token(t['call']['model'], 'out', t['call']['pm'])
        elif t['name'] == 'Bash':
            for op, p in bash_file_ops(t['input'].get('command')):
                lg = lang_of(p, known=True)
                if lg:
                    L[lg][op].add(norm(p, t['sid']))
    for it in m.items:                                     # what each Read put in context: written once, then re-read
        tu = it['tu']
        if tu and tu['name'] == 'Read':
            lg = lang_of(tu['input'].get('file_path'))
            if lg:
                L[lg]['tr'] += it['tokens']
                L[lg]['usd'] += it['carry_usd'] + it['tokens'] * m.prices.per_token(it['model'], 'cw5m', it['pm'])
    for fp, v in per_file.items():
        lg = lang_of(fp)
        if lg:
            L[lg]['add'] += v[1]
            L[lg]['rem'] += v[2]
    for a in L.values():
        a['edited'] -= a['created']                        # a file Claude created and then changed counts once, as created
        a['touched'] = a['created'] | a['edited'] | a['deleted'] | a['moved']
    m.cache['langs'] = L
    return L


def out4(m, g):
    L = _langs(m)
    q = 'Which languages does Claude work in: files, lines and tokens per language?'
    n = lambda k, f: len(L[k][f])
    lines = {k: a['add'] + a['rem'] for k, a in L.items()}
    tok = {k: a['tw'] + a['tr'] for k, a in L.items()}
    ks = sorted((k for k, a in L.items() if a['touched'] or a['read'] or lines[k]), key=lambda k: (-lines[k], -n(k, 'touched'), -tok[k]))
    if not ks:
        return card('OUT4', q, 'T P S', [], empty='Claude did not read or change any files in this scope.')
    top = sorted(ks, key=lambda k: -lines[k])[:8]
    top = [k for k in top if lines[k] or n(k, 'touched')] or ks[:8]
    rest = [k for k in ks if k not in top]
    cats = top + (['Other'] if rest else [])
    val = lambda f: [f(k) for k in top] + ([sum(f(k) for k in rest)] if rest else [])
    ser = []
    for name, f, unit, slot in (('Files touched', lambda k: n(k, 'touched'), 'count', 1), ('Lines changed', lambda k: lines[k], 'count', 2),
                                ('Tokens read + written', lambda k: round(tok[k]), 'tokens', 3)):
        raw = val(f)
        tot = sum(raw)
        ser.append(dict(S(f"{name} ({f_tok(tot) if unit == 'tokens' else f'{tot:,}'})", [r1(share(v, tot)) for v in raw], slot),
                        raw=raw, rawUnit=unit, rawName=name))
    code = [k for k in ks if k not in NOT_CODE and not k.startswith('.')]
    lead = max(code or ks, key=lambda k: lines[k])
    tl = sum(lines.values())
    files = [dict(k=k, t=n(k, 'touched'), c=n(k, 'created'), e=n(k, 'edited'), d=n(k, 'deleted'), mv=n(k, 'moved'), r=n(k, 'read')) for k in ks]
    work = [dict(k=k, a=L[k]['add'], rm=L[k]['rem'], w=round(L[k]['tw']), r=round(L[k]['tr']), u=r2(L[k]['usd'])) for k in ks]
    ins = (f"{lead} leads with {f_pct(share(lines[lead], tl))} of changed lines ({lines[lead]:,}) across {n(lead, 'touched')} files; "
           f"reading and writing {lead} cost about {f_usd(L[lead]['usd'])} in tokens.") if tl else None
    return card('OUT4', q, 'T P S', [
        K(kpi('Programming languages', len([k for k in code if n(k, 'touched')]), 'count', 'with files Claude changed'),
          kpi('Files created', sum(n(k, 'created') for k in ks), 'count'), kpi('Files edited', sum(n(k, 'edited') for k in ks), 'count'),
          kpi('Files deleted', sum(n(k, 'deleted') for k in ks), 'count', 'rm / git rm in Bash'),
          kpi('Files moved or renamed', sum(n(k, 'moved') for k in ks), 'count', 'mv / git mv in Bash')),
        BAR(cats, ser, 'pct', orient='v', title='Each language’s share of files touched, lines changed and tokens', labels=False),
        TABS([('Files', TABLE([('k', 'Language', None), ('t', 'Files touched', 'count'), ('c', 'Created', 'count'), ('e', 'Edited', 'count'),
                               ('d', 'Deleted', 'count'), ('mv', 'Moved', 'count'), ('r', 'Read', 'count')], files, limit=12)),
              ('Lines and tokens', TABLE([('k', 'Language', None), ('a', 'Lines added', 'count'), ('rm', 'Lines removed', 'count'),
                                          ('w', 'Tokens written', 'tokens'), ('r', 'Tokens read', 'tokens'), ('u', 'Token cost', 'usd')], work, limit=12))],
             title='Every language', collapsed=False)],
        why='Where Claude’s coding effort goes, by language: which files it made, changed or removed, and what that cost in tokens.',
        insight=ins,
        note='Distinct files per language, from the file extension. Created = new files from Write (a file created and then edited counts '
             'once, as created); edited = existing files changed by Edit, MultiEdit, NotebookEdit or a Write that replaced them; '
             'deleted and moved come from rm, git rm, mv and git mv in Bash (globs and variables can’t be resolved and are skipped). '
             'Tokens written = the output tokens of those edits; tokens read = what Read results put in context. Token cost adds '
             'the output price of the edits and the write and re-read cost of the reads. Files written by Bash heredocs are not counted.')


# ------------------------------------------------------------------------------------------ ME: working patterns

WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


def me1(m, g):
    grid = [[0] * 24 for _ in range(7)]
    for p in m.prompts:
        lt = local(p['t'])
        grid[lt.weekday()][lt.hour] += 1
    by_hour = [0.0] * 24
    for c in m.real:
        by_hour[local(c['t0']).hour] += c['usd']
    hours = [f'{h:02d}' for h in range(24)]
    return card('ME1', 'When do I work?', 'T W', [
        HEAT(WEEKDAYS, hours, grid, 'count', title='Your prompts by weekday and hour (local time)'),
        BAR(hours, [S('Cost', [r2(v) for v in by_hour])], 'usd', orient='v', title='Spend by hour of day', labels=False)],
        empty=None if m.prompts else 'No prompts in this scope.')


def me2(m, g):
    lens = [len(p['text']) for p in m.prompts]
    img_tok = sum(it['tokens'] for it in m.items if it['source'] == 'Your prompts (images)')
    n_img = sum(p['images'] for p in m.prompts)
    edges, labels = [50, 150, 400, 1000, 3000], ['<50', '50–150', '150–400', '400–1K', '1K–3K', '>3K']
    return card('ME2', 'How do I prompt, and what do screenshots cost?', 'T P', [
        K(kpi('Typed prompts', sum(1 for p in m.prompts if p['source'] == 'typed'), 'count'),
          kpi('Queued while Claude worked', sum(1 for p in m.prompts if p['source'] == 'queued'), 'count'),
          kpi('Slash commands', len(m.commands), 'count'), kpi('Median prompt length', median(lens), 'chars'),
          kpi('Screenshots/images', n_img, 'count', f'≈ {f_tok(img_tok / n_img) if n_img else "–"} tokens each')),
        BAR(labels, [S('Prompts', bucketize(lens, edges, labels))], 'count', orient='v', title='Prompt length (characters)')],
        empty=None if m.prompts else 'No prompts in this scope.')


def _replies(m):
    if 'replies' in m.cache:
        return m.cache['replies']
    out = []
    starts = collections.defaultdict(list)
    for t in m.turns:
        starts[t['sid']].append(t['start'])
    for sid, s in m.sessions.items():
        ends = sorted(c['t1'] for c in s['main_calls'])
        sp = sorted(starts.get(sid, []))
        for p in m.prompts:
            if p['sid'] != sid or p['source'] != 'typed':
                continue
            i = bisect.bisect_left(ends, p['t']) - 1
            if i < 0:
                continue
            j = bisect.bisect_left(sp, p['t']) - 1
            if j >= 0 and sp[j] > ends[i]:
                continue
            out.append((p['t'], p['t'] - ends[i]))
    m.cache['replies'] = out
    return out


def me3(m, g):
    rep = [r[1] for r in _replies(m)]
    tl = main_ttl(m)
    waits = collections.defaultdict(list)
    for t in m.tools:
        if t['name'] in WAITING_TOOLS and t['latency'] is not None and not t['agent']:
            waits[t['name']].append(t['latency'])
    edges, labels = [30, 120, 300, 900, 3600, 14400], ['<30s', '30s–2m', '2–5m', '5–15m', '15–60m', '1–4h', '>4h']
    return card('ME3', 'How long do I take to reply, and how often do my pauses outlast the cache?', 'T P', [
        K(kpi('Median reply time', median(rep), 'sec', 'from Claude’s last answer to your next prompt'),
          kpi(f'Replies after more than {life_word(tl)}', sum(1 for x in rep if x > tl), 'count', 'main-thread cache expired'),
          kpi('Answering questions (median)', median(waits.get('AskUserQuestion', [])), 'sec'),
          kpi('Approving plans (median)', median(waits.get('ExitPlanMode', [])), 'sec')),
        BAR(labels, [S('Replies', bucketize(rep, edges, labels))], 'count', orient='v', title='Your reply time')],
        empty=None if rep else 'No replies to measure in this scope.')


def me4(m, g):
    busy = collections.defaultdict(float)
    for d in m.sys.get('turn_duration', []):
        if d['_t'] is not None:
            busy[day(d['_t'])] += (d.get('durationMs') or 0) / 3.6e6
    think = collections.defaultdict(float)
    for t, r in _replies(m):
        think[day(t)] += min(r, 1800) / 3600
    days = fill_days(set(busy) | set(think))
    tb, tt = sum(busy.values()), sum(think.values())
    return card('ME4', "Who's the bottleneck: Claude's busy time or my think time?", 'T S W', [
        K(kpi('Claude busy', tb, 'hours'), kpi('Your think time', tt, 'hours', 'reply gaps, capped at 30 min each'),
          kpi('Claude ÷ you', tb / tt if tt else None, 'ratio')),
        BAR(days, [S('Claude busy', [r2(busy[d]) for d in days], 1), S('Your think time', [r2(think[d]) for d in days], 2)], 'hours', orient='v',
            title='Hours per day', labels=False)],
        empty=None if days else 'No turns to measure in this scope.')


def me5(m, g):
    first = rec = custom = total = 0
    for t in m.tools:
        tur = (t['res'] or {}).get('tur') or {}
        if t['name'] != 'AskUserQuestion' or not isinstance(tur.get('answers'), dict):
            continue
        for q in tur.get('questions') or []:
            ans = tur['answers'].get(q.get('question'))
            if ans is None:
                continue
            opts = [o.get('label') for o in q.get('options') or []]
            total += 1
            first += bool(opts) and ans == opts[0]
            rec += any('Recommended' in (o or '') and ans == o for o in opts)
            custom += ans not in opts
    plans = [t for t in m.tools if t['name'] == 'ExitPlanMode' and t['res']]
    ok = sum(1 for t in plans if not t['res']['is_error'])
    return card('ME5', "How do I answer Claude's questions and plans?", 'T', [
        K(kpi('Questions answered', total, 'count'), kpi('Picked the first option', share(first, total), 'pct'),
          kpi('Picked “(Recommended)”', share(rec, total), 'pct'), kpi('Wrote a custom answer', share(custom, total), 'pct'),
          kpi('Plans approved first time', share(ok, len(plans)), 'pct', f'{ok} of {len(plans)}')),
        BAR(['First option', '(Recommended)', 'Other listed option', 'Custom answer'],
            [S('Answers', [first, rec, max(0, total - first - custom - max(0, rec - first)), custom])], 'count')],
        empty=None if total or plans else 'No questions or plans in this scope.')


def me6(m, g):
    modes = collections.Counter(p['mode'] or 'not logged' for p in m.prompts)
    usd = collections.defaultdict(float)
    for t in m.turns:
        if t['kind'] == 'prompt':
            usd[t['mode'] or 'not logged'] += t['usd']
    ks = sorted(uniq(list(modes) + list(usd)), key=lambda k: (-modes[k], -usd[k]))
    plan_sessions = len({a['sid'] for a in m.atts if a['type'] == 'plan_mode'})
    n, tot = sum(modes.values()), sum(usd.values())
    ser = [dict(S(f'Prompts ({n:,})', [r1(share(modes[k], n)) for k in ks], 1), raw=[modes[k] for k in ks], rawUnit='count', rawName='Prompts'),
           dict(S(f'Cost ({f_usd(tot)})', [r1(share(usd[k], tot)) for k in ks], 2), raw=[r2(usd[k]) for k in ks], rawUnit='usd', rawName='Cost')]
    return card('ME6', 'Which modes do I work in, and how is spend split?', 'T P S', [
        hide(K(kpi('Sessions that used plan mode', plan_sessions, 'count'))),
        BAR(ks, ser, 'pct', orient='v', title='Each mode’s share of your prompts and of the cost of the turns they started', labels=False),
        hide(TABLE([('k', 'Mode', None), ('p', 'Prompts', 'count'), ('u', 'Cost', 'usd')],
                   [dict(k=k, p=modes[k], u=r2(usd[k])) for k in ks]))],   # the digest's numbers
        why='A mode whose cost share is bigger than its prompt share starts turns that cost more than your average prompt.',
        note='permissionMode is only logged by newer Claude Code versions; older prompts show as “not logged”.',
        empty=None if m.prompts else 'No prompts in this scope.')


# ------------------------------------------------------------------------------------------ TR: trends


def me7(m, g):
    grp = collections.defaultdict(lambda: dict(n=0, first=None, last=None))
    for c in m.errors:
        k = (c['err'] or 'client', c['errtext'] or '')
        a = grp[k]
        a['n'] += 1
        a['first'] = min(a['first'] or c['t0'], c['t0'])
        a['last'] = max(a['last'] or c['t0'], c['t0'])
    rows = [dict(e=k[0], m=clip(k[1], 90), n=v['n'], f=day(v['first']), l=day(v['last'])) for k, v in sorted(grp.items(), key=lambda kv: -kv[1]['n'])]
    return card('ME7', 'How often did sessions hit API errors?', 'T W', [
        K(kpi('API errors', len(m.errors), 'count')),
        TABLE([('e', 'Error', None), ('m', 'Message', None), ('n', 'Times', 'count'), ('f', 'First', None), ('l', 'Last', None)], rows)],
        empty=None if rows else 'No API errors in this scope.')


@functools.lru_cache(maxsize=None)
def _period(t, weekly):
    """The local day or ISO week of t. Cached, like day()."""
    lt = local(t)
    if weekly:
        y, w, _ = lt.isocalendar()
        return f'{y}-W{w:02d}'
    return lt.strftime('%Y-%m-%d')


def _trend_axis(m):
    """Trends run over active days, or active weeks past 45 days of data: idle stretches would only break the lines apart."""
    span = (m.real[-1]['t0'] - m.real[0]['t0']) / 86400
    weekly = span > 45
    return weekly, span, sorted({_period(c['t0'], weekly) for c in m.real})


def _period_name(k, weekly):
    if weekly:
        return f'week {k[-2:].lstrip("0")} of {k[:4]}'
    d = dt.date.fromisoformat(k)
    return f'{d:%b} {d.day}'


def tr1(m, g):
    if not m.real:
        return card('TR1', 'What drove my cost from day to day?', 'W', [])
    weekly, span, ks = _trend_axis(m)
    act = ks
    unit = 'week' if weekly else 'day'
    per = collections.defaultdict(lambda: dict(c=[], p=0, tools=0, err=0))
    for c in m.real:
        per[_period(c['t0'], weekly)]['c'].append(c)
    for p in m.prompts:
        per[_period(p['t'], weekly)]['p'] += 1
    for t in m.tools:
        if t['t']:
            a = per[_period(t['t'], weekly)]
            a['tools'] += 1
            a['err'] += bool(t['res'] and t['res']['is_error'])
    # cost = prompts × API calls per prompt × cost per API call; dividing each by its overall average keeps that product exact
    D, N = len(act), sum(len(per[k]['c']) for k in act)
    U, P = sum(c['usd'] for k in act for c in per[k]['c']), sum(per[k]['p'] for k in act)
    avg = dict(u=U / D, p=P / D, cpp=N / P if P else None, cpc=U / N)
    f = {}
    for k in act:
        a = per[k]
        u, n, p = sum(c['usd'] for c in a['c']), len(a['c']), a['p']
        f[k] = dict(u=u, p=p, cpp=n / p if p else None, cpc=u / n, n=n)
    x = lambda k, key: r2(f[k][key] / avg[key]) if k in f and f[k][key] is not None and avg[key] else None
    raw = lambda key, rnd: [rnd(f[k][key]) if k in f and f[k][key] is not None else None for k in ks]
    ser = [dict(S(f'Cost per {unit}', [x(k, 'u') for k in ks]), slot=0, raw=raw('u', r2), rawUnit='usd'),     # the product, in neutral ink
           dict(S('Prompts', [x(k, 'p') for k in ks], 1), raw=raw('p', int), rawUnit='count'),
           dict(S('API calls per prompt', [x(k, 'cpp') for k in ks], 2), raw=raw('cpp', r1), rawUnit='count'),
           dict(S('Cost per API call', [x(k, 'cpc') for k in ks], 3), raw=raw('cpc', lambda v: round(v, 3)), rawUnit='usd')]
    ins = None
    top = max(act, key=lambda k: f[k]['u'])
    if D > 1 and P and f[top]['p']:
        t = f[top]
        parts = [('prompts', x(top, 'p'), f"{t['p']} prompts"), ('API calls per prompt', x(top, 'cpp'), f"{t['cpp']:.0f} API calls per prompt"),
                 ('cost per API call', x(top, 'cpc'), f"{f_usd(t['cpc'])} per API call")]
        lead = max(parts, key=lambda q: q[1])
        ins = (f"Costliest {unit}: {_period_name(top, weekly)}, {f_usd(t['u'])} ({x(top, 'u'):.1f}× an average {unit}). "
               + ', '.join(f'{q[2]} ({q[1]:.1f}×)' for q in parts) + f', so {lead[0]} drove it most.')
    rows = []
    for k in act[::-1]:
        a = per[k]
        rows.append(dict(k=k, u=r2(f[k]['u']), p=f[k]['p'], cpp=r1(f[k]['cpp']) if f[k]['cpp'] else None, cpc=round(f[k]['cpc'], 3),
                         x=round(mean([c['ctx'] for c in a['c']])), h=r1(hit_rate(a['c'])), e=r1(share(a['err'], a['tools'])),
                         w=sum(c['rewritten'] for c in a['c'] if c['miss'])))
    return card('TR1', f'What drove my cost from {unit} to {unit}?', 'W', [
        LINE(ks, ser, 'x', x_kind='cat', title=f'Cost and its three drivers, each as a multiple of an average {unit} (1×)',
             ref_lines=[dict(axis='y', value=1)], indexed=True),
        TABLE([('k', unit.title(), None), ('u', 'Cost', 'usd'), ('p', 'Prompts', 'count'), ('cpp', 'API calls/prompt', 'count'), ('cpc', 'Cost/API call', 'usd'),
               ('x', 'Context/API call', 'tokens'), ('h', 'Hit rate', 'pct'), ('e', 'Tool error rate', 'pct'), ('w', 'Re-written', 'tokens')], rows, limit=14)],
        why=f'Cost = prompts × API calls per prompt × cost per API call, so each {unit} the cost line is the other three multiplied: '
            'the one furthest above 1× is what drove the spend.',
        insight=ins,
        note=f'Active {unit}s only ({len(ks)} in {span:.0f} days of data). Hover a point for the real values. '
             'Cost per API call rises with the context each call re-reads (TR2).')


def tr2(m, g):
    if not m.real:
        return card('TR2', 'What changed when my setup changed?', 'W', [])
    weekly, span, ks = _trend_axis(m)
    act = ks
    base, skills, ctx = (collections.defaultdict(list) for _ in range(3))
    for s in m.sessions.values():
        if s['baseline']:
            base[_period(s['start'], weekly)].append(s['baseline'])
    for a in m.atts:
        if a['type'] == 'skill_listing' and a['a'].get('isInitial') and a['t']:
            skills[_period(a['t'], weekly)].append(a['a'].get('skillCount') or len(a['a'].get('names') or []))
    first, calls, main = {}, collections.Counter(), collections.defaultdict(collections.Counter)
    for c in m.real:
        k = _period(c['t0'], weekly)
        if c['ver']:
            calls[c['ver']] += 1
            first[c['ver']] = min(first.get(c['ver'], k), k)
        if not c['is_sub']:
            ctx[k].append(c['ctx'])
            main[k][model_name(c['model'])] += 1
    changes = collections.defaultdict(list)
    for v, k in sorted(first.items(), key=lambda kv: kv[1]):
        if k != act[0] and calls[v] >= 20:                 # a new Claude Code version (one-off runs are noise)
            changes[k].append(f'v{v}')
    seen = set()
    for k in act:                                          # a new main-thread model that took over most of that period's calls
        tot = sum(main[k].values())
        for mdl, n in main[k].most_common():
            if mdl not in seen and k != act[0] and n >= 20 and n >= tot / 2:
                changes[k].append(mdl)
        seen |= set(main[k])
    avg = lambda d, k: round(mean(d[k])) if d.get(k) else None
    ser = [S('Context per API call (main thread)', [avg(ctx, k) for k in ks], 1),
           dict(S('Start-up context', [avg(base, k) for k in ks], 2), raw=[avg(skills, k) for k in ks], rawUnit='count', rawName='Skills listed',
                rawLabel='skills listed')]
    refs = [dict(axis='x', value=ks.index(k), label=' · '.join(v)) for k, v in sorted(changes.items())]
    ins = None
    best = None
    for k in changes:                                      # the change with the biggest before/after jump in context per call
        pre = [x for q in act if q < k for x in ctx.get(q, [])]
        post = [x for q in act if q >= k for x in ctx.get(q, [])]
        if pre and post:
            r = mean(post) / mean(pre)
            if best is None or abs(math.log(r)) > abs(math.log(best[1])):
                best = (k, r, mean(pre), mean(post))
    if best and abs(math.log(best[1])) > math.log(1.25):
        k, r, a0, a1 = best
        b0 = [x for q in act if q < k for x in base.get(q, [])]
        b1 = [x for q in act if q >= k for x in base.get(q, [])]
        ins = (f"Since {_period_name(k, weekly)} ({' · '.join(changes[k])}), each main-thread API call carries {r:.1f}× the context "
               f"({f_tok(a1)} against {f_tok(a0)} before)"
               + (f", while start-up context went from {f_tok(mean(b0))} to {f_tok(mean(b1))}." if b0 and b1 else '.'))
    ver = collections.defaultdict(lambda: dict(first=None, last=None, sess=set(), base=[]))
    for s in m.sessions.values():
        for v in sorted(s['versions'], key=str):          # sorted: set order changes from run to run
            a = ver[v]
            a['first'] = min(a['first'] or s['start'], s['start'])
            a['last'] = max(a['last'] or s['end'], s['end'])
            a['sess'].add(s['sid'])
            if s['baseline']:
                a['base'].append(s['baseline'])
    vrows = [dict(v=k, f=day(a['first']), l=day(a['last']), s=len(a['sess']), b=round(mean(a['base'])) if a['base'] else None)
             for k, a in sorted(ver.items(), key=lambda kv: kv[1]['first'])]
    return card('TR2', 'What changed when my setup changed?', 'W', [
        LINE(ks, ser, 'tokens', x_kind='cat', title=f'Context per API call and at session start, per active {"week" if weekly else "day"}',
             ref_lines=refs or None),
        TABLE([('v', 'Claude Code version', None), ('f', 'First seen', None), ('l', 'Last seen', None), ('s', 'Sessions', 'count'),
               ('b', 'Avg start-up context', 'tokens')], vrows)],
        why='Start-up context is what every session carries before your first prompt; context per API call is what each call re-reads, '
            'so it sets the cost per API call in TR1.',
        insight=ins,
        note=f'Vertical lines mark the first {"week" if weekly else "day"} a new Claude Code version ran (20+ API calls) or a new model '
             'took over most main-thread calls. '
             'Hover the start-up line for the number of skills listed.',
        empty=None if ctx or base else 'No sessions in this scope.')


# ------------------------------------------------------------------------------------------ assembly

SECTIONS = [('OV', 'Overview & cost'), ('CX', 'Context & Caching'), ('SE', 'Sessions'), ('EX', 'Plugins, MCP, Tools & Hooks'),
            ('OUT', 'Output & outcomes'), ('ME', 'Your working patterns'), ('SV', 'What would it have saved?'), ('TR', 'Trends')]


CARDS = [ov1, ov2, ov3, ov4, ov5, ov6, ov7, ov8, sv1, sv2, sv3, sv4, sv5, sv6, sv7, sv8, cx1, cx2, cx3, cx4, cx5, cx6, cx7, cx8, cx9, cx10, cx11, cx12, ca_miss_cost, ca_miss_causes, ca_miss_traces,
         se1, se2, se3, se4, se5, se6,
         ex1, ex2, ex3, ex4, ex5, ex6, ex7, ex8, ex9, ex10, ex11, ex12, ex13, ex14, ex15,
         out1, out2, out3, out4,
         me1, me2, me3, me4, me5, me6, me7, tr1, tr2]

HEADLINE_ORDER = [('SV1', 'info'), ('OV3', 'info'), ('CX8', 'warn'), ('CX3', 'info'), ('OV5', 'info'), ('EX2', 'warn'),
                  ('EX5', 'warn'), ('OV8', 'warn'), ('CX10', 'info'), ('OV7', 'info'), ('CX4', 'info'),
                  ('EX9', 'info'), ('EX8', 'info'), ('CX9', 'info'), ('CX7', None), ('OV4', 'info')]


def headline(m, cards):
    per_day = collections.defaultdict(float)
    for c in m.real:
        per_day[day(c['t0'])] += c['usd']
    days = fill_days(per_day)
    f = m.facts
    hero = {'label': 'Cost at API list prices', 'value': r2(m.usd), 'unit': 'usd',
            'sub': f"{len(m.real):,} API calls · {len(m.sessions)} sessions" if days else 'no API calls'}
    if len(days) > 1:               # the daily spend under the number (the date range is in the page header)
        hero['spark'] = {'x': days, 'values': [r2(per_day.get(d, 0.0)) for d in days]}
    ov6 = {i['label']: i['value'] for b in (cards.get('OV6') or {}).get('blocks', []) if b.get('kind') == 'kpis' for i in b['items']}
    kpis = [kpi('Sessions', len(m.sessions), 'count'), kpi('Your prompts', len(m.prompts), 'count'),
            kpi('Median session', ov6.get('Median session'), 'usd'), kpi('Median prompt', ov6.get('Median prompt'), 'usd'),
            kpi('Active hours', f.get('active_h'), 'hours'), kpi('Cache hit rate', f.get('hit_rate'), 'pct'),
            kpi('Re-read per output token', f.get('amp'), 'ratio'), kpi('Tokens re-written by misses', f.get('miss_tok'), 'tokens'),
            kpi('Subagent share of spend', f.get('sub_share'), 'pct'), kpi('Median peak context', f.get('peak_med'), 'tokens')]
    ins = []
    for cid, level in HEADLINE_ORDER:
        c = cards.get(cid)
        if c and not c.get('insight') and c.get('insights'):
            c = dict(c, insight=c['insights'][0])
        if not c or not c.get('insight') or c.get('empty'):
            continue
        lv = level
        if cid == 'EX5' and 'looks fixed' in c['insight']:
            lv = 'info'
        if lv is None:
            lv = 'good' if ('pays for itself' in c['insight'] or 'doing its job' in c['insight']) else 'warn'
        ins.append({'text': c['insight'], 'card': cid, 'level': lv})
        if len(ins) >= 10:
            break
    return {'hero': hero, 'kpis': kpis, 'insights': ins, 'days': r2(span_days(m))}   # days: what “all time” covers (30-day projections)


def add_refs(results):
    ref = results.get('all')
    if not ref:
        return
    for sid, res in results.items():
        if sid == 'all':
            continue
        for cid, c in res['cards'].items():
            rc = ref['cards'].get(cid)
            if not rc:
                continue
            rk = {i['label']: i['value'] for b in rc['blocks'] if b['kind'] == 'kpis' for i in b['items']}
            for b in c['blocks']:
                if b['kind'] == 'kpis':
                    for i in b['items']:
                        if i['label'] in rk and rk[i['label']] is not None:
                            i['ref'], i['refLabel'] = rk[i['label']], 'All projects'
        hk = {i['label']: i['value'] for i in ref['headline']['kpis']}
        for i in res['headline']['kpis']:
            if hk.get(i['label']) is not None:
                i['ref'], i['refLabel'] = hk[i['label']], 'All projects'


def clean(x):
    """Round floats and drop NaN/inf so the JSON stays small and valid."""
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
        return round(x, 2) if abs(x) < 100 else round(x)
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    return x


def build(src, prices, log):
    scopes = src.scopes()
    TRACES.clear()
    all_m = Model(src, src.recs, prices)
    all_m.window = span_days(all_m)
    order = sorted(uniq(c['model'] for c in all_m.real), key=lambda x: -sum(c['usd'] for c in all_m.real if c['model'] == x))
    slots = {mdl: i + 1 for i, mdl in enumerate(order[:8])}
    snaps = [dict(a, ver=all_m.sessions.get(a['sid'], {}).get('versions') or set()) for a in all_m.atts if a['type'] == 'prompt_snapshot']
    try:                                 # outside any card, so guarded like one: it must not sink the report
        where = where_used(all_m)
    except Exception as e:
        log(f'  ! what loads where it is unused could not be computed: {e}')
        if os.environ.get('USAGE_REPORT_DEBUG'):
            traceback.print_exc()
        where = {}
    results, facts = {}, {}
    for sc in scopes:
        t0 = time.time()
        m = all_m if sc['kind'] == 'all' else Model(src, [d for d in src.recs if d['_proj'] in sc['dirs']], prices)
        m.window = all_m.window
        g = G(src, prices, sc, slots)
        g.snaps = snaps                  # prompt snapshots from every project: a scope may have none of its own
        g.where = where                  # what is used in some projects and only loaded in others (all projects)
        cards = {}
        for f in CARDS:
            try:
                c = f(m, g)
            except Exception as e:  # one broken card must not sink the report
                cid = INTERNAL_IDS.get(f.__name__) or f.__name__.upper().replace('_PLACEHOLDER', '')
                log(f'  ! {cid} failed for {sc["label"]}: {e}')
                if os.environ.get('USAGE_REPORT_DEBUG'):
                    traceback.print_exc()
                c = card(cid, cid, '', [], empty=f'Could not compute this card: {e}')
            cards[c['id']] = c
        merge_misses(cards)
        results[sc['id']] = {'headline': headline(m, cards), 'cards': cards}
        facts[sc['id']] = m.facts
        log(f'  {sc["label"]}: {len(m.real):,} calls, {f_usd(m.usd)} ({time.time() - t0:.1f}s)')
        del m                       # freed before the next scope's model is built, not while it is (peak memory)
    add_refs(results)
    return scopes, results, all_m


def flat_blocks(c):
    """A card's blocks, with the blocks inside collapsible (tabs) sections listed too."""
    out = []
    for b in (c or {}).get('blocks', []):
        out.append(b)
        if b.get('kind') == 'tabs':
            out += [x for t in b['tabs'] for x in ([t['block']] if 'block' in t else t.get('blocks', []))]
    return out


INTERNAL_IDS = {'ca_miss_cost': '_CA_COST', 'ca_miss_causes': '_CA_CAUSES', 'ca_miss_traces': '_CA_TRACE'}   # folded into CX8


def merge_misses(cards):
    """CX8 is the cache-miss card: the cost tiles (what a miss cost against a hit), the cause insight and the step-by-step traces
    are built by their own functions and folded into it. The cause breakdown stays in the data (digest) but is not shown."""
    cost, causes, trace = cards.pop('_CA_COST', None), cards.pop('_CA_CAUSES', None), cards.pop('_CA_TRACE', None)
    a = cards.get('CX8')
    if not a or a.get('empty'):
        return
    kp = next((b for b in a['blocks'] if b.get('kind') == 'kpis'), None)
    for extra in (cost, causes):
        if not extra or extra.get('empty'):
            continue
        for b in extra['blocks']:
            if b.get('kind') == 'kpis' and kp is not None and extra is cost:
                kp['items'] = kp['items'] + b['items']          # extra cost of misses · share of spend · per miss
            else:
                b['hidden'] = True                              # kept for the digest and the skills, not drawn
                a['blocks'].append(b)
    ins = [a.pop('insight', None)]
    if causes and causes.get('insight'):
        ins.append(causes['insight'])
    if trace and not trace.get('empty'):
        desc = ' '.join(x for x in (trace.get('why'), trace.get('note')) if x)
        sec = TABS([('Step by step', trace['blocks'])], title='Step by step: what happened before each costly miss',
                   sub='a deep breakdown of every costly miss, to analyse what could have been avoided', desc=desc)
        if sec:
            a['blocks'].append(sec)
        ins.append(trace.get('insight'))
    a['insights'] = [x for x in ins if x]


def write_csvs(m, out):
    def w(name, header, rows):
        with open(layout.data(out, name), 'w', newline='', encoding='utf-8') as fh:
            wr = csv.writer(fh)
            wr.writerow(header)
            wr.writerows(rows)
    w('calls.csv', ['time', 'session', 'project', 'thread', 'model', 'effort', 'skill', 'input', 'output', 'thinking', 'cache_read',
                    'cache_write_5m', 'cache_write_1h', 'context', 'usd', 'gap_s', 'rewritten', 'miss_cause', 'tools'],
      [[local(c['t0']).isoformat(timespec='seconds'), c['sid'], c['proj'], c['agent'] or 'main', c['model'], c['effort'], c['skill'],
        c['inp'], c['out'], c['think'], c['cr'], c['cw5'], c['cw1'], c['ctx'], round(c['usd'], 5),
        round(c['gap']) if c['gap'] is not None else '', c['rewritten'] if c['miss'] else 0, m.miss_cause(c) if c['miss'] else '',
        ' '.join(b.get('name') or '' for b, _ in c['tools'])] for c in m.real])
    w('tool_calls.csv', ['time', 'session', 'thread', 'tool', 'phase', 'bash_type', 'detail', 'latency_s', 'is_error', 'result_chars'],
      [[local(t['t']).isoformat(timespec='seconds') if t['t'] else '', t['sid'], t['agent'] or 'main', t['name'], t['phase'], t['bash'] or '',
        tool_detail(t), round(t['latency'], 2) if t['latency'] is not None else '', int(bool(t['res'] and t['res']['is_error'])),
        t['res']['chars'] if t['res'] else ''] for t in m.tools])
    w('sessions.csv', ['session', 'project', 'title', 'kind', 'start', 'end', 'wall_s', 'active_s', 'prompts', 'commands', 'api_calls', 'usd',
                       'baseline', 'peak_context', 'hit_rate'],
      [[s['sid'], s['proj'], s['title'], s['kind'], local(s['start']).isoformat(timespec='seconds'), local(s['end']).isoformat(timespec='seconds'),
        round(s['wall']), round(s['active']), s['prompts'], s['commands'], s['n_calls'], round(s['usd'], 4), s['baseline'] or '', s['peak'],
        r1(hit_rate(s['calls'])) if s['calls'] else ''] for s in sorted(m.sessions.values(), key=lambda s: s['start'])])
    iso = lambda t: local(t).isoformat(timespec='seconds')
    w('cache_misses.csv', ['request_start', 'session', 'project', 'thread', 'model', 'cause', 'cache_state', 'previous_reply_end', 'idle_s',
                           'cache_lifetime_s', 'cache_expired_at', 'first_token_after_s', 'trigger',
                           'context_before', 'cache_read', 'rewritten', 'extra_usd'],
      [[iso(c['start']), c['sid'], c['proj'], c['agent'] or 'main', c['model'], r['cause'], 'expired' if expired(r) else 'alive',
        iso(c['prev']['t1']), round(idle_of(c), 1), c['ttl'],
        iso(cache_expiry(c)), round(c['t0'] - c['start'], 1), (_trigger(m, c) or (0, ''))[1], c['prev']['ctx'], c['cr'],
        c['rewritten'], round(r['usd'], 4)] for r in sorted(_miss_rows(m), key=lambda r: r['c']['start']) for c in [r['c']]])
    w('subagents.csv', ['agent', 'session', 'type', 'description', 'model', 'calls', 'start', 'duration_s', 'peak_context', 'start_context',
                        'returned_chars', 'usd', 'misses'],
      [[s['aid'], s['sid'], s['type'], s['desc'], s['model'], s['n_calls'], local(s['start']).isoformat(timespec='seconds'),
        round(s['end'] - s['start']), s['peak'], s['cold'], s['returned_chars'] or '', round(s['usd'], 4), s['misses']]
       for s in sorted(m.subs.values(), key=lambda s: s['start'])])


def md_cell(v, unit=None):
    if v is None:
        return ''
    if unit == 'usd' and isinstance(v, (int, float)):
        return f_usd(v)
    if unit == 'pct' and isinstance(v, (int, float)):
        return f_pct(v)
    if unit in ('tokens', 'chars') and isinstance(v, (int, float)):
        return f_tok(v)
    if unit in ('sec', 'min', 'ms') and isinstance(v, (int, float)):
        return f_span(v * (60 if unit == 'min' else 0.001 if unit == 'ms' else 1))
    if isinstance(v, float):
        return f'{v:,.2f}'
    if isinstance(v, int):
        return f'{v:,}'
    return str(v).replace('|', '/').replace('\n', ' ')


def md_card(c, full=True, rows=6):
    """One report card as compact markdown: the numbers, not the styling."""
    out = [f"### {c['id']} — {c['q']}" + (f" (shown inside {c['alias']}; citing {c['id']} opens {c['alias']})" if c.get('alias') else
                                          ' (not shown in the report: use its numbers, but do not cite it)' if c.get('hidden') else '')]
    if c.get('empty'):
        return out + [f"(no data: {c['empty']})", '']
    blocks = []
    for b in c.get('blocks', []):          # a tabs block holds ordinary blocks
        blocks.extend([x for t in b.get('tabs', []) for x in ([t['block']] if 'block' in t else t.get('blocks', []))]
                      if b.get('kind') == 'tabs' else [b])
    for b in blocks:
        k = b.get('kind')
        if k == 'kpis':
            out.append('- ' + ' · '.join(f"{i['label']}: {md_cell(i['value'], i.get('unit'))}"
                                          + (f" all time, ≈ {md_cell(i['month'], 'usd')} per 30 days" if i.get('month') is not None else '')
                                          + (f" ({i['sub']})".replace('\n', ' · ') if i.get('sub') else '') for i in b['items']))
        elif not full:
            continue
        elif k == 'table':
            cols = b['columns'][:7]
            out.append(f"Table: {b.get('title') or 'rows'} ({len(b['rows'])} rows{', first ' + str(rows) if len(b['rows']) > rows else ''})")
            out.append('| ' + ' | '.join(col['label'] for col in cols) + ' |')
            out.append('|' + '---|' * len(cols))
            for r in b['rows'][:rows]:
                out.append('| ' + ' | '.join(md_cell(r.get(col['key']), col.get('unit')) for col in cols) + ' |')
        elif k == 'chart':
            ch = b['chart']
            if ch['type'] in ('bar', 'pie'):
                cats, ser = ch['categories'], ch['series']
                tot = [sum((s_['values'][i] or 0) for s_ in ser) for i in range(len(cats))]
                top = sorted(range(len(cats)), key=lambda i: -tot[i])[:8]
                val = ((lambda i: ' / '.join(md_cell(s_['values'][i], s_.get('unit') or ch.get('unit')) for s_ in ser))   # side by side: each series
                       if len(ser) > 1 and ch['type'] == 'bar' and not ch.get('stacked') else (lambda i: md_cell(tot[i], ch.get('unit'))))
                out.append(f"Chart: {ch.get('title') or 'bar'} — " + '; '.join(f"{cats[i]}: {val(i)}" for i in top)
                           + (f" (series: {', '.join(s_['name'] for s_ in ser)})" if len(ser) > 1 else ''))
            elif ch['type'] == 'line':
                for s_ in ch['series'][:4]:
                    real = ch.get('indexed') and s_.get('raw')        # an indexed series: report its real values, not the multiples
                    u_ = s_.get('rawUnit') if real else ch.get('unit')
                    vals = [(x, v) for x, v in zip(ch['x'], s_['raw'] if real else s_['values']) if v is not None]
                    if vals:
                        hi = max(vals, key=lambda xv: xv[1])
                        out.append(f"Chart: {ch.get('title') or 'line'} / {s_['name']} — first {md_cell(vals[0][1], u_)} at {vals[0][0]}, "
                                   f"last {md_cell(vals[-1][1], u_)} at {vals[-1][0]}, max {md_cell(hi[1], u_)} at {hi[0]}")
            elif ch['type'] == 'scatter':
                out.append(f"Chart: {ch.get('title') or 'scatter'} — {len(ch['points'])} points")
        elif k == 'list':
            out.append(f"{b.get('title') or 'List'}: " + ', '.join(str(x) for x in b['items'][:30]))
        elif k == 'text':
            out.append(b['text'])
    for t in ([c['insight']] if c.get('insight') else []) + list(c.get('insights') or []):
        out.append(f"Insight: {t}")
    if c.get('note') and full:
        out.append(f"Note: {c['note']}")
    return out + ['']


def md_config(config):
    """The current setup in a few lines: identical settings files grouped, hooks as event → command."""
    home = config.get('home') or os.path.expanduser('~')
    short = lambda p: p.replace(home, '~')
    try:        # a hook apply.py installed in its fast form reads as the `python3 "<script>" args` it came from
        from apply import plain_command
    except Exception:
        plain_command = str
    files = {}
    for group in ('user_settings', 'user_local_settings'):
        files.update(config.get(group) or {})
    for proj in (config.get('project_settings') or {}).values():
        files.update(proj)
    groups = collections.OrderedDict()
    for path, js in files.items():
        groups.setdefault(json.dumps(js, sort_keys=True), []).append(path)
    L = ['Settings files (identical files grouped):']
    for key, paths in groups.items():
        js = json.loads(key)
        paths = sorted(paths, key=len)
        parts = []
        for k, v in js.items():
            if k == 'hooks' and isinstance(v, dict):
                hk = []
                for ev, grps in v.items():
                    for g_ in grps or []:
                        for h_ in (g_ or {}).get('hooks') or []:
                            hk.append(f"{ev}{'[' + g_['matcher'] + ']' if g_.get('matcher') not in (None, '', '*') else ''} → {clip(plain_command(str(h_.get('command'))), 160)}")
                parts.append('hooks: ' + '; '.join(hk))
            elif isinstance(v, (dict, list)):
                parts.append(f'{k}: {json.dumps(v, ensure_ascii=False)[:160]}')
            else:
                parts.append(f'{k}={clip(str(v), 120)}')
        more = f" (same in {len(paths) - 1} more: {', '.join(short(p_) for p_ in paths[1:])})" if len(paths) > 1 else ''
        L.append(f"- {short(paths[0])}{more}: " + (' · '.join(parts) or 'empty'))
    if not any('statusLine' in json.loads(k) for k in groups):
        L.append('- No statusLine is configured in any of these files.')
    mcp = config.get('mcp_servers') or {}
    L.append('MCP servers configured: user scope: ' + (', '.join(f'{k} ({v})' for k, v in (mcp.get('user') or {}).items()) or 'none')
             + ''.join(f"; {short(pth)}: " + ', '.join((d.get('servers') or {}).keys()) for pth, d in (mcp.get('projects') or {}).items())
             + '. Servers connected without a config entry (browser extension, claude.ai connectors) appear only in EX2.')
    L.append('Plugins: ' + (', '.join(f"{k} ({(v[0] or {}).get('scope', '?')})" for k, v in (config.get('installed_plugins') or {}).items()) or 'none'))
    inv = config.get('skills_inventory') or {}
    L.append('Skills by source (anything in EX2 not listed here ships with Claude Code):')
    L.append(f"- personal ({config.get('claude_dir') or '~/.claude'}/skills): " + (', '.join(x['name'] for x in inv.get('user_skills') or []) or 'none'))
    L.append('- synced from your account (listed as anthropic-skills:<name>): ' + (', '.join(inv.get('synced_skills') or []) or 'none'))
    seen = {}
    for proj, sk in (inv.get('project_skills') or {}).items():
        seen.setdefault(', '.join(sorted(x['name'] for x in sk)), []).append(proj)
    for names, projs in seen.items():
        L.append(f"- project skills/commands in {', '.join(projs)}: {names}")
    for name, sk in (inv.get('plugin_skills') or {}).items():
        if sk:
            L.append(f'- plugin {name}: ' + ', '.join(sk))
    if inv.get('skills_dir_plugins'):
        L.append('- skills-dir plugins: ' + ', '.join(inv['skills_dir_plugins']))
    return L


def write_digest(report, config, out):
    """digest.md: every number in the report, compactly, for Claude to read instead of the transcripts."""
    meta, data = report['meta'], report['data']
    sec_title = dict((s_['id'], s_['title']) for s_ in report['sections'])
    allc = data['all']['cards']
    L = [f"# Claude Code usage digest", '',
         f"Generated {meta['generated']} from {meta['subtitle']}; period {meta['range']['start']} → {meta['range']['end']}.",
         'Every figure below was counted from the local transcripts by usage_report.py. Cite questions by their ID (e.g. CX8).',
         'Dollars are API list-price equivalents. SV cards are theoretical savings "if applied from the first day": each is given all time '
         '(the analysed days) and per 30 days (the same pace, projected); levers overlap.', '',
         '## Scopes', '']
    for sc in report['scopes']:
        f = data.get(sc['id'], {}).get('headline', {}).get('hero', {})
        L.append(f"- `{sc['id']}` {sc['label']} ({sc['kind']}): {f_usd(f.get('value'))}")
    L += ['', '## Headline (all projects)', '']
    hd = data['all']['headline']
    L.append('- ' + ' · '.join(f"{i['label']}: {md_cell(i['value'], i.get('unit'))}" for i in hd['kpis']))
    for i in hd['insights']:
        L.append(f"- [{i['card']}] {i['text']}")
    L += ['', '## Every question, all projects', '']
    for sid, title in sec_title.items():
        ids = sorted((k for k in allc if section_of(allc[k]) == sid), key=lambda k: int(re.sub(r'\D', '', k) or 0))
        if not ids:
            continue
        L += [f'## {sid} — {title}', '']
        for cid in ids:
            L += md_card(allc[cid])
    tr = report.get('traces') or {}
    ids = next((b['ids'] for b in flat_blocks(allc.get('CX8', {})) if b['kind'] == 'traces'), [])
    if ids:
        L += ['## Costliest cache misses, step by step (CX8)', '']
        for tid in ids[:8]:
            t = tr.get(tid) or {}
            L.append(f"- {f_usd(t.get('usd'))} · {t.get('cause')} · {t.get('session')} · {t.get('thread')}: " + ' '.join(t.get('story') or []))
        L.append('')
    L += ['## Other scopes (headline numbers and insights only; scopes under 2% of spend left out)', '']
    total = (data['all'].get('headline') or {}).get('hero', {}).get('value') or 0
    for sc in report['scopes']:
        hero_ = data.get(sc['id'], {}).get('headline', {}).get('hero', {})
        if sc['id'] == 'all' or (total and (hero_.get('value') or 0) < 0.02 * total):
            continue
        cards = data.get(sc['id'], {}).get('cards', {})
        hero = data.get(sc['id'], {}).get('headline', {}).get('hero', {})
        L += [f"### Scope `{sc['id']}` — {sc['label']} ({f_usd(hero.get('value'))})", '']
        for cid in ('OV2', 'OV3', 'SV1', 'CX7', 'CX8', 'CX1', 'EX2', 'EX5', 'OV8'):
            c = cards.get(cid)
            if c and not c.get('empty'):
                kp = next((b for b in c['blocks'] if b['kind'] == 'kpis'), None)
                line = f"- {cid}: " + (' · '.join(f"{i['label']}: {md_cell(i['value'], i.get('unit'))}" for i in kp['items']) if kp else '')
                if c.get('insight'):
                    line += f" — {c['insight']}"
                L.append(line)
        L.append('')
    L += ['## Current setup (secrets removed; every detail is in config.json)', ''] + md_config(config) + ['']
    with open(layout.data(out, 'digest.md'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L))


def render(out, template, log=print):
    """report.html from metrics.json plus the insights and optimizations Claude wrote (if any). Fast: nothing is recomputed."""
    with open(layout.data(out, 'metrics.json'), encoding='utf-8') as fh:
        report = json.load(fh)
    for name in ('insights', 'optimizations'):
        path = os.path.join(out, name + '.json')
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding='utf-8') as fh:
                js = json.load(fh)
        except ValueError as e:
            log(f'  ! {name}.json is not valid JSON ({e}); left out of the report')
            continue
        js['stale'] = (js.get('source') or {}).get('metrics_generated') not in (None, report['meta'].get('generated'))
        report[name] = js
    report['meta']['out'] = out
    home = os.path.expanduser('~')
    tilde = lambda p_: '~' + p_[len(home):] if p_.startswith(home + os.sep) and re.fullmatch(r'[\w./~-]+', p_) else shlex.quote(p_)
    cd_ = report['meta'].get('claude_dir')
    if not report['meta'].get('shared'):       # someone else's report (share.py unpack): its optimizations are for their machine
        report['meta']['apply'] = (f"python3 {tilde(os.path.join(HERE, 'apply.py'))} --dir {tilde(out)}"
                                   + (f" --claude-dir {tilde(cd_)}" if cd_ and cd_ != os.path.join(home, '.claude') else ''))
    with open(template, encoding='utf-8') as fh:
        tpl = fh.read()
    marker = '/*__REPORT_DATA__*/null'
    if marker not in tpl:
        sys.exit('Template is missing the /*__REPORT_DATA__*/null marker.')
    # every '<' escaped (only strings can hold one), so no data can end or re-open the <script> element: '</script>' and
    # '<!-- <script' alike
    blob = json.dumps(report, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    path = os.path.join(out, 'report.html')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(tpl.replace(marker, blob))
    return path, report


def safe_console():
    """Never crash on a console that can't show a character (e.g. a Windows code page): replace it instead."""
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass


def main(argv=None):
    safe_console()
    ap = argparse.ArgumentParser(description='Claude Code usage report (see QUESTIONS.md).')
    ap.add_argument('--claude-dir', metavar='DIR',
                    help='the folder Claude Code keeps its data in, with projects/ inside (default $CLAUDE_CONFIG_DIR, else ~/.claude)')
    ap.add_argument('--projects', metavar='DIR', help='transcripts folder, if not <claude-dir>/projects')
    ap.add_argument('--out', metavar='DIR', help='output folder (default $CLAUDE_USAGE_OUT, else <claude-dir>-usage, e.g. ~/.claude-usage)')
    ap.add_argument('--since', help='first day to include, YYYY-MM-DD (local time)')
    ap.add_argument('--until', help='last day to include, YYYY-MM-DD (local time)')
    ap.add_argument('--days', type=int, help=f'only the last N days, counted back from --until when given (default {DEFAULT_DAYS})')
    ap.add_argument('--all', action='store_true', help='every transcript on disk, however old (instead of the last days)')
    ap.add_argument('--prices', default=os.path.join(HERE, 'prices.json'), help='USD per million tokens, per model')
    ap.add_argument('--template', default=os.path.join(HERE, 'report_template.html'), help='HTML template to fill')
    ap.add_argument('--no-csv', action='store_true', help='skip the CSV exports')
    ap.add_argument('--open', action='store_true', help='open the report in your browser when done')
    ap.add_argument('--tab', choices=['report', 'insights', 'optimizations'], help='with --open: the tab to open on')
    ap.add_argument('--where', action='store_true', help='print the Claude Code folder and the output folder this would use, then exit')
    ap.add_argument('--quiet', action='store_true', help='no progress output')
    for skill_flag in ('--no-insights', '--no-open'):            # /claude-usage:report's own flags: accepted and ignored here
        ap.add_argument(skill_flag, action='store_true', help=argparse.SUPPRESS)
    ap.add_argument('--render', action='store_true',
                    help='only rebuild report.html from <out>/data/metrics.json + insights.json + optimizations.json (no recomputing)')
    a = ap.parse_args(argv)
    cdir = claude_dir(a.claude_dir)
    a.projects = os.path.abspath(os.path.expanduser(a.projects)) if a.projects else os.path.join(cdir, 'projects')
    a.out = os.path.abspath(os.path.expanduser(a.out)) if a.out else default_out(cdir)
    if a.where:
        print(f'claude_dir={cdir}' + ('' if os.path.isdir(a.projects) else '   (no projects/ folder here: pass --claude-dir)'))
        print(f'out={a.out}')
        print(f'data={layout.data_dir(a.out)}')
        return
    url = lambda p_: pathlib.Path(p_).as_uri() + (f'#tab={a.tab}' if a.tab else '')
    log = (lambda *x: None) if a.quiet else (lambda *x: print(*x, file=sys.stderr))
    old = legacy_out(cdir)
    if old and os.path.abspath(old) != a.out:
        log(f'Note: reports now go to {tilde(a.out)}; an older one is still in {tilde(old)} (safe to delete).')
    layout.migrate(a.out, log)
    if a.render:
        if not os.path.exists(layout.data(a.out, 'metrics.json')):
            sys.exit(f'No metrics.json in {layout.data_dir(a.out)}: run the full report first.')
        path, rep_ = render(a.out, a.template, log)
        log(f"Rendered → {path}" + (f" (insights: {len((rep_.get('insights') or {}).get('insights') or [])}, "
                                     f"optimizations: {len((rep_.get('optimizations') or {}).get('optimizations') or [])})"))
        print(path)
        if a.open:
            webbrowser.open(url(path))
        return

    def day_ts(s, end=False):
        d = dt.datetime.strptime(s, '%Y-%m-%d') + (dt.timedelta(days=1) if end else dt.timedelta())
        return d.timestamp()
    if a.all and (a.since or a.days):
        ap.error('--all reads every transcript: leave out --since and --days')
    until = day_ts(a.until, end=True) if a.until else None
    if a.since:
        since = day_ts(a.since)
    elif a.all:
        since = None
    else:                 # the last N days (by default DEFAULT_DAYS), up to --until when given
        since = (until or time.time()) - (a.days or DEFAULT_DAYS) * 86400
    if not os.path.isdir(a.projects):
        tried = f'{a.projects}' + ('' if a.claude_dir else ' (from $CLAUDE_CONFIG_DIR)' if os.environ.get('CLAUDE_CONFIG_DIR') else '')
        sys.exit(f'No Claude Code transcripts folder at {tried}.\n'
                 'Point the script at the folder Claude Code keeps its data in (the one with projects/ inside):\n'
                 '  python3 usage_report.py --claude-dir /path/to/.claude\n'
                 'or set CLAUDE_CONFIG_DIR=/path/to/.claude (Claude Code reads the same variable).')
    if not os.path.exists(a.template):
        sys.exit(f'Report template not found: {a.template}')
    t0 = time.time()
    log('Reading transcripts…')
    log(f'  Claude Code folder: {cdir}')
    src = Source(a.projects, since, until, log=log, home=cdir if os.path.isdir(cdir) else None)
    if not any(d.get('type') == 'assistant' and d['_t'] is not None for d in src.recs):
        sys.exit('No API calls found for that range.')
    prices = Prices(a.prices)
    ALIASES.clear()
    ALIASES.update(model_aliases(prices, [src.settings, src.local_settings]
                                 + [js for f in src.project_settings.values() for js in f.values()]))
    log('Computing…')
    scopes, results, all_m = build(src, prices, log)
    rng = (min(c['t0'] for c in all_m.real), max(c['t1'] for c in all_m.real)) if all_m.real else (None, None)
    notes = ['Every number comes from counting your local transcripts — no LLM was involved.',
             'Dollars are API list-price equivalents (tokens × prices.json). ' + (prices.source or '')]
    if not (a.since or a.days or a.all):
        notes.append(f"Covers {DEFAULT_DAYS} days{' up to ' + a.until if a.until else ''}, the default (--days N, --since or --all to change it).")
    if src.bad_lines:
        notes.append(f'{src.bad_lines} unreadable transcript lines were skipped.')
    if prices.missing:
        arn = any(str(x).startswith('arn:') for x in prices.missing)
        notes.append('No price for: ' + ', '.join(sorted(prices.missing)) + ' (counted as $0).'
                     + (' An arn:… is a Bedrock application inference profile: map it to its model in prices.json\'s _aliases (or in '
                        'modelOverrides in your settings) to price it.' if arn else ''))
    for v, (use, var) in sorted(prices.guessed.items()):
        notes.append(f'{clip(v, 90)} (your {var}) names no model version, so it is priced as {model_name(use)}; map it in '
                     "prices.json's _aliases to price it exactly.")
    where = collections.Counter(c['where'] for c in all_m.real)
    if set(where) - {None, 'api_global'}:                  # only native calls: nothing to say, the report reads as before
        up = collections.Counter(c['where'] for c in all_m.real if prices.mult(c['model'], c['where']) > 1)   # location premium only

        def above(k):
            """prices.json's _modifiers[k] in words, e.g. '10% above list, Claude 4.5 and later'."""
            mod = prices.modifiers.get(k) or {}
            v = [str(x) for x in mod.get('from') or []]
            return f"{(mod.get('mult') or 1) - 1:.0%} above list" + (f", Claude {'.'.join(v)} and later" if v else '')
        parts = [(f"{where[k]:,} {what}" + (f" ({up[k]:,} priced {above(k)})" if up[k] else ' (list price)'))
                 for k, what in (('api_global', 'on the Claude API, global'),
                                 ('api_regional', 'on the Claude API, pinned to one location'),
                                 ('bedrock_global', 'through Bedrock global profiles'),
                                 ('bedrock_regional', 'through Bedrock regional profiles'),
                                 ('bedrock', "on Bedrock, global or regional not recorded"),
                                 (None, 'with no location recorded, as on a subscription')) if where[k]]
        notes.append('Where the API calls ran: ' + '; '.join(parts) + '.')
    fast = sum(1 for c in all_m.real if c['fast'])
    if fast:
        notes.append(f'{fast:,} API call' + ('s' if fast != 1 else '') + ' ran in fast mode: priced at its premium where the model has one (prices.json "fast").')
    if prices.estimated:
        notes.append('No exact price for ' + ', '.join(f'{k} (priced as {v})' for k, v in sorted(prices.estimated.items()))
                     + '; add it to prices.json to price it exactly.')
    report = {
        'meta': {'title': 'Claude Code usage report',
                 'subtitle': f'{src.files} transcripts · {len(scopes) - 1} project scopes',
                 'generated': dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'version': VERSION,   # seconds: two runs in a minute differ
                 'range': {'start': day(rng[0]) if rng[0] else None, 'end': day(rng[1]) if rng[1] else None}, 'notes': notes,
                 'claude_dir': cdir},
        'scopes': [{k: v for k, v in sc.items() if k != 'dirs'} for sc in scopes],
        'sections': [{'id': k, 'title': v} for k, v in SECTIONS],
        'data': results,
        'traces': TRACES,
    }
    report = clean(report)
    os.makedirs(layout.data_dir(a.out), exist_ok=True)
    with open(layout.data(a.out, 'metrics.json'), 'w', encoding='utf-8') as fh:
        fh.write(json.dumps(report, ensure_ascii=False, separators=(',', ':')))
    config = config_snapshot(src)
    with open(layout.data(a.out, 'config.json'), 'w', encoding='utf-8') as fh:
        json.dump(config, fh, indent=1, ensure_ascii=False)
    write_digest(report, config, a.out)
    try:        # the catalog's drafts for the report and optimize skills; a failure there must not sink the report
        sys.modules.setdefault('usage_report', sys.modules[__name__])      # candidates.py imports this module: reuse it
        import candidates
        candidates.write_candidates(a.out)
    except Exception as e:
        log(f'  ! candidates.json not written ({e}); the skills rebuild it with candidates.py')
        with contextlib.suppress(OSError):
            os.remove(layout.data(a.out, 'candidates.json'))              # never leave the previous run's drafts behind
    path, _ = render(a.out, a.template, log)
    if not a.no_csv:
        write_csvs(all_m, a.out)
    log(f'Done in {time.time() - t0:.1f}s → {path}')
    print(path)
    if a.open:
        webbrowser.open(url(path))


if __name__ == '__main__':
    main()
