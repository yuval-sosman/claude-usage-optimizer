#!/usr/bin/env python3
"""The optimize skill's catalog as code: which changes this report's numbers call for, drafted ready to use.

  python3 candidates.py [--out DIR]                  write <out>/data/candidates.json; print one line per catalog entry
  python3 candidates.py [--out DIR] --show levers    print sections compactly (one JSON item per line) for a skill to read
  python3 candidates.py [--out DIR] --show insights,optimizations,judgment,links,skipped,problems --brief
  python3 candidates.py [--out DIR] --show card:CX3            every figure of one card, all rows (for numbers the digest cuts)

Reads only <out>/data/metrics.json and config.json (never the transcripts). Every rule of skills/optimize/reference/catalog.md
that is a threshold on the report's figures or a check of the current setup is evaluated here. candidates.json holds:
  optimizations  a complete, schema-valid draft for each entry whose rule holds and isn't in place yet (numbers from the
                 cards, apply steps from the catalog's templates, manual/undo/verify/docs, related links on both sides)
  judgment       entries whose call needs Claude (a habit or a setting, a close call, a path to check), with the facts
                 and, where one can be written, a draft to adopt
  skipped        entries whose rule doesn't hold, or that are already in place, with the reason
  links          how the drafts relate (the catalog's Related lines), for assemble.py
  levers         one bundle per savings lever (SV1's rows) for the insights: savings, evidence quoted as digest.md
                 writes it, questions, overlaps, a draft title, bottom line and actions
Figures are read from the cards by label, like video.py does: a card or label that is missing skips that entry with the
reason, never the whole run. usage_report.py calls write_candidates() after a full run. Standard library only.
"""
import argparse
import datetime as dt
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import layout  # noqa: E402
import usage_report as UR  # noqa: E402  (the digest's number formats, so every figure reads exactly as digest.md has it)
import validate as VA  # noqa: E402  (which questions the report shows; the self-check of the drafts)

NAME = 'candidates.json'
NOTES = ('notes-insights.json', 'notes-optimizations.json')      # what the skills Write for assemble.py, in <out>/data/
SETTINGS = '~/.claude/settings.json'
HOOKS = '~/.claude/hooks/claude-usage'
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
CATEGORIES = ('cost', 'context', 'cache', 'delegation', 'workflow', 'tooling', 'reliability')
DOCS = {'settings': 'Settings', 'hooks': 'Hooks', 'hooks-guide': 'Hooks guide', 'statusline': 'Status line', 'costs': 'Manage costs',
        'prompt-caching': 'Prompt caching', 'sub-agents': 'Subagents', 'mcp': 'MCP', 'skills': 'Skills', 'memory': 'Memory (CLAUDE.md)',
        'model-config': 'Model configuration'}


class Missing(Exception):
    """A figure a rule needs isn't in this report: the entry is skipped with this as the reason."""


# ---------- reading the report ----------

class Report:
    """The all-projects figures of metrics.json, found by card id and label (never by position)."""

    def __init__(self, metrics):
        meta = metrics.get('meta') or {}
        self.metrics, self.generated, self.range = metrics, meta.get('generated'), meta.get('range') or {}
        top = (metrics.get('data') or {}).get('all') or {}
        self.cards, head = top.get('cards') or {}, top.get('headline') or {}
        self.spend, self.days = (head.get('hero') or {}).get('value') or 0, head.get('days') or 0
        self.shown = VA.report_cards(metrics)[0]

    def blocks(self, cid):
        c = self.cards.get(cid)
        if not c or c.get('empty'):
            raise Missing(f'{cid} has no data in this report')
        return UR.flat_blocks(c)

    def kpi(self, cid, label, prefix=False):
        for b in self.blocks(cid):
            for i in (b.get('items') or []) if b.get('kind') == 'kpis' else []:
                lb = i.get('label') or ''
                if (lb.startswith(label) if prefix else lb == label) and i.get('value') is not None:
                    return i
        raise Missing(f'{cid} has no “{label}” figure')

    def has(self, cid, label, prefix=False):
        try:
            return self.kpi(cid, label, prefix)
        except Missing:
            return None

    def table(self, cid, key, title=None):
        """The rows of the card's table that has a column `key` (and that title, when given)."""
        for b in self.blocks(cid):
            if b.get('kind') == 'table' and (title is None or b.get('title') == title) and \
                    any(c.get('key') == key for c in b.get('columns') or []):
                return b.get('rows') or []
        raise Missing(f'{cid} has no “{title}” table' if title else f'{cid} has no table with a “{key}” column')

    def rows(self, cid, key, title=None):
        try:
            return self.table(cid, key, title)
        except Missing:
            return []

    def series(self, cid, title):
        """A chart's first series as (category, value), largest first; the “Other (n)” bucket left out."""
        for b in self.blocks(cid):
            ch = b.get('chart') if b.get('kind') == 'chart' else None
            if ch and ch.get('title') == title and ch.get('series'):
                s = ch['series'][0]
                pairs = [(c, v) for c, v in zip(ch.get('categories') or [], s.get('values') or [])
                         if isinstance(v, (int, float)) and not str(c).startswith('Other (')]
                return sorted(pairs, key=lambda p: -p[1]), s.get('unit') or ch.get('unit')
        raise Missing(f'{cid} has no “{title}” chart')

    def insights_text(self, cid):
        c = self.cards.get(cid) or {}
        return ' '.join(x for x in [c.get('insight')] + list(c.get('insights') or []) if x)

    def fact(self, cid, *labels, prefix=False):
        """Figures exactly as digest.md writes them, e.g. 'Net saving: $127 all time, ≈ $185 per 30 days (…)'."""
        return ev(cid, ' · '.join(kpi_text(self.kpi(cid, lb, prefix)) for lb in labels))

    def chart_fact(self, cid, title, n=4):
        """A chart's largest entries as digest.md writes them: 'By model — Opus 5: $375; Opus 5.5: $237'."""
        pairs, unit = self.series(cid, title)
        return ev(cid, f'{title} — ' + '; '.join(f'{c}: {UR.md_cell(v, unit)}' for c, v in pairs[:n]))

    def row_fact(self, cid, key, row, keys, title=None):
        """A table row in the digest's words: its text cells, then 'Column: value' with each value as digest.md formats it,
        e.g. 'You came back after a break · Misses: 9 · Extra cost, all time: $30.18'."""
        cols = next((b['columns'] for b in self.blocks(cid) if b.get('kind') == 'table' and (title is None or b.get('title') == title)
                     and any(c.get('key') == key for c in b.get('columns') or [])), [])
        info = {c['key']: c for c in cols}
        text = [str(row[k]) for k in keys if k in row and info.get(k, {}).get('unit') in (None, 'text') and row[k] not in (None, '')]
        nums = [f"{info[k]['label']}: {UR.md_cell(row[k], info[k].get('unit'))}" for k in keys
                if k in row and k in info and info[k].get('unit') not in (None, 'text') and row[k] is not None]
        return ev(cid, ' · '.join(text + nums))

    def questions(self, *cids):
        """The ids the report shows, in order: a hidden or missing card can't be cited."""
        out = []
        for c in cids:
            if c in self.shown and c not in out:
                out.append(c)
        if not out or out[0] != cids[0]:
            raise Missing(f'{cids[0]} is not shown in this report')
        return out[:6]


def soft(fn, *a, **k):
    """An optional figure: None when the report lacks it, instead of skipping the whole entry."""
    try:
        return fn(*a, **k)
    except Missing:
        return None


def kpi_text(i):
    """One figure as md_card() writes it into digest.md."""
    t = f"{i['label']}: {UR.md_cell(i['value'], i.get('unit'))}"
    if i.get('month') is not None:
        t += f" all time, ≈ {UR.md_cell(i['month'], 'usd')} per 30 days"
    if i.get('sub'):
        t += f" ({i['sub']})".replace('\n', ' · ')
    return t


def clip(s, n):
    """Text cut to a schema limit, so an unusually long name never makes a draft invalid."""
    return s if not isinstance(s, str) or len(s) <= n else s[:n - 1].rstrip() + '…'


def ev(q, fact):
    return {'question': q, 'fact': clip(fact, 240)}


class Setup:
    """What config.json says is set already, so no draft duplicates, conflicts with or overwrites it."""

    def __init__(self, config):
        self.home = config.get('home') or ''
        user = {}
        for group in ('user_settings', 'user_local_settings'):
            user.update(config.get(group) or {})
        # ~/.claude/settings.json itself, the file every draft writes to
        self.main = next((js for f, js in sorted((config.get('user_settings') or {}).items())
                          if os.path.basename(f.replace('\\', '/')) == 'settings.json' and isinstance(js, dict)), {})
        self.user = {}
        for js in user.values():                         # settings.local.json after settings.json: the later one wins,
            for k, x in (js if isinstance(js, dict) else {}).items():      # objects (env, skillOverrides…) key by key
                self.user[k] = dict(self.user[k], **x) if isinstance(x, dict) and isinstance(self.user.get(k), dict) else x
        self.files = dict(user)
        for proj in (config.get('project_settings') or {}).values():
            self.files.update(proj or {})

    def get(self, *path):
        cur = self.user
        for p in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(p)
        return cur

    def hooks(self):
        """Every hook command in every settings file: (file, event, JSON pointer, command)."""
        out = []
        for f in sorted(self.files):
            for ev_, groups in sorted(((self.files[f] or {}).get('hooks') or {}).items()) if isinstance(self.files[f], dict) else []:
                for gi, g in enumerate(groups if isinstance(groups, list) else []):
                    for hi, h in enumerate((g or {}).get('hooks') or [] if isinstance(g, dict) else []):
                        if isinstance(h, dict) and isinstance(h.get('command'), str):
                            out.append((f, ev_, f'/hooks/{ev_}/{gi}/hooks/{hi}/command', h['command']))
        return out

    def has_hook(self, script):
        return any(script in cmd for _, _, _, cmd in self.hooks())

    def expand(self, cmd):
        """A hook command with ~, $HOME and ${HOME} spelled out as this report's home folder."""
        if not self.home:
            return cmd
        cmd = re.sub(r'\$\{HOME\}|\$HOME(?![A-Za-z0-9_])', lambda m: self.home, cmd)
        return re.sub(r'(^|[\s"\'=])~(?=/)', lambda m: m.group(1) + self.home, cmd)

    def has_statusline(self):
        return any(isinstance(js, dict) and js.get('statusLine') for js in self.files.values())

    def mac(self):
        return self.home.startswith('/Users/')

    def tilde(self, p):
        return '~' + p[len(self.home):] if self.home and p.startswith(self.home) else p


# ---------- small helpers for the drafts ----------

usd, tok, pct, span = UR.f_usd, UR.f_tok, UR.f_pct, UR.f_span


def save(so, mo, *notes):
    """A saving as the report words it; notes go inside its parenthesis: '$127 all time (≈ $185 per 30 days, SV4)'."""
    return f'{usd(so)} all time (≈ {", ".join((f"{usd(mo)} per 30 days",) + notes)})'


def size(n):
    """A token count for prose: 150K, 1M (f_tok without a trailing .0)."""
    return re.sub(r'\.0(?=[KMB]$)', '', tok(n))


def savings(so, mo, kind, basis):
    return {'usd_so_far': so, 'usd_per_month': mo, 'kind': kind, 'basis': basis}


def month(R, so):
    return round(so * 30 / R.days, 2) if R.days else so


def plural(n, word, many=None):
    """'1 day', '2 days', '1.5 days'."""
    return f"{n:,g} {word if n == 1 else many or word + 's'}" if isinstance(n, (int, float)) else f'{n} {many or word + "s"}'


def sentence(*parts):
    return ' '.join(p for p in parts if p)


def and_list(xs):
    xs = [x for x in xs if x]
    return xs[0] if len(xs) == 1 else ', '.join(xs[:-1]) + ' and ' + xs[-1] if xs else ''


def lead_int(s):
    m = re.match(r'\s*([\d,]+)', s or '')
    return int(m.group(1).replace(',', '')) if m else None


def parse_tok(s):
    """'8.0K' -> 8000, '1M' -> 1000000: a size as f_tok writes it."""
    m = re.match(r'\s*([\d.]+)\s*([KMB]?)', str(s or ''))
    if not m:
        return None
    return float(m.group(1)) * {'': 1, 'K': 1e3, 'M': 1e6, 'B': 1e9}[m.group(2)]


def docs(*keys):
    return [{'title': DOCS[k], 'url': f'https://code.claude.com/docs/en/{k}'} for k in keys]


def hook_steps(script, event, args='', matcher=None, runner='python3'):
    """The catalog's install template: a copy under ~/.claude/hooks/claude-usage/ and one hook group appended to settings."""
    cmd = f'{runner} "$HOME/.claude/hooks/claude-usage/{script}"' + (f' {args}' if args else '')
    group = {'hooks': [{'type': 'command', 'command': cmd, 'timeout': 10}]}
    if matcher:
        group = {'matcher': matcher, 'hooks': group['hooks']}
    return [{'action': 'write_file', 'path': f'{HOOKS}/{script}', 'source': f'hooks/{script}', 'mode': '755'},
            {'action': 'merge_json', 'path': SETTINGS, 'value': {'hooks': {event: [group]}}}], cmd


def hook_manual(script, event, cmd, matcher=None):
    group = {'hooks': [{'type': 'command', 'command': cmd, 'timeout': 10}]}
    if matcher:
        group = {'matcher': matcher, 'hooks': group['hooks']}
    return [f'Copy scripts/hooks/{script}' + (' (plus _session.py and prices.json)' if script.endswith('.py') else '')
            + f' from the claude-usage plugin to {HOOKS}/.',
            f'In {SETTINGS} add under hooks.{event}: {json.dumps(group, ensure_ascii=False)}.',
            'Start a new session.']


