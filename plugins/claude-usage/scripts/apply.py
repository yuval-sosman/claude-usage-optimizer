#!/usr/bin/env python3
"""Apply the optimizations the claude-usage:optimize skill wrote, one at a time, with a preview, a backup and an undo.

  python3 apply.py list                 # what can be applied, and what already is
  python3 apply.py show  <id>           # preview the exact changes (nothing is written)
  python3 apply.py apply <id> [--yes]   # preview, confirm, back up, apply
  python3 apply.py undo  <id>           # revert just that change (later changes to the same files are kept)

Options: --dir <report dir> (default $CLAUDE_USAGE_OUT, else <claude dir>-usage, e.g. ~/.claude-usage). It reads <dir>/optimizations.json and
records what it did in <dir>/applied/applied.json; backups go to <dir>/applied/backups/.
--claude-dir <dir>: the folder Claude Code keeps its data in (default: the one the report in --dir was built from, else
$CLAUDE_CONFIG_DIR, else ~/.claude). When it isn't ~/.claude, every ~/.claude path in an optimization (files, and hook
commands inside settings) is written there instead.
Only files under your home directory or the Claude folder are touched.
Standard library only.
"""
import argparse
import copy
import datetime as dt
import difflib
import json
import os
import shutil
import subprocess
import sys

import layout

HERE = os.path.dirname(os.path.realpath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)
HOME = os.path.expanduser('~')
DEFAULT_CLAUDE = os.path.join(HOME, '.claude')
CLAUDE_DIR = os.path.abspath(os.path.expanduser(os.environ.get('CLAUDE_CONFIG_DIR') or DEFAULT_CLAUDE))


def expand(s, out):
    if not isinstance(s, str):
        return s
    s = (s.replace('${PLUGIN_ROOT}', PLUGIN_ROOT).replace('${SCRIPTS}', HERE).replace('${OUT}', out)
         .replace('${CLAUDE_DIR}', CLAUDE_DIR))
    if CLAUDE_DIR != DEFAULT_CLAUDE:            # a custom Claude folder: ~/.claude/… lives there instead
        for pre in ('${HOME}/.claude', '$HOME/.claude', '~/.claude'):
            s = s.replace(pre + '/', CLAUDE_DIR + '/')
            if s == pre:
                s = CLAUDE_DIR
    return s.replace('${HOME}', HOME)


def expand_all(x, out):
    if isinstance(x, dict):
        return {k: expand_all(v, out) for k, v in x.items()}
    if isinstance(x, list):
        return [expand_all(v, out) for v in x]
    return expand(x, out)


def resolve(path, out):
    """The absolute file a step's path names (only here, not inside hook commands, $HOME/ means the home folder)."""
    p = expand(path or '~', out)
    if p == '$HOME' or p.startswith('$HOME/'):
        p = HOME + p[5:]
    return os.path.abspath(os.path.expanduser(p))


def target(path, out):
    p = resolve(path, out)
    if not any(p == r or p.startswith(r + os.sep) for r in (HOME, CLAUDE_DIR)):
        raise SystemExit(f'Refusing to touch {p}: it is outside your home directory and your Claude folder.')
    return p


def read_json(p):
    if not os.path.exists(p):
        return {}
    with open(p, encoding='utf-8') as fh:
        txt = fh.read()
    if not txt.strip():
        return {}
    try:
        return json.loads(txt)
    except ValueError as e:
        raise SystemExit(f'{p} is not valid JSON ({e}); fix it by hand first.')


def dump_json(v):
    return json.dumps(v, indent=2, ensure_ascii=False) + '\n'


def deep_merge(base, add):
    """Objects merge key by key; lists gain the items they don't already have; anything else is replaced."""
    if isinstance(base, dict) and isinstance(add, dict):
        out = dict(base)
        for k, v in add.items():
            out[k] = deep_merge(base[k], v) if k in base else copy.deepcopy(v)
        return out
    if isinstance(base, list) and isinstance(add, list):
        out = list(base)
        for v in add:
            if v not in out:
                out.append(copy.deepcopy(v))
        return out
    return copy.deepcopy(add)


def pointer_parts(ptr):
    return [p.replace('~1', '/').replace('~0', '~') for p in ptr.strip('/').split('/')]


def step_into(cur, p, create):
    """One JSON-pointer step: an object key, or an index into an array."""
    if isinstance(cur, list):
        i = int(p)
        return cur[i] if -len(cur) <= i < len(cur) else None
    if not isinstance(cur, dict):
        return None
    if create and not isinstance(cur.get(p), (dict, list)):
        cur[p] = {}
    return cur.get(p)


