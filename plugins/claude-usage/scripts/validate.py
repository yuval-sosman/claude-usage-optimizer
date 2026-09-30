#!/usr/bin/env python3
"""Check insights.json / optimizations.json against their schema and against the report they describe.

  python3 validate.py insights      <out>/insights.json      --metrics <out>/data/metrics.json
  python3 validate.py optimizations <out>/optimizations.json --metrics <out>/data/metrics.json [--insights <out>/insights.json]

Prints OK, or one line per problem, and exits 1 when there are problems. Standard library only.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import apply as AP  # noqa: E402  (the exact path rules apply.py uses)
import layout  # noqa: E402
import policy  # noqa: E402  (what an optimization may change at all)
ROOT = os.path.dirname(HERE)
TYPES = {'object': dict, 'array': list, 'string': str, 'boolean': bool, 'null': type(None)}


def type_ok(v, t):
    if isinstance(t, list):
        return any(type_ok(v, x) for x in t)
    if t == 'number':
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == 'integer':
        return isinstance(v, int) and not isinstance(v, bool)
    return isinstance(v, TYPES.get(t, object))


def check(v, sch, path, errs):
    """The subset of JSON Schema the plugin's schemas use."""
    if 'const' in sch and v != sch['const']:
        errs.append(f'{path}: must be {sch["const"]!r}')
    if 'enum' in sch and v not in sch['enum']:
        errs.append(f'{path}: {v!r} is not one of {sch["enum"]}')
    if 'type' in sch and not type_ok(v, sch['type']):
        errs.append(f'{path}: expected {sch["type"]}, got {type(v).__name__}')
        return
    if isinstance(v, str):
        if len(v) < sch.get('minLength', 0):
            errs.append(f'{path}: shorter than {sch["minLength"]} characters')
        if 'maxLength' in sch and len(v) > sch['maxLength']:
            errs.append(f'{path}: {len(v)} characters, over the {sch["maxLength"]} limit')
        if 'pattern' in sch and not re.search(sch['pattern'], v):
            errs.append(f'{path}: {v!r} does not match {sch["pattern"]}')
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 'minimum' in sch and v < sch['minimum']:
            errs.append(f'{path}: below {sch["minimum"]}')
        if 'maximum' in sch and v > sch['maximum']:
            errs.append(f'{path}: above {sch["maximum"]}')
    if isinstance(v, list):
        if len(v) < sch.get('minItems', 0):
            errs.append(f'{path}: needs at least {sch["minItems"]} items')
        if 'maxItems' in sch and len(v) > sch['maxItems']:
            errs.append(f'{path}: more than {sch["maxItems"]} items')
        for i, x in enumerate(v):
            if 'items' in sch:
                check(x, sch['items'], f'{path}[{i}]', errs)
    if isinstance(v, dict):
        props = sch.get('properties', {})
        for k in sch.get('required', []):
            if k not in v:
                errs.append(f'{path}: missing "{k}"')
        extra = sch.get('additionalProperties')
        for k, x in v.items():
            if k in props:
                check(x, props[k], f'{path}.{k}', errs)
            elif extra is False:
                errs.append(f'{path}: unknown field "{k}"')
            elif isinstance(extra, dict):
                check(x, extra, f'{path}.{k}', errs)


def load(path, what):
    path = os.path.expanduser(path)
    moved = layout.data(os.path.dirname(path), os.path.basename(path))
    if not os.path.exists(path) and os.path.basename(path) in layout.DATA_FILES and os.path.exists(moved):
        path = moved                                   # <out>/metrics.json, from before data/ existed
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except FileNotFoundError:
        sys.exit(f'{what} not found: {path}')
    except ValueError as e:
        sys.exit(f'{what} is not valid JSON: {e}')


def report_cards(metrics, scope='all'):
    """The questions the report shows (cards kept only in the data, marked hidden, are left out) and its scope ids."""
    data = metrics.get('data') or {}
    cards = (data.get(scope) or data.get('all') or {}).get('cards') or {}
    return {k for k, v in cards.items() if not (isinstance(v, dict) and v.get('hidden') and not v.get('alias'))}, set(data.keys())


def month_errs(sv, metrics, scope, where):
    """usd_per_month must be the same saving at 30 days' pace: usd_so_far × 30 / the days the scope covers."""
    d = ((((metrics or {}).get('data') or {}).get(scope or 'all') or {}).get('headline') or {}).get('days')
    so, mo = (sv or {}).get('usd_so_far'), (sv or {}).get('usd_per_month')
    if not (isinstance(d, (int, float)) and d > 0 and isinstance(so, (int, float)) and isinstance(mo, (int, float))):
        return []
    want = so * 30 / d
    if abs(mo - want) > max(0.5, 0.1 * want):
        return [f'{where}: savings.usd_per_month ${mo:.2f} should be about ${want:.2f} (usd_so_far × 30 / {d:g} days)']
    return []


