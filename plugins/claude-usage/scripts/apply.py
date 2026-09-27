#!/usr/bin/env python3
"""Apply the optimizations the claude-usage:optimize skill wrote, one at a time, with a preview, a backup and an undo.

  python3 apply.py list                 # what can be applied, and what already is
  python3 apply.py show  <id>           # preview the exact changes (nothing is written)
  python3 apply.py apply <id> [--yes]   # preview, confirm, back up, apply
  python3 apply.py undo  <id>           # revert just that change (later changes to the same files are kept)
  python3 apply.py check                # preview all of them at once: one line each (ok, or what is wrong)

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
import re
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


FAST = re.compile(r'h="([^"]*)"; \[ -f "\$h" \] \|\| exit 0; p="[^"]*"; \[ -x "\$p" \] \|\| p=python3; exec "\$p" -S "\$h"(.*)', re.S)


def fast_command(cmd):
    """A bundled Python hook's command, run by the interpreter running apply.py instead of `python3` (on macOS a shim,
    with pyenv/asdf a shell script, costing more than the hook itself on every prompt or tool call), and by `python3`
    again if that interpreter is removed. -S skips the site module and site-packages (the hooks use only the standard
    library). A missing script exits 0: `python3 missing.py` exits 2, which blocks every prompt or Read. The script path
    stays first: the usage report names a hook by the first *.py in its command. POSIX shells only, and only for the
    plain form the catalog writes; anything else is left as it is."""
    if os.name == 'nt' or sys.platform.startswith(('cygwin', 'msys')) or not isinstance(cmd, str) or not cmd.startswith('python3 "'):
        return cmd
    script, quote, args = cmd[len('python3 "'):].partition('"')
    homes = [os.path.join(CLAUDE_DIR, 'hooks', 'claude-usage')] + (['$HOME/.claude/hooks/claude-usage'] if CLAUDE_DIR == DEFAULT_CLAUDE else [])
    exe = sys.executable or ''
    if (not quote or os.path.dirname(script) not in homes or not re.fullmatch(r'\w+\.py', os.path.basename(script))
            or not re.fullmatch(r'(?: [\w.=:-]+)*', args) or not os.path.isabs(exe) or re.search(r'["$`\\\n]', exe)
            or sys.prefix != sys.base_prefix):             # a virtualenv's python belongs to one project
        return cmd
    return f'h="{script}"; [ -f "$h" ] || exit 0; p="{exe}"; [ -x "$p" ] || p=python3; exec "$p" -S "$h"{args}'


def plain_command(cmd):
    """The `python3 …` command a fast_command() was made from (with any interpreter), else cmd."""
    m = FAST.fullmatch(cmd)
    return f'python3 "{m.group(1)}"{m.group(2)}' if m else cmd


def commands(x):
    """Every "command" string in a JSON value."""
    if isinstance(x, dict):
        return [v for k, v in x.items() if k == 'command' and isinstance(v, str)] + [c for v in x.values() for c in commands(v)]
    if isinstance(x, list):
        return [c for v in x for c in commands(v)]
    return []


def swap_commands(x, f):
    """x with f() applied to every "command" string in it."""
    if isinstance(x, dict):
        return {k: f(v) if k == 'command' and isinstance(v, str) else swap_commands(v, f) for k, v in x.items()}
    if isinstance(x, list):
        return [swap_commands(v, f) for v in x]
    return x


def fast_commands(value, doc):
    """value with its hook commands made fast_command()s, except where doc already runs the same hook in either form:
    that command is kept, so an installed hook shows as up to date instead of being added a second time."""
    have = {}
    for c in commands(doc):
        have.setdefault(plain_command(c), c)
    return swap_commands(value, lambda c: have.get(c) or fast_command(c))


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


JSON_ACTIONS = {'merge_json', 'set_json', 'unset_json'}


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
        try:
            before = files[p] if p in files else read_text(p, strict=True)
        except OSError as e:
            raise SystemExit(f'{p} could not be read ({e.strerror}); fix it by hand first.')
        except ValueError as e:
            raise SystemExit(f'{p} is not UTF-8 text ({e}); fix it by hand first.')
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
            try:
                now = doc = json.loads(before) if before and before.strip() else {}
            except ValueError as e:
                raise SystemExit(f'{p} is not valid JSON ({e}); fix it by hand first.')
            if act == 'merge_json':
                doc = deep_merge(doc, fast_commands(st['value'], doc))
            elif act == 'set_json':
                doc = set_pointer(doc, st['pointer'], fast_commands(st['value'], doc))
            elif act == 'unset_json':
                doc = unset_pointer(doc, st['pointer'])
            after = before if before is not None and same_json(doc, now) else dump_json(doc)   # keep the user's layout
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


REL = {'alternative': 'alternative to', 'conflicts': "don't combine with", 'overlaps': 'savings overlap with',
       'complements': 'pairs well with', 'requires': 'needs first'}


def relation_warnings(opt, opts, state, out):
    """A warning when an alternative to opt or one it conflicts with is already applied, or one it needs isn't."""
    warns = []
    for r in opt.get('related') or []:
        oid, kind = r.get('id'), r.get('relation')
        if oid not in opts or kind not in REL:
            continue
        on = oid in state
        if on and kind in ('alternative', 'conflicts'):
            warns.append(f"! {oid} is already applied and this is {'an alternative to it' if kind == 'alternative' else 'in conflict with it'}. "
                         f"Keep one: undo it with  python3 {HERE}/apply.py undo {oid} --dir {out}  if you want this one instead.")
        elif not on and kind == 'requires':
            warns.append(f'! This needs {oid} first:  python3 {HERE}/apply.py apply {oid} --dir {out}')
    return warns