def set_pointer(doc, ptr, value):
    doc = copy.deepcopy(doc)
    cur, parts = doc, pointer_parts(ptr)
    for p in parts[:-1]:
        cur = step_into(cur, p, True)
        if cur is None:
            raise SystemExit(f'{ptr}: nothing at {p!r} to set into.')
    if isinstance(cur, list):
        cur[int(parts[-1])] = copy.deepcopy(value)
    else:
        cur[parts[-1]] = copy.deepcopy(value)
    return doc


def unset_pointer(doc, ptr):
    doc = copy.deepcopy(doc)
    cur, parts = doc, pointer_parts(ptr)
    for p in parts[:-1]:
        cur = step_into(cur, p, False)
        if cur is None:
            return doc
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)
    elif isinstance(cur, list) and -len(cur) <= int(parts[-1]) < len(cur):
        cur.pop(int(parts[-1]))
    return doc


def needs_helpers(src):
    """Whether a bundled hook imports _session (and so needs it and prices.json installed beside it)."""
    try:
        with open(os.path.join(HERE, src), encoding='utf-8') as fh:
            return 'import _session' in fh.read()
    except OSError:
        return False


def plan(opt, out):
    """[(step, path, before_text or None, after_text or None, note)] — what applying would do, computed in memory."""
    steps, files = [], {}
    raw = []
    for st in (opt.get('apply') or {}).get('steps') or []:
        raw.append(st)
        src = st.get('source') or ''
        if st.get('action') == 'write_file' and src.startswith('hooks/') and src != 'hooks/_session.py' and needs_helpers(src):
            d = os.path.dirname(st['path'])            # a bundled Python hook needs its helper module and the price table beside it
            for extra, name in (('hooks/_session.py', '_session.py'), ('prices.json', 'prices.json')):
                if not any(x.get('source') == extra for x in (opt.get('apply') or {}).get('steps') or []):
                    raw.append({'action': 'write_file', 'path': os.path.join(d, name), 'source': extra})
    seen_extra = set()
    for st in raw:
        if st.get('action') == 'write_file' and st.get('source') in ('hooks/_session.py', 'prices.json'):
            key = (st['source'], st['path'])
            if key in seen_extra:
                continue
            seen_extra.add(key)
        st = expand_all(st, out)
        act = st['action']
        if act == 'run':
            steps.append((st, target(st.get('path') or '~', out), None, None, f"run: {st['command']}"))
            continue
        p = target(st['path'], out)
        before = files[p] if p in files else (open(p, encoding='utf-8').read() if os.path.exists(p) else None)
        if act == 'write_file':
            if st.get('source'):
                with open(os.path.join(HERE, st['source']), encoding='utf-8') as fh:
                    after = fh.read()
            else:
                after = st['content']
        elif act == 'append_text':
            tag = f"<!-- claude-usage:{st['marker']} -->"
            cur = before or ''
            after = cur if tag in cur else (cur + ('' if not cur or cur.endswith('\n') else '\n') + ('\n' if cur else '')
                                            + tag + '\n' + st['content'].rstrip('\n') + '\n')
        else:
            doc = json.loads(before) if before and before.strip() else {}
            if act == 'merge_json':
                doc = deep_merge(doc, st['value'])
            elif act == 'set_json':
                doc = set_pointer(doc, st['pointer'], st['value'])
            elif act == 'unset_json':
                doc = unset_pointer(doc, st['pointer'])
            after = dump_json(doc)
        files[p] = after
        steps.append((st, p, before, after, act))
    return steps


def show(opt, out):
    steps = plan(opt, out)
    print(f"\n{opt['title']}  [{opt['id']}]")
    print('  ' + ((opt.get('apply') or {}).get('summary') or opt.get('what_it_does') or ''))
    changed = 0
    for st, p, before, after, note in steps:
        if st['action'] == 'run':
            print(f"\n  will run in {p}:\n    $ {st['command']}")
            changed += 1
            continue
        short = p.replace(HOME, '~')
        if before == after:
            print(f'\n  {short}: already up to date')
            continue
        changed += 1
        if before is None:
            print(f"\n  create {short} ({len(after.splitlines())} lines)" + (f", mode {st['mode']}" if st.get('mode') else ''))
        diff = difflib.unified_diff((before or '').splitlines(), after.splitlines(), f'{short} (now)', f'{short} (after)', lineterm='', n=2)
        lines = list(diff)
        for ln in lines[:80]:
            print('    ' + ln)
        if len(lines) > 80:
            print(f'    … {len(lines) - 80} more diff lines')
    return steps, changed