def opt(oid, title, category, kind, problem, what, questions, effort, risk, manual, undo, docs_, sv=None, tradeoffs=None,
        steps=None, apply_summary=None, verify=None, insights=None):
    """One optimization, its keys in the order optimizations.json uses."""
    if sv:
        sv['basis'] = clip(sv['basis'], 400)
    o = {'id': oid, 'title': clip(title, 90), 'category': category, 'kind': kind, 'problem': clip(problem, 500),
         'what_it_does': clip(what, 700)}
    if insights:
        o['insights'] = list(insights)
    o['questions'] = questions
    if sv:
        o['savings'] = sv
    o.update(effort=effort, risk=risk)
    if tradeoffs:
        o['tradeoffs'] = clip(tradeoffs, 500)
    if steps:
        o['apply'] = {'summary': clip(apply_summary, 300), 'steps': steps}
    o['manual'] = [clip(m, 400) for m in manual[:10]]
    if verify:
        o['verify'] = clip(verify, 300)
    o.update(undo=clip(undo, 300), docs=docs_)
    return o


def with_related(o, rel):
    """The same optimization with `related` in its usual place (after tradeoffs), or without it when empty."""
    out = {}
    for k, x in o.items():
        if k == 'related':
            continue
        if k in ('apply', 'manual') and 'related' not in out and rel:
            out['related'] = rel
        out[k] = x
    return out


def draft(rule, o, levers=(), notes=None, carry=None):
    """carry: settings keys an applied earlier version of this optimization may have set that this one should keep
    (assemble.py adds them back from apply.py's record, so a newer version never undoes them)."""
    return {'kind': 'draft', 'rule': rule, 'draft': o, 'levers': list(levers), 'notes': notes or [], 'carry': carry}


def judge(why, ask, facts, o=None, levers=(), carry=None):
    return {'kind': 'judgment', 'why': why, 'ask': ask, 'facts': [f for f in facts if f], 'draft': o, 'levers': list(levers),
            'carry': carry}


def skip(reason):
    return {'kind': 'skip', 'reason': reason}


# ---------- figures several entries share ----------

def lever_row(R, key):
    """SV1's row for a lever: SV1 shows each lever's card and a label; the model levers share SV6 and differ in wording."""
    card, prefix = LEVER_ROWS[key]
    return next((r for r in R.rows('SV1', 'c') if r.get('c') == card and (r.get('l') or '').startswith(prefix)), None)


def sv3_row(R, cause):
    return next((r for r in R.rows('SV3', 'w') if r.get('k') == cause), None)


def window(R):
    """The context window of the latest session's model (CX5), e.g. (1000000, 'Opus 5.5')."""
    k = R.has('CX5', 'Of the context window')
    m = re.search(r'([\d.]+[KM]) window', (k or {}).get('sub') or '')
    start = R.has('CX5', 'A new session starts with')
    return (parse_tok(m.group(1)) if m else None), ((start or {}).get('sub') or '').split(' · ')[0] or 'your model'


def main_lifetime(R):
    """The main thread's current cache lifetime: SV5's actual cost equals its 5-minute or its 1-hour replay."""
    r = next((x for x in R.rows('SV5', 'c5', 'Cache read + write cost per thread kind') if x.get('k') == 'Main threads'), None)
    if not r:
        return None
    return '5-minute' if abs(r['a'] - r['c5']) < abs(r['a'] - r['c1']) else '1-hour'


def mix_of(k):
    """SV5's cheapest mix, e.g. 'main 1 hour · subagents 5 min', from its tile's 'main 1 hour · subagents 5 min: $823 in total'."""
    return (k.get('sub') or 'the cheapest mix').split(':')[0]


TTL_KEYS = ['promptCacheTtl', 'subagentPromptCacheTtl']
PRICES = UR.Prices(os.path.join(HERE, 'prices.json'))
ALIASES = ('opus', 'sonnet', 'haiku', 'fable', 'mythos')


def model_id(name):
    """'Opus 5.5' -> 'claude-opus-5-5': a model as the report names it, back to its canonical id."""
    m = re.match(r'([A-Za-z]+) (\d+)(?:\.(\d+))?$', (name or '').strip())
    return f'claude-{m.group(1).lower()}-{m.group(2)}' + (f'-{m.group(3)}' if m.group(3) else '') if m else UR.canon_model(name)


def model_pointer(model):
    """The JSON pointer to a model's effort level, its key escaped as RFC 6901 asks ('~' -> '~0', '/' -> '~1')."""
    return '/modelSettings/' + model.replace('~', '~0').replace('/', '~1') + '/effortLevel'


def slug(model):
    """A model setting's key as an id part: 'claude-opus-5-5[1m]' -> 'opus55', 'us.anthropic.claude-sonnet-5-v1:0' -> 'sonnet5'."""
    cm = UR.canon_model(model)
    s = re.sub(r'-(?=\d)', '', re.sub(r'^claude-', '', cm)) if cm.startswith('claude-') else cm
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')[:30] or 'model'


def in_price(name):
    """Input price per million tokens (prices.json, nearest family version when missing): which model is pricier."""
    return (PRICES.rate(model_id(name)) or {}).get('in')


def setting_model(val):
    """What a `model` setting names: (family, canonical id or None). 'opus[1m]' -> ('opus', None), 'claude-opus-5-5' ->
    ('opus', 'claude-opus-5-5'); 'opusplan' or 'default' name no single family -> (None, None)."""
    s = re.sub(r'\[.*?\]', '', str(val)).strip().lower()
    cm = UR.canon_model(s)
    fam = UR.model_parts(cm)[0]
    if fam:
        return fam, cm
    return (s if s in ALIASES else None), None


def compact_best(R):
    T = R.kpi('SV4', 'Best threshold')['value']
    return T, R.kpi('SV4', 'Net saving')


# ---------- the catalog: one function per entry, in catalog.md's order ----------

ENTRIES = []


def entry(name):
    def reg(fn):
        ENTRIES.append((name, fn))
        return fn
    return reg


@entry('main-model')
def main_model(R, S, v):
    """SV6's main-thread saving on the current model is over $10. A habit when the `model` setting already names that model
    (or its family's alias); when it names another model, or none, whether to set it is Claude's call."""
    k = R.kpi('SV6', 'Main threads on ', prefix=True)
    cur = k['label'][len('Main threads on '):]
    if k['value'] <= 10:
        return skip(f"SV6: main threads on {cur} would have saved {usd(k['value'])}, under the $10 bar")
    cur_id = model_id(cur)
    fam = UR.model_parts(cur_id)[0]
    base = in_price(cur)
    pairs = (soft(R.series, 'OV2', 'By model') or ([], None))[0]
    pricier = [(c, x) for c, x in pairs if c != cur and base and (in_price(c) or 0) > base and R.spend and x >= 0.05 * R.spend]
    share = sum(x for _, x in pricier) / R.spend * 100 if R.spend else 0
    slash_row = next((r for r in R.rows('EX1', 'n', 'Slash commands') if r.get('k') == '/model'), None)
    slash = (slash_row or {}).get('n')
    facts = [R.fact('SV6', k['label']), soft(R.chart_fact, 'OV2', 'By model')] + ([R.row_fact('EX1', 'n', slash_row, ('k', 'n'), 'Slash commands')] if slash else [])
    problem = sentence(f"{and_list(f'{c} ({usd(x)})' for c, x in pricier)}, pricier than {cur}, did {pct(share)} of your spend "
                       '(main threads and subagents, OV2).' if pricier else '',
                       f"The same main-thread tokens on {cur} would have saved {save(k['value'], k['month'], 'SV6')}.",
                       f'You ran /model {plural(slash, "time")} (EX1).' if slash else '')
    sv = lambda extra: savings(k['value'], k['month'], 'theoretical', f'SV6: the same main-thread tokens re-priced at {cur} list prices, '
                                                                      'pricier calls only.' + extra)
    model = S.get('model')
    set_fam, set_id = setting_model(model) if model else (None, None)
    if model and (set_id == cur_id or (set_id is None and set_fam == fam)):
        o = opt('main-model-habit', f'Stay on {cur}; switch to a pricier model only for the hard step', 'cost', 'habit', problem,
                f'Your settings already default to model={model}, so new sessions start there. What is left is the habit: switch to a '
                f'stronger model with /model only for one hard subtask, then switch back. Switching models mid-session also re-writes the '
                f'cache, so switch at a natural break or in a new session.',
                R.questions(*(['SV6', 'OV2'] + (['EX1'] if slash else []))), 'habit', 'low',
                ['Start sessions on the default (check with /model).',
                 'For a hard design or debugging step, run /model and pick the stronger model, do that step, then /model back.',
                 'Switch at a natural break: a model switch re-writes the cache.'],
                "Nothing to undo: it's a habit.", docs('model-config', 'costs'),
                sv=sv(f' With model={model} already set, part of it is realised for new work; this is the ceiling, not what is left.'),
                tradeoffs='A cheaper model can take more turns or do worse on the hardest problems; that is why you switch up for those, '
                          'not for whole sessions.',
                verify=f"In the next report, OV2's by-model split shows {cur} carrying most of the cost.", insights=['main_model'])
        return draft(f"SV6: main threads on {cur} would have saved {save(k['value'], k['month'])}, over the $10 bar; model={model} already "
                     f"names {cur}'s family, so it's a habit.", o, ['main_model'])
    alias = fam or cur.split()[0].lower()
    oid = f'main-model-{alias}'
    o = opt(oid, f'Make {cur} your default model', 'cost', 'setting', problem,
            f'Sets model to "{alias}" in {SETTINGS}, so new sessions start on {cur}' + (f' instead of {model}' if model else '')
            + '. Switch to a stronger model with /model only for one hard subtask, then back.',
            R.questions(*(['SV6', 'OV2'] + (['EX1'] if slash else []))), 'one-click', 'medium',
            [f'In {SETTINGS} set "model": "{alias}" (check that /model shows {cur} for it).', 'Start a new session.'],
            f'apply.py undo {oid}, or ' + (f'set "model" back to "{model}"' if model else 'remove "model"') + f' in {SETTINGS}.',
            docs('model-config', 'settings'), sv=sv(''),
            tradeoffs='A cheaper model can take more turns or do worse on the hardest problems; switch up for those.',
            steps=[{'action': 'set_json', 'path': SETTINGS, 'pointer': '/model', 'value': alias}],
            apply_summary=f'Set "model": "{alias}" in {SETTINGS}.',
            verify=f"A new session starts on {cur} (/model), and the next report's OV2 shows it carrying most of the cost.",
            insights=['main_model'])
    return judge(f"SV6: main threads on {cur} would have saved {save(k['value'], k['month'])}; "
                 + (f'settings name model={model}, which is not {cur}.' if model else f'no default model is set in {SETTINGS}.'),
                 f'Set the default (check the alias /model shows for {cur}; keep "[1m]" when the 1M window is needed), or keep it a habit '
                 '(write that one yourself)?', facts, o, ['main_model'])


@entry('subagent-model')
def subagent_model(R, S, v):
    """SV6's subagent saving is over $5 and env.CLAUDE_CODE_SUBAGENT_MODEL isn't set."""
    k = R.kpi('SV6', 'Subagents on ', prefix=True)
    target = k['label'][len('Subagents on '):]
    if k['value'] <= 5:
        return skip(f"SV6: subagents on {target} would have saved {usd(k['value'])}, under the $5 bar")
    have = S.get('env', 'CLAUDE_CODE_SUBAGENT_MODEL')
    if have:
        return skip(f'env.CLAUDE_CODE_SUBAGENT_MODEL is already {have}')
    alias = target.split()[0].lower()
    v['sub_target'] = target
    share = R.has('SE5', 'Share of spend')
    try:
        top = R.series('SE5', 'Cost by model')[0][0]
    except (Missing, IndexError):
        top = None
    explore = R.has('SV6', 'Explore subagents on ', prefix=True)
    oid = f'subagent-model-{alias}'
    o = opt(oid, f'Run subagents on {target} by default', 'delegation', 'setting',
            sentence(f"Subagents inherit your main model: {usd(top[1])} of {share['sub']} subagent spend ran on {top[0]} (SE5)."
                     if top and share and share.get('sub') and top[0] != target else '',
                     f"The same subagent work on {target} would have saved {save(k['value'], k['month'], 'SV6')}."),
            f'Sets CLAUDE_CODE_SUBAGENT_MODEL={alias} in {SETTINGS} env. Subagents that don\'t name a model run on {target}, including '
            f'Explore, Plan and general-purpose. A per-call model ("use Opus for this review") or an agent file\'s own model field still wins.',
            R.questions('SV6', 'SE5', 'OV7'), 'one-click', 'medium',
            [f'In {SETTINGS} add "env": {{"CLAUDE_CODE_SUBAGENT_MODEL": "{alias}"}}.', 'Start a new session.',
             'When a subagent needs the strongest model, say so in the request (e.g. "review this with an Opus subagent").']
            + ([f"Optionally give read-only research agents model: {explore['label'].split(' on ')[-1].split()[0].lower()} in their agent files "
                f"(~/.claude/agents/<name>.md): SV6 puts Explore subagents on {explore['label'].split(' on ')[-1]} at "
                f"{save(explore['value'], explore['month'])}."] if explore and explore.get('month') is not None else []),
            f'apply.py undo {oid}, or remove env.CLAUDE_CODE_SUBAGENT_MODEL from {SETTINGS}.', docs('sub-agents', 'model-config'),
            sv=savings(k['value'], k['month'], 'theoretical', f'SV6: the same subagent tokens at {target} list prices, pricier calls only.'),
            tradeoffs=f'Long implementation and review agents may take more turns or miss more on {target}; ask for a stronger model '
                      'explicitly when delegating a hard review or design task.',
            steps=[{'action': 'merge_json', 'path': SETTINGS, 'value': {'env': {'CLAUDE_CODE_SUBAGENT_MODEL': alias}}}],
            apply_summary=f'Add env CLAUDE_CODE_SUBAGENT_MODEL={alias} to {SETTINGS}.',
            verify=f"In the next report, SE5's cost by model shows {target} carrying most subagent calls.", insights=['sub_model'])
    return draft(f"SV6: subagents on {target} would have saved {save(k['value'], k['month'])}, over the $5 bar; CLAUDE_CODE_SUBAGENT_MODEL isn't set.",
                 o, ['sub_model'])