def related_lines(opt, opts, state, out):
    """How opt relates to the other optimizations, then its relation_warnings()."""
    lines = []
    for r in opt.get('related') or []:
        oid, kind = r.get('id'), r.get('relation')
        if oid in opts and kind in REL:
            lines.append(f"    {REL[kind]:<22} {oid}{' (applied)' if oid in state else ''}: {r.get('note', '')}")
    for o in opts.values():                       # the reverse side of "requires"
        if any(r.get('id') == opt['id'] and r.get('relation') == 'requires' for r in o.get('related') or []):
            lines.append(f"    {'needed by':<22} {o['id']}{' (applied)' if o['id'] in state else ''}")
    warns = relation_warnings(opt, opts, state, out)
    return (['\n  Related:'] + lines if lines else []) + (['\n' + '\n'.join(warns)] if warns else [])


def same_json(a, b):
    """Equal as JSON: key order doesn't matter, but true is not 1."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def json_changes(b, a, path=''):
    """The keys two JSON values differ in, briefly: +added, -removed, key=new value, list +n/-n items."""
    if isinstance(b, dict) and isinstance(a, dict):
        out = []
        for k in list(b) + [k for k in a if k not in b]:
            p = f'{path}.{k}' if path else k
            if k not in a:
                out.append('-' + p)
            elif k not in b and isinstance(a[k], (dict, list)):
                out += json_changes(type(a[k])(), a[k], p)
            elif k not in b:
                out.append(f'+{p}={json.dumps(a[k], ensure_ascii=False)[:40]}')
            elif not same_json(a[k], b[k]):
                out += json_changes(b[k], a[k], p)
        return out
    if isinstance(b, list) and isinstance(a, list):
        n_add, n_del = sum(v not in b for v in a), sum(v not in a for v in b)
        return [path + (f' +{n_add}' if n_add else '') + (f' -{n_del}' if n_del else '')]
    return [f'{path}={json.dumps(a, ensure_ascii=False)[:40]}']


def plan_summary(steps):
    """What the planned steps would do, in one line: files created (by folder), changed (with the JSON keys), commands run."""
    first, last, acts, ran = {}, {}, {}, []
    for st, p, before, after, note in steps:
        if st['action'] == 'run':
            ran.append(f"run `{st['command']}` in {p.replace(HOME, '~')}")
            continue
        first.setdefault(p, before)
        last[p] = after
        acts.setdefault(p, set()).add(st['action'])
    made, parts = {}, []
    for p, before in first.items():
        after, short = last[p], p.replace(HOME, '~')
        if before == after:
            continue
        if acts[p] & JSON_ACTIONS:
            try:
                keys = json_changes(json.loads(before) if before and before.strip() else {}, json.loads(after))
            except ValueError:
                keys = []
            parts.append(f"{'create' if before is None else 'change'} {short}" + (': ' + ', '.join(keys[:4]) if keys else '')
                         + (f' (+{len(keys) - 4} more)' if len(keys) > 4 else ''))
        elif before is None:
            made.setdefault(os.path.dirname(short), []).append(os.path.basename(p))
        elif 'append_text' in acts[p]:
            parts.append(f'append {len(after.splitlines()) - len(before.splitlines())} lines to {short}')
        else:
            parts.append(f'replace {short}')
    made = [f'create {d}/' + (names[0] if len(names) == 1 else '{' + ','.join(names) + '}') for d, names in made.items()]
    return '; '.join(made + parts + ran)


def plan_problems(steps):
    """What would stop an apply partway, as far as a preview can tell."""
    out = []
    for st, p, before, after, note in steps:
        short = p.replace(HOME, '~')
        if st['action'] == 'run':
            if not os.path.isdir(p):
                out.append(f"{short} (where it runs `{st['command']}`) is not a folder")
            continue
        if st.get('mode'):
            try:
                int(st['mode'], 8)
            except (TypeError, ValueError):
                out.append(f"mode {st['mode']!r} is not an octal string")
        if p.endswith('.json') and after != before and after.strip():
            try:
                json.loads(after)
            except ValueError:
                out.append(f'{short} would not be valid JSON')
    return out


def failure(e):
    """Why plan() failed, in a few words."""
    if isinstance(e, SystemExit):
        return str(e.code).replace(HOME, '~')
    if isinstance(e, KeyError):
        return f'a step has no {e.args[0]!r}'
    if isinstance(e, OSError) and e.filename:
        return f"{e.strerror}: {str(e.filename).replace(HOME, '~')}"
    return f'{type(e).__name__}: {e}'


def do_check(opts, out):
    """Preview every optimization that has apply steps (nothing is written): one line each, whether it would apply
    cleanly and what it would change, then the warnings `show` gives about related ones. Returns the number of errors."""
    state = load_state(out)
    tally = {}
    for o in opts.values():
        if not (o.get('apply') or {}).get('steps'):
            continue
        oid = o['id']
        try:
            steps = plan(o, out)
            problems = plan_problems(steps)
            what = plan_summary(steps)
        except (Exception, SystemExit) as e:
            problems, what = [failure(e)], ''
        if problems:
            status, what = 'ERROR', '; '.join(dict.fromkeys(problems))
        elif not what:
            status, what = ('applied', '') if oid in state else ('in place', 'already set up this way: nothing to change')
        else:
            status = 'update' if oid in state else 'ok'
            what = ('applied before with different content; applying again replaces it: ' if oid in state else '') + what
        tally[status] = tally.get(status, 0) + 1
        print(f'  {status:<9} {oid:<34} {what}'.rstrip())
        for w in relation_warnings(o, opts, state, out):
            print(f'            {w}')
    print('  ' + (', '.join(f'{n} {s}' for s, n in tally.items()) or 'No optimization has apply steps.'))
    return tally.get('ERROR', 0)


MISSING = object()


def state_path(out):
    return os.path.join(layout.applied_dir(out), 'applied.json')


def load_state(out):
    p = state_path(out)
    state = read_json(p) if os.path.exists(p) else {}
    if isinstance(state, dict):
        read_origins(state)
    return state


def save_state(out, state):
    os.makedirs(layout.applied_dir(out), exist_ok=True)
    tmp = state_path(out) + '.tmp'
    live = live_paths(state)                                # origins of files nothing applied uses any more go
    o = {p: v for p, v in (state.get(ORIGINS) or {}).items() if p in live}
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(dump_json({k: (o if k == ORIGINS else v) for k, v in state.items() if k != ORIGINS or o}))
    os.replace(tmp, state_path(out))


def read_text(p, strict=False):
    """The file's text, or None if there is none. Unless strict, a file that can't be read (a folder, no access) counts
    as none too; text that isn't UTF-8 always raises."""
    if strict and not os.path.exists(p):
        return None
    try:
        with open(p, encoding='utf-8') as fh:
            return fh.read()
    except OSError:
        if strict:
            raise
        return None