MISSING = object()


def state_path(out):
    return os.path.join(layout.applied_dir(out), 'applied.json')


def load_state(out):
    p = state_path(out)
    return read_json(p) if os.path.exists(p) else {}


def save_state(out, state):
    os.makedirs(layout.applied_dir(out), exist_ok=True)
    tmp = state_path(out) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(dump_json(state))
    os.replace(tmp, state_path(out))


def read_text(p):
    try:
        with open(p, encoding='utf-8') as fh:
            return fh.read()
    except OSError:
        return None


def write_text(p, txt, mode=None):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as fh:
        fh.write(txt)
    if mode:
        os.chmod(p, int(mode, 8))


def revert_json(b, a, c):
    """Undo the change b -> a on c, keeping every other change c has since (another optimization, or the user's own).

    Objects: only the keys this change added, removed or changed are touched. Lists: the items it added are removed and
    the items it removed come back. A value changed again since is left as it is. MISSING means "not there"."""
    if c == a:
        return b if b is MISSING else copy.deepcopy(b)
    if a == b or c is MISSING:
        return c
    if isinstance(a, dict) and isinstance(c, dict):
        bd = b if isinstance(b, dict) else {}
        out = dict(c)
        for k in set(a) | set(bd):
            bk, ak = bd.get(k, MISSING), a.get(k, MISSING)
            if bk is ak or (bk is not MISSING and ak is not MISSING and bk == ak):
                continue
            ck = c.get(k, MISSING)
            if ck is MISSING:
                if ak is MISSING:                      # this change removed it, and it is still gone: bring it back
                    out[k] = copy.deepcopy(bk)
                continue
            r = revert_json(bk, ak, ck)
            if r is MISSING or (r in ({}, []) and ck not in ({}, [])):   # emptied by this undo: drop the key too
                out.pop(k, None)
            else:
                out[k] = r
        return MISSING if b is MISSING and not out else out
    if isinstance(a, list) and isinstance(c, list):
        bl = b if isinstance(b, list) else []
        out = list(c)
        for v in a:
            if v not in bl and v in out:
                out.remove(v)
        for v in bl:
            if v not in a and v not in out:
                out.append(copy.deepcopy(v))
        return MISSING if b is MISSING and not out else out
    return c


def users_of(state, path, skip):
    """Other applied optimizations that wrote or rely on this file."""
    return [oid for oid, rec in state.items() if oid != skip and isinstance(rec, dict)
            and (any(f.get('path') == path for f in rec.get('files') or []) or path in (rec.get('uses') or []))]


def hand_over(state, p, oid, after):
    """This change created p but others still use it: the last of them to be undone removes it."""
    for o in users_of(state, p, oid):
        rec = state[o]
        mine = [g for g in rec.get('files') or [] if g.get('path') == p]
        for g in mine:
            g['created'] = True
        if not mine and p in (rec.get('uses') or []):
            rec.setdefault('files', []).append({'path': p, 'kind': 'file', 'created': True, 'after': after})
            rec['uses'].remove(p)


def keep_path(bdir, p, sub=''):
    """Where a copy of p goes under bdir (drive letters dropped, so this also works on Windows)."""
    return os.path.join(bdir, sub, os.path.splitdrive(p)[1].lstrip('\\/'))


