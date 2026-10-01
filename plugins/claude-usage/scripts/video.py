#!/usr/bin/env python3
"""A 30–60 second video of your own usage highlights, made from the report's data (never from the transcripts).

  python3 video.py tools                             is the MP4 possible here (a Chromium-based browser, ffmpeg)? how to install
  python3 video.py plan   [--out DIR] [--seconds N]   draft <out>/video/storyboard.json and print it, with what else fits
  python3 video.py check  [--out DIR]                 check the storyboard: schema, 30–60 s, ids, numbers, no names
  python3 video.py render [--out DIR] [--no-mp4] [--open] [--stills 3,20] [--fps 30] [--scale 2]

The storyboard names what each scene shows (tiles, a miss trace, insight and optimization ids, SV2 levers); every figure
comes from data/metrics.json, insights.json and optimizations.json, and a number typed into a headline must match one of
them. render writes <out>/video/video.html (plays by itself, works offline) and, with a Chromium-based browser and ffmpeg
installed, <out>/video/claude-usage-video.mp4 (1080 x 1080, H.264). Standard library only.
"""
import argparse
import base64
import datetime as dt
import html
import json
import math
import os
import pathlib
import re
import sys
import webbrowser

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import layout  # noqa: E402
import usage_report as UR  # noqa: E402  (folders, number formats)
import validate as VA  # noqa: E402  (the JSON Schema subset the plugin's schemas use)
import video_capture as CAP  # noqa: E402
ROOT = os.path.dirname(HERE)
SCHEMA = os.path.join(ROOT, 'schemas', 'video.schema.json')
TEMPLATE = os.path.join(HERE, 'video_template.html')
FONTS = [('Bricolage Grotesque', 'BricolageGrotesque-latin.woff2', '400 800'), ('JetBrains Mono', 'JetBrainsMono-latin.woff2', '400 700')]
MP4 = 'claude-usage-video.mp4'

SECONDS = {'intro': 5, 'numbers': 5.5, 'models': 6, 'miss': 7.5, 'insights': 7, 'levers': 6.5, 'optimizations': 6.5, 'savings': 7}
MIN_S, MAX_S, SCENE_MIN, SCENE_MAX = 30, 60, 3.5, 12
FIELDS = {'numbers': {'tiles'}, 'miss': {'trace'}, 'insights': {'items'}, 'levers': {'items'}, 'optimizations': {'items'}}
ITEMS = {'insights': (1, 3), 'optimizations': (1, 4), 'levers': (2, 5)}
EYEBROW = {'intro': 'My Claude Code usage', 'numbers': 'The numbers', 'models': 'Where it went', 'miss': 'Cache misses',
           'insights': 'Insights', 'levers': 'Savings, lever by lever', 'optimizations': 'Optimizations', 'savings': ''}
TILES = ['Sessions', 'Your prompts', 'Median session', 'Active hours', 'Cache hit rate', 'Re-read per output token',
         'Subagent share of spend', 'Median peak context']
TILE_LABEL = {'Re-read per output token': 'Re-read per output', 'Subagent share of spend': 'Subagent share',
              'Tokens re-written by misses': 'Re-written by misses'}
TRIGGER = {'prompt': 'You sent a prompt', 'command': 'You ran a command', 'answer': 'You answered', 'message': 'A message came in',
           'notif': 'A notification came in', 'tool': 'A tool finished'}
BASIS = {'upper_bound': 'at most', 'theoretical': 'in theory', 'measured': 'measured'}
# words in project paths that name nothing private
COMMON = {'claude', 'code', 'projects', 'project', 'worktree', 'worktrees', 'checkout', 'users', 'home', 'desktop', 'documents',
          'repos', 'repo', 'work', 'workspace', 'github', 'temp', 'task', 'tasks', 'main', 'master', 'src', 'app', 'apps'}


def read_json(p):
    with open(p, encoding='utf-8') as fh:
        return json.load(fh)


def fmt_day(s, year=False):
    try:
        d = dt.date.fromisoformat(s[:10])
    except (TypeError, ValueError):
        return s or ''
    return f'{d:%b} {d.day}' + (f', {d.year}' if year else '')


def period(rng):
    a, b = (rng or {}).get('start'), (rng or {}).get('end')
    if not a or not b:
        return ''
    if a[:4] == b[:4]:
        return f'{fmt_day(a)} – {fmt_day(b, True)}'
    return f'{fmt_day(a, True)} – {fmt_day(b, True)}'


def hm(seconds):
    s = int(round(seconds or 0))
    if s >= 3600:
        return f'{s // 3600}h {s % 3600 // 60}m'
    if s >= 60:
        return f'{s // 60}m'
    return f'{s}s'