def temp_of(p):
    """The temp file write_text() writes before renaming it over p (beside p's real file, so the rename is atomic)."""
    real = os.path.realpath(p)
    return os.path.join(os.path.dirname(real), f'.{os.path.basename(real)}.claude-usage-tmp')


def write_text(p, txt, mode=None):
    """Write p whole or not at all: a crash can't leave half a hook or settings.json. A symlinked p (a dotfiles repo)
    stays a symlink, and p keeps its permissions unless mode is given."""
    real = os.path.realpath(p)
    os.makedirs(os.path.dirname(real), exist_ok=True)
    tmp = temp_of(p)
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(txt)
    if mode:
        os.chmod(tmp, int(mode, 8))
    elif os.path.exists(real):
        shutil.copymode(real, tmp)
    os.replace(tmp, real)


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
    return [oid for oid, rec in state.items() if oid not in (skip, ORIGINS) and isinstance(rec, dict)
            and (any(f.get('path') == path for f in rec.get('files') or []) or path in (rec.get('uses') or []))]


ORIGINS = '_origins'        # in applied.json beside the optimizations' records (ids never start with "_")


def origins(state):
    """{path: {'created': True} or {'backup': copy}, plus 'after': copy} for each whole file applied optimizations wrote
    (hooks and their shared helpers): what was there before the first of them wrote it, and what the latest one wrote.
    The last one to be undone puts the first back, whichever order they are undone in and however many versions of a
    shared helper came in between."""
    o = state.get(ORIGINS)
    if not isinstance(o, dict):
        o = state[ORIGINS] = {}
    return o


