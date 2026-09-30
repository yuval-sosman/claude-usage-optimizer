#!/usr/bin/env python3
"""Write insights.json or optimizations.json from the script's candidates and Claude's short notes, then validate them.

  python3 assemble.py insights      [--out DIR] [--notes FILE|-]   notes: the levers to use, the prose, the other insights
  python3 assemble.py optimizations [--out DIR] [--notes FILE|-]   notes: candidates to drop or edit, research-pass additions, pairs

The notes are a small JSON object that the skill Writes to <out>/data/notes-insights.json or notes-optimizations.json (the
default); --notes reads another file, or stdin with "-".
skills/report/SKILL.md and skills/optimize/SKILL.md give the formats. Everything the data decides comes from
<out>/data/candidates.json (written by usage_report.py, rebuilt here when missing or older than metrics.json): savings,
evidence, question ids, apply steps, manual/undo/verify/docs, related links on both sides. This fills in the source stamps,
generated and author; drops links to items left out; links insights and optimizations both ways; and keeps an applied
optimization's id when a newer version of the same change replaces it (so apply.py undoes the old one first). A file is
written only when validate.py finds no problem; otherwise the problems are printed, one per line, and nothing changes
(exit 1). Either way the notes file stays, to Edit and run again; the next full report run clears it. Standard library only.
"""
import argparse
import copy
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import candidates as CA  # noqa: E402
import layout  # noqa: E402
import usage_report as UR  # noqa: E402  (default folders)
import validate as VA  # noqa: E402

INSIGHT_KEYS = ('id', 'category', 'title', 'bottom_line', 'detail', 'questions', 'evidence', 'priority', 'confidence', 'scope',
                'savings', 'actions', 'optimizations')
OPT_KEYS = ('id', 'title', 'category', 'kind', 'problem', 'what_it_does', 'insights', 'questions', 'savings', 'effort', 'risk',
            'tradeoffs', 'related', 'apply', 'manual', 'verify', 'undo', 'docs')
RELATIONS = ('alternative', 'conflicts', 'overlaps', 'complements', 'requires', 'none')


class Problems(Exception):
    """What stops the file from being written, one line each."""


def read_json(p):
    with open(p, encoding='utf-8') as fh:
        return json.load(fh)


def maybe(p):
    try:
        return read_json(p) if os.path.exists(p) else None
    except ValueError:
        return None


def now():
    return dt.datetime.now().strftime('%Y-%m-%d %H:%M')


def ordered(d, keys):
    """The keys in the order the schema lists them; anything else after (validate.py then names it)."""
    return dict([(k, d[k]) for k in keys if k in d] + [(k, x) for k, x in d.items() if k not in keys])


def stem(i):
    """An id without its trailing threshold: context-guard-150k -> context-guard."""
    return re.sub(r'-\d+[a-z]?$', '', i or '')


def merge(base, edit):
    """A shallow edit: a value replaces the field, null removes it; `savings` is merged key by key the same way."""
    out = dict(base)
    for k, x in edit.items():
        if x is None:
            out.pop(k, None)
        elif k == 'savings' and isinstance(x, dict) and isinstance(out.get('savings'), dict):
            sv = dict(out['savings'])
            for sk, sx in x.items():
                if sx is None:
                    sv.pop(sk, None)
                else:
                    sv[sk] = sx
            out['savings'] = sv
        else:
            out[k] = copy.deepcopy(x)
    return out


def write_atomic(p, doc):
    tmp = p + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as fh:
            fh.write(json.dumps(doc, indent=1, ensure_ascii=False) + '\n')
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def notes_path(out, kind):
    return layout.data(out, f'notes-{kind}.json')


def load_notes(a, out):
    """The notes, and the default notes file when they came from it: the default file, --notes FILE, or stdin. Always
    UTF-8 (a BOM is fine), whatever the console's locale."""
    default, where = notes_path(out, a.kind), None
    try:
        if a.notes == '-':
            raw, where = sys.stdin.buffer.read().decode('utf-8-sig'), 'stdin'
        elif a.notes or os.path.exists(default):
            with open(a.notes or default, encoding='utf-8-sig') as fh:
                raw, where = fh.read(), a.notes or default
        else:
            raise Problems([f'no notes: Write them to {default} first'])
    except (OSError, UnicodeDecodeError) as e:
        raise Problems([f'notes: {e}'])
    if not raw.strip():
        raise Problems([f'notes: {where} is empty'])
    try:
        notes = json.loads(raw)
    except ValueError as e:
        raise Problems([f'notes ({where}): not valid JSON ({e})'])
    if not isinstance(notes, dict):
        raise Problems([f'notes ({where}): expected a JSON object'])
    return notes, (default if where == default else None)