class Data:
    """What the video can show, from <out>: metrics.json always, insights and optimizations when they match it."""

    def __init__(self, out):
        self.out = out
        p = layout.data(out, 'metrics.json')
        if not os.path.exists(p):
            sys.exit(f'No report data in {UR.tilde(layout.data_dir(out))}: run /claude-usage:report first.')
        self.metrics = UR.current_ids(read_json(p))        # a report from before the SV renumbering, under today's ids
        meta = self.metrics.get('meta') or {}
        self.generated, self.range = meta.get('generated'), meta.get('range') or {}
        top = (self.metrics.get('data') or {}).get('all') or {}
        self.cards, self.head = top.get('cards') or {}, top.get('headline') or {}
        self.hero = self.head.get('hero') or {}
        self.spend, self.days = self.hero.get('value') or 0, self.head.get('days') or 0
        self.notes = []
        self.insights = self._fresh('insights.json', 'Insights', '/claude-usage:report')
        self.opts = self._fresh('optimizations.json', 'Optimizations',            # /claude-usage:report writes both tabs
                                '/claude-usage:optimize' if self.insights else '/claude-usage:report')
        state = os.path.join(layout.applied_dir(out), 'applied.json')
        try:
            self.applied = set(read_json(state)) if os.path.exists(state) else set()
        except ValueError:
            self.applied = set()

    def _fresh(self, name, what, skill):
        p = os.path.join(self.out, name)
        if not os.path.exists(p):
            self.notes.append(f'No {name} yet, so no {what} scene: run {skill} to add one.')
            return None
        try:
            doc = read_json(p)
        except ValueError:
            self.notes.append(f'{name} is not valid JSON, so it is left out.')
            return None
        if (doc.get('source') or {}).get('metrics_generated') != self.generated:
            self.notes.append(f'{name} was written from an older report, so it is left out: run {skill} again to include it.')
            return None
        return doc

    def blocks(self, cid):
        return UR.flat_blocks(self.cards.get(cid))

    def kpis(self, cid):
        return {i['label']: i for b in self.blocks(cid) if b.get('kind') == 'kpis' for i in b.get('items', []) if i.get('value') is not None}

    def table(self, cid):
        return next((b.get('rows') or [] for b in self.blocks(cid) if b.get('kind') == 'table' and not b.get('title')), [])

    def tiles(self):
        return {k['label']: k for k in self.head.get('kpis') or [] if k.get('value') is not None}

    def models(self):
        ch = next((b['chart'] for b in self.blocks('OV2') if b.get('kind') == 'chart' and (b.get('chart') or {}).get('title') == 'By model'), None)
        if not ch or not ch.get('series'):
            return []
        pairs = [(c, v) for c, v in zip(ch.get('categories') or [], ch['series'][0].get('values') or []) if v]
        total = sum(v for _, v in pairs) or 1
        pairs.sort(key=lambda x: -x[1])
        if len(pairs) > 5:
            pairs = pairs[:4] + [('Other models', sum(v for _, v in pairs[4:]))]
        return [{'label': c, 'usd': v, 'share': 100 * v / total} for c, v in pairs]

    def tokens(self):
        items = [i for i in self.kpis('OV3').values() if i.get('unit') == 'pct']
        return [{'label': i['label'], 'pct': i['value']} for i in sorted(items, key=lambda i: -i['value'])]

    def traces(self):
        ids = next((b.get('ids') or [] for b in self.blocks('CX8') if b.get('kind') == 'traces'), [])
        tr = self.metrics.get('traces') or {}
        return [tr[i] for i in ids if i in tr]

    def miss_kpis(self):
        k = self.kpis('CX8')
        return [{'label': lbl, 'value': k[lbl]['value'], 'unit': k[lbl]['unit']}
                for lbl in ('Cache misses', 'Extra cost of misses', 'Share of spend') if lbl in k]

    def tip(self, cause):
        row = next((r for r in self.table('SV4') if r.get('k') == cause and r.get('h')), None)
        return (row['h'][0].upper() + row['h'][1:] + ('' if row['h'].endswith('.') else '.')) if row else None

    def levers(self):
        rows = [r for r in self.table('SV2') if (r.get('u') or 0) > 0]
        return [{'id': r['l'], 'usd': r['u'], 'month': r.get('mo') or r['u'] * 30 / max(self.days, 1), 'share': r.get('s')}
                for r in sorted(rows, key=lambda r: -r['u'])]

    def insight_items(self):
        ins = (self.insights or {}).get('insights') or []
        return sorted([i for i in ins if ((i.get('savings') or {}).get('usd_so_far') or 0) > 0], key=lambda i: -i['savings']['usd_so_far'])

    def opt_items(self):
        opts = (self.opts or {}).get('optimizations') or []
        return sorted(opts, key=lambda o: -((o.get('savings') or {}).get('usd_so_far') or 0))

    def month(self, sv):
        v = sv.get('usd_per_month')
        return v if isinstance(v, (int, float)) else (sv.get('usd_so_far') or 0) * 30 / max(self.days, 1)

    def combined(self):
        """What the changes together would have saved, overlaps removed, as the report's tabs work it out: the
        optimizations (alternatives and conflicts counted once, at their best), else the cost insights."""
        spend = self.spend
        if not spend:
            return None
        if self.opts and self.opt_items():
            opts = self.opt_items()
            parent = {o['id']: o['id'] for o in opts}

            def find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x
            for o in opts:
                for r in o.get('related') or []:
                    if r.get('relation') in ('alternative', 'conflicts') and r.get('id') in parent:
                        a, b = find(o['id']), find(r['id'])
                        if a != b:
                            parent[b] = a
            best = {}
            for o in opts:
                g = find(o['id'])
                best[g] = max(best.get(g, 0), (o.get('savings') or {}).get('usd_so_far') or 0)
            saves, basis = [s for s in best.values() if s > 0], 'optimizations'
            applied, total = len(self.applied & set(parent)), len(opts)
        elif self.insight_items():
            saves = [i['savings']['usd_so_far'] for i in self.insight_items() if i.get('category') == 'cost']
            basis, applied, total = 'insights', 0, 0
        else:
            return None
        if not saves:
            return None
        saved = spend * (1 - math.prod(1 - min(0.99, s / spend) for s in saves))
        return {'saved': saved, 'spend': spend, 'month': saved * 30 / max(self.days, 1), 'pct': 100 * saved / spend,
                'days': self.days, 'basis': basis, 'applied': applied, 'total': total}

    def names(self):
        """Words that would identify the user's work: project folders, session titles, the account name."""
        out = set()
        for s in self.metrics.get('scopes') or []:
            if s.get('kind') == 'all':
                continue
            for part in re.split(r'[/\\▸+()\s,]+', s.get('label') or ''):
                p = part.strip(' .~…-_')
                if len(p) >= 4 and p.lower() not in COMMON and not p.isdigit():
                    out.add(p)
        for t in (self.metrics.get('traces') or {}).values():
            if len(t.get('session') or '') >= 6:
                out.add(t['session'])
        user = os.path.basename(os.path.expanduser('~'))
        if len(user) >= 3:
            out.add(user)
        return out