def validate_insights(doc, metrics):
    errs = []
    check(doc, load(os.path.join(ROOT, 'schemas', 'insights.schema.json'), 'schema'), 'insights.json', errs)
    if not isinstance(doc, dict):
        return errs
    ids = [i.get('id') for i in doc.get('insights') or [] if isinstance(i, dict)]
    dup = {x for x in ids if ids.count(x) > 1}
    if dup:
        errs.append(f'duplicate insight ids: {sorted(dup)}')
    if metrics:
        gen = (metrics.get('meta') or {}).get('generated')
        if (doc.get('source') or {}).get('metrics_generated') not in (None, gen):
            errs.append(f'source.metrics_generated should be {gen!r} (the metrics.json you read)')
        spend = ((metrics.get('data') or {}).get('all') or {}).get('headline', {}).get('hero', {}).get('value') or 0
        for n, it in enumerate(doc.get('insights') or []):
            if not isinstance(it, dict):
                continue
            cards, scopes = report_cards(metrics, it.get('scope') or 'all')
            where = f'insights[{n}] ({it.get("id")})'
            if it.get('scope') and it['scope'] not in scopes:
                errs.append(f'{where}: scope {it["scope"]!r} is not in the report')
            for q in (it.get('questions') or []) + [e.get('question') for e in it.get('evidence') or [] if isinstance(e, dict)]:
                if q and q not in cards:
                    errs.append(f'{where}: question {q} is not shown in the report (cite a visible question; the digest marks hidden ones)')
            sv = it.get('savings')
            if it.get('category') == 'cost' and not sv:
                errs.append(f'{where}: cost insights need "savings" (what applying it so far would have saved)')
            if isinstance(sv, dict) and isinstance(sv.get('usd_so_far'), (int, float)) and spend and sv['usd_so_far'] > spend:
                errs.append(f'{where}: savings.usd_so_far ${sv["usd_so_far"]:.2f} is more than the whole spend ${spend:.2f}')
            errs += month_errs(sv, metrics, it.get('scope'), where)
            for o in (sv or {}).get('overlaps_with') or []:
                if o not in ids:
                    errs.append(f'{where}: overlaps_with {o!r} is not an insight id')
    return errs


def home_path(p, out):
    """Where apply.py would write p: the same variable expansion (${HOME}, ${CLAUDE_DIR}, ${OUT}, ~/.claude mapping…)."""
    return AP.resolve(p, out)


def step_errs(st, out):
    """What policy.py says about one apply step: its shape and target, then the settings change it makes, tried on an
    empty settings file (apply.py checks it again against the user's real file before it previews or writes)."""
    errs = policy.step_problems(st, lambda p: home_path(p, out), AP.CLAUDE_DIR, AP.HOME)
    if errs or st.get('action') not in policy.JSON_ACTIONS:
        return errs
    st = AP.expand_all(st, out)
    try:
        if st['action'] == 'merge_json':
            before, after = {}, AP.deep_merge({}, st['value'])
        else:
            before = AP.set_pointer({}, st['pointer'], None)      # something there to set over or to remove
            after = AP.set_pointer(before, st['pointer'], st['value']) if st['action'] == 'set_json' else AP.unset_pointer(before, st['pointer'])
    except (Exception, SystemExit) as e:
        return [f'the JSON change can\'t be made ({e})']
    return policy.settings_problems(before, after, AP.hook_dirs(), (), AP.fast_command)


SYMMETRIC = ('alternative', 'conflicts', 'overlaps', 'complements')      # listed by both sides; "requires" by one
SAME_LEVER = (   # changes that act on the same cost: two optimizations making them must say how they relate
    ('context size (compacting automatically vs a notice)', {'setting:autoCompactWindow', 'hook:context_guard.py'}),
    ('returns after the cache expired (the main-thread cache lifetime vs the stale-cache guard)',
     {'setting:promptCacheTtl', 'hook:stale_cache_guard.py'}),
)


def key_paths(v, pre=''):
    """The settings keys a merge_json value sets, e.g. {"env": {"X": 1}} -> ["env/X"]. Hook lists are appended, not set."""
    if not isinstance(v, dict) or not v or pre == 'hooks':
        return [pre] if pre and pre != 'hooks' else []
    return [k for key, x in v.items() for k in key_paths(x, f'{pre}/{key}' if pre else key)]


def touches(o, out):
    """What an optimization's apply steps change: {(file, key path)} for JSON settings, and lever tags."""
    keys, tags = set(), set()
    for st in ((o.get('apply') or {}).get('steps')) or []:
        if not isinstance(st, dict):
            continue
        act, path = st.get('action'), home_path(st.get('path') or '', out)
        if act == 'write_file' and st.get('source'):
            tags.add('hook:' + os.path.basename(st['source']))
        paths = key_paths(st.get('value')) if act == 'merge_json' else \
            [st['pointer'].strip('/')] if act in ('set_json', 'unset_json') and st.get('pointer') else []
        for k in paths:
            keys.add((path, k))
            tags.add('setting:' + k)
    return keys, tags