def fit(first, mine, rest, limit):
    """A list cut to the schema's limit without failing: the lever's main item, then Claude's additions, then the lever's
    other items, duplicates dropped."""
    out = []
    for x in list(first) + list(mine) + list(rest):
        if x not in out:
            out.append(x)
    return out[:limit]


def shown_questions(qs, metrics, scope, where, warn):
    """Question ids as the report can open them: a hidden card becomes the card it is shown inside (its alias), or is left
    out with a note. The last one is never dropped: then validate.py names it. (Evidence is not touched: a fact must come
    from a card the report shows.)"""
    data = (metrics or {}).get('data') or {}
    cards = (data.get(scope or 'all') or data.get('all') or {}).get('cards') or {}
    out, dropped = [], []
    for q in qs:
        c = cards.get(q) if isinstance(q, str) else None
        if isinstance(c, dict) and c.get('hidden'):
            if c.get('alias'):
                q = c['alias']
            else:
                dropped.append(q)
                continue
        if q not in out:
            out.append(q)
    if not out:
        return list(qs)
    if dropped:
        warn.append(f"{where}: left out {', '.join(dropped)} from its questions (not shown in the report; its numbers can still be "
                    'quoted in the text)')
    return out


def applied_ids(out):
    """Ids apply.py has recorded as applied (<out>/applied/applied.json). Its other keys (`_origins`, apply.py's record of
    each shared file's original state) start with an underscore, which no optimization id can."""
    st = maybe(os.path.join(layout.applied_dir(out), 'applied.json'))
    return {k for k, v in st.items() if not k.startswith('_') and isinstance(v, dict)} if isinstance(st, dict) else set()


def fresh_json(out, name, gen, skill):
    doc = maybe(os.path.join(out, name))
    if not doc:
        raise Problems([f'{name} is missing: run {skill} first'])
    if (doc.get('source') or {}).get('metrics_generated') != gen:
        raise Problems([f'{name} was written from an older report ({(doc.get("source") or {}).get("metrics_generated")}, the report is {gen}): '
                        f'run {skill} first'])
    return doc


def lever_insights(levers, insights):
    """Which insights tell each lever's story: the bundle's id, the same id with another threshold, or the same saving on the
    same main question."""
    out = {}
    for b in levers:
        sv = (b.get('savings') or {}).get('usd_so_far')
        for it in insights:
            same = it.get('id') == b['id'] or (stem(it.get('id')) == stem(b['id']) and it.get('category') == b.get('category'))
            same = same or (sv is not None and (it.get('savings') or {}).get('usd_so_far') == sv
                            and (it.get('questions') or [None])[0] == (b.get('questions') or [None])[0])
            if same and it.get('id') not in out.setdefault(b['lever'], []):
                out[b['lever']].append(it['id'])
    return out


# ---------- insights ----------