# ---------- numbers: every figure in the words must be one the data has ----------
NUM = re.compile(r'(?<![\w.])(\$)?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(\s?(?:%|×|x\b|[KMB]\b))?')
MODEL = re.compile(r'\b(?:Claude\s+)?(?:Opus|Sonnet|Haiku|Fable|Mythos|Claude)\s+\d+(?:\.\d+)*\b', re.I)
CLOCK = re.compile(r'\b\d{1,2}:\d{2}(?::\d{2})?\b')


def numbers_in(text):
    for m in NUM.finditer(text or ''):
        cur, whole, frac, unit = m.groups()
        unit = (unit or '').strip()
        yield m.group(0).strip(), float(whole.replace(',', '') + (frac or '')), (len(frac) - 1 if frac else 0), bool(cur), unit


def untraced(text, facts, clocks):
    text = MODEL.sub(' ', text or '')
    bad = [c for c in CLOCK.findall(text) if c not in clocks]
    text = CLOCK.sub(' ', text)
    for raw, x, decimals, cur, unit in numbers_in(text):
        if not cur and not unit and decimals == 0 and x <= 12:
            continue                                   # small counts: "3 fixes", "1-hour cache"
        mult = {'K': 1e3, 'M': 1e6, 'B': 1e9}.get(unit, 1)
        tol = 0.5 * 10 ** -decimals + 1e-9
        if not any(abs(v / mult - x) <= tol for v in facts):
            bad.append(raw)
    return bad