@entry('effort-default')
def effort_default(R, S, v):
    """OV4's thinking share is over 35%, an effort level is high/xhigh/max, and output is a real part of cost (OV3). Always a call
    for Claude: effort changes quality."""
    think, out = R.kpi('OV4', 'Thinking share of output'), R.kpi('OV3', 'Output')
    glob = S.get('effortLevel')
    per = {m: x.get('effortLevel') for m, x in sorted((S.get('modelSettings') or {}).items())
           if isinstance(x, dict) and x.get('effortLevel') in EFFORTS}
    high = [lv for lv in [glob] + list(per.values()) if lv in ('high', 'xhigh', 'max')]
    if think['value'] <= 35:
        return skip(f"OV4: thinking is {pct(think['value'])} of output, not over 35%")
    if not high:
        return skip('no effort level is set to high, xhigh or max')
    if out['value'] < 10:
        return skip(f"OV3: output is only {pct(out['value'])} of cost")
    try:
        cats, _ = R.series('OV4', 'Cost by effort level')
        per_call = {m.group(1): m.group(2) for c, _ in cats for m in [re.match(r'(\w+) \((\$[\d.,]+) per call\)', c)] if m}
        cost_fact = R.chart_fact('OV4', 'Cost by effort level', 5)
    except Missing:
        per_call, cost_fact = {}, None
    set_ = f'effortLevel={glob}' if glob else 'no global effortLevel'
    if per:
        set_ += '; ' + ', '.join(f'modelSettings.{m}.effortLevel={x}' for m, x in per.items())
    out_usd = parse_money(out.get('sub'))
    facts = [R.fact('OV4', 'Thinking share of output'), cost_fact, soft(R.fact, 'OV3', 'Output')]
    why = (f"OV4: thinking is {pct(think['value'])} of output (over 35%); settings: {set_}; output is {pct(out['value'])} of cost (OV3)"
           + (f"; thinking is at most about {usd(out_usd * think['value'] / 100)} of that output cost, an upper bound of what less "
              'thinking could save' if out_usd else '') + '.')
    raise_ = [(m, x) for m, x in per.items() if glob in EFFORTS and EFFORTS.index(x) > EFFORTS.index(glob)]
    if raise_:
        mid, lvl = raise_[0]
        name, oid = UR.model_name(mid), f'effort-{slug(mid)}-{glob}'
        ptr = model_pointer(mid)
        thinking = R.has('OUT1', 'Thinking')
        share = f"{pct(thinking['value'])} of all output tokens (OUT1)" if thinking else f"{pct(think['value'])} of output (OV4)"
        o = opt(oid, f'Let {name} use your global {glob} effort instead of {lvl}', 'cost', 'setting',
                f'settings.json sets effortLevel={glob}, but modelSettings raise {name} to {lvl}, so every {name} session starts there. '
                + (f'At {lvl}, calls cost {per_call[lvl]} against {per_call[glob]} at {glob} (OV4), and thinking is {share}.'
                   if lvl in per_call and glob in per_call else f'Thinking is {share}.'),
                f'Removes the effortLevel override for {mid} from {SETTINGS}, so {name} uses your global effortLevel={glob}. You can still '
                f'raise it for one hard task with /effort {lvl}.',
                R.questions('OV4', 'OUT1', 'OV3'), 'one-click', 'medium',
                [f'Open {SETTINGS}.', f'Delete "effortLevel": "{lvl}" inside modelSettings → {mid}.',
                 f'Start a new session; use /effort {lvl} for a hard task, then /effort {glob}.'],
                f'apply.py undo {oid}, or put "effortLevel": "{lvl}" back under modelSettings.{mid}.', docs('model-config', 'settings'),
                tradeoffs='Less thinking on hard problems unless you raise it with /effort; the per-call gap in OV4 is descriptive, since tasks '
                          'differ between effort levels, so no saving is claimed.',
                steps=[{'action': 'unset_json', 'path': SETTINGS, 'pointer': ptr}],
                apply_summary=f'Remove modelSettings.{mid}.effortLevel from {SETTINGS}.',
                verify=f'A new {name} session shows effort {glob} (/effort), and the next report\'s OV4 shows a smaller {lvl} share.',
                insights=['main_model'])
    else:
        # lower every level that would still win: the global one, and each model's own (an override beats the global level)
        over = [(m, x) for m, x in per.items() if x in ('high', 'xhigh', 'max')]
        steps = ([{'action': 'set_json', 'path': SETTINGS, 'pointer': '/effortLevel', 'value': 'medium'}] if glob in ('high', 'xhigh', 'max') else [])
        steps += [{'action': 'set_json', 'path': SETTINGS, 'pointer': model_pointer(m), 'value': 'medium'} for m, _ in over][:8 - len(steps)]
        names = and_list([UR.model_name(m) for m, _ in over])
        where = and_list(['effortLevel' if glob in ('high', 'xhigh', 'max') else ''] + [f'modelSettings.{m}.effortLevel' for m, _ in over])
        oid = 'effort-default'
        o = opt(oid, 'Lower the default effort to medium' + (f' (also for {names})' if over and glob in ('high', 'xhigh', 'max') else
                                                            f' for {names}' if over else ''), 'cost', 'setting',
                f"Thinking is {pct(think['value'])} of your output (OV4) with {set_}, and output is {pct(out['value'])} of cost (OV3).",
                f'Sets {where} to medium in {SETTINGS}' + ('; a model\'s own level beats the global one, so those change too' if over else '')
                + '. Raise it for one hard task with /effort high, then go back.',
                R.questions('OV4', 'OV3', 'OUT1'), 'one-click', 'medium',
                [f'In {SETTINGS} set {where} to "medium".', 'Use /effort high for hard tasks, then /effort medium.'],
                f'apply.py undo {oid}, or set {where} back to what it was ({set_}).', docs('model-config', 'settings'),
                tradeoffs='Less thinking on hard problems. Changing effort mid-session can also invalidate the cache.',
                steps=steps, apply_summary=f'Set {where} to "medium" in {SETTINGS}.', verify="The next report's OV4 shows medium carrying most calls.",
                insights=['main_model'])
    return judge(why, 'Propose it only with strong evidence (effort changes quality): adopt the draft, pick another level, or leave it '
                      'as an insight. If you give it a saving, mark it upper_bound and say how you got it.', facts, o, ['main_model'])


@entry('stop-hook-followup')
def stop_hook_followup(R, S, v):
    """SV1's Stop-hook follow-up lever is over $5. No generic apply: it is the user's own automation."""
    row = lever_row(R, 'stop_hook')
    if not row or (row.get('u') or 0) <= 5:
        return skip('SV1: Stop-hook follow-up work is under $5' if row else 'SV1 has no Stop-hook follow-up lever')
    hooks = sorted([r for r in R.rows('EX5', 'tot') if r.get('e') == 'Stop'], key=lambda r: -(r.get('n') or 0))
    name = hooks[0]['s'] if hooks else 'Your Stop hook'
    where = sorted({S.tilde(f) for f, e, _, cmd in S.hooks() if e == 'Stop' and (not hooks or hooks[0]['s'] in cmd)}, key=lambda f: (len(f), f))
    facts = [R.row_fact('SV1', 'c', row, ('l', 'u', 'mo'))]
    facts += [R.row_fact('EX5', 'tot', r, ('e', 's', 'n', 'tot')) for r in hooks[:3]]
    mem = next((r for r in R.rows('OUT3', 'n') if r.get('k') == 'Memory'), None)
    if mem:
        facts.append(R.row_fact('OUT3', 'n', mem, ('k', 'n', 'a')))
    if where:
        facts.append({'config': ', '.join(where), 'fact': f'the Stop hook command is set in {plural(len(where), "settings file")}'})
    o = opt('stop-hook-followup', f"Make {name}'s follow-up work cheaper", 'cost', 'hook',
            sentence(f"{name} ran {plural(hooks[0]['n'], 'time')} on Stop (EX5)." if hooks else '',
                     f"The work Claude does after it and before your next prompt costs up to {save(row['u'], row['mo'], 'SV1')}."),
            'Makes the hook ask for less: it speaks up only after turns that changed files or ran long, does nothing when stop_hook_active '
            'is set (so it cannot loop), and asks for one short, specific write instead of an open-ended task.',
            R.questions('SV1', 'EX5', 'OUT3' if mem else 'EX5'), 'minutes', 'low',
            [f"Open the hook's script (the command is in {and_list(where[:2] + ([f'{len(where) - 2} more settings files'] if len(where) > 2 else []))})."
             if where else "Open the hook's script (see /hooks).",
             'Make it exit without output unless the turn changed files or ran long.',
             'Read stop_hook_active from its JSON input and do nothing when it is true, so it cannot loop.',
             'Keep what it asks Claude to write short and specific, or move summarising to a script that calls a cheaper model.'],
            'Revert the change to the hook script.', docs('hooks', 'hooks-guide'),
            sv=savings(row['u'], row['mo'], 'upper_bound', "SV1's Stop-hook follow-up lever: the calls Claude makes after the hook fires and "
                                                          'before your next prompt; an upper bound, since some of that work is wanted.'),
            tradeoffs='A turn that mattered but changed no files no longer gets the reminder; you can still ask for it. If a tool '
                      'generates the hook, change it at the source or a rebuild overwrites the edit.',
            verify="In the next report, SV1's Stop-hook lever drops.", insights=['stop_hook'])
    return judge(f"SV1: Stop-hook follow-up work would have saved up to {save(row['u'], row['mo'])}, over the $5 bar.",
                 "Make the draft specific (which script, the real change, where its source lives), or drop it if the follow-up work is wanted.",
                 facts, o, ['stop_hook'])


def configured_window(S):
    """The auto-compact window the user set, in tokens, and how the setting reads: (200000, 'autoCompactWindow=200k')."""
    for val, name in ((S.get('env', 'CLAUDE_CODE_AUTO_COMPACT_WINDOW'), 'env.CLAUDE_CODE_AUTO_COMPACT_WINDOW'),
                      (S.get('autoCompactWindow'), 'autoCompactWindow')):
        n = parse_tok(str(val).upper()) if val not in (None, '', 'auto') else None
        if n:
            return int(n), f'{name}={val}'
    return None, None


def next_window(R, T):
    """The first SV4 threshold above the best one that still saves money: where auto-compact's window goes."""
    rows = sorted([r for r in R.table('SV4', 'mo') if isinstance(r.get('t'), (int, float))], key=lambda r: r['t'])
    return next((r for r in rows if r['t'] > T and (r.get('u') or 0) > 0), None)


@entry('auto-compact-window')
def auto_compact_window(R, S, v):
    """SV4's best net saving is over $5 and the next threshold up is well below the model's window. The window is set to the
    first SV4 threshold above the best one (compaction triggers as usage approaches the window), with that row's saving."""
    T, net = compact_best(R)
    if net['value'] <= 5:
        return skip(f"SV4: compacting at {size(T)} would have saved {usd(net['value'])}, under the $5 bar")
    row = next_window(R, T)
    if not row:
        return skip(f'SV4: no threshold above {size(T)} saves money')
    W = int(row['t'])
    win, model = window(R)
    if not win:
        return skip("CX5 doesn't give the model's context window")
    if W >= 0.75 * win:
        return skip(f"the model's window is {size(win)}: auto-compact already runs near {size(W)}")
    have, setting = configured_window(S)
    if S.get('env', 'CLAUDE_CODE_AUTO_COMPACT_WINDOW'):
        return skip('env.CLAUDE_CODE_AUTO_COMPACT_WINDOW is set, and it wins over the setting')
    if have and have <= W:
        return skip(f'{setting} already compacts at or below {size(W)}')
    if S.get('autoCompactEnabled') is False:
        return skip('autoCompactEnabled is false')
    v['window'] = W
    now = min(have, win) if have else win
    c1, c6, big = R.has('CX1', 'Calls above 200K'), R.has('CX6', 'From calls above 200K'), R.has('CX1', 'Largest ever')
    oid = f'auto-compact-{W // 1000}k'
    o = opt(oid, f'Let auto-compact run at about {size(W)} instead of near {size(now)}', 'context', 'setting',
            sentence(f'Your {setting} makes auto-compact wait until the context nears {size(now)}.' if have else
                     f'On {model} with a {size(win)} window, auto-compact waits until the context nears {size(win)}.',
                     f"{pct(c1['value'])} of calls ran above 200K (CX1) and made {pct(c6['value'])} of cache-read spend (CX6)." if c1 and c6 else '',
                     f"The largest context reached {size(big['value'])} (CX1)." if big else '',
                     f"Compacting at {size(W)} would have saved {save(row['u'], row['mo'], 'SV4')}."),
            f'Sets autoCompactWindow to {W} in {SETTINGS}. Claude Code then summarises the conversation as it approaches {size(W)}, so '
            f"compaction lands between {size(T)} and {size(W)}, where SV4's saving is highest, and big contexts stop being re-read on every call.",
            R.questions('SV4', 'CX1', 'CX6', 'OV5'), 'one-click', 'medium',
            [f'Open {SETTINGS}.', f'Set "autoCompactWindow": {W} at the top level (or via /config → auto-compact window).',
             'Start a new session.'],
            f'apply.py undo {oid}, or ' + (f'set "autoCompactWindow" back to {S.get("autoCompactWindow")!r}' if S.get('autoCompactWindow') else
                                           'remove "autoCompactWindow"') + f' in {SETTINGS}.', docs('settings', 'costs'),
            sv=savings(row['u'], row['mo'], 'theoretical',
                       f"SV4's {size(W)} row: every main thread replayed with a compaction at {size(W)}, net of the compactions' own cost."),
            tradeoffs='Claude Code recommends its automatic window. A compaction drops detail and costs one summary call, and a long '
                      'single task can lose some context mid-way.',
            steps=[{'action': 'merge_json', 'path': SETTINGS, 'value': {'autoCompactWindow': W}}],
            apply_summary=f'Set "autoCompactWindow": {W} in {SETTINGS}.',
            verify=f'In a long session, /context shows the auto-compact threshold near {size(W)}, and a compaction happens before the context passes it.',
            insights=['compact'])
    return draft(f"SV4: best threshold {size(T)} (net {usd(net['value'])}, over $5); the next row up, {size(W)}, saves {usd(row['u'])} and is well "
                 f"below the {size(win)} window; " + (f'{setting} is higher.' if have else "autoCompactWindow isn't set."), o, ['compact'])