def build_insights(notes, cand, metrics, prev, opts):
    """insights.json from the lever bundles and the notes."""
    errs, warn = [], []
    gen = (metrics.get('meta') or {}).get('generated')
    head = ((metrics.get('data') or {}).get('all') or {}).get('headline') or {}
    spend, days = (head.get('hero') or {}).get('value') or 0, head.get('days') or 0
    bundles = {b['id']: b for b in cand.get('levers') or []}
    bundles.update({b['lever']: b for b in cand.get('levers') or [] if b['lever'] not in bundles})
    summary = notes.get('summary')
    if not isinstance(summary, str) or not summary.strip():
        errs.append('notes.summary: write 3–5 sentences, cost first (the first one is the lead, under ~160 characters)')
    items, lever_of, auto_overlap = [], {}, set()
    given = notes.get('insights')
    if not isinstance(given, list) or not given:
        errs.append('notes.insights: a list of insights (lever items and whole ones)')
        given = []
    for n, x in enumerate(given):
        if not isinstance(x, dict):
            errs.append(f'notes.insights[{n}]: expected an object')
            continue
        lv = x.get('lever')
        if lv:
            b = bundles.get(lv)
            if not b:
                errs.append(f'notes.insights[{n}]: no lever {lv!r} in candidates.json (there are: {", ".join(sorted(k for k in bundles if "-" in k))})')
                continue
            it = {k: copy.deepcopy(b[k]) for k in ('id', 'category', 'title', 'bottom_line', 'questions', 'evidence', 'priority',
                                                   'confidence', 'savings', 'actions') if k in b}
            if not (isinstance(x.get('savings'), dict) and 'overlaps_with' in x['savings']) and it.get('savings'):
                auto_overlap.add(n)
        else:
            it = {}
        adds = {}
        for k in ('evidence_add', 'questions_add'):
            adds[k] = x.get(k) or []
            if not isinstance(adds[k], list):
                errs.append(f'notes.insights[{n}].{k}: expected a list')
                adds[k] = []
        it = merge(it, {k: x[k] for k in x if k not in ('lever', 'evidence_add', 'questions_add', 'optimizations')})
        mine = x.get('savings') if isinstance(x.get('savings'), dict) else {}
        if lv and isinstance(it.get('savings'), dict) and 'usd_so_far' in mine and mine['usd_so_far'] != (b.get('savings') or {}).get('usd_so_far'):
            for k in ('pct_of_spend', 'usd_per_month'):     # the bundle's were for its own amount: recomputed below unless given
                if k not in mine:
                    it['savings'].pop(k, None)
        ev_, qs = it.get('evidence') or [], it.get('questions') or []
        if lv and isinstance(ev_, list) and isinstance(qs, list):     # a lever's lists fitted to the schema's 6, never an error
            it['evidence'] = fit(ev_[:1], adds['evidence_add'], ev_[1:], 6)
            qs = shown_questions(fit(qs[:1], adds['questions_add'], qs[1:], 99), metrics, it.get('scope'), f"insight {it.get('id') or n}", warn)
            it['questions'] = qs[:6]
        else:
            it['evidence'] = list(ev_) + adds['evidence_add'] if isinstance(ev_, list) else ev_
            it['questions'] = list(qs) + [q for q in adds['questions_add'] if q not in qs] if isinstance(qs, list) else qs
        if not it['evidence']:
            del it['evidence']
        if isinstance(it.get('questions'), list):
            it['questions'] = shown_questions(it['questions'], metrics, it.get('scope'), f"insight {it.get('id') or n}", warn)
        sv = it.get('savings')
        if isinstance(sv, dict) and isinstance(sv.get('usd_so_far'), (int, float)):
            if 'pct_of_spend' not in sv and spend:
                sv['pct_of_spend'] = round(sv['usd_so_far'] / spend * 100, 1)
            if 'usd_per_month' not in sv and days:
                sv['usd_per_month'] = round(sv['usd_so_far'] * 30 / days, 2)
            it['savings'] = ordered(sv, ('usd_so_far', 'pct_of_spend', 'usd_per_month', 'tokens_so_far', 'basis', 'kind', 'overlaps_with'))
        if lv:
            lever_of[n] = bundles[lv]
        items.append((n, it))
    ids = {it.get('id') for _, it in items}
    default = {b['id']: it['id'] for n, it in items for b in [lever_of.get(n)] if b}
    for n, it in items:
        sv = it.get('savings')
        if not isinstance(sv, dict) or not isinstance(sv.get('overlaps_with'), list):
            continue
        sv['overlaps_with'] = [default.get(o, o) for o in sv['overlaps_with']]      # lever ids as this file names them
        if n in auto_overlap:
            sv['overlaps_with'] = [o for o in sv['overlaps_with'] if o in ids and o != it['id']]
            if not sv['overlaps_with']:
                del sv['overlaps_with']
    out = [ordered(it, INSIGHT_KEYS) for _, it in items]
    if opts and (opts.get('source') or {}).get('metrics_generated') == gen:       # optimizations of this same report: keep their links
        for it in out:
            back = [o['id'] for o in opts.get('optimizations') or [] if it['id'] in (o.get('insights') or [])]
            if back:
                it['optimizations'] = back
    src = {'metrics_generated': gen, 'range': (metrics.get('meta') or {}).get('range') or {}, 'scope': 'all', 'spend_usd': spend}
    doc = {'schema_version': 1, 'generated': now(), 'author': 'Claude (claude-usage:report)', 'source': src,
           'summary': summary if isinstance(summary, str) else '', 'insights': out}
    used = {b['lever'] for b in lever_of.values()}
    big = [b for b in cand.get('levers') or [] if b['lever'] not in used and ((b.get('savings') or {}).get('pct_of_spend') or 0) >= 5]
    if big:
        warn.append('not used: ' + ', '.join(f"{b['id']} ({b['savings']['pct_of_spend']}% of spend)" for b in big))
    cost = sum(1 for it in out if it.get('category') == 'cost')
    if not 12 <= len(out) <= 20 or cost < 4:
        warn.append(f'{len(out)} insights, {cost} in cost: the guide asks for 12–20, at least 4 in cost')
    gone = [i.get('id') for i in (prev or {}).get('insights') or [] if isinstance(i, dict) and i.get('id') not in ids]
    if gone and (prev.get('source') or {}).get('metrics_generated') == gen:
        warn.append('the previous insights.json of this report also had: ' + ', '.join(gone[:12]) + (' …' if len(gone) > 12 else ''))
    return doc, errs, warn