def facts(D):
    """Every figure the video may quote, and the clock times it may name."""
    vals, clocks = {30.0, float(D.spend), float(D.days), float(round(D.days))}, set()

    def add(*xs):
        for x in xs:
            if isinstance(x, (int, float)) and not isinstance(x, bool):
                vals.add(float(x))

    def add_text(s):
        for _, x, _, _, _ in numbers_in(CLOCK.sub(' ', s or '')):
            vals.add(x)
    for k in ('start', 'end'):
        try:
            d = dt.date.fromisoformat((D.range.get(k) or '')[:10])
            add(d.day, d.year)
        except ValueError:
            pass
    try:
        add((dt.date.fromisoformat(D.range['end'][:10]) - dt.date.fromisoformat(D.range['start'][:10])).days + 1)
    except (KeyError, TypeError, ValueError):
        pass
    add_text(D.hero.get('sub'))
    spark = (D.hero.get('spark') or {}).get('values') or []
    add(*(spark[:1] and [max(spark)]))
    add(*[k['value'] for k in D.tiles().values()])
    for r in D.models():
        add(r['usd'], r['share'])
    add(*[t['pct'] for t in D.tokens()])
    for cid in ('CX8', 'OV2', 'OV3', 'OV8', 'SV2', 'SV4', 'CX7'):
        add(*[k['value'] for k in D.kpis(cid).values()])
    for t in D.traces():
        v = trace_view(t)
        add(t.get('usd'), t.get('rw'), t.get('ctx'), (t.get('ttl') or 0) / 60, (t.get('ttl') or 0) / 3600, (t.get('idle') or 0) / 60, (t.get('idle') or 0) / 3600)
        add_text(v['idle_label'])
        add_text(v['meta'])
        clocks.update([v['prev_end'], v['expiry'], v['start']])
    for r in D.levers():
        add(r['usd'], r['month'], r['share'])
    for r in D.table('SV4'):
        add(r.get('u'), r.get('mo'), r.get('n'))
    for i in D.insight_items():
        sv = i['savings']
        add(sv.get('usd_so_far'), D.month(sv), sv.get('pct_of_spend'))
    for o in D.opt_items():
        sv = o.get('savings') or {}
        add(sv.get('usd_so_far'), D.month(sv) if sv else None)
    c = D.combined()
    if c:
        add(c['saved'], c['month'], c['pct'], c['applied'], c['total'])
    return vals, clocks


def trace_view(t):
    tz = dt.timedelta(minutes=t.get('tz') or 0)
    marks = t.get('marks') or {}

    def local(ms):
        return dt.datetime.fromtimestamp((ms or 0) / 1000, dt.timezone.utc).replace(tzinfo=None) + tz
    start = local(marks.get('start') or t.get('at'))
    thread = t.get('thread') or ''
    if thread and thread != 'main thread':
        thread = thread.split(' · ')[0] + ' subagent'          # "general-purpose · a529f76": the id means nothing on screen
    return {'cause': t.get('cause') or 'Cache miss', 'usd': t.get('usd') or 0, 'rw': t.get('rw') or 0, 'ctx': t.get('ctx') or 0,
            'ttl': t.get('ttl') or 0, 'idle': t.get('idle') or 0, 'expired': bool(t.get('expired')),
            'idle_label': hm(t.get('idle')), 'prev_end': f'{local(marks.get("prevEnd")):%H:%M}', 'expiry': f'{local(marks.get("expiry")):%H:%M}',
            'start': f'{start:%H:%M}', 'trigger': TRIGGER.get(t.get('trigger'), 'The next request'),
            'meta': ' · '.join(x for x in (f'{start:%b} {start.day} · {start:%H:%M}', thread, t.get('model')) if x)}


