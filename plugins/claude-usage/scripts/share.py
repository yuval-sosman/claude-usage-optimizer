#!/usr/bin/env python3
"""Your whole usage report in one JSON file to send; and a file someone sent you, back into a report you can open.

  python3 share.py pack   [--out DIR] [--name NAME] [--team TEAM] [--reveal]
  python3 share.py unpack FILE [--out DIR] [--to DIR] [--open]

pack reads the report folder, never the transcripts, and writes <out>/share/claude-usage-share-<who>-<date>.json (the
contract: schemas/share.schema.json). It holds everything report.html holds and more: every number, chart, table and miss
trace of every scope (data/metrics.json), insights.json and optimizations.json (marked when out of date), the optimization
drafts (candidates.json), the setup (config.json, secrets already removed), the optimizations applied, and every row of
the CSV exports, with numbers as numbers. Like report.html, it holds project names, session titles, file paths, prompt
snippets and commands.

unpack writes a share file back into a report folder (default <out>/received/<file name>/) and renders its report.html,
without the apply commands: the optimizations in it are for the sender's machine. Standard library only.
"""
import argparse
import csv
import datetime as dt
import getpass
import json
import os
import pathlib
import re
import subprocess
import sys
import webbrowser

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import layout  # noqa: E402
import usage_report as UR  # noqa: E402  (folders, render, digest)
import validate as VA  # noqa: E402  (the JSON Schema subset the plugin's schemas use)
ROOT = os.path.dirname(HERE)
SCHEMA = os.path.join(ROOT, 'schemas', 'share.schema.json')
TEMPLATE = os.path.join(HERE, 'report_template.html')
FORMAT, VERSION = 'claude-usage-share', 1
TABLES = [n[:-4] for n in layout.DATA_FILES if n.endswith('.csv')]
INT = re.compile(r'-?\d+', re.ASCII)
NUM = re.compile(r'-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?', re.ASCII)
PRIVATE = ('Like report.html, it holds your project names, session titles, file paths, prompt snippets and commands: send it '
           'only to someone you would show your report to.')


def read_json(p):
    with open(p, encoding='utf-8') as fh:
        return json.load(fh)


def write_json(p, js, indent=None):
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(json.dumps(js, ensure_ascii=False, indent=indent, separators=None if indent else (',', ':')) + ('\n' if indent else ''))
    os.replace(tmp, p)


def optional(p, notes):
    """A JSON file of the report folder, or None when it is missing or broken (said in notes)."""
    if not os.path.exists(p):
        return None
    try:
        return read_json(p)
    except (OSError, ValueError) as e:
        notes.append(f'{os.path.basename(p)} could not be read ({e}), so it was left out.')
        return None


def number(v):
    """v as a number, or None when it isn't one or wouldn't be written back as the same text ('007', '1.50', '5\\n')."""
    x = int(v) if INT.fullmatch(v) else float(v) if NUM.fullmatch(v) else None
    return x if x is not None and str(x) == v else None


def read_table(p):
    """A CSV as columns and rows. A column whose every value is a number holds numbers; an empty cell is None. Written back
    by write_table, every cell reads as it did (byte for byte)."""
    with open(p, newline='', encoding='utf-8') as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return {'columns': [], 'rows': []}
    cols, body = rows[0], rows[1:]
    for j in range(len(cols)):
        nums = [number(r[j]) for r in body if j < len(r) and r[j] != '']
        if not nums or any(x is None for x in nums):
            continue
        it = iter(nums)
        for r in body:
            if j < len(r):
                r[j] = None if r[j] == '' else next(it)
    return {'columns': cols, 'rows': body}