# ---------- optimizations ----------

def signature(o, out):
    """What an optimization changes (hook scripts, settings keys, CLAUDE.md markers): the same change keeps the same id."""
    tags = set(VA.touches(o, out)[1])
    tags |= {'marker:' + st['marker'] for st in ((o.get('apply') or {}).get('steps') or []) if isinstance(st, dict) and st.get('marker')}
    return frozenset(tags)


def build_optimizations(notes, cand, metrics, insights, prev, out):
    errs, warn, info = [], [], []
    gen = (metrics.get('meta') or {}).get('generated')
    wrap = [(x, 'draft') for x in cand.get('optimizations') or []] + [(x, 'judgment') for x in cand.get('judgment') or [] if x.get('draft')]
    pool = {x['draft']['id']: x for x, _ in wrap}
    by_entry = {x['entry']: x['draft']['id'] for x, _ in wrap}
    judged = {x['draft']['id'] for x, k in wrap if k == 'judgment'}
    summary = notes.get('summary')
    if not isinstance(summary, str) or not summary.strip():
        errs.append('notes.summary: write 3–5 sentences: the choices ("pick one: …"), the biggest savings (overlapping ones as a range, never a sum)')
    for k, t in (('drop', (dict, list)), ('edit', dict), ('add', list), ('relate', list)):
        if k in notes and notes[k] is not None and not isinstance(notes[k], t):
            errs.append(f'notes.{k}: expected ' + ('an object keyed by candidate id' if t is dict else 'a list' if t is list else 'an object or a list'))
    drop = notes.get('drop') or {}
    if isinstance(drop, list):
        errs += [f'notes.drop[{n}]: expected a candidate id (a string)' for n, k in enumerate(drop) if not isinstance(k, str)]
        drop = {k: '' for k in drop if isinstance(k, str)}
    edit = notes.get('edit') or {}
    for what, keys in (('drop', drop), ('edit', edit)):
        if not isinstance(keys, dict):
            errs.append(f'notes.{what}: expected an object keyed by candidate id')
            continue
        for k in keys:
            if k not in pool and k not in by_entry:
                errs.append(f'notes.{what}: {k!r} is not a candidate (candidates: {", ".join(pool)})')
    drop = {by_entry.get(k, k) for k in (drop if isinstance(drop, dict) else {})}
    edit = {by_entry.get(k, k): x for k, x in (edit if isinstance(edit, dict) else {}).items()}
    chosen = [i for i in pool if i not in drop and (i not in judged or i in edit)]
    items, levers_of, explicit_ins, renames, entry_of = [], {}, set(), {}, {}
    for i in chosen:
        o = copy.deepcopy(pool[i]['draft'])
        o.pop('related', None)
        e = edit.get(i) or {}
        if not isinstance(e, dict):
            errs.append(f'notes.edit.{i}: expected an object of fields')
            continue
        if 'related' in e:
            errs.append(f'notes.edit.{i}.related: say how items relate in notes.relate (it sets both sides)')
        o = merge(o, {k: x for k, x in e.items() if k != 'related'})
        if o['id'] != i:
            renames[i] = o['id']
        if 'insights' in e:
            explicit_ins.add(id(o))
        levers_of[o['id']] = pool[i].get('levers') or []
        entry_of[id(o)] = (pool[i].get('entry'), pool[i].get('carry') or [])
        items.append(o)
    added = []
    for n, x in enumerate(notes.get('add') if isinstance(notes.get('add'), list) else []):
        if not isinstance(x, dict) or not x.get('id'):
            errs.append(f'notes.add[{n}]: expected a whole optimization with an id')
            continue
        o = copy.deepcopy(x)
        if o.get('apply') and not o.get('undo'):
            o['undo'] = f"apply.py undo {o['id']}"
        rel = o.get('related')
        if rel is not None and not (isinstance(rel, list) and all(isinstance(r, dict) and isinstance(r.get('id'), str) for r in rel)):
            errs.append(f"notes.add[{n}].related: a list of {{\"id\", \"relation\", \"note\"}} (or say it in notes.relate)")
            o.pop('related', None)
        explicit_ins.add(id(o))
        added.append((n, o))
        items.append(o)
    ids = [o.get('id') for o in items]
    for d in sorted({x for x in ids if ids.count(x) > 1}):
        errs.append(f'two optimizations have the id {d!r}')
    # an applied optimization keeps its id when a newer version of the same change (same hook script, settings keys or
    # CLAUDE.md marker; or the same entry with another threshold) replaces it: apply.py then undoes the applied version
    # before applying this one, instead of adding a second copy. Anything not applied gets its own, accurate id.
    applied = applied_ids(out)
    prev_opts = [p for p in (prev or {}).get('optimizations') or [] if isinstance(p, dict) and p.get('id')]
    taken = set(ids)
    for o in items:
        if not isinstance(o.get('id'), str) or o['id'] in applied or o['id'] in renames.values():
            continue                    # already the applied id, or renamed by the notes on purpose
        sig = signature(o, out)
        cands = [p for p in prev_opts if p['id'] in applied and p['id'] not in taken]
        old = [p['id'] for p in cands if sig and signature(p, out) == sig]
        old = old or [p['id'] for p in cands if stem(p['id']) == stem(o['id']) and p.get('kind') == o.get('kind')]
        if len(old) == 1:
            info.append(f"kept the id {old[0]} for {o['id']}: that change is applied, so apply.py replaces it instead of adding a second copy")
            renames[o['id']] = old[0]
            levers_of[old[0]] = levers_of.pop(o['id'], [])
            taken.add(old[0])
            o['id'] = old[0]
    rename_undo(items, renames)
    carried = carry_applied(items, entry_of, applied, out, S=CA.Setup(maybe(layout.data(out, 'config.json')) or {}), info=info)
    gone = sorted(i for i in applied if i not in {o.get('id') for o in items})
    if gone:
        warn.append('applied earlier and not in this file: ' + ', '.join(gone) + ' (they stay applied; apply.py undo <id> removes one)')
    rn = lambda i: renames.get(i, i)
    present = {o['id'] for o in items}
    # related: the catalog's pairs between the items kept, then the notes' own pairs
    pairs = {}
    for x in cand.get('links') or []:
        a, b = rn(x['a']), rn(x['b'])
        if a in present and b in present:
            pairs[(a, b) if x['relation'] == 'requires' else frozenset((a, b))] = dict(x, a=a, b=b)
    # a carried main-thread cache lifetime pairs cache-ttl-fit with the stale-cache guard, as a changed one would
    by_entry_final = {entry_of[id(o)][0]: o for o in items if id(o) in entry_of}
    ttl, guard = by_entry_final.get('cache-ttl-fit'), by_entry_final.get('stale-cache-guard')
    main = carried.get(id(ttl), {}).get('promptCacheTtl') if ttl else None
    if main and guard and frozenset((ttl['id'], guard['id'])) not in pairs:
        for x in CA.links({'stale-cache-guard': guard['id'], 'cache-ttl-fit': ttl['id']}, {'ttl_main': main}):
            pairs[frozenset((x['a'], x['b']))] = x
    for n, r in enumerate(notes.get('relate') if isinstance(notes.get('relate'), list) else []):
        r = r if isinstance(r, dict) else {}
        a, b, rel = r.get('a'), r.get('b'), r.get('relation')
        a, b = (rn(a) if isinstance(a, str) else None), (rn(b) if isinstance(b, str) else None)
        if a not in present or b not in present or rel not in RELATIONS or a == b:
            errs.append(f'notes.relate[{n}]: needs "a" and "b" (ids being written) and a "relation" from {", ".join(RELATIONS)}')
            continue
        need = ('note_a',) if rel == 'requires' else () if rel == 'none' else ('note_a', 'note_b')
        if any(not isinstance(r.get(k), str) for k in need):
            errs.append(f'notes.relate[{n}]: needs ' + ' and '.join(need) + ', one sentence from each side')
            continue
        for key in (frozenset((a, b)), (a, b), (b, a)):
            pairs.pop(key, None)
        if rel != 'none':
            pairs[(a, b) if rel == 'requires' else frozenset((a, b))] = {'a': a, 'b': b, 'relation': rel, 'note_a': r.get('note_a'),
                                                                        'note_b': r.get('note_b')}
    for n, o in added:                  # a whole item's own `related`: the other side gets the same note unless it says otherwise
        rels = o.pop('related', None) or []
        seen = [rn(r['id']) for r in rels]
        for r in rels:
            b = rn(r['id'])
            if b not in present or b == o['id'] or r.get('relation') not in RELATIONS[:-1] or seen.count(b) > 1 or not isinstance(r.get('note'), str):
                errs.append(f"notes.add[{n}].related: {r['id']!r} needs to be another optimization being written, once, with a "
                            f"relation from {', '.join(RELATIONS[:-1])} and a note")
                continue
            if r.get('relation') == 'requires' or frozenset((o['id'], b)) not in pairs:
                pairs[(o['id'], b) if r.get('relation') == 'requires' else frozenset((o['id'], b))] = {
                    'a': o['id'], 'b': b, 'relation': r.get('relation'), 'note_a': r.get('note'), 'note_b': r.get('note')}
    links = list(pairs.values())
    # insights: the notes' own list, else the insights that tell the story of the item's levers
    by_lever = lever_insights(cand.get('levers') or [], insights.get('insights') or [])
    ins_ids = {i.get('id') for i in insights.get('insights') or []}
    now_named = {b['id']: by_lever[b['lever']][0] for b in cand.get('levers') or [] if by_lever.get(b['lever'])}
    final = []
    for o in items:
        o['related'] = CA.related_from(links, o['id'], present)
        if isinstance(o.get('questions'), list):
            o['questions'] = shown_questions(o['questions'], metrics, 'all', f"optimization {o['id']}", warn)
        if id(o) in explicit_ins:             # a lever's insight named by its candidates.json id: the id it has now
            o['insights'] = [i if i in ins_ids else now_named.get(i, i) for i in o.get('insights') or []]
        else:
            got = [i for lv in levers_of.get(o['id'], []) for i in by_lever.get(lv, [])]
            got += [i for i in o.get('insights') or [] if i in ins_ids]
            o['insights'] = list(dict.fromkeys(got))
        for k in ('related', 'insights'):
            if not o.get(k):
                o.pop(k, None)
        final.append(ordered(o, OPT_KEYS))
    rank = {c: n for n, c in enumerate(CA.CATEGORIES)}
    final.sort(key=lambda o: (rank.get(o.get('category'), 99), -((o.get('savings') or {}).get('usd_so_far') or 0)))
    src = {'metrics_generated': gen, 'insights_generated': insights.get('generated')}
    ccv = notes.get('claude_code_version')
    if isinstance(ccv, (str, int, float)) and not isinstance(ccv, bool) and str(ccv).strip():
        src['claude_code_version'] = str(ccv).strip()
    elif ccv not in (None, ''):
        errs.append('notes.claude_code_version: the version `claude --version` prints, as a string')
    doc = {'schema_version': 1, 'generated': now(), 'author': 'Claude (claude-usage:optimize)', 'source': src,
           'summary': summary if isinstance(summary, str) else '', 'optimizations': final}
    unused = sorted(i for i in judged if i not in edit and i not in drop)
    if unused:
        warn.append('judgment drafts left out: ' + ', '.join(unused) + ' (name one in notes.edit to include it)')
    return doc, errs, warn, info