@entry('context-guard')
def context_guard(R, S, v):
    """Same data as auto-compact-window: SV4's best net saving is over $5. The notice fires at SV4's best threshold. When the
    user's own auto-compact window already sits near it, the notice adds little and SV4's saving may already be theirs:
    Claude's call."""
    T, net = compact_best(R)
    if net['value'] <= 5:
        return skip(f"SV4: compacting at {size(T)} would have saved {usd(net['value'])}, under the $5 bar")
    if S.has_hook('context_guard.py'):
        return skip('a context_guard.py hook is already installed')
    win, _ = window(R)
    if win and T >= win:
        return skip(f"SV4's best threshold {size(T)} is not below the model's {size(win)} window")
    T = int(T)
    v['threshold'] = T
    have, setting = configured_window(S)
    row = soft(next_window, R, T)
    near = have and have <= (row['t'] if row else T + 50000)
    steps, cmd = hook_steps('context_guard.py', 'UserPromptSubmit', f'--threshold {T} --step 50000')
    c1, clear = R.has('CX1', 'Calls above 200K'), R.has('CX4', 'Median context at /clear')
    oid = f'context-guard-{T // 1000}k'
    o = opt(oid, f'Get a notice when the context passes {size(T)}', 'context', 'hook',
            sentence(f"Compacting whenever a main thread passes about {size(T)} would have saved {save(net['value'], net['month'], 'SV4')}.",
                     f'Your {setting} makes auto-compact wait until about {size(have)}.' if have else '',
                     (f"{pct(c1['value'])} of calls run above 200K (CX1)" if c1 else '')
                     + (f", while you /clear at a median of {size(clear['value'])} (CX4): the notice is for the sessions you keep going."
                        if c1 and clear else '.' if c1 else '')),
            f'Installs context_guard.py as a UserPromptSubmit hook. When the context passes {size(T)} it shows a one-line notice, repeated '
            'every 50K more, so you can /compact with a note on what to keep or /clear between subtasks. It never blocks a prompt.',
            R.questions('SV4', 'CX4', 'CX1'), 'one-click', 'low', hook_manual('context_guard.py', 'UserPromptSubmit', cmd),
            f'apply.py undo {oid}, or remove that hook group from {SETTINGS} and delete the script.', docs('hooks', 'costs'),
            sv=None if near else savings(net['value'], net['month'], 'theoretical',
                                         f'SV4: net saving if every main thread had compacted at {size(T)}; realised only when you act on the notice.'),
            tradeoffs='Only a line of text; the saving depends on you acting on it.', steps=steps,
            apply_summary=f'Copy context_guard.py to {HOOKS}/ and add it as a UserPromptSubmit hook (threshold {size(T)}) in {SETTINGS}.',
            verify=f'In a session past {size(T)}, the next prompt shows a notice with the context size.', insights=['compact'])
    if near:
        return judge(f"SV4: compacting at {size(T)} would have saved {save(net['value'], net['month'])}, but {setting} already compacts near "
                     f'{size(have)}: the notice would fire just before a compaction that happens anyway.',
                     'Only worth proposing when the user wants to compact earlier, by hand; the draft claims no saving (SV4 may count '
                     'compactions the setting already brings, if it was set during the period).', [R.fact('SV4', 'Best threshold', 'Net saving')],
                     o, ['compact'])
    return draft(f"SV4: compacting at {size(T)} would have saved {save(net['value'], net['month'])}, over the $5 bar; no context_guard.py hook yet.",
                 o, ['compact'])


@entry('big-read-guard')
def big_read_guard(R, S, v):
    """SV8's saving is over $3. --max-kb matches the reads SV8 counts: its token threshold at ~4 bytes a token, rounded up to 10 KB."""
    k = R.kpi('SV8', 'Saved if read in ranges')
    if k['value'] <= 3:
        return skip(f"SV8: reading large files in ranges would have saved {usd(k['value'])}, under the $3 bar")
    if S.has_hook('big_read_guard.py'):
        return skip('a big_read_guard.py hook is already installed')
    n = R.kpi('SV8', 'Whole-file reads over ', prefix=True)
    thr = parse_tok(n['label'][len('Whole-file reads over '):]) or 8000
    kb = max(20, int(math.ceil(thr * 4 / 1024 / 10.0)) * 10)
    steps, cmd = hook_steps('big_read_guard.py', 'PreToolUse', f'--max-kb {kb}', matcher='Read')
    cost = R.has('SV8', 'What they cost')
    top = next(iter(R.rows('EX8', 'rep', 'Files read most')), None)
    half = (k.get('sub') or '').replace('if a range kept', 'keeps').strip() or 'keeps half'
    o = opt('big-read-guard', 'Steer Claude to targeted reads of large files', 'context', 'hook',
            sentence(f"{plural(n['value'], 'whole-file read')} over {size(thr)} tokens" + (f" cost {usd(cost['value'])} to write and carry (SV8)." if cost else ' (SV8).'),
                     f"{top['f']} alone was read {plural(top['r'], 'time')} in {plural(top['s'], 'session')} (EX8)." if top else ''),
            f'Installs big_read_guard.py as a PreToolUse hook on Read. The first whole-file Read of a file over {kb} KB (about '
            f'{size(kb * 1024 / 4)} tokens) is denied with a reason telling Claude to grep for the part it needs and Read with offset/limit. '
            'If Claude does need the whole file, repeating the Read goes through.',
            R.questions('SV8', 'EX8', 'CX3'), 'one-click', 'low', hook_manual('big_read_guard.py', 'PreToolUse', cmd, matcher='Read'),
            f'apply.py undo big-read-guard, or remove that hook group from {SETTINGS} and delete the script.', docs('hooks'),
            sv=savings(k['value'], k['month'], 'upper_bound', f'SV8: whole-file reads over {size(thr)} tokens, assuming a targeted read {half}.'),
            tradeoffs='One extra round-trip the first time a big file really is needed whole. File reads through cat or sed in Bash are not covered.',
            steps=steps, apply_summary=f'Copy big_read_guard.py to {HOOKS}/ and add it as a PreToolUse hook on Read ({kb} KB) in {SETTINGS}.',
            verify=f'Ask Claude to read a file over {kb} KB whole: the first Read is denied with a hint, and Claude follows up with a Grep or a ranged Read.',
            insights=['reads'])
    return draft(f"SV8: ranged reads would have saved up to {save(k['value'], k['month'])}, over the $3 bar; no big_read_guard.py hook yet.",
                 o, ['reads'])


@entry('bash-output-cap')
def bash_output_cap(R, S, v):
    """Bash results are among CX3's three biggest context sources and EX10 lists Bash outputs over 15,000 characters."""
    src, _ = R.series('CX3', 'Tokens added and what re-reading them cost, by source')
    top3 = [c for c, _ in src[:3]]
    if 'Bash results' not in top3:
        return skip("CX3: Bash results aren't among the three biggest context sources")
    cap = S.get('bashOutputMaxChars')
    if isinstance(cap, (int, float)) and cap <= 15000:
        return skip(f'bashOutputMaxChars is already {cap}')
    big = [r for r in R.rows('EX10', 'tok', 'Largest single tool results') if r.get('tool') == 'Bash' and (r.get('tok') or 0) * 4 > 15000]
    if not big:
        return skip("CX3: Bash results are a top context source, but EX10's largest tool results include no Bash output over 15,000 characters")
    bash = dict(src).get('Bash results')
    facts = [ev('CX3', f'Tokens added and what re-reading them cost, by source — Bash results: {tok(bash)}'), soft(R.fact, 'EX6', 'Bash share')]
    facts += [{'hidden': 'EX10', 'fact': R.row_fact('EX10', 'tok', r, ('tool', 'd', 'tok', 'r', 'u'), 'Largest single tool results')['fact']}
              for r in big[:3]]
    o = opt('bash-output-cap', 'Keep long Bash output out of the context', 'context', 'setting',
            f'Bash results added {size(bash)} tokens to your contexts (CX3), and some single outputs were long enough to be re-read for '
            'the rest of their session.',
            f'Sets bashOutputMaxChars to 15000 in {SETTINGS} (default 30000). Longer output goes to a file Claude can read on demand, '
            'with a short preview inline.',
            R.questions('CX3', 'EX6'), 'one-click', 'medium',
            [f'In {SETTINGS} add "bashOutputMaxChars": 15000.', 'Start a new session.'],
            f'apply.py undo bash-output-cap, or remove "bashOutputMaxChars" from {SETTINGS}.', docs('settings'),
            tradeoffs='Claude sees less of long logs inline and may need an extra read.',
            steps=[{'action': 'merge_json', 'path': SETTINGS, 'value': {'bashOutputMaxChars': 15000}}],
            apply_summary=f'Add "bashOutputMaxChars": 15000 to {SETTINGS}.')
    return judge(f"CX3: Bash results are a top-3 context source ({size(bash)} tokens); EX10 lists {plural(len(big), 'Bash output')} over 15,000 characters.",
                 'Worth a setting (no saving is measured), or only an insight?', facts, o)


@entry('transcript-retention')
def transcript_retention(R, S, v):
    """The report covers close to the 30 days Claude Code keeps by default (25+) and cleanupPeriodDays isn't set higher."""
    keep = S.get('cleanupPeriodDays')
    if isinstance(keep, (int, float)) and keep > 30:
        return skip(f'cleanupPeriodDays is already {keep}')
    if R.days < 25:
        return skip(f"the report covers {plural(R.days, 'day')}, well inside the 30 days Claude Code keeps by default")
    o = opt('transcript-retention', 'Keep 90 days of history for the next report', 'workflow', 'setting',
            f"The report covers {plural(R.days, 'day')}, close to the 30 days of transcripts Claude Code keeps by default, so older sessions "
            'drop out of every later report.',
            f'Sets cleanupPeriodDays to 90 in {SETTINGS}, so the next reports can compare months instead of weeks.',
            R.questions('SV1', 'TR1'), 'one-click', 'low', [f'In {SETTINGS} add "cleanupPeriodDays": 90.'],
            f'apply.py undo transcript-retention, or remove "cleanupPeriodDays" from {SETTINGS}.', docs('settings'),
            tradeoffs='Transcripts take more disk space.',
            steps=[{'action': 'merge_json', 'path': SETTINGS, 'value': {'cleanupPeriodDays': 90}}],
            apply_summary=f'Add "cleanupPeriodDays": 90 to {SETTINGS}.')
    return draft(f"The report covers {plural(R.days, 'day')} (25 or more) and cleanupPeriodDays is {keep or 'not set'}.", o)


@entry('stale-cache-guard')
def stale_cache_guard(R, S, v):
    """SV7's saving is over $2, or CX8's costliest misses are returns after a break. --min-context is SV7's fresh-start size
    rounded up to 10K (below it a fresh start saves nothing), at least 60000."""
    k = R.kpi('SV7', 'Saved by starting fresh')
    brk = sv3_row(R, 'You came back after a break')
    first = [r.get('cause') for r in R.rows('CX8', 'cause', 'Every miss')[:3]]
    if k['value'] <= 2 and 'You came back after a break' not in first:
        return skip(f"SV7: starting fresh would have saved {usd(k['value'])}, under the $2 bar, and CX8's costliest misses aren't returns after a break")
    if S.has_hook('stale_cache_guard.py'):
        return skip('a stale_cache_guard.py hook is already installed')
    fresh = R.has('SV7', 'A fresh session starts at')
    minc = max(60000, int(math.ceil(((fresh or {}).get('value') or 0) / 10000.0)) * 10000)
    steps, cmd = hook_steps('stale_cache_guard.py', 'UserPromptSubmit', f'--min-context {minc} --grace 180')
    n = lead_int(k.get('sub'))
    ctx = sorted((r.get('x') or 0 for r in R.rows('SV7', 'x')), reverse=True)[:2]
    life = main_lifetime(R)
    start = R.has('CX5', 'A new session starts with')
    if k['value'] > 2:
        sv = savings(k['value'], k['month'], 'upper_bound', 'SV7: each return after the cache expired priced as a fresh session; '
                                                            'realised when you start fresh at the prompt it holds.')
        basis = f"SV7: starting fresh would have saved up to {save(k['value'], k['month'])}, over the $2 bar"
    else:
        sv = savings(brk['u'], brk['mo'], 'measured', "SV3's “You came back after a break” row: the extra cost of those misses.") if brk else None
        basis = "CX8's costliest misses are returns after a break"
    o = opt('stale-cache-guard', 'Pause before your first prompt into a big session whose cache has expired', 'cache', 'hook',
            sentence(f"{plural(n, 'time')} you came back to a session after its {life + ' ' if life else ''}cache expired"
                     + (f", including {and_list([size(x) for x in ctx])} contexts (SV7)." if ctx else ' (SV7).') if n else '',
                     f"Each return re-wrote the whole context ({usd(brk['u'])} of misses, SV3), and every later call kept re-reading it." if brk else ''),
            f'Installs stale_cache_guard.py as a UserPromptSubmit hook. When the last call is older than the cache lifetime (read from the '
            f'transcript) and the context is over {size(minc)}, it holds that one prompt, shows what continuing will re-write, and suggests '
            '/clear with a handoff or /compact. Sending the same prompt again within 3 minutes goes through.',
            R.questions('SV7', 'SV3', 'CX8', 'ME3'), 'one-click', 'low',
            hook_manual('stale_cache_guard.py', 'UserPromptSubmit', cmd)[:2]
            + ['Or, without the hook: after a break longer than the cache lifetime with a big context, /clear and paste a short handoff (or /compact first).'],
            f'apply.py undo stale-cache-guard, or remove that hook group from {SETTINGS} and delete the script.', docs('hooks', 'prompt-caching'),
            sv=sv, tradeoffs=f'One extra Enter when you really do want to continue a big, expired session. Sessions under {size(minc)} are never held'
                             + (f" (a new session starts at about {size(start['value'])})." if start else '.'),
            steps=steps, apply_summary=f'Copy stale_cache_guard.py to {HOOKS}/ and add it as a UserPromptSubmit hook (min context {size(minc)}) in {SETTINGS}.',
            verify=f'Return to a session over {size(minc)} after the cache lifetime: the first prompt is held with the re-write cost, and sending it again goes through.',
            insights=['fresh', 'misses'])
    return draft(basis + '; no stale_cache_guard.py hook yet.', o, ['fresh', 'misses'])