def relation_errs(opts, out):
    errs, by_id = [], {o.get('id'): o for o in opts}
    rel = {o.get('id'): {r.get('id'): r.get('relation') for r in o.get('related') or [] if isinstance(r, dict)} for o in opts}
    for o in opts:
        oid, where = o.get('id'), f'optimization {o.get("id")}'
        seen = [r.get('id') for r in o.get('related') or [] if isinstance(r, dict)]
        for x in sorted({x for x in seen if seen.count(x) > 1}):
            errs.append(f'{where}: related lists {x!r} more than once')
        for x, kind in rel[oid].items():
            if x == oid:
                errs.append(f'{where}: related to itself')
            elif x not in by_id:
                errs.append(f'{where}: related {x!r} is not an optimization id in this file')
            elif kind in SYMMETRIC and rel[x].get(oid) != kind:
                errs.append(f'{where}: lists {x} as {kind}, so {x} must list {oid} as {kind} too' +
                            (f' (it says {rel[x][oid]})' if oid in rel[x] else ''))
            elif kind == 'requires' and rel[x].get(oid) == 'requires':
                errs.append(f'{where}: {oid} and {x} require each other')
    info = {o.get('id'): touches(o, out) for o in opts}
    ids = list(by_id)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if b in rel[a] or a in rel[b]:
                continue
            same = sorted(k for f, k in info[a][0] & info[b][0])
            if same:
                errs.append(f'{a} and {b} both change {", ".join(same)}: say how they relate in "related" (usually conflicts or alternative)')
                continue
            for name, lever in SAME_LEVER:
                if info[a][1] & lever and info[b][1] & lever:
                    errs.append(f'{a} and {b} act on the same cost, {name}: say how they relate in "related" '
                                '(alternative, overlaps or complements) and why')
                    break
    return errs


def validate_optimizations(doc, metrics, insights, out=None):
    errs = []
    check(doc, load(os.path.join(ROOT, 'schemas', 'optimizations.schema.json'), 'schema'), 'optimizations.json', errs)
    if not isinstance(doc, dict):
        return errs
    ids = [o.get('id') for o in doc.get('optimizations') or [] if isinstance(o, dict)]
    dup = {x for x in ids if ids.count(x) > 1}
    if dup:
        errs.append(f'duplicate optimization ids: {sorted(dup)}')
    src = doc.get('source') or {}
    if metrics:
        gen = (metrics.get('meta') or {}).get('generated')
        if src.get('metrics_generated') not in (None, gen):
            errs.append(f'source.metrics_generated should be {gen!r} (the metrics.json you read)')
    if insights and src.get('insights_generated') not in (None, insights.get('generated')):
        errs.append(f'source.insights_generated should be {insights.get("generated")!r} (the insights.json you read)')
    cards = report_cards(metrics)[0] if metrics else None
    ins_ids = {i.get('id') for i in (insights or {}).get('insights') or []} if insights else None
    for n, o in enumerate(doc.get('optimizations') or []):
        if not isinstance(o, dict):
            continue
        where = f'optimizations[{n}] ({o.get("id")})'
        for q in o.get('questions') or []:
            if cards is not None and q not in cards:
                errs.append(f'{where}: question {q} is not shown in the report (cite a visible question; the digest marks hidden ones)')
        for i in o.get('insights') or []:
            if ins_ids is not None and i not in ins_ids:
                errs.append(f'{where}: insight {i!r} is not in insights.json')
        errs += month_errs(o.get('savings'), metrics, 'all', where)
        if o.get('effort') == 'one-click' and not o.get('apply'):
            errs.append(f'{where}: effort "one-click" needs an "apply" block')
        for k, st in enumerate(((o.get('apply') or {}).get('steps')) or []):
            if not isinstance(st, dict):
                continue
            w = f'{where}.apply.steps[{k}]'
            errs += [f'{w}: {e}' for e in step_errs(st, out or os.getcwd())]
    errs += relation_errs([o for o in doc.get('optimizations') or [] if isinstance(o, dict)], out or os.getcwd())
    return errs


def safe_console():
    """Never crash on a console that can't show a character (e.g. a Windows code page): replace it instead."""
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass


def main(argv=None):
    safe_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('kind', choices=['insights', 'optimizations'])
    ap.add_argument('file')
    ap.add_argument('--metrics', help='the metrics.json the file was written from')
    ap.add_argument('--insights', help='insights.json (checks optimization → insight links)')
    a = ap.parse_args(argv)
    doc = load(a.file, a.kind)
    metrics = load(a.metrics, 'metrics.json') if a.metrics else None
    if a.kind == 'insights':
        errs = validate_insights(doc, metrics)
    else:
        out = os.path.dirname(os.path.abspath(a.file))
        AP.CLAUDE_DIR = AP.report_claude_dir(out) or AP.CLAUDE_DIR     # the folder apply.py will write to
        errs = validate_optimizations(doc, metrics, load(a.insights, 'insights.json') if a.insights else None, out=out)
    if errs:
        print(f'{len(errs)} problem(s) in {a.file}:')
        for e in errs:
            print('  - ' + e)
        sys.exit(1)
    n = len(doc.get('insights') or doc.get('optimizations') or [])
    print(f'OK: {n} {a.kind} in {a.file}')


if __name__ == '__main__':
    main()