def written_by(record, target):
    """What an applied optimization changed in one JSON file, from apply.py's record (its copies of the file before and
    after): {key: value}, and for objects {key: {sub key: value}}."""
    for f in (record.get('files') or []) if isinstance(record, dict) else []:
        if not isinstance(f, dict) or os.path.normpath(f.get('path') or '') != target:
            continue
        after = maybe(f.get('after') or '')
        before = (maybe(f['backup']) if f.get('backup') else {}) or {}
        if not isinstance(after, dict):
            return {}
        before = before if isinstance(before, dict) else {}
        got = {}
        for k, a in after.items():
            b = before.get(k)
            if isinstance(a, dict):
                sub = {s_: x for s_, x in a.items() if not isinstance(b, dict) or b.get(s_) != x}
                if sub:
                    got[k] = sub
            elif a != b:
                got[k] = a
        return got
    return {}


def carry_applied(items, entry_of, applied, out, S, info):
    """A newer version of an applied optimization replaces it (apply.py undoes the old version first), so what the applied
    version set under the draft's `carry` keys is set again, where settings.json still holds it. Only what that apply wrote:
    the user's own entries are never copied. Returns {id(item): {key: value}} of what was carried."""
    target = os.path.normpath(VA.AP.resolve(CA.SETTINGS, out))
    state = maybe(os.path.join(layout.applied_dir(out), 'applied.json')) or {}
    done = {}
    for o in items:
        entry, keys = entry_of.get(id(o), (None, []))
        if not keys or o.get('id') not in applied:
            continue
        wrote, now, keep = written_by(state.get(o['id']), target), S.main, {}
        for k in keys:
            x = wrote.get(k)
            if isinstance(x, dict):
                sub = {s_: v for s_, v in x.items() if isinstance(now.get(k), dict) and now[k].get(s_) == v}
                if sub:
                    keep[k] = sub
            elif k in wrote and now.get(k) == x:
                keep[k] = x
        step = next((st for st in (o.get('apply') or {}).get('steps') or [] if isinstance(st, dict) and st.get('action') == 'merge_json'
                     and st.get('path') == CA.SETTINGS and isinstance(st.get('value'), dict)), None)
        if not keep or not step:
            continue
        added = {}
        for k, x in keep.items():
            if isinstance(x, dict):
                new = {s_: v for s_, v in x.items() if s_ not in (step['value'].get(k) or {})}
                if new:
                    step['value'][k] = dict(new, **(step['value'].get(k) or {}))
                    added[k] = new
            elif k not in step['value']:
                step['value'][k] = x
                added[k] = x
        if added:
            done[id(o)] = added
            what = ', '.join(f"{len(x)} {k} entr{'y' if len(x) == 1 else 'ies'}" if isinstance(x, dict) else f'{k}={json.dumps(x)}'
                             for k, x in added.items())
            o['what_it_does'] = CA.clip((o.get('what_it_does') or '') + f' It also keeps what the version you applied earlier set ({what}).', 700)
            info.append(f"{o['id']}: carried from the applied version ({what}), so re-applying doesn't undo them")
    return done