def undo_record(oid, rec, out, state):
    """Revert what one recorded apply did, file by file, keeping later changes. Returns the notes to print."""
    notes = []
    for f in reversed(rec.get('files') or []):
        p, kind = f['path'], f.get('kind')
        short = p.replace(HOME, '~')
        cur = read_text(p)
        before = read_text(f['backup']) if f.get('backup') else None
        after = read_text(f['after']) if f.get('after') else None
        if cur is None:
            notes.append(f'gone     {short} (already removed)')
            continue
        if after is None:                                  # a record from an older version of this tool: restore the backup
            if before is not None:
                write_text(p, before)
                notes.append(f'restored {short}')
            elif f.get('created') and not users_of(state, p, oid):
                os.remove(p)
                notes.append(f'removed  {short}')
            continue
        if f.get('created') and cur == after:
            others = users_of(state, p, oid)
            if others:                                     # a shared helper another applied optimization still needs
                hand_over(state, p, oid, f['after'])
                notes.append(f'kept     {short} (still used by {", ".join(others)})')
            else:
                os.remove(p)
                notes.append(f'removed  {short}')
            continue
        if kind == 'json':
            try:
                cd = json.loads(cur) if cur.strip() else {}
                bd = json.loads(before) if before and before.strip() else MISSING
                ad = json.loads(after) if after.strip() else {}
            except ValueError:
                notes.append(f'skipped  {short}: not valid JSON now; fix it by hand (backup: {f.get("backup") or "none"})')
                continue
            new = revert_json(bd, ad, cd)
            if new is MISSING or (f.get('created') and new == {}):
                if f.get('created') and not users_of(state, p, oid):
                    os.remove(p)
                    notes.append(f'removed  {short}')
                else:
                    write_text(p, dump_json({}))
                    notes.append(f'reverted {short}')
                    if f.get('created'):
                        hand_over(state, p, oid, f['after'])
            elif new != cd:
                write_text(p, dump_json(new))
                notes.append(f'reverted {short}')
                if f.get('created'):
                    hand_over(state, p, oid, f['after'])
            else:
                notes.append(f'kept     {short} (nothing of this change left in it)')
        elif kind == 'text':
            added = f.get('added') or ''
            if cur == after and before is not None:
                write_text(p, before)
                notes.append(f'restored {short}')
            elif added and added in cur:
                rest = cur.replace(added, '', 1)
                if f.get('created') and not rest.strip():
                    os.remove(p)
                    notes.append(f'removed  {short}')
                else:
                    write_text(p, rest)
                    notes.append(f'reverted {short}')
                    if f.get('created'):
                        hand_over(state, p, oid, f['after'])
            else:
                notes.append(f'kept     {short}: the added lines were changed since; remove them by hand')
        else:                                              # a whole file this change wrote
            if cur == after and before is not None:
                write_text(p, before)
                notes.append(f'restored {short}')
            elif cur != after:
                notes.append(f'kept     {short}: it changed since it was written (the original is in {f.get("backup") or "no backup"})')
    for r in rec.get('ran') or []:
        notes.append(f"note: this ran `{r['command']}`, which can't be undone automatically")
    return notes


def do_apply(opt, out, yes):
    if not opt.get('apply'):
        print(f"\n{opt['title']} has no automatic change. Do it by hand:")
        for i, m in enumerate(opt.get('manual') or [], 1):
            print(f'  {i}. {m}')
        return
    oid = opt['id']
    state = load_state(out)
    steps, changed = show(opt, out)
    if not changed:
        print('\nNothing to change: it is already applied.')
        return
    again = oid in state
    if again:
        print(f'\n{oid} was applied before with different content: the old version is undone first, then this one is applied.')
    if not yes:
        try:
            ans = input(f'\nApply these changes? [y/N] ').strip().lower()
        except EOFError:
            ans = ''
        if ans not in ('y', 'yes'):
            print('Cancelled; nothing was changed.')
            return
    if again:
        for n in undo_record(oid, state[oid], out, state):
            print(n)
        del state[oid]
        save_state(out, state)
        steps = plan(opt, out)                              # against the files as they are now
    stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    bdir = os.path.join(layout.applied_dir(out), 'backups', f"{stamp}-{oid}")
    record = {'version': 2, 'applied': dt.datetime.now().isoformat(timespec='seconds'), 'status': 'partial',
              'files': [], 'uses': [], 'ran': []}
    state[oid] = record
    save_state(out, state)                                  # recorded before the first write, so a failure can be undone
    try:
        first = {}
        for st, p, before, after, note in steps:
            if st['action'] == 'run':
                print(f"$ {st['command']}")
                r = subprocess.run(st['command'], shell=True, cwd=p)
                record['ran'].append({'command': st['command'], 'exit': r.returncode})
                save_state(out, state)
                if r.returncode:
                    print(f'  (exit code {r.returncode})')
                continue
            if before == after:
                if p not in record['uses'] and p not in first:
                    record['uses'].append(p)               # already there (e.g. a shared helper): relied on, not written
                continue
            kind = 'json' if st['action'] in ('merge_json', 'set_json', 'unset_json') else 'text' if st['action'] == 'append_text' else 'file'
            if p not in first:
                cur = read_text(p)
                entry = {'path': p, 'kind': kind, 'created': cur is None}
                if cur is not None:
                    entry['backup'] = keep_path(bdir, p)
                    write_text(entry['backup'], cur)
                first[p] = entry
                if p in record['uses']:
                    record['uses'].remove(p)
                record['files'].append(entry)
            entry = first[p]
            entry['after'] = keep_path(bdir, p, 'after')
            write_text(entry['after'], after)
            if kind == 'text':
                orig = read_text(entry['backup']) if entry.get('backup') else ''
                entry['added'] = after[len(orig):] if after.startswith(orig) else after
            save_state(out, state)
            write_text(p, after, st.get('mode'))
            if p.endswith('.json'):
                read_json(p)
    except (Exception, SystemExit) as e:
        save_state(out, state)
        print(f'\nStopped partway: {e}')
        print(f"What was already changed is recorded. Revert it with: python3 {HERE}/apply.py undo {oid} --dir {out}")
        raise SystemExit(1)
    record['status'] = 'done'
    save_state(out, state)
    print(f"\nApplied “{opt['title']}”.")
    if opt.get('verify'):
        print(f"Check: {opt['verify']}")
    print(f"Undo:  python3 {HERE}/apply.py undo {oid} --dir {out}")
    if any(f['path'].endswith('settings.json') or f['path'].endswith('settings.local.json') for f in record['files']):
        print('Settings are read when a session starts: restart Claude Code (or open a new session) to use the change.')