# ---------- scenes ----------
def resolve(s, D):
    """A storyboard scene with its figures filled in from the data (what the page draws). Raises ValueError."""
    t = s['type']
    extra = set(s) - {'type', 'seconds', 'headline', 'eyebrow'} - FIELDS.get(t, set())
    if extra:
        raise ValueError(f'a {t} scene takes no {", ".join(sorted(extra))}')
    r = {'type': t, 'seconds': s['seconds'], 'headline': s['headline'].strip(), 'eyebrow': (s.get('eyebrow') or EYEBROW[t]).strip()}
    items = [x if isinstance(x, dict) else {'id': x} for x in s.get('items') or []]
    if t in ITEMS and items:
        lo, hi = ITEMS[t]
        if not lo <= len(items) <= hi:
            raise ValueError(f'{t} shows {lo}–{hi} items, not {len(items)}')

    def pick(avail, what, key='id', default_n=3):
        if not items:
            return [({'id': a[key]}, a) for a in avail[:default_n]]
        by = {a[key]: a for a in avail}
        missing = [x['id'] for x in items if x['id'] not in by]
        if missing:
            raise ValueError(f'no {what} {", ".join(map(repr, missing))}; available: ' + ', '.join(repr(a[key]) for a in avail[:10]))
        return [(x, by[x['id']]) for x in items]

    if t == 'intro':
        vals = [v or 0 for v in (D.hero.get('spark') or {}).get('values') or []]
        xs = (D.hero.get('spark') or {}).get('x') or []
        peak = None
        if vals:
            i = max(range(len(vals)), key=vals.__getitem__)
            peak = {'i': i, 'value': vals[i], 'day': fmt_day(xs[i]) if i < len(xs) else ''}
        r.update(value=D.spend, spark=vals, peak=peak, sub=D.hero.get('sub'))
    elif t == 'numbers':
        avail = D.tiles()
        labels = s.get('tiles') or [lbl for lbl in TILES if lbl in avail]
        missing = [lbl for lbl in labels if lbl not in avail]
        if missing:
            raise ValueError(f'no tile {", ".join(map(repr, missing))}; available: ' + ', '.join(map(repr, avail)))
        if len(labels) < 3:
            raise ValueError('a numbers scene needs at least 3 tiles')
        r['tiles'] = [{'label': TILE_LABEL.get(lbl, lbl), 'value': avail[lbl]['value'], 'unit': avail[lbl].get('unit')} for lbl in labels]
    elif t == 'models':
        r['rows'], r['tokens'] = D.models(), D.tokens()
        if not r['rows']:
            raise ValueError('the report has no cost by model (OV2)')
    elif t == 'miss':
        trs = D.traces()
        if not trs:
            raise ValueError('the report traced no cache misses (CX8)')
        tr = next((x for x in trs if x.get('id') == s['trace']), None) if s.get('trace') else trs[0]
        if not tr:
            raise ValueError(f'no trace {s["trace"]!r}; available: ' + ', '.join(repr(x['id']) for x in trs[:10]))
        r.update(trace=trace_view(tr), kpis=D.miss_kpis(), tip=D.tip(tr.get('cause')))
    elif t == 'insights':
        if not D.insights:
            raise ValueError('no current insights.json: run /claude-usage:report, or use a levers scene')
        links = {o['id']: o.get('title') for o in D.opt_items()}
        r['items'] = []
        for x, i in pick(D.insight_items(), 'insight with a saving'):
            sv = i['savings']
            fix = next((links[o] for o in i.get('optimizations') or [] if o in links), None)
            r['items'].append({'title': x.get('title') or i.get('title'), 'priority': i.get('priority'), 'usd': sv['usd_so_far'],
                               'month': D.month(sv), 'basis': BASIS.get(sv.get('kind'), ''), 'fix': fix})
    elif t == 'levers':
        avail = D.levers()
        if len(avail) < 2:
            raise ValueError('the report has fewer than 2 levers with a saving (SV2)')
        r['items'] = [{'title': x.get('title') or a['id'], 'usd': a['usd'], 'month': a['month']} for x, a in pick(avail, 'lever', default_n=4)]
    elif t == 'optimizations':
        if not D.opts:
            raise ValueError('no current optimizations.json: run /claude-usage:report first (it writes both tabs)')
        r['items'] = []
        for x, o in pick(D.opt_items(), 'optimization', default_n=4):
            sv = o.get('savings') or {}
            kind = 'One command' if o.get('apply') else ('A habit' if o.get('effort') == 'habit' or o.get('kind') == 'habit' else 'By hand')
            r['items'].append({'title': x.get('title') or o.get('title'), 'kind': kind, 'usd': sv.get('usd_so_far') or 0,
                               'month': D.month(sv) if sv else 0, 'done': o['id'] in D.applied})
        opts = D.opt_items()
        r['applied'], r['total'] = len(D.applied & {o['id'] for o in opts}), len(opts)
    elif t == 'savings':
        c = D.combined()
        if not c:
            raise ValueError('nothing to add up: needs a current optimizations.json or insights with savings')
        r.update(c)
    return r


def plan(D, seconds=None):
    """The default storyboard: every scene the data supports, in story order, sized to 30–60 s."""
    k = D.tiles()
    tiles = [lbl for lbl in TILES if lbl in k]
    scenes = [{'type': 'intro', 'headline': f'{round(D.days)} days of Claude Code, by the numbers.' if D.days >= 1.5
               else 'My Claude Code usage, by the numbers.'}]
    if len(tiles) >= 3:
        hit, n = k.get('Cache hit rate'), k.get('Sessions')
        scenes.append({'type': 'numbers', 'tiles': tiles, 'headline': f'{n["value"]:,} sessions and a {hit["value"]:.1f}% cache hit rate.'
                       if hit and n else 'How I used it.'})
    if D.models():
        m = D.models()[0]
        scenes.append({'type': 'models', 'headline': f'{m["label"]} took {m["share"]:.0f}% of the spend.'})
    if D.traces():
        scenes.append({'type': 'miss', 'trace': D.traces()[0]['id'], 'headline': f'My costliest cache miss: {UR.f_usd(D.traces()[0].get("usd"))}.'})
    if D.insights and D.insight_items():
        scenes.append({'type': 'insights', 'items': [i['id'] for i in D.insight_items()[:3]], 'headline': 'What Claude found I could save.'})
    elif len(D.levers()) >= 2:
        scenes.append({'type': 'levers', 'items': [r['id'] for r in D.levers()[:4]], 'headline': 'What would have saved the most.'})
    if D.opts and D.opt_items():
        opts = D.opt_items()
        n_done = len(D.applied & {o['id'] for o in opts})
        scenes.append({'type': 'optimizations', 'items': [o['id'] for o in opts[:4]],
                       'headline': f'{n_done} of {len(opts)} fixes applied so far.' if n_done else 'The fixes, ready to apply.'})
    c = D.combined()
    if c:
        scenes.append({'type': 'savings', 'headline': 'Applied together, these fixes would have saved' if c['basis'] == 'optimizations'
                       else 'Together, these changes would have saved'})
    for s in scenes:
        s['seconds'] = SECONDS[s['type']]
    fit_seconds(scenes, seconds)
    return {'schema_version': 1,
            'source': {'metrics_generated': D.generated, 'insights_generated': (D.insights or {}).get('generated'),
                       'optimizations_generated': (D.opts or {}).get('generated')},
            'allow_names': False,
            'scenes': [dict(type=s['type'], seconds=s['seconds'], headline=s['headline'], **{k2: v for k2, v in s.items() if k2 not in ('type', 'seconds', 'headline')})
                       for s in scenes]}