def live_paths(state):
    """Every file an applied optimization wrote or relies on."""
    return {p for oid, rec in state.items() if oid != ORIGINS and isinstance(rec, dict)
            for p in [f.get('path') for f in rec.get('files') or []] + list(rec.get('uses') or [])}


def read_origins(state):
    """Fill in origins() for whole files recorded before it existed (or by an older apply.py), from the records in apply
    order, and drop entries for files no applied optimization refers to any more."""
    o, new, live = origins(state), {}, live_paths(state)
    for p in [p for p, v in o.items() if p not in live or not (isinstance(v, dict) and (v.get('created') or v.get('backup')))]:
        del o[p]                                            # left over, or damaged by hand: rebuilt from the records below
    for oid, rec in state.items():
        for f in (rec.get('files') or []) if oid != ORIGINS and isinstance(rec, dict) else []:
            p = f.get('path')
            if f.get('kind') in ('json', 'text') or not f.get('after') or (p in o and p not in new):
                continue
            new.setdefault(p, {'created': True} if f.get('created') else {'backup': f.get('backup')})['after'] = f['after']
    o.update(new)


def release(state, p, oid, f=None):
    """Undo one optimization's part in a whole file (f: its record of writing it; None if it only relied on the file).
    While others use the file it stays as it is. The last one puts back what was there before the first wrote it, or
    removes it, unless it was edited since. Returns the note to print, or None if there is none."""
    after = (f or {}).get('after')
    short = p.replace(HOME, '~')
    others = users_of(state, p, oid)
    o = origins(state).get(p) if others else origins(state).pop(p, None)
    cur = read_text(p)
    if cur is None:
        return f'gone     {short} (already removed)' if after else None
    if others:
        if after and o is not None and cur == read_text(after):
            o['after'] = after                             # what it holds now (an older apply.py may have written it)
        if after and (o or {}).get('created'):
            hand_over(state, p, oid, after)                # the records alone still say so, for an older apply.py
        return f'kept     {short} (still used by {", ".join(others)})' if after else None
    if not o:
        return None
    written = [t for t in (read_text(x) for x in (o.get('after'), after) if x) if t is not None]
    if not written:
        return f'kept     {short}: the copies in backups/ are missing, so it is left as it is'
    if cur not in written:
        was = "it didn't exist before" if o.get('created') else f'the original is in {o.get("backup")}'
        return f'kept     {short}: it changed since it was written ({was})'
    if o.get('created'):
        os.remove(p)
        return f'removed  {short}'
    first = read_text(o['backup'])
    if first is None:
        return f'kept     {short}: the copy of the original in backups/ is missing'
    write_text(p, first)
    return f'restored {short}'


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