@entry('statusline-cache')
def statusline_cache(R, S, v):
    """No statusLine in any settings file."""
    if S.has_statusline():
        return skip('a statusLine is already configured')
    brk = [r for r in R.rows('CX8', 'cause', 'Every miss') if r.get('cause') == 'You came back after a break' and r.get('gap')]
    gaps = sorted(r['gap'] for r in brk)
    o = opt('statusline-cache', 'Show context size and cache warmth in the status line', 'cache', 'statusline',
            "No status line is configured, so you can't see how big a session is or whether its cache is still warm before you type"
            + (f'; {len(brk)} of your cache misses were returns after {span(gaps[0])} to {span(gaps[-1])} away (CX8).' if len(gaps) > 1 else '.'),
            'Installs statusline.py as your status line: model · context size · cache warm Xm left, or cold and what the next message '
            're-writes · session cost.',
            R.questions('CX8', 'CX4', 'CX1'), 'one-click', 'low',
            [f'Copy scripts/hooks/statusline.py (plus _session.py and prices.json) to {HOOKS}/.',
             f'In {SETTINGS} add "statusLine": {{"type": "command", "command": "python3 \\"$HOME/.claude/hooks/claude-usage/statusline.py\\""}}.'],
            f'apply.py undo statusline-cache, or remove statusLine from {SETTINGS}.', docs('statusline'),
            tradeoffs='None beyond a line at the bottom of the terminal.',
            steps=[{'action': 'write_file', 'path': f'{HOOKS}/statusline.py', 'source': 'hooks/statusline.py', 'mode': '755'},
                   {'action': 'merge_json', 'path': SETTINGS, 'value': {'statusLine': {
                       'type': 'command', 'command': 'python3 "$HOME/.claude/hooks/claude-usage/statusline.py"'}}}],
            apply_summary=f'Copy statusline.py to {HOOKS}/ and set it as statusLine in {SETTINGS}.',
            verify='A new session shows the model, context size and cache state at the bottom.', insights=['fresh', 'compact'])
    return draft('No statusLine is configured in any settings file.', o, ['fresh', 'compact'])


@entry('cache-ttl-fit')
def cache_ttl_fit(R, S, v):
    """SV5's cheapest mix differs from the actual one and saves over $3. A close call (under 5%) or a last week that favours the
    other lifetime is left to Claude. Like unused-listings-off, a lifetime an applied earlier version set is carried."""
    k = R.kpi('SV5', 'Cheapest mix saves')
    rows = {r.get('k'): r for r in R.table('SV5', 'c5', 'Cache read + write cost per thread kind')}
    recent = re.search(r'Last 7 days alone favour (.+?)\.(?:\s|$)', R.insights_text('SV5'))
    change, close, against = {}, [], []
    for kind, key in (('Main threads', 'promptCacheTtl'), ('Subagents', 'subagentPromptCacheTtl')):
        r = rows.get(kind)
        if not r:
            continue
        cur = '5m' if abs(r['a'] - r['c5']) < abs(r['a'] - r['c1']) else '1h'
        best = '5m' if r.get('b') == '5 minutes' else '1h'
        if best == cur or S.get(key) == best:
            continue
        change[key] = best
        if (r.get('d') or 0) / max(r['c5'], r['c1'], 1e-9) < 0.05:
            close.append(kind.lower())
        other = '1 hour' if best == '5m' else '5 minutes'
        if recent and f'{kind.lower()} {other}' in recent.group(1):
            against.append(kind.lower())
    if not change or k['value'] <= 3:
        return skip(f'SV5: the cheapest mix ({mix_of(k)})' + (' is your actual mix' if not change else f" saves only {usd(k['value'])}"))
    v['ttl_main'], v['ttl_sub'] = change.get('promptCacheTtl'), change.get('subagentPromptCacheTtl')
    what = and_list([f'the main thread to {change["promptCacheTtl"]}' if 'promptCacheTtl' in change else '',
                     f'subagents to {change["subagentPromptCacheTtl"]}' if 'subagentPromptCacheTtl' in change else ''])
    o = opt('cache-ttl-fit', f'Move {what} cache lifetime', 'cache', 'setting',
            f"Replaying your whole history under each lifetime mix, {mix_of(k)} would have cost the least: {save(k['value'], k['month'])} less "
            'than your actual mix (SV5).',
            f'Sets {and_list([f"{x}={y}" for x, y in change.items()])} in {SETTINGS}.',
            R.questions('SV5', 'CX10'), 'one-click', 'low',
            [f'In {SETTINGS} add ' + ', '.join(f'"{x}": "{y}"' for x, y in change.items()) + '.', 'Start a new session.'],
            f'apply.py undo cache-ttl-fit, or remove {and_list(list(change))} from {SETTINGS}.', docs('prompt-caching', 'settings'),
            sv=savings(k['value'], k['month'], 'theoretical', "SV5: the whole history re-priced under the cheapest lifetime mix, against your actual total."),
            tradeoffs='A 1-hour entry costs 2× input to write instead of 1.25×; a 5-minute one expires during short pauses. SV5 prices both on your history.',
            steps=[{'action': 'merge_json', 'path': SETTINGS, 'value': dict(change)}],
            apply_summary=f'Add {and_list([f"{x}={y}" for x, y in change.items()])} to {SETTINGS}.',
            verify="The next report's SV5 shows the new lifetime as your actual mix.", insights=['ttl'])
    rule = f"SV5: {mix_of(k)} saves {save(k['value'], k['month'])} against your actual mix, over the $3 bar."
    if close or against:
        return judge(rule + (f" A close call for {and_list(close)}." if close else '') + (f" The last 7 days favour the other lifetime for {and_list(against)}." if against else ''),
                     "Propose the change, or say in an insight that it's too close to call?", [R.fact('SV5', 'Cheapest mix saves')], o, ['ttl'],
                     carry=TTL_KEYS)
    return draft(rule, o, ['ttl'], carry=TTL_KEYS)


@entry('keep-awake')
def keep_awake(R, S, v):
    """SV3 has a “Computer went to sleep” row and the user is on macOS."""
    R.table('SV3', 'w')
    row = sv3_row(R, 'Computer went to sleep')
    if not row:
        return skip('SV3 has no misses after the computer went to sleep')
    if not S.mac():
        return skip("keep_awake.sh uses macOS's caffeinate, and this report's home folder isn't a macOS one")
    if S.has_hook('keep_awake.sh'):
        return skip('a keep_awake.sh hook is already installed')
    slept = next((r.get('n') for r in R.rows('ME7', 'm') if 'went to sleep' in (r.get('m') or '')), None)
    steps, cmd = hook_steps('keep_awake.sh', 'UserPromptSubmit', '7200', runner='bash')
    o = opt('keep-awake', 'Keep the Mac awake while Claude works', 'reliability', 'hook',
            sentence(f"Your computer went to sleep mid-response {plural(slept, 'time')} (ME7);" if slept else '',
                     f"the retries after {row['n']} of them were cache misses costing {save(row['u'], row['mo'], 'SV3')}, plus the cut-off work."
                     if slept else f"{row['n']} cache misses followed the computer going to sleep, costing {save(row['u'], row['mo'], 'SV3')}."),
            'Installs keep_awake.sh as a UserPromptSubmit hook. After each prompt it runs caffeinate -i for up to 2 hours (one process at a '
            'time), so the Mac doesn\'t idle-sleep during long subagent or pipeline runs.',
            R.questions('SV3', 'ME7', 'CX8'), 'one-click', 'low',
            ['Before a long run, start `caffeinate -i -t 7200` in a terminal.',
             f'Or install the hook: copy keep_awake.sh to {HOOKS}/ and add it under hooks.UserPromptSubmit: {cmd}.'],
            f'apply.py undo keep-awake, or remove that hook group from {SETTINGS} and delete the script.', docs('hooks'),
            sv=savings(row['u'], row['mo'], 'measured', "SV3's “Computer went to sleep” row: the extra cost of the misses that followed a sleep error."),
            tradeoffs='The Mac stays awake up to 2 hours after each prompt; closing the lid on battery still sleeps.', steps=steps,
            apply_summary=f'Copy keep_awake.sh to {HOOKS}/ and add it as a UserPromptSubmit hook (7200 s) in {SETTINGS}.',
            verify='After a prompt, `pgrep -fl caffeinate` shows a caffeinate -i process.', insights=['misses'])
    return draft(f"SV3: {row['n']} misses after the computer went to sleep cost {save(row['u'], row['mo'])}; macOS.", o, ['misses'])


@entry('unused-listings-off')
def unused_listings_off(R, S, v):
    """SV2 lists unused items a user setting switches off: skillOverrides for personal and synced skills, and
    disableClaudeAiConnectors when no claude.ai connector is used at all. Project skills and built-in items are left out.
    When a newer version replaces an applied one (apply.py undoes the old first), assemble.py carries what the applied
    version switched off (the `carry` keys), so nothing comes back on; the user's own entries are never copied."""
    rows = R.table('SV2', 'h', 'Every unused item, largest first')
    have = S.get('skillOverrides') or {}
    used_conn = any((r.get('k') or '').startswith('claude.ai') and r.get('u') for r in R.rows('EX2', 's', 'MCP servers'))
    skills, conn, proj = [], [], []
    for r in rows:
        m = re.match(r'skillOverrides "([^"]+)": "off"(.*)$', r.get('h') or '')
        if m and m.group(2):
            proj.append(r)
        elif m and m.group(1) not in have:
            skills.append((m.group(1), r))
        elif r.get('h') == '"disableClaudeAiConnectors": true' and not used_conn and not S.get('disableClaudeAiConnectors'):
            conn.append(r)
    if not skills and not conn:
        return skip('SV2 has no unused skill or claude.ai connector a user setting can switch off')
    notes = [f"Left out project skills {and_list([r['n'] for r in proj])} ({and_list(sorted({r.get('o', '') for r in proj}))}): they belong to "
             "that project; add them (a merge_json into that project's .claude/settings.local.json) only if the user doesn't use them there."] if proj else []
    so = round(sum(r['u'] for _, r in skills) + sum(r['u'] for r in conn), 2)
    mo = month(R, so)
    synced = [n for n, _ in skills if n.startswith('anthropic-skills:')]
    own = [n for n, _ in skills if not n.startswith('anthropic-skills:')]
    cname = conn[0]['n'].replace('claude.ai ', '', 1) if len(conn) == 1 else None
    conn_t = (f'the {cname} connector' if cname else f'{len(conn)} claude.ai connectors') if conn else ''
    items = and_list([f"the claude.ai {cname} connector ({conn[0]['t']:,} tokens per session)" if cname else conn_t,
                      and_list(own[:3] + ([f'{len(own) - 3} more'] if len(own) > 3 else [])),
                      f'{len(synced)} synced anthropic-skills' if synced else ''])
    unused = R.has('EX2', 'Unused skills')
    value = {'skillOverrides': {n: 'off' for n, _ in skills}} if skills else {}
    if conn:
        value['disableClaudeAiConnectors'] = True
    short = [n.split(':', 1)[1] for n in synced]
    o = opt('unused-listings-off', 'Stop listing ' + and_list([f"{len(skills)} unused skill{'s' if len(skills) != 1 else ''}" if skills else '', conn_t]),
            'tooling', 'setting',
            sentence(f"{unused['value']} of {(unused.get('sub') or '').replace('of ', '')} skills were never used (EX2)." if unused and unused.get('sub') else '',
                     f'Of what you can switch off, {items} add up to {save(so, mo, "SV2")}.'),
            f'In {SETTINGS}, ' + and_list([f"sets skillOverrides to \"off\" for {and_list(own + ([f'the {len(synced)} unused anthropic-skills'] if synced else []))}" if skills else '',
                                           'sets disableClaudeAiConnectors to true' if conn else '']) + '. Built-in skills are left alone; each skill comes back by removing its line.',
            R.questions('SV2', 'EX2', 'CX5'), 'one-click', 'low',
            ([f'In {SETTINGS} add "skillOverrides": {{' + ', '.join(f'"{n}": "off"' for n, _ in skills[:2]) + (', …' if len(skills) > 2 else '')
              + '} for each skill you don\'t use.'] if skills else [])
            + (['Add "disableClaudeAiConnectors": true if you don\'t use claude.ai connectors from Claude Code.'] if conn else []) + ['Start a new session.'],
            f'apply.py undo unused-listings-off, or remove those keys from {SETTINGS}.', docs('skills', 'settings', 'mcp') if conn else docs('skills', 'settings'),
            sv=savings(so, mo, 'theoretical', f"SV2's rows for {items}, priced on every main-thread call that re-read them."),
            tradeoffs=sentence(f"Claude won't offer {and_list(short[:4] + (['the other synced skills'] if len(short) > 4 else []))} until you turn them back on"
                               + (f', and {cname or "claude.ai connectors"} (and any other claude.ai connector) stops loading in Claude Code.' if conn else '.')
                               if short else (f'{cname or "claude.ai connectors"} (and any other claude.ai connector) stops loading in Claude Code.' if conn else
                                              "Claude won't offer those skills until you turn them back on.")),
            steps=[{'action': 'merge_json', 'path': SETTINGS, 'value': value}],
            apply_summary=f'Merge {and_list([f"skillOverrides ({len(skills)} skills → off)" if skills else "", "disableClaudeAiConnectors: true" if conn else ""])} into {SETTINGS}.',
            verify='In a new session, /skills no longer lists those skills' + (f' and /mcp no longer shows claude.ai {cname}.' if cname else '.'),
            insights=['unused'])
    return draft(f"SV2: {len(skills)} skill{'s' if len(skills) != 1 else ''}" + (' and a claude.ai connector' if conn else '') + f' a user setting switches off, worth {save(so, mo)}.',
                 o, ['unused'], notes, carry=['skillOverrides', 'disableClaudeAiConnectors'])


