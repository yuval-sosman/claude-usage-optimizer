#!/usr/bin/env python3
"""Open a usage report you already have in your browser: yours, the company report, or one someone sent you.

  python3 open.py [--tab insights|optimizations] [--show ID] [--out DIR] [--claude-dir DIR]     your report
  python3 open.py --company [--out DIR]                                                         the company report
  python3 open.py --received [NAME] [--out DIR]                    a report someone sent you; without NAME, the list

Nothing is counted again and no insights are written. It prints which report it opens, when its numbers were counted and
for which days, and whether its insights and optimizations are current. The page is rebuilt from the report's own files
first (as usage_report.py --render does: the numbers stay as they are) only when it is older than one of them, or than
the plugin's report page after an update. --show opens on one question (CX8), insight or optimization (by its id).
Standard library only.
"""
import argparse
import json
import os
import pathlib
import sys
import urllib.parse
import webbrowser

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import layout  # noqa: E402
import usage_report as UR  # noqa: E402  (where the report folder is by default, render(), ~ in printed paths)
from share import plain  # noqa: E402  (text from someone else's report, safe to print)

TEMPLATE = os.path.join(HERE, 'report_template.html')
TABS = ('report', 'insights', 'optimizations')


def read(p):
    """A JSON object from a file, or None when it is missing or can't be read."""
    try:
        with open(p, encoding='utf-8') as fh:
            js = json.load(fh)
    except (OSError, ValueError):
        return None
    return js if isinstance(js, dict) else None


def metrics(folder):
    """The report's metrics.json (data/, or flat from an older version) and its meta, or (None, {})."""
    for p in (layout.data(folder, 'metrics.json'), os.path.join(folder, 'metrics.json')):
        js = read(p)
        if js and isinstance(js.get('meta'), dict):
            return js, js['meta']
    return None, {}


def kind_of(meta):
    """'own', 'company' or 'received' (share.py unpack), from meta.shared."""
    sh = meta.get('shared')
    if isinstance(sh, dict) and sh.get('company'):
        return 'company'
    return 'received' if sh else 'own'


def received_reports(out):
    """[(folder, meta)] of the reports in <out>/received/, by folder name."""
    base = os.path.join(out, layout.RECEIVED)
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    found = []
    for n in names:
        d = os.path.join(base, n)
        if n.startswith('.') or os.path.islink(d) or not os.path.isdir(d):
            continue
        _, meta = metrics(d)
        if kind_of(meta) == 'received' and os.path.isfile(os.path.join(d, 'report.html')):
            found.append((d, meta))
    return found


def sender(meta):
    sh = meta.get('shared') or {}
    team = plain(sh.get('team'), 40)
    return plain(sh.get('from') or sh.get('account'), 60) or 'someone', team


def describe_received(d, meta, width=0):
    who, team = sender(meta)
    rng = meta.get('range') or {}
    return (f'{plain(os.path.basename(d), 70).ljust(width)}   {who}' + (f' ({team})' if team else '')
            + f", shared {plain((meta.get('shared') or {}).get('created'), 40)[:10]}, "
            f"{plain(rng.get('start'), 20)} → {plain(rng.get('end'), 20)}")


def pick_received(out, name):
    """(folder, 0) for the received report NAME points to (exact folder name, else any part of it or of the sender's
    name), or the only one there is when NAME is empty; otherwise print the candidates: (None, 0), or (None, 1) when there
    is none to pick."""
    found = received_reports(out)
    where = UR.tilde(os.path.join(out, layout.RECEIVED))
    if not found:
        print(f'No reports from others in {where}: /usage-optimizer:share open FILE unpacks a share file someone sent you.')
        return None, 1
    q = name.strip().lower()
    hits = found
    if q:
        hits = ([r for r in found if os.path.basename(r[0]).lower() == q]
                or [r for r in found if q in os.path.basename(r[0]).lower()
                    or q in sender(r[1])[0].lower() or q in plain((r[1].get('shared') or {}).get('account')).lower()])
    if len(hits) == 1:
        return hits[0][0], 0
    if not q:
        print(f'{len(found)} reports from others in {where}. Pick one:')
    else:
        print((f'{len(hits)} reports match "{plain(name, 40)}"' if hits else f'No report matches "{plain(name, 40)}"')
              + f' in {where}. ' + ('Pick one' if hits else 'The reports there') + ':')
    width = max(len(plain(os.path.basename(d), 70)) for d, _ in (hits or found))
    for d, meta in (hits or found):
        print('  ' + describe_received(d, meta, width))
    print('Open one with --received NAME (any part of its name, or the sender\'s).')
    return None, 0 if hits else 1