def rename_undo(items, renames):
    """An item renamed (by the notes or to keep an applied id) says so in its undo line too."""
    for o in items:
        for old, new in renames.items():
            if o.get('id') == new and isinstance(o.get('undo'), str):
                o['undo'] = re.sub(r'(apply\.py undo )' + re.escape(old) + r'\b', lambda m: m.group(1) + new, o['undo'])


def link_back(insights, opts):
    """Each insight lists the optimizations that name it, and nothing else."""
    doc = copy.deepcopy(insights)
    for it in doc.get('insights') or []:
        back = [o['id'] for o in opts.get('optimizations') or [] if it.get('id') in (o.get('insights') or [])]
        if back:
            it['optimizations'] = back
        else:
            it.pop('optimizations', None)
    return doc


# ---------- command line ----------

def quiet(found, errs, summary_path, items=()):
    """validate.py's problems, each item named by its id as well as its place in the file, without repeating the
    missing-summary problem the notes check already named."""
    if any(e.startswith('notes.summary') for e in errs):
        found = [e for e in found if not e.startswith(summary_path)]
    ids = [x.get('id') if isinstance(x, dict) else None for x in items]
    name = lambda m: m.group(0) + (f' ({ids[int(m.group(2))]})' if int(m.group(2)) < len(ids) else '')
    return [re.sub(r'\.(insights|optimizations)\[(\d+)\](?! \()', name, e) for e in found]