def write_table(p, tbl):
    with open(p, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(tbl['columns'])
        w.writerows([['' if v is None else v for v in r] for r in tbl['rows']])


def applied(out):
    """The optimizations applied with apply.py: id, when, done or partial, and the files each one changed."""
    try:
        import apply as AP
        state = AP.load_state(out)
    except Exception:
        return []
    recs = [{'id': k, 'applied': v.get('applied'), 'status': v.get('status'),
             'files': [f['path'] for f in v.get('files') or [] if isinstance(f, dict) and isinstance(f.get('path'), str)]}
            for k, v in state.items() if not k.startswith('_') and isinstance(v, dict)]
    return sorted(recs, key=lambda r: (r['applied'] or '', r['id']))


def plugin_version():
    try:
        return read_json(os.path.join(ROOT, '.claude-plugin', 'plugin.json')).get('version')
    except (OSError, ValueError):
        return None


def account():
    try:
        return getpass.getuser()
    except Exception:
        return os.path.basename(os.path.expanduser('~')) or 'unknown'


def slug(s):
    return re.sub(r'[^a-z0-9]+', '-', (s or '').lower()).strip('-')[:40]


def size(n):
    return f'{n / 1e6:.1f} MB' if n >= 1e6 else f'{max(1, round(n / 1e3))} KB'


def made_from(js):
    return (js.get('source') or {}).get('metrics_generated')


def pack(out, name=None, team=None):
    mp = layout.data(out, 'metrics.json')
    if not os.path.exists(mp):
        sys.exit(f'No report data in {UR.tilde(out)}: run /claude-usage:report first (or usage_report.py).')
    notes = []
    report = read_json(mp)
    gen = report['meta'].get('generated')
    ins = optional(os.path.join(out, 'insights.json'), notes)
    opts = optional(os.path.join(out, 'optimizations.json'), notes)
    cand = optional(layout.data(out, 'candidates.json'), notes)
    config = optional(layout.data(out, 'config.json'), notes)
    made = os.path.getmtime(mp)          # a run writes its CSVs after metrics.json; older ones are a --no-csv run's leftovers
    tables = {n: read_table(p) for n, p in ((n, layout.data(out, n + '.csv')) for n in TABLES)
              if os.path.exists(p) and os.path.getmtime(p) >= made - 2}

    def state(js, current):
        return 'missing' if js is None else 'current' if current else 'out of date'
    status = {
        'insights': state(ins, ins is not None and made_from(ins) == gen),
        'optimizations': state(opts, opts is not None and made_from(opts) == gen and ins is not None
                               and (opts.get('source') or {}).get('insights_generated') == ins.get('generated')),
        'candidates': state(cand, cand is not None and made_from(cand) == gen),
        'config': 'missing' if config is None else 'current',
        'tables': 'current' if len(tables) == len(TABLES) else 'partial' if tables else 'missing',
    }
    if status['insights'] == 'missing':
        notes.append('No insights yet: /claude-usage:report writes them; share again afterwards to include them.')
    elif status['insights'] == 'out of date':
        notes.append(f'The insights were written from an older report ({made_from(ins)}; the report is from {gen}): run '
                     '/claude-usage:report, then share again, to send current ones.')
    if status['optimizations'] == 'missing':
        notes.append('No optimizations yet: /claude-usage:optimize writes them; share again afterwards to include them.')
    elif status['optimizations'] == 'out of date':
        notes.append('The optimizations were written from an older report or older insights: run /claude-usage:optimize, then '
                     'share again, to send current ones.')
    if status['candidates'] != 'current':
        notes.append('The optimization drafts (candidates.json) are missing or older than the report: the next report run '
                     'rewrites them.')
    if status['tables'] != 'current':
        gone = [n for n in TABLES if n not in tables]
        notes.append(f'No rows for {", ".join(gone)}: the report was built with --no-csv (any CSVs there are from an older run); '
                     'run it again without it to include them.')
    try:
        age = (dt.datetime.now() - dt.datetime.strptime(gen, '%Y-%m-%d %H:%M:%S')).days
    except (TypeError, ValueError):
        age = 0
    if age >= 1:
        notes.append(f'The report is {age} day{"s" if age > 1 else ""} old: run /claude-usage:report for current numbers, then share again.')

    now = dt.datetime.now().astimezone()
    off = now.strftime('%z')
    who = account()
    share = {
        'format': FORMAT, 'format_version': VERSION, 'created': now.isoformat(timespec='seconds'), 'plugin_version': plugin_version(),
        'person': {'account': who, 'name': name or None, 'team': team or None},
        'machine': {'platform': sys.platform, 'utc_offset': off[:3] + ':' + off[3:] if len(off) == 5 else off},
        'status': status, 'notes': notes,
        'report': report, 'insights': ins, 'optimizations': opts, 'candidates': cand, 'config': config,
        'applied': applied(out), 'tables': tables,
    }
    errs = []
    VA.check(share, read_json(SCHEMA), 'share', errs)
    if errs:
        sys.exit('The share file would not match schemas/share.schema.json:\n  ' + '\n  '.join(errs[:20]))
    path = layout.share(out, f'claude-usage-share-{slug(name) or slug(who) or "me"}-{now:%Y-%m-%d}.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_json(path, share)

    cards = (report['data'].get('all') or {}).get('cards') or {}
    rng = report['meta'].get('range') or {}
    items = lambda js, key: len((js or {}).get(key) or [])
    print(f'Wrote {UR.tilde(path)} ({size(os.path.getsize(path))})')
    print(f'  report         {rng.get("start")} → {rng.get("end")}, made {gen}: {len(report["scopes"])} scopes, '
          f'{len(cards)} questions, {len(report.get("traces") or {})} miss traces')
    print(f'  insights       {items(ins, "insights")}, {status["insights"]}')
    print(f'  optimizations  {items(opts, "optimizations")}, {status["optimizations"]}; {len(share["applied"])} applied')
    print('  tables         ' + (' · '.join(f'{n} {len(t["rows"]):,} rows' for n, t in tables.items()) or 'none'))
    print(f'  setup          config.json {"(secrets removed)" if config is not None else "missing"}, '
          f'optimization drafts {status["candidates"]}')
    if notes:
        print('Notes:')
        for n in notes:
            print('  - ' + n)
    print(PRIVATE)
    return path


def own_report(dest):
    """Whether dest holds a report of one's own rather than one unpacked from a share file (meta.shared): an unreadable
    metrics.json counts as one's own, and so does the flat layout of older versions (metrics.json beside insights.json)."""
    for p in (layout.data(dest, 'metrics.json'), os.path.join(dest, 'metrics.json')):
        if os.path.exists(p):
            try:
                return not read_json(p)['meta'].get('shared')
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                return True
    return False


def unpack(path, out, to=None, open_=False):
    path = os.path.abspath(os.path.expanduser(path))
    try:
        share = read_json(path)
    except (OSError, ValueError) as e:
        sys.exit(f'Could not read {path}: {e}')
    if not isinstance(share, dict) or share.get('format') != FORMAT:
        sys.exit(f'{path} is not a claude-usage share file.')
    v = share.get('format_version')
    if not isinstance(v, int) or v > VERSION:
        sys.exit(f'{path} was made by a newer claude-usage (share format {v}); update the plugin to open it.')
    errs = []
    VA.check(share, read_json(SCHEMA), 'share', errs)
    if errs:
        sys.exit(f'{path} is damaged or incomplete:\n  ' + '\n  '.join(errs[:20]))
    dest = os.path.abspath(os.path.expanduser(to)) if to else layout.received(out, os.path.splitext(os.path.basename(path))[0])
    if own_report(dest):                                       # never over a report of one's own
        sys.exit(f'{UR.tilde(dest)} holds a report of your own: unpack somewhere else (--to DIR).')
    left_out = []
    for name in ('insights', 'optimizations'):                 # the page renders them (links too): only what their schemas allow
        if share[name] is not None:
            bad = []
            VA.check(share[name], read_json(os.path.join(ROOT, 'schemas', name + '.schema.json')), name, bad)
            if bad:
                left_out.append(f'{name} (not valid: {bad[0]})')
                share[name] = None
    person = share['person']
    who = person.get('name') or person.get('account')
    report = share['report']
    meta = report['meta']
    meta['shared'] = {'from': who, 'account': person.get('account'), 'team': person.get('team'), 'created': share['created'],
                      'file': os.path.basename(path)}
    meta['subtitle'] = (f"{meta.get('subtitle') or ''} · shared by {who}" + (f" ({person['team']})" if person.get('team') else '')
                        + f" on {share['created'][:10]}").lstrip(' ·')
    os.makedirs(layout.data_dir(dest), exist_ok=True)
    write_json(layout.data(dest, 'metrics.json'), report)
    for name, p, indent in (('insights', os.path.join(dest, 'insights.json'), 2),
                            ('optimizations', os.path.join(dest, 'optimizations.json'), 2),
                            ('candidates', layout.data(dest, 'candidates.json'), 1),
                            ('config', layout.data(dest, 'config.json'), 1)):
        if share[name] is None:
            if os.path.exists(p):
                os.remove(p)                                   # left from an earlier unpack of another file
        else:
            write_json(p, share[name], indent)
    for n in TABLES:
        p = layout.data(dest, n + '.csv')
        if n in share['tables']:
            write_table(p, share['tables'][n])
        elif os.path.exists(p):
            os.remove(p)
    UR.write_digest(report, share['config'] or {}, dest)
    html, _ = UR.render(dest, TEMPLATE, log=lambda *x: None)
    rng = meta.get('range') or {}
    print(f"Unpacked {who}'s report ({rng.get('start')} → {rng.get('end')}, shared {share['created'][:10]}) into {UR.tilde(dest)}")
    odd = [f'{k} {v}' for k, v in share['status'].items() if v != 'current']
    print('  as sent: ' + (', '.join(odd) if odd else 'everything current (insights, optimizations, drafts, setup, tables)'))
    if left_out:
        print('  left out: ' + '; '.join(left_out))
    print(html)
    if open_:
        webbrowser.open(pathlib.Path(html).as_uri())
    return html


def reveal(path):
    """Show the file in the file manager, selected, ready to attach."""
    try:
        if sys.platform == 'darwin':
            subprocess.run(['open', '-R', path], check=False)
        elif os.name == 'nt':
            subprocess.run(['explorer', '/select,', path], check=False)
        else:
            webbrowser.open(pathlib.Path(os.path.dirname(path)).as_uri())
    except OSError:
        pass


def main(argv=None):
    UR.safe_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=['pack', 'unpack'])
    ap.add_argument('file', nargs='?', help='unpack: the share file')
    ap.add_argument('--out', metavar='DIR', help='the report folder (default: the one usage_report.py uses, e.g. ~/.claude-usage)')
    ap.add_argument('--claude-dir', metavar='DIR', help='the Claude Code folder the report was built from, if not the default')
    ap.add_argument('--name', help='pack: your name, added to the file and its name')
    ap.add_argument('--team', help='pack: your team, added to the file')
    ap.add_argument('--reveal', action='store_true', help='pack: show the file in your file manager when done')
    ap.add_argument('--to', metavar='DIR', help='unpack: the folder to write the report into (default <out>/received/<file name>)')
    ap.add_argument('--open', action='store_true', help='unpack: open the report when done')
    a = ap.parse_args(argv)
    out = os.path.abspath(os.path.expanduser(a.out)) if a.out else UR.default_out(UR.claude_dir(a.claude_dir))
    if a.command == 'pack':
        if a.file:
            ap.error('pack takes no file: it reads the report folder (--out)')
        path = pack(out, a.name, a.team)
        if a.reveal:
            reveal(path)
    else:
        if not a.file:
            ap.error('unpack needs the share file: share.py unpack FILE')
        unpack(a.file, out, a.to, a.open)
    return 0


if __name__ == '__main__':
    sys.exit(main())