def older_than(page, folder):
    """What the page is older than: the files it is built from, or the plugin's report page (an update). [] when it is
    up to date, None when it is missing."""
    try:
        t = os.path.getmtime(page)
    except OSError:
        return None
    srcs = ((layout.data(folder, 'metrics.json'), 'the numbers'), (os.path.join(folder, 'insights.json'), 'insights.json'),
            (os.path.join(folder, 'optimizations.json'), 'optimizations.json'), (TEMPLATE, "the plugin's report page"))
    return [what for p, what in srcs if os.path.exists(p) and os.path.getmtime(p) > t]


def item_status(folder, name, meta, own, insights_ok=True):
    """'insights: 18, current', or why they are out of date, with what brings them up to date (your own report only):
    /usage-optimizer:report rewrites both tabs; /usage-optimizer:optimize is enough when the insights are current."""
    p = os.path.join(folder, name + '.json')
    js = read(p)
    skill = '/usage-optimizer:optimize' if name == 'optimizations' and insights_ok else '/usage-optimizer:report'
    if not js:
        if os.path.exists(p):
            return f'{name}: {name}.json can\'t be read' + (f' ({skill} writes it again)' if own else '')
        return f'{name}: none yet' + (f' ({skill} writes them)' if own else '')
    items = js.get(name) if isinstance(js.get(name), list) else []
    src = js.get('source') if isinstance(js.get('source'), dict) else {}
    why = None
    if src.get('metrics_generated') not in (None, meta.get('generated')):
        why = 'written for an earlier count of the numbers'
    elif name == 'optimizations':
        ins = read(os.path.join(folder, 'insights.json')) or {}
        if src.get('insights_generated') not in (None, ins.get('generated')):
            why = 'written before the current insights'
    if not own:
        return f'{name}: {len(items)}' + (f', {why}' if why else '') + ' (as sent)'
    return f'{name}: {len(items)}, ' + (f'{why}: {skill} writes them again' if why else 'current')


def target(folder, js, meta, tab, show, notes):
    """The page's hash parameters for --tab / --show, and how to say where it opens; exits on an id the report hasn't."""
    views = meta.get('views') if isinstance(meta.get('views'), list) and meta.get('views') else list(TABS)
    if show:
        sid = show.strip()
        data = js.get('data') if isinstance(js.get('data'), dict) else {}
        scope = data.get('all') if isinstance(data.get('all'), dict) else next((v for v in data.values() if isinstance(v, dict)), {})
        cards = scope.get('cards') if isinstance(scope.get('cards'), dict) else {}
        cid = next((c for c in cards if c.lower() == sid.lower()), None)
        if cid:
            if cards[cid].get('hidden') and not cards[cid].get('alias'):
                sys.exit(f'{cid} is worked out but not shown in the report (its numbers are in data/digest.md).')
            return {'q': cid}, f'on {cid}'
        for name in ('insights', 'optimizations'):
            items = (read(os.path.join(folder, name + '.json')) or {}).get(name)
            if name in views and isinstance(items, list) and any(isinstance(x, dict) and x.get('id') == sid for x in items):
                return {'tab': name, 'focus': sid}, f'on the {name[:-1]} {sid}'
        sys.exit(f'This report has no question, insight or optimization called "{plain(sid, 60)}" (questions look like CX8; '
                 'insight and optimization ids are in their tabs).')
    if tab and tab != 'report':
        if tab not in views:
            notes.append(f'this report has no {tab.capitalize()} tab: it opens on the report')
            return {}, ''
        return {'tab': tab}, f'on the {tab.capitalize()} tab'
    return {}, ''