@entry('mcp-off-where-unused')
def mcp_off_where_unused(R, S, v):
    """SV2 lists MCP servers switched off by hand (/mcp disable, /chrome); SV3's tool-list-change misses add the connect-first
    habit (the catalog's mcp-connect-first, folded in here; alone when no server is unused)."""
    rows = [r for r in R.rows('SV2', 'h', 'Every unused item, largest first') if r.get('k') == 'MCP server' and not (r.get('h') or '').startswith('"')]
    tl = sv3_row(R, 'Tool list changed (MCP/tools)')
    ex2 = {r.get('k'): r for r in R.rows('EX2', 's', 'MCP servers')}
    changes = R.has('EX3', 'Mid-session tool-list changes')
    names = [r['n'] for r in rows]
    low = [r for k, r in sorted(ex2.items()) if k not in names and not k.startswith('claude.ai') and 0 < (r.get('u') or 0) <= 2 and (r.get('s') or 0) >= 10]
    notes = [f"{r['k']} loaded in {plural(r['s'], 'session')} with {plural(r['u'], 'tool call')} (EX2): nearly unused; add it to the manual steps if the user "
             'agrees (the Chrome extension: /chrome, turn off “enabled by default”).' for r in low]
    tl_text = (f"The tool list changed mid-session {plural(changes['value'], 'time')} (EX3), and {tl['n']} of those changes re-wrote whole contexts "
               f"({usd(tl['u'])}, SV3)." if tl and changes else '')
    connect = 'Before the first prompt of a session, wait until MCP servers have finished connecting (check /mcp).'
    if not rows:
        if not tl:
            return skip('SV2 lists no unused MCP server, and SV3 has no tool-list-change misses')
        o = opt('mcp-connect-first', "Don't send the first prompt while MCP servers connect", 'cache', 'habit',
                tl_text or f"{tl['n']} cache misses followed a tool-list change ({usd(tl['u'])}, SV3).",
                'A habit: let the MCP servers finish connecting before the first prompt, so their tools join the prompt before anything is cached.',
                R.questions('SV3', 'EX3', 'CX8'), 'habit', 'low', [connect, 'Disconnect servers a project never uses (/mcp disable <server>).'],
                "Nothing to undo: it's a habit.", docs('mcp', 'prompt-caching'), verify='In the next report, SV3 shows fewer “Tool list changed” misses.')
        return judge(f"SV3: {tl['n']} misses after a tool-list change ({usd(tl['u'])}); no MCP server is unused.",
                     'Did those changes happen right at session start (EX3 lists them)? Only then is waiting for the servers the fix.',
                     [R.row_fact('SV3', 'w', tl, ('k', 'n', 'u', 'mo')),
                      R.fact('EX3', 'Mid-session tool-list changes') if changes else None], o, ['misses'])
    so = round(sum(r['u'] for r in rows), 2)
    mo = month(R, so)
    chrome = [r for r in rows if r.get('h') == '/chrome settings']
    plain = [r for r in rows if r not in chrome]
    manual = [f"In each project where you don't use {r['n']}, run /mcp disable {r['n']} in a Claude Code session (reversible with /mcp enable {r['n']})."
              for r in plain]
    manual += ['Run /chrome and turn off “enabled by default”; turn it on in the sessions where you want browser automation.'] if chrome else []
    manual += [connect] if tl else []
    oid = 'mcp-off-where-unused'
    o = opt(oid, f"Disconnect {and_list(names)} where you don't use {'it' if len(names) == 1 else 'them'}", 'tooling', 'command',
            sentence(*[f"{r['n']} loaded in {plural(ex2[r['n']]['s'], 'session')} with {plural(ex2[r['n']]['u'], 'tool call')} (EX2)."
                       for r in rows if r['n'] in ex2], tl_text),
            f"Turns {and_list(names)} off per project with /mcp disable where you don't use {'it' if len(names) == 1 else 'them'}"
            + (' (the Chrome extension: connect only when asked)' if chrome else '') + '. Reversible, and the server configs stay in place.',
            R.questions('SV2', 'EX2', 'EX3', 'SV3') if tl else R.questions('SV2', 'EX2'), 'minutes', 'low', manual,
            ' '.join([f'/mcp enable {r["n"]} in each project.' for r in plain] + (["Turn Chrome's default back on in /chrome."] if chrome else [])
                     + ['Never use claude mcp remove, which deletes the config.']), docs('mcp'),
            sv=savings(so, mo, 'theoretical', f"SV2's {and_list(names)} row{'s' if len(rows) > 1 else ''} ("
                       + ', '.join(f"{r['t']:,} tokens per session" for r in rows) + ')'
                       + (f". The tool-list-change misses (SV3, {usd(tl['u'])}) could shrink too, but the transcripts don't say which server changed, "
                          'so they are not counted.' if tl else '.')),
            tradeoffs=f'Re-enable with /mcp enable {names[0]}' + (' (or /chrome)' if chrome else '') + f' when you need {"it" if len(names) == 1 else "them"}.',
            verify=f'/mcp in those projects shows {and_list(names)} disabled.', insights=['unused'])
    return draft(f"SV2: {and_list(names)} unused, switched off by hand, worth {save(so, mo)}" + ('; SV3 has tool-list-change misses.' if tl else '.'),
                 o, ['unused', 'misses'] if tl else ['unused'], notes)


@entry('fix-broken-hook')
def fix_broken_hook(R, S, v):
    """EX5 lists hooks failing with “No such file or directory” and a settings file still has that path. Which path is right
    (and that the script exists there) is Claude's to check."""
    fails = {}
    for r in R.rows('EX5', 'err', 'Failures'):
        m = re.search(r'([~/][^\s:\'"]*): No such file or directory', r.get('err') or '')
        if m:
            fails[m.group(1)] = fails.get(m.group(1), 0) + 1
    if not fails:
        return skip('EX5 shows no hook failing with “No such file or directory”')
    def runs(p, cmd):
        """How a hook command runs the failing path: 'path' when it names it (with ~ and $HOME spelled out), 'name' when it
        builds the path from another variable ($CLAUDE_PROJECT_DIR …) and names the script; else None. A bare name match
        alone would flag a command already fixed to this machine's path."""
        full = S.expand(cmd)
        if p in cmd or p in full:
            return 'path'
        return 'name' if os.path.basename(p) in full and re.search(r'\$\{?[A-Za-z_]', full) else None
    live = [(p, n, [(f, ptr, cmd, how) for f, _, ptr, cmd in S.hooks() for how in [runs(p, cmd)] if how]) for p, n in sorted(fails.items())]
    live = [x for x in live if x[2]]
    if not live:
        return skip(f"EX5: {and_list([os.path.basename(p) for p in sorted(fails)])} failed with a missing file, but no settings file uses "
                    'that path any more (fixed)')
    facts = [ev('EX5', f'{os.path.basename(p)}: {plural(n, "failure")} with “No such file or directory” ({p})') for p, n, _ in live]
    facts += [{'config': S.tilde(f), 'fact': f'{ptr}: {cmd}' + (' (matched by the script name: the path comes from a variable)' if how == 'name' else '')}
              for _, _, where in live for f, ptr, cmd, how in where]
    return judge(f"EX5: {plural(len(live), 'hook path')} fail{'s' if len(live) == 1 else ''} with “No such file or directory” and "
                 f"{'is' if len(live) == 1 else 'are'} still in settings.",
                 'Find the right path (ls), then propose a reliability optimization: one set_json per settings file with the pointer above '
                 'and the corrected command. Worktree copies of a project often carry their own settings file: fix each.', facts)


@entry('subagent-briefs')
def subagent_briefs(R, S, v):
    """SV3 has avoidable “Subagent resumed via SendMessage” misses worth $1 or more."""
    R.table('SV3', 'w')
    row = sv3_row(R, 'Subagent resumed via SendMessage')
    if not row or (row.get('u') or 0) < 1:
        return skip('SV3 has no subagent-resume misses worth $1')
    cx = next((r for r in R.rows('CX8', 'h') if r.get('k') == row['k']), None)
    start = R.has('CX11', 'Subagent start')
    o = opt('subagent-briefs', 'Start a fresh subagent instead of resuming a big finished one', 'delegation', 'claude_md',
            sentence(f"{plural(cx['n'], 'subagent resume')} via SendMessage re-wrote {size(cx['t'])} tokens (CX8);" if cx else '',
                     f"the avoidable ones cost {save(row['u'], row['mo'], 'SV3')}" + (f", while a fresh subagent starts at about {size(start['value'])} (CX11)." if start else '.')),
            'Adds one line to ~/.claude/CLAUDE.md telling Claude to launch a fresh subagent with a short brief (what changed, what to check) '
            'when the old one is large and finished, instead of resuming it. Resuming re-writes its whole history to the cache.',
            R.questions('CX8', 'SV3', 'CX11'), 'one-click', 'low',
            ['Add the line above to ~/.claude/CLAUDE.md.',
             'When delegating a follow-up review yourself, ask for "a new subagent with the diff and what to check" rather than "resume the reviewer".'],
            'apply.py undo subagent-briefs, or delete the line from ~/.claude/CLAUDE.md.', docs('memory', 'sub-agents'),
            sv=savings(row['u'], row['mo'], 'measured', 'SV3: the extra cost of the resume misses counted as avoidable.'),
            tradeoffs='CLAUDE.md loads in every session (one line, ~50 tokens). A fresh subagent may need to re-read a few files.',
            steps=[{'action': 'append_text', 'path': '~/.claude/CLAUDE.md', 'marker': 'subagent-briefs',
                    'content': '- When a finished subagent has a large context (over ~100K), start a fresh subagent with a short brief of what '
                               'changed and what to check instead of resuming it with SendMessage: resuming re-writes its whole history to the cache.\n'}],
            apply_summary='Append one line to ~/.claude/CLAUDE.md (created if missing).',
            verify='In the next report, CX8 shows few or no “Subagent resumed via SendMessage” misses.', insights=['misses'])
    return draft(f"SV3: {row['n']} avoidable subagent-resume misses cost {save(row['u'], row['mo'])}.", o, ['misses'])


def parse_money(s):
    m = re.match(r'\s*\$([\d,]+(?:\.\d+)?)', s or '')
    return float(m.group(1).replace(',', '')) if m else None


# ---------- how the entries relate (catalog.md's Related lines), notes from each side ----------

LINKS = [
    ('main-model', 'effort-default', 'overlaps', None,
     'Both cut the same main-thread output cost: SV6 re-prices the tokens you had, a lower effort cuts the thinking tokens themselves.',
     'Both cut main-thread output cost on the same calls: effort cuts the thinking tokens, the model habit their price.'),
    ('subagent-model', 'subagent-briefs', 'overlaps', None,
     'The resume misses subagent-briefs avoids are priced at the model the subagents ran on; on {sub_target} they cost less, so the two savings share that part.',
     "Its saving is priced at the subagents' current model; with subagents on {sub_target} the same misses would cost less."),
    ('subagent-model', 'cache-ttl-fit', 'overlaps', lambda v: v.get('ttl_sub'),
     "SV5's subagent cache writes are priced at the model the subagents ran on; on {sub_target} the lifetime change is worth less.",
     'Its subagent saving is priced at the subagents\' current model; with subagents on {sub_target} it shrinks.'),
    ('auto-compact-window', 'context-guard', 'alternative', None,
     "The same SV4 saving, done for you. Pick this if you would rather not think about it; don't use both.",
     'The same SV4 saving, but you decide when to compact. Pick this if you want that control; with both, the notice fires just before a '
     'compaction that happens anyway.'),
    ('auto-compact-window', 'stale-cache-guard', 'overlaps', None,
     "Smaller contexts also make each return after the cache expired cheaper, so part of the stale-cache guard's saving is the same money.",
     'Its saving is priced at the context sizes you had. Compacting at {window_t} keeps them smaller, so each expired return would cost '
     'less and part of this saving goes.'),
    ('auto-compact-window', 'big-read-guard', 'overlaps', None,
     'Part of what compaction drops is big whole-file reads the big-read guard would have kept out, so the two savings share that part.',
     'SV8 counts carrying each big read for the rest of the session; compacting earlier drops it from the context too, so part of the saving is shared.'),
    ('context-guard', 'statusline-cache', 'complements', None,
     'The status line shows the context size all the time; this notice speaks up once it passes {threshold_t}.',
     'It shows the context size all the time; the context notice speaks up once it passes {threshold_t}.'),
    ('context-guard', 'stale-cache-guard', 'overlaps', None,
     'Acting on the notice keeps contexts smaller, which also makes each return after the cache expired cheaper: part of the two savings is shared.',
     'Its saving is priced at the context sizes you had. Compacting at the {threshold_t} notice keeps them smaller, so each expired return would cost less.'),
    ('context-guard', 'big-read-guard', 'overlaps', None,
     'Part of what compacting at the notice drops is big whole-file reads the big-read guard would have kept out.',
     'SV8 counts carrying each big read for the rest of the session; compacting at the notice drops it from the context too.'),
    ('stale-cache-guard', 'statusline-cache', 'complements', None,
     "The status line's cache countdown warns you before you type; the guard catches the prompt when you didn't look.",
     'Its cache countdown shows when the cache is about to expire; the stale-cache guard stops the costly prompt if you miss it.'),
    ('stale-cache-guard', 'cache-ttl-fit', 'alternative', lambda v: v.get('ttl_main') == '1h',
     'Both go after the returns after a break. Pick this if you would rather restart small after long breaks; with the 1-hour lifetime in '
     'place it only fires after pauses over an hour.',
     'Both go after the returns after a break. Pick this if you mostly continue after pauses of 5–60 minutes (SV5\'s break-even); with it in '
     "place the guard's saving mostly goes."),
    ('stale-cache-guard', 'cache-ttl-fit', 'complements', lambda v: v.get('ttl_main') == '5m',
     'The 5-minute lifetime lets more returns expire; the guard stops the costly ones.',
     'The stale-cache guard stops the costly returns that the shorter lifetime lets expire, which softens the switch.'),
]


def links(ids, v):
    """The catalog's pairs whose both ends were drafted, as {a, b, relation, note_a, note_b} with this report's numbers."""
    fill = dict(v, threshold_t=size(v['threshold']) if v.get('threshold') else 'the threshold',
                window_t=size(v['window']) if v.get('window') else 'that size', sub_target=v.get('sub_target') or 'the cheaper model')
    out = []
    for a, b, rel, cond, na, nb in LINKS:
        if a in ids and b in ids and (cond is None or cond(v)):
            out.append({'a': ids[a], 'b': ids[b], 'relation': rel, 'note_a': na.format(**fill), 'note_b': nb.format(**fill)})
    return out