def do_undo(oid, out):
    state = load_state(out)
    rec = state.get(oid)
    if not rec:
        raise SystemExit(f'{oid} was not applied with this tool (nothing recorded in {state_path(out)}).')
    for n in undo_record(oid, rec, out, state):
        print(n)
    del state[oid]
    save_state(out, state)
    print(f'Undid {oid}.')


def report_claude_dir(out):
    """The Claude folder the report in <out> was built from (config.json records it), or None."""
    try:
        with open(layout.data(out, 'config.json'), encoding='utf-8') as fh:
            d = json.load(fh).get('claude_dir')
    except (OSError, ValueError, AttributeError):
        return None
    return os.path.abspath(os.path.expanduser(d)) if isinstance(d, str) and d else None


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
    ap.add_argument('cmd', choices=['list', 'show', 'apply', 'undo'])
    ap.add_argument('id', nargs='?')
    ap.add_argument('--dir', help='report directory holding optimizations.json (default $CLAUDE_USAGE_OUT, else <claude-dir>-usage, e.g. ~/.claude-usage)')
    ap.add_argument('--claude-dir', help='the folder Claude Code keeps its data in (default: the one the report was built from, '
                                         'else $CLAUDE_CONFIG_DIR, else ~/.claude)')
    ap.add_argument('--yes', action='store_true', help="don't ask before applying")
    a = ap.parse_args(argv)
    global CLAUDE_DIR
    if a.claude_dir:
        CLAUDE_DIR = os.path.abspath(os.path.expanduser(a.claude_dir))
    out = os.path.abspath(os.path.expanduser(a.dir or os.environ.get('CLAUDE_USAGE_OUT') or CLAUDE_DIR.rstrip('/\\') + '-usage'))
    layout.migrate(out, log=print)
    if not a.claude_dir:                                    # apply to the Claude folder the optimizations were computed for
        CLAUDE_DIR = report_claude_dir(out) or CLAUDE_DIR
    if a.cmd == 'undo':
        if not a.id:
            ap.error('undo needs an id')
        return do_undo(a.id, out)
    doc = read_json(os.path.join(out, 'optimizations.json'))
    opts = {o['id']: o for o in doc.get('optimizations') or []}
    if not opts:
        raise SystemExit(f'No optimizations in {out}/optimizations.json. Run /claude-usage:optimize in Claude Code first.')
    if a.cmd == 'list':
        state = load_state(out)
        for o in opts.values():
            how = 'applied' if o['id'] in state else ('one command' if o.get('apply') else 'by hand')
            sv = (o.get('savings') or {}).get('usd_so_far')
            print(f"  {o['id']:<34} {how:<12} {('$%.2f so far' % sv) if sv else '':<14} {o['title']}")
        return
    if not a.id or a.id not in opts:
        raise SystemExit(f"Unknown id {a.id!r}. Known: {', '.join(opts)}")
    if a.cmd == 'show':
        show(opts[a.id], out)
    else:
        do_apply(opts[a.id], out, a.yes)


if __name__ == '__main__':
    main()