def run(kind, out, notes):
    try:
        return build_and_write(kind, out, notes)
    except (AttributeError, KeyError, TypeError, ValueError) as e:     # notes in a shape the checks above didn't foresee
        raise Problems([f'notes: unexpected shape ({type(e).__name__}: {e}); compare them with the format in the skill'])


def build_and_write(kind, out, notes):
    metrics = read_json(layout.data(out, 'metrics.json'))
    gen = (metrics.get('meta') or {}).get('generated')
    cand = CA.load_fresh(out)
    info = []
    if kind == 'insights':
        prev = maybe(os.path.join(out, 'insights.json'))
        doc, errs, warn = build_insights(notes, cand, metrics, prev, maybe(os.path.join(out, 'optimizations.json')))
        errs = errs + quiet(VA.validate_insights(doc, metrics), errs, 'insights.json.summary', doc['insights'])
        if errs:
            raise Problems(errs)
        write_atomic(os.path.join(out, 'insights.json'), doc)
        n = len(doc['insights'])
        return [f"OK: {n} insights ({sum(1 for i in doc['insights'] if i.get('savings'))} with savings) in {UR.tilde(os.path.join(out, 'insights.json'))}"] \
            + info + [f'note: {w}' for w in warn]
    insights = fresh_json(out, 'insights.json', gen, '/claude-usage:report')
    prev = maybe(os.path.join(out, 'optimizations.json'))
    saved = VA.AP.CLAUDE_DIR
    try:
        VA.AP.CLAUDE_DIR = VA.AP.report_claude_dir(out) or saved      # the folder apply.py will write to
        doc, errs, warn, info = build_optimizations(notes, cand, metrics, insights, prev, out)
        ins_after = link_back(insights, doc)
        errs = errs + quiet(VA.validate_optimizations(doc, metrics, ins_after, out=out), errs, 'optimizations.json.summary',
                            doc['optimizations'])
    finally:
        VA.AP.CLAUDE_DIR = saved
    errs += [f'insights.json: {e}' for e in VA.validate_insights(ins_after, metrics)]
    if errs:
        raise Problems(errs)
    write_atomic(os.path.join(out, 'optimizations.json'), doc)
    if ins_after != insights:
        write_atomic(os.path.join(out, 'insights.json'), ins_after)
    linked = sum(1 for i in ins_after.get('insights') or [] if i.get('optimizations'))
    return [f"OK: {len(doc['optimizations'])} optimizations in {UR.tilde(os.path.join(out, 'optimizations.json'))}; "
            f"{linked} insights link to them"] + info + [f'note: {w}' for w in warn]