def related_from(links_, oid, present):
    """One optimization's `related` list from the pair list: every pair whose other end is in `present`."""
    rel = []
    for x in links_:
        if x['relation'] == 'requires':
            if x['a'] == oid and x['b'] in present:
                rel.append({'id': x['b'], 'relation': 'requires', 'note': x['note_a']})
            continue
        for me, other, note in ((x['a'], x['b'], x.get('note_a')), (x['b'], x['a'], x.get('note_b'))):
            if me == oid and other in present:
                rel.append({'id': other, 'relation': x['relation'], 'note': note})
    return rel


# ---------- savings levers, for the insights ----------

LEVER_ROWS = {   # SV1 row: (its Details card, label prefix)
    'main_model': ('SV6', 'Main threads on '), 'sub_model': ('SV6', 'Run subagents on'), 'compact': ('SV4', ''), 'misses': ('SV3', ''),
    'ttl': ('SV5', ''), 'fresh': ('SV7', ''), 'reads': ('SV8', ''), 'unused': ('SV2', ''), 'stop_hook': ('EX5', ''),
}
LEVER_OVERLAPS = [   # levers whose savings count some of the same cost
    ('main_model', 'compact'), ('main_model', 'misses'), ('main_model', 'fresh'), ('main_model', 'reads'), ('main_model', 'stop_hook'),
    ('sub_model', 'misses'), ('sub_model', 'ttl'), ('compact', 'fresh'), ('compact', 'reads'), ('compact', 'misses'),
    ('fresh', 'misses'), ('ttl', 'fresh'), ('ttl', 'misses'), ('unused', 'misses'),
]


def lever_bundle(R, key, row):
    """What one lever's insight needs, ready: the id to use, its savings object, evidence as digest.md words it, questions."""
    so, mo = row['u'], row['mo']
    pct_ = row.get('s')
    kind, cat, facts, extra = 'theoretical', 'cost', [], {}
    if key == 'main_model':
        k = R.kpi('SV6', 'Main threads on ', prefix=True)
        cur = k['label'][len('Main threads on '):]
        slash_row = next((r for r in R.rows('EX1', 'n', 'Slash commands') if r.get('k') == '/model'), None)
        slash = (slash_row or {}).get('n')
        oid, qs = 'cost-model-mix', ('SV6', 'OV2', 'EX1', 'OV4')
        facts = [R.fact('SV6', k['label']), soft(R.chart_fact, 'OV2', 'By model')] + ([R.row_fact('EX1', 'n', slash_row, ('k', 'n'), 'Slash commands')] if slash else [])
        basis = f'SV6/SV1: the same main-thread tokens re-priced at {cur} list prices, counting only calls that ran on a pricier model.'
        title = f'Main-thread work on pricier models than {cur} drove the bill'
        bottom = f'At list prices, the same main-thread tokens on {cur} would have saved {save(so, mo, pct(pct_) + " of spend", "SV6")}.'
        actions = [f'Keep {cur} as the default and switch up with /model only for a hard subtask, then back',
                   'Switch models at a natural break: a switch re-writes the cache']
    elif key == 'sub_model':
        k = R.kpi('SV6', 'Subagents on ', prefix=True)
        target = k['label'][len('Subagents on '):]
        oid, qs = f"cost-subagents-{target.split()[0].lower()}", ('SV6', 'SE5', 'OV7')
        facts = [R.fact('SV6', k['label']), soft(R.chart_fact, 'SE5', 'Cost by model', 3)]
        explore = R.has('SV6', 'Explore subagents on ', prefix=True)
        if explore:
            facts.append(R.fact('SV6', explore['label']))
        basis = f'SV6: the same subagent tokens re-priced at {target} list prices, counting only calls on pricier models.'
        title = f'Subagents ran on pricier models where {target} would likely have done'
        bottom = f'At list prices, the same subagent work on {target} would have saved {save(so, mo, pct(pct_) + " of spend", "SV6")}.'
        actions = [f'Run subagents on {target} by default (see the optimization)', 'Ask for a stronger model explicitly for hard reviews or designs']
    elif key == 'compact':
        T, net = compact_best(R)
        oid, qs = f'cost-compact-{int(T) // 1000}k', ('SV4', 'CX6', 'CX1', 'CX4', 'OV3')
        facts = [R.fact('SV4', 'Best threshold', 'Net saving')]
        for cid, lb in (('CX6', 'From calls above 200K'), ('CX4', 'Median context at /clear'), ('CX1', 'Calls above 200K'), ('OV3', 'Cache read')):
            if R.has(cid, lb):
                facts.append(R.fact(cid, lb))
        basis = f'SV4: every main thread replayed with /compact at {size(T)} (start-up size + 20K summary + 10K re-read detail), net of the compactions\' own cost.'
        title = f'Compacting near {size(T)} tokens pays for itself'
        bottom = (f'Every call re-reads the whole context, so compacting whenever a main thread passes about {size(T)} would have saved '
                  f'{save(so, mo, pct(pct_) + " of spend")} after paying for the compactions (SV4).')
        actions = [f'Run /compact (with what to keep) when a task is done and the context is past ~{size(T)}',
                   'Or let Claude Code do it: see the auto-compact and context-notice optimizations']
    elif key == 'misses':
        k = R.kpi('SV3', 'Avoidable')
        oid, qs, kind = 'cost-avoidable-misses', ('SV3', 'CX8', 'ME3'), 'measured'
        facts = [R.fact('SV3', 'Avoidable'), soft(R.fact, 'CX8', 'Cache misses', 'Tokens re-written', 'Extra cost of misses')]
        top = next(iter(R.rows('SV3', 'w')), None)
        if top:
            facts.append(R.row_fact('SV3', 'w', top, ('k', 'n', 'u', 'mo')))
        basis = 'SV3: the extra cost (write − read price) of every miss whose cause a habit or a setting could have prevented.'
        title = 'Most cache-miss cost was avoidable' if (lead_int(k.get('sub')) or 0) >= 50 else 'Part of the cache-miss cost was avoidable'
        bottom = (f"Cache misses a habit or a setting would have prevented cost {save(so, mo, pct(pct_) + ' of spend')}, "
                  f"{(k.get('sub') or '').strip()} (SV3).")
        actions = [f"Start with the biggest cause: {top['k'].lower()} ({top['h']})" if top and top.get('h') else 'Start with the biggest cause in SV3']
    elif key == 'stop_hook':
        hooks = sorted([r for r in R.rows('EX5', 'tot') if r.get('e') == 'Stop'], key=lambda r: -(r.get('n') or 0))
        oid, qs, kind = 'cost-stop-hook-followup', ('SV1', 'EX5', 'OUT3'), 'upper_bound'
        facts = [R.row_fact('SV1', 'c', row, ('l', 'u', 'mo'))]
        facts += [R.row_fact('EX5', 'tot', r, ('e', 's', 'n', 'tot')) for r in hooks[:2]]
        basis = ("SV1's Stop-hook follow-up lever: the calls Claude makes after the hook fires and before your next prompt; an upper bound "
                 'since some of that work is wanted.')
        title = 'A Stop hook sets off extra work after every turn'
        bottom = f"The work Claude does after {hooks[0]['s'] if hooks else 'your Stop hook'} fires costs up to {save(so, mo, pct(pct_) + ' of spend', 'SV1')}."
        actions = ['Make the hook fire only after turns that changed files or ran long', 'Keep what it asks Claude to write short and specific']
    elif key == 'fresh':
        k = R.kpi('SV7', 'Saved by starting fresh')
        oid, qs, kind = 'cost-fresh-after-breaks', ('SV7', 'CX8', 'ME3'), 'upper_bound'
        facts = [R.fact('SV7', 'Saved by starting fresh')]
        top = next(iter(R.rows('SV7', 'x')), None)
        if top:
            facts.append(R.row_fact('SV7', 'x', top, ('t', 's', 'x', 'u')))
        brk = sv3_row(R, 'You came back after a break')
        if brk:
            facts.append(R.row_fact('SV3', 'w', brk, ('k', 'n', 'u', 'mo')))
        basis = ('SV7: each return after the cache expired priced as a fresh session (median start-up + 5K summary + 10K re-read detail), '
                 'until the next break or compaction.')
        title = 'Returning to a big, expired session re-writes all of it'
        n_back = lead_int(k.get('sub'))
        bottom = (f"Starting fresh instead of returning to an expired session would have saved up to {save(so, mo, pct(pct_) + ' of spend')}"
                  + (f", over {plural(n_back, 'return')} (SV7)." if n_back else ' (SV7).'))
        actions = ['After a break longer than the cache lifetime with a big context, /clear and paste a short handoff (or /compact first)']
    elif key == 'reads':
        oid, qs, kind, cat = 'context-large-reads', ('SV8', 'EX8', 'CX3'), 'upper_bound', 'context'
        facts = [ev('SV8', ' · '.join(kpi_text(i) for i in (R.kpi('SV8', 'Whole-file reads over ', True), R.kpi('SV8', 'Saved if read in ranges'))))]
        top = next(iter(R.rows('EX8', 'rep', 'Files read most')), None)
        if top:
            facts.append(R.row_fact('EX8', 'rep', top, ('f', 'r', 's', 'ln'), 'Files read most'))
        half = (R.kpi('SV8', 'Saved if read in ranges').get('sub') or '').replace('if a range kept', 'would have loaded').strip() or 'would have loaded half'
        basis = f'SV8: Read calls without offset/limit over the size threshold, assuming a ranged read (or a grep first) {half}.'
        title = 'Large files read whole stay in context for the rest of the session'
        bottom = f'Reading large files in ranges would have saved up to {save(so, mo, pct(pct_) + " of spend")}: each whole read is re-read on every later call (SV8).'
        actions = ['Grep for the part you need, then Read with offset/limit', 'Split files Claude reads whole again and again']
    elif key == 'unused':
        k = R.kpi('SV2', 'Of which you can switch off')
        so, mo = k['value'], k.get('month') if k.get('month') is not None else month(R, k['value'])
        pct_ = round(so / R.spend * 100, 1) if R.spend else None
        oid, qs, cat = 'tooling-unused-listings', ('SV2', 'EX2', 'CX5'), 'tooling'
        facts = [R.fact('SV2', 'Of which you can switch off')]
        ex = [lb for lb in ('Unused skills', 'Unused MCP servers') if R.has('EX2', lb)]
        if ex:
            facts.append(R.fact('EX2', *ex))
        if R.has('CX5', 'A new session starts with'):
            facts.append(R.fact('CX5', 'A new session starts with'))
        n_off = lead_int(k.get('sub'))
        basis = f"SV2: the listing size of the {f'{n_off} ' if n_off else ''}items with a way to switch them off, priced on every main-thread call that re-read them."
        n_sk, n_mcp = R.has('EX2', 'Unused skills'), R.has('EX2', 'Unused MCP servers')
        title = and_list([f"{n_sk['value']} unused skills" if n_sk else '', f"{n_mcp['value']} unused MCP servers" if n_mcp and n_mcp['value'] else '']) \
            + ' load in every session' if n_sk or n_mcp else 'Unused listings load in every session'
        bottom = f'Switching off the unused skills and servers you can would have saved {save(so, mo, pct(pct_) + " of spend", "SV2")}.'
        actions = ['Switch off unused skills with skillOverrides (see the optimization)', "Disconnect MCP servers per project where they're unused"]
        extra = {'lever_total': {'usd_so_far': row['u'], 'usd_per_month': row['mo'],
                                 'note': 'SV1 counts every unused item, built-in ones too; the savings above count only what you can switch off.'}}
    elif key == 'ttl':
        k = R.kpi('SV5', 'Cheapest mix saves')
        oid, qs, cat = 'cache-lifetime-fit', ('SV5', 'CX10', 'CX7'), 'cache'
        facts = [R.fact('SV5', 'Cheapest mix saves')]
        basis = 'SV5: the whole history re-priced under the cheapest lifetime mix, against your actual total.'
        title = 'A different cache lifetime would have been cheaper'
        bottom = f"Replaying your history under each lifetime mix, {mix_of(k)} would have saved {save(so, mo, 'SV5')} against your actual mix."
        actions = ['See the cache lifetime optimization, and SV5\'s break-even line before switching']
    else:
        raise Missing(f'no bundle for lever {key}')
    b = {'lever': key, 'label': row['l'], 'id': oid, 'category': cat, 'questions': R.questions(*qs),
         'savings': {'usd_so_far': so, 'pct_of_spend': pct_, 'usd_per_month': mo, 'kind': kind, 'basis': basis},
         'evidence': [f for f in facts if f][:3], 'facts': [f for f in facts if f][3:], 'title': clip(title, 90),
         'bottom_line': clip(bottom, 280), 'actions': [clip(x, 240) for x in actions[:4]],
         'priority': 'high' if (pct_ or 0) >= 5 else 'medium' if (pct_ or 0) >= 1 else 'low',
         'confidence': 'high' if kind == 'measured' else 'medium'}
    b['savings']['basis'] = clip(basis, 400)
    if pct_ is None:
        del b['savings']['pct_of_spend']
    if not b['facts']:
        del b['facts']
    b.update(extra)
    return b


def lifetimes_fit(R):
    """SV5 finds the actual lifetime mix is already the cheapest: an insight about what not to change."""
    k = R.kpi('SV5', 'Cheapest mix saves')
    if k['value'] > 0.005:
        return None
    facts = [R.fact('SV5', 'Cheapest mix saves')]
    for cid, lb in (('CX10', 'Main threads: pauses of 5–60 min'), ('CX7', 'Cache hit rate'), ('CX10', 'Subagents: pauses of 5–60 min')):
        if R.has(cid, lb):
            facts.append(R.fact(cid, lb))
    mix = mix_of(k)
    return {'lever': 'ttl', 'label': 'Cache lifetime that fits each thread kind', 'id': 'cache-lifetimes-right', 'category': 'cache',
            'questions': R.questions('SV5', 'CX10', 'CX7'), 'evidence': facts[:3],
            'title': 'Your cache lifetimes are already the cheapest mix',
            'bottom_line': f'Replaying your whole history under every lifetime mix, your actual one ({mix}) costs the least (SV5): leave the '
                           'cache lifetime settings as they are.',
            'actions': ['Keep promptCacheTtl and subagentPromptCacheTtl as they are'], 'priority': 'low', 'confidence': 'high',
            **({'facts': facts[3:]} if facts[3:] else {})}