def utf8(p):
    """Whether p is missing or UTF-8 text: undo leaves anything else (a hand edit) to the user."""
    try:
        read_text(p)
    except ValueError:
        return False
    return True


def undo_record(oid, rec, out, state):
    """Revert what one recorded apply did, file by file, keeping later changes. Returns the notes to print."""
    notes = []
    for f in reversed(rec.get('files') or []):
        p, kind = f['path'], f.get('kind')
        short = p.replace(HOME, '~')
        if os.path.exists(temp_of(p)):                     # left by a crash inside write_text()
            os.remove(temp_of(p))
        if not utf8(p):
            notes.append(f'skipped  {short}: not UTF-8 text; fix it by hand')
            continue
        if kind not in ('json', 'text') and f.get('after'):   # a whole file (a hook, a shared helper)
            n = release(state, p, oid, f)
            if n:
                notes.append(n)
            continue
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
    for p in rec.get('uses') or []:                        # a whole file others wrote, which this one relied on
        if p in origins(state):
            n = release(state, p, oid) if utf8(p) else None if users_of(state, p, oid) else \
                f'skipped  {p.replace(HOME, "~")}: not UTF-8 text; fix it by hand'
            if n:
                notes.append(n)
    for r in rec.get('ran') or []:
        notes.append(f"note: this ran `{r['command']}`, which can't be undone automatically")
    return notes


def do_apply(opt, out, yes, opts=None):
    state = load_state(out)
    if not opt.get('apply'):
        print(f"\n{opt['title']} has no automatic change. Do it by hand:")
        for i, m in enumerate(opt.get('manual') or [], 1):
            print(f'  {i}. {m}')
        for ln in related_lines(opt, opts or {}, state, out):
            print(ln)
        return
    oid = opt['id']
    steps, changed = show(opt, out)
    for ln in related_lines(opt, opts or {}, state, out):
        print(ln)
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
    n = 1
    while os.path.exists(bdir):                             # never over copies an earlier apply (or an origin) points to
        n += 1
        bdir = os.path.join(layout.applied_dir(out), 'backups', f"{stamp}-{oid}-{n}")
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
            kind = 'json' if st['action'] in JSON_ACTIONS else 'text' if st['action'] == 'append_text' else 'file'
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
            if kind == 'file':
                o = origins(state)
                if p not in o or not users_of(state, p, oid):     # the first applied optimization to write it
                    o[p] = {'created': True} if entry['created'] else {'backup': entry['backup']}
                o[p]['after'] = entry['after']
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
    rec = state.get(oid) if oid != ORIGINS else None
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
    ap.add_argument('cmd', choices=['list', 'show', 'apply', 'undo', 'check'])
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
    state = load_state(out)
    if a.cmd == 'check':
        raise SystemExit(1 if do_check(opts, out) else 0)
    if a.cmd == 'list':
        for o in opts.values():
            how = 'applied' if o['id'] in state else ('one command' if o.get('apply') else 'by hand')
            sv = (o.get('savings') or {}).get('usd_so_far')
            print(f"  {o['id']:<34} {how:<12} {('$%.2f so far' % sv) if sv else '':<14} {o['title']}")
            for r in o.get('related') or []:
                if r.get('relation') in ('alternative', 'conflicts', 'requires') and r.get('id') in opts:
                    print(f"  {'':<34} {REL[r['relation']]} {r['id']}")
        return
    if not a.id or a.id not in opts:
        raise SystemExit(f"Unknown id {a.id!r}. Known: {', '.join(opts)}")
    if a.cmd == 'show':
        show(opts[a.id], out)
        for ln in related_lines(opts[a.id], opts, state, out):
            print(ln)
    else:
        do_apply(opts[a.id], out, a.yes, opts)


if __name__ == '__main__':
    main()