def main(argv=None):
    VA.safe_console()
    os.umask(0o077)                                     # the report folder holds private data: yours only
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('kind', choices=['insights', 'optimizations'])
    ap.add_argument('--out', metavar='DIR', help='the report folder (default: the one usage_report.py uses, e.g. ~/.claude-usage)')
    ap.add_argument('--claude-dir', help='the Claude folder the report was built from (only to find the default --out)')
    ap.add_argument('--notes', metavar='FILE', help='read the notes from this file ("-" for stdin) instead of <out>/data/notes-<kind>.json')
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')         # ids, “quotes” and ≈ read the same on every console
    except (AttributeError, ValueError):
        pass
    out = os.path.abspath(os.path.expanduser(a.out)) if a.out else UR.default_out(UR.claude_dir(a.claude_dir))
    layout.require_report(out)                             # it only ever adds to a report folder
    if not os.path.exists(layout.data(out, 'metrics.json')):
        sys.exit(f'No report data in {UR.tilde(layout.data_dir(out))}: run usage_report.py first.')
    try:
        notes, kept = load_notes(a, out)
        print('\n'.join(run(a.kind, out, notes)))
        if kept:                    # it stays until the next full run (write_candidates removes notes older than metrics.json)
            print(f'The notes stay in {UR.tilde(kept)}: to change anything (a note: line above, an apply.py check problem), '
                  'Edit them and run this again.')
    except Problems as e:
        errs = e.args[0]
        print(f'{len(errs)} problem(s); nothing was written' + (f' (the notes are still in {UR.tilde(notes_path(out, a.kind))}: '
                                                             'Edit them and run this again)' if os.path.exists(notes_path(out, a.kind)) else '') + ':')
        for x in errs[:40]:
            print('  - ' + x)
        if len(errs) > 40:
            print(f'  … and {len(errs) - 40} more')
        sys.exit(1)


if __name__ == '__main__':
    main()