def levers(R, by_lever):
    out, errs = [], []
    rows = {k: lever_row(R, k) for k in LEVER_ROWS}
    for key, row in sorted(((k, r) for k, r in rows.items() if r and (r.get('u') or 0) > 0), key=lambda x: -x[1]['u']):
        try:
            out.append(lever_bundle(R, key, row))
        except Missing as e:
            errs.append({'lever': key, 'reason': str(e)})
    if not rows.get('ttl') or not (rows['ttl'].get('u') or 0) > 0:
        try:
            b = lifetimes_fit(R)
            if b:
                out.append(b)
        except Missing as e:
            errs.append({'lever': 'ttl', 'reason': str(e)})
    ids = {b['lever']: b['id'] for b in out if b.get('savings')}
    for b in out:
        if b.get('savings'):
            b['savings']['overlaps_with'] = [ids[o] for a, c in LEVER_OVERLAPS for me, o in ((a, c), (c, a)) if me == b['lever'] and o in ids]
        b['optimizations'] = by_lever.get(b['lever'], [])
    return out, errs


# ---------- putting it together ----------

def build(metrics, config, out=None):
    """candidates.json's content for this report (no file written)."""
    R, S = Report(metrics), Setup(config)
    v, results = {}, []
    for name, fn in ENTRIES:
        try:
            res = fn(R, S, v)
        except Missing as e:
            res = skip(f'needs a figure this report lacks: {e}')
        except Exception as e:                       # never let one entry stop the others (or the report run)
            res = skip(f'internal error: {type(e).__name__}: {e}')
        res['entry'] = name
        results.append(res)
    ids = {r['entry']: r['draft']['id'] for r in results if r.get('draft')}
    pairs = links(ids, v)
    lever_ids = {}
    for r in results:
        for lv in r.get('levers') or []:
            if r.get('draft'):
                lever_ids.setdefault(lv, []).append(r['draft']['id'])
    bundles, lever_errs = levers(R, lever_ids)
    default_ins = {b['lever']: b['id'] for b in bundles}
    drafted = {r['draft']['id'] for r in results if r['kind'] == 'draft'}
    for r in results:
        o = r.get('draft')
        if not o:
            continue
        if o.get('insights'):
            o['insights'] = [default_ins[lv] for lv in o['insights'] if lv in default_ins] or None
            if not o['insights']:
                del o['insights']
        present = drafted if r['kind'] == 'draft' else drafted | {o['id']}
        r['draft'] = with_related(o, related_from(pairs, o['id'], present))
    doc = {'schema_version': 1, 'generated': dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
           'source': {'metrics_generated': R.generated, 'range': R.range, 'spend_usd': R.spend, 'days': R.days,
                      'claude_dir': (metrics.get('meta') or {}).get('claude_dir')},
           'optimizations': [{k: r[k] for k in ('entry', 'rule', 'levers', 'notes', 'carry', 'draft') if r.get(k) or k in ('entry', 'draft')}
                             for r in results if r['kind'] == 'draft'],
           'judgment': [{k: r[k] for k in ('entry', 'why', 'ask', 'facts', 'levers', 'carry', 'draft') if r.get(k)} for r in results if r['kind'] == 'judgment'],
           'skipped': [{'entry': r['entry'], 'reason': r['reason']} for r in results if r['kind'] == 'skip'] + [
               {'entry': 'lever:' + e['lever'], 'reason': e['reason']} for e in lever_errs],
           'links': pairs, 'levers': bundles}
    doc['problems'] = self_check(doc, metrics, out)
    return doc


def self_check(doc, metrics, out):
    """The drafts, all together with every judgment draft, must pass validate.py as one optimizations.json would."""
    opts = [x['draft'] for x in doc['optimizations']] + [x['draft'] for x in doc['judgment'] if x.get('draft')]
    present = {o['id'] for o in opts}
    opts = [with_related(o, related_from(doc['links'], o['id'], present)) for o in opts]
    if not opts:
        return []
    test = {'schema_version': 1, 'generated': doc['generated'], 'source': {'metrics_generated': doc['source']['metrics_generated']},
            'summary': 'Every candidate drafted from this report, checked together.', 'optimizations': opts}
    saved = VA.AP.CLAUDE_DIR
    try:
        if out:
            VA.AP.CLAUDE_DIR = VA.AP.report_claude_dir(out) or saved
        return VA.validate_optimizations(test, metrics, None, out=out or os.getcwd())
    finally:
        VA.AP.CLAUDE_DIR = saved


def read_json(p):
    with open(p, encoding='utf-8') as fh:
        return json.load(fh)


def write_candidates(out):
    """Evaluate the catalog against <out>/data/metrics.json and config.json; write <out>/data/candidates.json. Returns the doc.
    Notes files older than metrics.json were written for an earlier report: they go, so a skill's next Write starts clean."""
    mpath = layout.data(out, 'metrics.json')
    for name in NOTES:
        p = layout.data(out, name)
        try:
            if os.path.getmtime(p) < os.path.getmtime(mpath):
                os.remove(p)
        except OSError:
            pass
    metrics = read_json(mpath)
    cfg_path = layout.data(out, 'config.json')
    config = read_json(cfg_path) if os.path.exists(cfg_path) else {}
    doc = build(metrics, config, out)
    p = layout.data(out, NAME)
    tmp = p + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as fh:
            fh.write(json.dumps(doc, indent=1, ensure_ascii=False) + '\n')
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return doc


def summary_lines(doc):
    L = []
    for x in doc['optimizations']:
        sv = x['draft'].get('savings') or {}
        L.append(f"draft     {x['draft']['id']:<26} " + (f"{usd(sv['usd_so_far'])} all time" if sv else 'no saving'))
    for x in doc['judgment']:
        L.append(f"judgment  {x['entry']:<26} " + (f"draft {x['draft']['id']}" if x.get('draft') else 'facts only'))
    for x in doc['skipped']:
        L.append(f"skipped   {x['entry']:<26} {x['reason']}")
    L.append(f"levers    {', '.join(b['id'] for b in doc['levers'])}")
    for p in doc.get('problems') or []:
        L.append(f'PROBLEM   {p}')
    return L


def brief(x):
    """A candidate as a reviewer reads it: the prose, numbers and rule; the boilerplate and steps stay in the file."""
    d = x.get('draft') if isinstance(x, dict) else None
    if not d:
        return x
    d = {k: v for k, v in d.items() if k not in ('manual', 'verify', 'undo', 'docs', 'related')}
    if d.get('apply'):
        d['apply'] = {'summary': d['apply'].get('summary')}
    return dict(x, draft=d)


def insights_view(out, gen):
    """insights.json in a few lines for the optimize skill: whether it belongs to this report (the stamp check the skill
    would otherwise do by hand), then per insight its id, category, title, bottom line and saving per 30 days."""
    p = os.path.join(out, 'insights.json')
    if not os.path.exists(p):
        return ['## insights: missing: run /claude-usage:report first']
    try:
        doc = read_json(p)
    except (OSError, ValueError):
        doc = None
    if not isinstance(doc, dict) or not isinstance(doc.get('insights') or [], list):
        return ['## insights: insights.json is not valid JSON of the expected shape: run /claude-usage:report again']
    items = [i for i in doc.get('insights') or [] if isinstance(i, dict)]
    was = (doc.get('source') if isinstance(doc.get('source'), dict) else {}).get('metrics_generated')
    head = (f"## insights ({len(items)}): current (written {doc.get('generated')} from this report, metrics {gen})" if was == gen else
            f'## insights ({len(items)}): stale (written from the report of {was}; this report is {gen}): run /claude-usage:report first')
    rows = []
    for i in items:
        x = {k: i[k] for k in ('id', 'category', 'title', 'bottom_line') if k in i}
        mo = (i.get('savings') or {}).get('usd_per_month') if isinstance(i.get('savings'), dict) else None
        if isinstance(mo, (int, float)):
            x['usd_per_month'] = mo
        rows.append(json.dumps(x, ensure_ascii=False))
    return [head] + rows


def card_view(out, spec):
    """card:CX3 (or card:CX3@<scope id>): every figure of one card as digest.md words them, but whole: all KPIs, every table
    row and column, every chart entry. For a number the digest cuts short, instead of computing it by hand."""
    cid, _, scope = spec[len('card:'):].partition('@')
    metrics = read_json(layout.data(out, 'metrics.json'))
    data = metrics.get('data') or {}
    c = ((data.get(scope or 'all') or {}).get('cards') or {}).get(cid.strip().upper())
    if not isinstance(c, dict):
        return [f'## {spec}: no such card' + (f' in scope {scope!r} (scopes: {", ".join(sorted(data))})' if scope else ' in this report')]
    L = [f"## {spec}: {c.get('q', '')}" + (' (not shown in the report: use its numbers, but do not cite it)'
                                          if c.get('hidden') and not c.get('alias') else '')]
    if c.get('empty'):
        L.append(f"(no data: {c['empty']})")
    cell = UR.md_cell
    for b in UR.flat_blocks(c):
        k = b.get('kind')
        if k == 'kpis':
            L.append('- ' + ' · '.join(kpi_text(i) for i in b.get('items') or [] if i.get('value') is not None))
        elif k == 'table':
            cols = b.get('columns') or []
            L.append(f"Table: {b.get('title') or 'rows'} ({plural(len(b.get('rows') or []), 'row')})")
            L.append('| ' + ' | '.join(col.get('label', '') for col in cols) + ' |')
            L += ['| ' + ' | '.join(cell(r.get(col.get('key')), col.get('unit')) for col in cols) + ' |' for r in b.get('rows') or []]
        elif k == 'chart':
            ch = b.get('chart') or {}
            ser = ch.get('series') or []
            if ch.get('type') in ('bar', 'pie'):
                val = lambda i: ' / '.join(cell(x['values'][i] if i < len(x.get('values') or []) else None, x.get('unit') or ch.get('unit')) for x in ser)
                L.append(f"Chart: {ch.get('title') or ch.get('type')} — " + '; '.join(f'{cat}: {val(i)}' for i, cat in enumerate(ch.get('categories') or []))
                         + (f" (series: {', '.join(x.get('name', '') for x in ser)})" if len(ser) > 1 else ''))
            elif ch.get('type') == 'line':
                for x in ser:
                    real = ch.get('indexed') and x.get('raw')
                    u = x.get('rawUnit') if real else ch.get('unit')
                    L.append(f"Chart: {ch.get('title') or 'line'} / {x.get('name', '')} — "
                             + '; '.join(f'{xx}: {cell(v, u)}' for xx, v in zip(ch.get('x') or [], x['raw'] if real else x.get('values') or []) if v is not None))
            else:
                L.append(f"Chart: {ch.get('title') or ch.get('type')} — {plural(len(ch.get('points') or []), 'point')}")
        elif k == 'list':
            L.append(f"{b.get('title') or 'List'}: " + ', '.join(str(x) for x in b.get('items') or []))
        elif k == 'text':
            L.append(str(b.get('text', '')))
        elif k == 'traces':
            L.append(f"Step-by-step traces: {len(b.get('ids') or [])} (the digest's section “Costliest cache misses, step by step”)")
    L += [f'Insight: {t}' for t in [c.get('insight')] + list(c.get('insights') or []) if t]
    if c.get('note'):
        L.append(f"Note: {c['note']}")
    return L


def load_fresh(out):
    """candidates.json when it was built from the current metrics.json, else rebuilt now."""
    p = layout.data(out, NAME)
    try:
        doc = read_json(p)
        meta = read_json(layout.data(out, 'metrics.json')).get('meta') or {}
        if (doc.get('source') or {}).get('metrics_generated') == meta.get('generated'):
            return doc
    except (OSError, ValueError):
        pass
    return write_candidates(out)


def main(argv=None):
    VA.safe_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', metavar='DIR', help='the report folder (default: the one usage_report.py uses, e.g. ~/.claude-usage)')
    ap.add_argument('--claude-dir', help='the Claude folder the report was built from (only to find the default --out)')
    ap.add_argument('--show', metavar='SECTIONS', help='print these sections of candidates.json, one compact JSON item per line '
                    '(levers; optimizations,judgment,links,skipped), rebuilding it first only if it is missing or out of date; '
                    '"insights" lists insights.json compactly and says whether it is current for this report; "card:CX3" '
                    '(or card:CX3@<scope id>) prints every figure of one card, all rows')
    ap.add_argument('--brief', action='store_true', help="with --show: drafts without what reviewing them doesn't need (manual, "
                    'verify, undo, docs, the apply steps, related: links lists the pairs)')
    a = ap.parse_args(argv)
    out = os.path.abspath(os.path.expanduser(a.out)) if a.out else UR.default_out(UR.claude_dir(a.claude_dir))
    if not os.path.exists(layout.data(out, 'metrics.json')):
        sys.exit(f'No report data in {UR.tilde(layout.data_dir(out))}: run usage_report.py first.')
    if a.show:
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')     # ≈, →, “ ” read the same on every console
        except (AttributeError, ValueError):
            pass
        doc = load_fresh(out)
        print(f"# {UR.tilde(layout.data(out, NAME))} · metrics {doc['source'].get('metrics_generated')} · "
              f"spend {usd(doc['source'].get('spend_usd'))} over {doc['source'].get('days')} days")
        for sec in [x.strip() for x in a.show.split(',') if x.strip()]:
            if sec == 'insights':
                print('\n'.join(insights_view(out, doc['source'].get('metrics_generated'))))
                continue
            if sec.startswith('card:'):
                print('\n'.join(card_view(out, sec)))
                continue
            items = doc.get(sec)
            if items is None:
                sys.exit(f'No section {sec!r}: pick from ' + ', '.join(k for k, x in doc.items() if isinstance(x, list)))
            print(f'## {sec} ({len(items)})')
            for x in items:
                print(json.dumps(brief(x) if a.brief else x, ensure_ascii=False))
        return
    doc = write_candidates(out)
    print('\n'.join(summary_lines(doc)))
    print(UR.tilde(layout.data(out, NAME)))
    if doc.get('problems'):
        sys.exit(1)


if __name__ == '__main__':
    main()