def fit_seconds(scenes, target=None):
    """Stretch or shrink the scenes to the target length (default: what they need, kept within 30–60 s), in half seconds."""
    total = sum(s['seconds'] for s in scenes)
    target = min(MAX_S, max(MIN_S, target or total))
    for s in scenes:
        s['seconds'] = min(SCENE_MAX, max(SCENE_MIN, round(s['seconds'] * target / total * 2) / 2))
    for _ in range(200):
        diff = target - sum(s['seconds'] for s in scenes)
        if abs(diff) < 0.25:
            break
        room = [s for s in scenes if (s['seconds'] < SCENE_MAX if diff > 0 else s['seconds'] > SCENE_MIN)]
        if not room:
            break
        s = max(room, key=lambda x: x['seconds']) if diff < 0 else min(room, key=lambda x: x['seconds'])
        s['seconds'] += 0.5 if diff > 0 else -0.5


def check(sb, D):
    """Problems with the storyboard (empty when it's fine), and the scenes resolved for the page."""
    errs = []
    VA.check(sb, read_json(SCHEMA), 'storyboard', errs)
    if errs:
        return errs, []
    if sb['source']['metrics_generated'] != D.generated:
        errs.append(f'planned from an older report ({sb["source"]["metrics_generated"]}, now {D.generated}): run `video.py plan` again')
    total = sum(s['seconds'] for s in sb['scenes'])
    if not MIN_S <= total <= MAX_S:
        errs.append(f'the scenes add up to {total:g} s; the video must be {MIN_S}–{MAX_S} s')
    types = [s['type'] for s in sb['scenes']]
    errs += [f'more than one {t} scene' for t in sorted(set(types)) if types.count(t) > 1]
    vals, clocks = facts(D)
    names = set() if sb.get('allow_names') else D.names()
    out = []
    for i, s in enumerate(sb['scenes']):
        where = f'scenes[{i}] ({s["type"]})'
        try:
            r = resolve(s, D)
        except ValueError as e:
            errs.append(f'{where}: {e}')
            continue
        own = [('headline', s.get('headline')), ('eyebrow', s.get('eyebrow'))] + \
              [(f'items[{j}].title', x.get('title')) for j, x in enumerate(s.get('items') or []) if isinstance(x, dict)]
        for field, text in own:
            bad = untraced(text, vals, clocks)
            if bad:
                errs.append(f'{where} {field}: {", ".join(bad)} is not a figure from the report; quote the numbers `plan` lists, or leave them out')
        shown = dict.fromkeys([text for _, text in own] + [x.get('title') for x in r.get('items') or []])   # each text once
        for text in filter(None, shown):
            hit = sorted(n for n in names if re.search(r'(?<![\w-])' + re.escape(n) + r'(?![\w-])', text, re.I))
            if hit:
                errs.append(f'{where}: "{text[:60]}" names {", ".join(hit)} (a project, session or account name); reword it, or set '
                            f'"allow_names": true if the user wants names in the video')
            if re.search(r'(^|[\s(])(~/|/Users/|/home/|[A-Za-z]:\\)', text):
                errs.append(f'{where}: "{text[:60]}" shows a file path')
        out.append(r)
    return errs, out


# ---------- output ----------
def font_css():
    rules = []
    for family, file, weight in FONTS:
        with open(os.path.join(HERE, 'fonts', file), 'rb') as fh:
            b64 = base64.b64encode(fh.read()).decode()
        rules.append(f'@font-face {{ font-family: "{family}"; font-style: normal; font-weight: {weight}; font-display: block; '
                     f'src: url(data:font/woff2;base64,{b64}) format("woff2"); }}')
    return '\n'.join(rules)


def homepage():
    try:
        url = read_json(os.path.join(ROOT, '.claude-plugin', 'plugin.json')).get('homepage') or ''
    except (OSError, ValueError):
        url = ''
    return re.sub(r'^https?://', '', url)