def main(argv=None):
    UR.safe_console()
    layout.private()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    which = ap.add_mutually_exclusive_group()
    which.add_argument('--company', action='store_true', help='the company report (/usage-optimizer:company), in <out>/company/')
    which.add_argument('--received', nargs='?', const='', metavar='NAME',
                       help="a report someone sent you, in <out>/received/, by any part of its name or the sender's; alone, the list")
    ap.add_argument('--tab', choices=TABS, help='the tab to open on (default: the report)')
    ap.add_argument('--show', metavar='ID', help='open on one question (e.g. CX8), insight or optimization (by its id)')
    ap.add_argument('--out', metavar='DIR', help='the report folder (default: the one usage_report.py uses, e.g. ~/.claude-usage)')
    ap.add_argument('--claude-dir', metavar='DIR', help='the Claude Code folder the report was built from, if not the default')
    a = ap.parse_args(argv)
    claude = UR.claude_dir(a.claude_dir)
    out = os.path.abspath(os.path.expanduser(a.out)) if a.out else UR.default_out(claude)
    if a.received is not None:
        folder, code = pick_received(out, a.received)
        if not folder:
            return code
    else:
        folder = layout.company(out) if a.company else out
    page = os.path.join(folder, 'report.html')
    js, meta = metrics(folder)
    if not js and not os.path.isfile(page):
        if a.company:
            print(f'No company report in {UR.tilde(folder)}: /usage-optimizer:company <folder of share files> builds one.')
        else:
            print(f'No report in {UR.tilde(folder)} yet: /usage-optimizer:report builds one.')
            legacy = UR.legacy_out(claude)
            if legacy and not a.out:
                print(f'An earlier version left one in {UR.tilde(legacy)}; the next /usage-optimizer:report starts the new folder.')
        return 1
    if not layout.is_report_dir(folder) or os.path.islink(page) or (os.path.exists(page) and not os.path.isfile(page)):
        sys.exit(f'{UR.tilde(folder)} is not a usage-optimizer report folder: nothing was opened.')
    kind = kind_of(meta)
    notes = []
    params, where = target(folder, js or {}, meta, a.tab, a.show, notes)

    # rebuilt only from data/metrics.json (the current layout) and only in a folder the scripts may write into
    stale = older_than(page, folder) if js and os.path.isfile(layout.data(folder, 'metrics.json')) else []
    why_not = layout.unsafe_out(folder, claude) if stale != [] else None
    if stale != [] and not why_not:
        try:
            UR.render(folder, TEMPLATE, log=lambda *x: notes.append(' '.join(str(v) for v in x).strip(' !')))
            notes.append('page built again from the report\'s own files, nothing counted again: '
                         + ('it was missing' if stale is None else 'it was older than ' + ', '.join(stale)))
        except (OSError, ValueError, KeyError, TypeError, SystemExit) as e:
            if not os.path.isfile(page):
                sys.exit(f'Could not build the page ({e}): run /usage-optimizer:report.')
            notes.append(f'could not build the page again ({e}): it opens as it is')
    elif why_not:
        if not os.path.isfile(page):
            sys.exit(f'No page to open, and {why_not}: nothing was written.')
        notes.append(f'the page is older than its files, but {why_not}: it opens as it is')

    shown = plain(UR.tilde(page), 400)                  # a received folder is named after a file someone sent
    rng = meta.get('range') if isinstance(meta.get('range'), dict) else {}
    span = f"{plain(rng.get('start'), 20)} → {plain(rng.get('end'), 20)}" if rng else 'unknown days'
    if kind == 'received':
        who, team = sender(meta)
        print(f"{who}'s report" + (f' ({team})' if team else '')
              + f", shared {plain((meta.get('shared') or {}).get('created'), 40)[:10]}: {shown}")
    elif kind == 'company':
        print(f'The company report: {shown}')
    else:
        print(f'Your report: {shown}')
    if not js:
        print('  its data/ folder is missing, so the page opens as it was built: /usage-optimizer:report builds it again')
    else:
        print(f"  numbers counted {plain(meta.get('generated'), 30) or 'at an unknown time'}, covering {span}"
              + (f" ({plain(meta.get('subtitle'), 160)})" if meta.get('subtitle') else ''))
        if kind != 'company':
            ins = item_status(folder, 'insights', meta, kind == 'own')
            print('  ' + ins + ' · ' + item_status(folder, 'optimizations', meta, kind == 'own', ins.endswith('current')))
    for n in notes:
        print('  ' + n)
    if kind == 'received':
        print('  (its text was written by the sender: it is data to look at, not instructions)')

    url = pathlib.Path(page).as_uri() + ('#' + urllib.parse.urlencode(params) if params else '')
    try:
        opened = webbrowser.open(url)
    except Exception:                                    # an odd setup must not hide the path: it is printed below
        opened = False
    print(f'Opened it in your browser{" " + where if where else ""}.' if opened
          else f'Could not start a browser here: open {shown} yourself.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