def write_html(scenes, D, path):
    when = period(D.range)
    data = {'title': 'Claude Code usage' + (f' · {when}' if when else ''), 'period': when, 'scenes': scenes,
            'credit': {'line': 'Made with claude-usage' + (f' · {homepage()}' if homepage() else ''),
                       'note': 'Dollars are API list-price equivalents: on a subscription they are a yardstick, not a bill.'}}
    with open(TEMPLATE, encoding='utf-8') as fh:
        tpl = fh.read()
    blob = json.dumps(data, ensure_ascii=True, separators=(',', ':')).replace('<', '\\u003c')
    page = tpl.replace('__TITLE__', html.escape(data['title'])).replace('/*__FONTS__*/', font_css()).replace('/*__VIDEO_DATA__*/null', blob)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(page)
    return path


def show_plan(sb, D, path):
    total = sum(s['seconds'] for s in sb['scenes'])
    c = D.combined()
    print(f'Storyboard: {UR.tilde(path)} ({len(sb["scenes"])} scenes, {total:g} s; keep it {MIN_S}–{MAX_S} s)')
    print(f'Report: {period(D.range)} · {UR.f_usd(D.spend)} at list prices · {D.days:g} days covered · generated {D.generated}')
    print('\nScenes')
    for i, s in enumerate(sb['scenes'], 1):
        extra = ''
        if s.get('tiles'):
            extra = '  tiles: ' + ', '.join(s['tiles'])
        elif s.get('items'):
            extra = '  items: ' + ', '.join(x if isinstance(x, str) else x['id'] for x in s['items'])
        elif s.get('trace'):
            extra = '  trace: ' + s['trace']
        print(f'  {i}. {s["type"]:<14}{s["seconds"]:>5g} s  "{s["headline"]}"{extra}')
    print('\nWhat each scene can show (ids to use in the storyboard, with the figures you may quote)')
    print('  intro: ' + ' · '.join(filter(None, [f'{UR.f_usd(D.spend)} over {D.days:g} days', D.hero.get('sub')])))
    print('  numbers (tiles): ' + ' · '.join(f'{k["label"]} {tile_text(k)}' for k in D.tiles().values()))
    if D.models():
        print('  models: ' + ' · '.join(f'{r["label"]} {UR.f_usd(r["usd"])} ({r["share"]:.1f}%)' for r in D.models()))
    for t in D.traces()[:6]:
        v = trace_view(t)
        print(f'  miss (trace): {t["id"]}  {v["cause"]}  +{UR.f_usd(v["usd"])}  {v["meta"]}  idle {v["idle_label"]}  {UR.f_tok(v["rw"])} re-written')
    for i in D.insight_items()[:8]:
        sv = i['savings']
        print(f'  insights (items): {i["id"]}  {UR.f_usd(sv["usd_so_far"])} (≈ {UR.f_usd(D.month(sv))}/30 d)  {i.get("title")}')
    for r in D.levers()[:6]:
        print(f'  levers (items): "{r["id"]}"  {UR.f_usd(r["usd"])} (≈ {UR.f_usd(r["month"])}/30 d)')
    for o in D.opt_items()[:8]:
        sv = o.get('savings') or {}
        done = ' [applied]' if o['id'] in D.applied else ''
        print(f'  optimizations (items): {o["id"]}  {UR.f_usd(sv.get("usd_so_far") or 0)}{done}  {o.get("title")}')
    if c:
        print(f'  savings: {UR.f_usd(c["saved"])} of {UR.f_usd(c["spend"])} ({c["pct"]:.1f}%), ≈ {UR.f_usd(c["month"])} per 30 days, '
              f'from the {c["basis"]}' + (f'; {c["applied"]} of {c["total"]} applied' if c['total'] else ''))
    if D.notes:
        print('\nNotes')
        for n in D.notes:
            print('  - ' + n)
    print('\nFor the MP4')
    tools()


def tile_text(k):
    v, u = k['value'], k.get('unit')
    return {'usd': UR.f_usd(v), 'pct': f'{v:.1f}%', 'tokens': UR.f_tok(v), 'hours': f'{v:,.0f} h', 'ratio': f'{v:.0f}×'}.get(u, f'{v:,.0f}')


def tools(browser=None, ffmpeg=None):
    """What the MP4 needs, found or not, and for anything missing the install command for this machine."""
    found = {'browser': CAP.find_browser(browser), 'ffmpeg': CAP.find_ffmpeg(ffmpeg)}
    print(f'python: {sys.version.split()[0]} (ok)')
    for tool, path in found.items():
        if path:
            print(f'{tool}: {UR.tilde(path)} (ok, {CAP.ROLE[tool]})')
            continue
        h = CAP.install_hint(tool)
        print(f'{tool}: missing (it {CAP.ROLE[tool]})')
        if h['command']:
            print(f'  install: {h["command"]}\n           (for the user to run in their own terminal, if they want it; never run for them)')
        print(f'  by hand: {h["url"]}')
    ok = all(found.values())
    print('ready: ' + ('yes (render makes the MP4)' if ok else 'no (render makes only the HTML page until these are installed)'))
    return found['browser'], found['ffmpeg']


def main(argv=None):
    UR.safe_console()
    os.umask(0o077)                                     # the report folder holds private data: yours only
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=['tools', 'plan', 'check', 'render'])
    ap.add_argument('--out', metavar='DIR', help='the report folder (default: the one usage_report.py uses, e.g. ~/.claude-usage)')
    ap.add_argument('--claude-dir', metavar='DIR', help='the Claude Code folder the report was built from, if not the default')
    ap.add_argument('--seconds', type=float, help=f'plan: total length, {MIN_S}–{MAX_S} s (default: what the scenes need)')
    ap.add_argument('--no-mp4', action='store_true', help='render: only the HTML page')
    ap.add_argument('--open', action='store_true', help='render: open the result when done')
    ap.add_argument('--stills', help='render: comma-separated seconds; write PNGs of those moments to <out>/video/stills/ instead of an MP4')
    ap.add_argument('--fps', type=int, default=30)
    ap.add_argument('--scale', type=float, default=2.0, help='render frames at this multiple of 1080 px, then scale down (sharper text)')
    ap.add_argument('--browser', help='a Chromium-based browser to record with (default: found automatically)')
    ap.add_argument('--ffmpeg', help='the ffmpeg program (default: on PATH)')
    a = ap.parse_args(argv)
    if a.command == 'tools':
        tools(a.browser, a.ffmpeg)
        return 0
    out = os.path.abspath(os.path.expanduser(a.out)) if a.out else UR.default_out(UR.claude_dir(a.claude_dir))
    layout.require_report(out)                             # it only ever adds to a report folder
    D = Data(out)
    sb_path = layout.video(out, 'storyboard.json')

    if a.command == 'plan':
        if a.seconds is not None and not MIN_S <= a.seconds <= MAX_S:
            sys.exit(f'--seconds must be {MIN_S}–{MAX_S}')
        sb = plan(D, a.seconds)
        os.makedirs(os.path.dirname(sb_path), exist_ok=True)
        with open(sb_path, 'w', encoding='utf-8') as fh:
            fh.write(json.dumps(sb, indent=2, ensure_ascii=False) + '\n')
        show_plan(sb, D, sb_path)
        errs, _ = check(sb, D)
        if errs:
            print('\nThe draft needs edits before it renders:')
            for e in errs:
                print('  - ' + e)
        return 0

    if not os.path.exists(sb_path):
        sys.exit(f'No storyboard at {UR.tilde(sb_path)}: run `video.py plan` first.')
    try:
        sb = read_json(sb_path)
    except ValueError as e:
        sys.exit(f'{UR.tilde(sb_path)} is not valid JSON: {e}')
    errs, scenes = check(sb, D)
    if errs:
        for e in errs:
            print(e)
        return 1
    total = sum(s['seconds'] for s in scenes)
    if a.command == 'check':
        print(f'OK: {len(scenes)} scenes, {total:g} s')
        return 0

    page = write_html(scenes, D, layout.video(out, 'video.html'))
    print(f'Video page: {page}  ({total:g} s; plays by itself, works offline)')
    target = page
    if a.stills or not a.no_mp4:
        browser = CAP.find_browser(a.browser)
        ffmpeg = CAP.find_ffmpeg(a.ffmpeg)
        if a.stills:
            if not browser:
                sys.exit(CAP.missing('browser'))
            for p in CAP.stills(page, [float(x) for x in a.stills.split(',')], layout.video(out, 'stills'), browser):
                print(p)
        elif not browser or not ffmpeg:
            print('No MP4 yet. ' + ' '.join(CAP.missing(t) for t, p in (('browser', browser), ('ffmpeg', ffmpeg)) if not p))
            print('Then render again. Until then, the page above plays the same video: open it, press H to hide the controls, '
                  'and screen-record it.')
        else:
            mp4 = layout.video(out, MP4)
            print(f'Recording {total:g} s at {a.fps} fps (about {max(1, round(total * a.fps / 12 / 60))} min)…', file=sys.stderr)
            CAP.record(page, mp4, browser, ffmpeg, fps=a.fps, scale=a.scale, log=lambda *x: print(*x, file=sys.stderr))
            print(f'MP4: {mp4}  (1080 x 1080, {total:g} s)')
            target = mp4
    if a.open:
        webbrowser.open(pathlib.Path(target).as_uri())
    return 0


if __name__ == '__main__':
    sys.exit(main())
