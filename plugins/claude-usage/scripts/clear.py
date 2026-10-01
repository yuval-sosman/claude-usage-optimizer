#!/usr/bin/env python3
"""Remove this plugin's files from the report folder, to start from scratch.

  python3 clear.py [--out DIR] [--claude-dir DIR] [--keep video,share,received,company]    show what would go (changes nothing)
  python3 clear.py --yes [the same options]                                                  remove it

It removes only what this plugin writes, by name, and only in a claude-usage report folder (layout.is_report_dir, never
home, the filesystem root or the Claude folder): report.html, insights.json and optimizations.json, data/ (the numbers,
CSVs and the notes Claude wrote), video/, share/ (the share files you made), received/ (reports others sent you),
company/ (the company report), and the files an older version left flat in the folder. Anything else there stays. When
nothing is left but the folder's marker, the folder goes too, and the next report starts a new one.

applied/ (apply.py's record and backups) stays while any optimization is applied, so `apply.py undo <id>` keeps working:
undo them first, each at your terminal, to have it go too. Nothing outside the report folder is touched: your Claude Code
settings, the hooks and CLAUDE.md lines an applied optimization installed, and the transcripts stay as they are. A link
inside the folder is removed as a link, never followed. Standard library only.
"""
import argparse
import json
import os
import shutil
import stat
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import layout  # noqa: E402
import usage_report as UR  # noqa: E402  (where the report folder is by default; ~ in printed paths)

FILES = {'report.html': 'the report page', 'insights.json': 'the Insights tab (written by Claude)',
         'optimizations.json': 'the Optimizations tab (written by Claude)'}
FOLDERS = {layout.DATA: 'the numbers, CSVs and notes the report is built from', layout.VIDEO: 'your highlights video',
           layout.SHARE: 'the share files you made', layout.RECEIVED: 'reports others sent you',
           layout.COMPANY: 'the company report'}
KEEPABLE = (layout.VIDEO, layout.SHARE, layout.RECEIVED, layout.COMPANY)       # what --keep can spare
LEGACY = layout.DATA_FILES + ('candidates.json', 'notes-insights.json', 'notes-optimizations.json')   # flat, from older versions
APPLIED = (layout.APPLIED, 'applied.json', 'backups')      # apply.py's record: applied/, or flat from an older version
JUNK = ('.DS_Store', 'Thumbs.db', 'desktop.ini')           # a file manager's own files: they go with the folder


def size_of(p):
    """Bytes under p, links counted as themselves (never followed)."""
    if os.path.islink(p) or not os.path.isdir(p):
        try:
            return os.lstat(p).st_size
        except OSError:
            return 0
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def human(n):
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:,} bytes' if unit == 'bytes' else f'{n:.1f} {unit}'
        n /= 1024.0


def applied_ids(out):
    """The optimizations apply.py has on record as applied, or None when a record can't be read (so it has to stay)."""
    ids = []
    for p in (os.path.join(layout.applied_dir(out), 'applied.json'), os.path.join(out, 'applied.json')):
        if not os.path.lexists(p):
            continue
        try:
            with open(p, encoding='utf-8') as fh:
                state = json.load(fh)
        except (OSError, ValueError):
            return None
        if not isinstance(state, dict):
            return None
        ids += [k for k, v in state.items() if not k.startswith('_') and isinstance(v, dict)]
    return sorted(set(ids))


def plan(out, keep):
    """What to remove and what stays: ([(path, what)], [(path, why)])."""
    remove, kept = [], []
    for name, what in FILES.items():
        p = os.path.join(out, name)
        if os.path.lexists(p):
            remove.append((p, what))
    for name, what in FOLDERS.items():
        p = os.path.join(out, name)
        if not os.path.lexists(p):
            continue
        if name in keep:
            kept.append((p, 'you asked to keep it'))
            continue
        if name == layout.RECEIVED and os.path.isdir(p) and not os.path.islink(p):
            n = len([x for x in os.listdir(p) if not x.startswith('.')])
            what = f'{n} report{"s" if n != 1 else ""} others sent you'
        remove.append((p, what))
    for name in LEGACY:
        p = os.path.join(out, name)
        if os.path.isfile(p) or os.path.islink(p):
            remove.append((p, 'left in the folder by an older version'))
    rec = [os.path.join(out, x) for x in APPLIED if os.path.lexists(os.path.join(out, x))]
    if rec:
        ids = applied_ids(out)
        if ids is None:
            kept += [(p, "apply.py's record can't be read, so it stays") for p in rec]
        elif ids:
            kept += [(p, f'{len(ids)} optimization{"s are" if len(ids) != 1 else " is"} still applied ({", ".join(ids)}): '
                         'undo keeps working from it') for p in rec]
        else:
            remove += [(p, "apply.py's record and backups (nothing is applied)") for p in rec]
    listed = {os.path.basename(p) for p, _ in remove + kept}
    for name in sorted(os.listdir(out)):
        if name not in listed and name != layout.MARKER and name not in JUNK:
            kept.append((os.path.join(out, name), "not this plugin's: left as it is"))
    return remove, kept


def _retry(func, path, _exc):
    """A read-only file (Windows) blocks its removal: make it writable and try once more."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def remove_path(p):
    if os.path.islink(p) or not os.path.isdir(p):
        os.unlink(p)
    else:
        shutil.rmtree(p, onerror=_retry)


def check(out, claude):
    """Why out must not be cleared, or None."""
    if os.path.islink(out) and not os.path.isdir(out):
        return f'{UR.tilde(out)} is a broken link'
    why = layout.unsafe_out(out, claude)
    if why:
        return why
    if not layout.is_report_dir(out):
        return f'{UR.tilde(out)} is not a claude-usage report folder'
    return None


def main(argv=None):
    UR.safe_console()
    layout.private()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', metavar='DIR', help='the report folder (default: the one usage_report.py uses, e.g. ~/.claude-usage)')
    ap.add_argument('--claude-dir', metavar='DIR', help='the Claude Code folder the report was built from, if not the default')
    ap.add_argument('--keep', default='', metavar='NAMES', help='folders to keep, comma-separated: ' + ', '.join(KEEPABLE))
    ap.add_argument('--yes', action='store_true', help='remove them; without it, only show what would go')
    a = ap.parse_args(argv)
    keep = {k.strip().lower() for k in a.keep.split(',') if k.strip()}
    if keep - set(KEEPABLE):
        ap.error(f'--keep takes {", ".join(KEEPABLE)}, not {", ".join(sorted(keep - set(KEEPABLE)))}')
    claude = UR.claude_dir(a.claude_dir)
    out = os.path.abspath(os.path.expanduser(a.out)) if a.out else UR.default_out(claude)
    legacy = UR.legacy_out(claude)
    if not os.path.lexists(out):
        print(f'No report folder at {UR.tilde(out)}: nothing to clear.')
        if legacy:
            print(f'An earlier version left a report in {UR.tilde(legacy)}; remove that folder by hand if you no longer need it.')
        return 0
    why = check(out, claude)
    if why:
        raise SystemExit(f'Refusing to clear {UR.tilde(out)}: {why}. Nothing was removed.')
    remove, kept = plan(out, keep)
    total = sum(size_of(p) for p, _ in remove)
    width = max([len(os.path.basename(p)) + 1 for p, _ in remove + kept] + [12])
    line = lambda p, text: f'  {(os.path.basename(p) + ("/" if os.path.isdir(p) and not os.path.islink(p) else "")).ljust(width)}  {text}'
    print(f'Report folder: {UR.tilde(out)}')
    if remove:
        print(f'{"Removing" if a.yes else "Would remove"} {len(remove)} item{"s" if len(remove) != 1 else ""} ({human(total)}):')
        for p, what in remove:
            print(line(p, f'{what} · {human(size_of(p))}'))
    else:
        print('Nothing of this plugin\'s to remove.')
    if kept:
        print('Keeping:')
        for p, why_ in kept:
            print(line(p, why_))
    print('Outside this folder nothing is touched: your settings, the hooks and CLAUDE.md lines an applied optimization '
          'installed, and your transcripts.')
    if legacy:
        print(f'An earlier version left a report in {UR.tilde(legacy)}, inside your Claude folder; remove it by hand if you no '
              'longer need it.')
    if not a.yes:
        if remove:
            print(f'This was a preview: nothing was removed. To remove it: python3 "{os.path.join(HERE, "clear.py")}" --yes'
                  + (f' --out "{out}"' if a.out else f' --claude-dir "{a.claude_dir}"' if a.claude_dir else '')
                  + (f' --keep {",".join(sorted(keep))}' if keep else ''))
        return 0
    failed = []
    for p, _ in remove:
        try:
            remove_path(p)
        except OSError as e:
            failed.append((p, e))
    for p, e in failed:
        print(f'  Could not remove {UR.tilde(p)}: {e.strerror or e}', file=sys.stderr)
    left = [x for x in os.listdir(out) if x != layout.MARKER and x not in JUNK]
    if not left:
        for x in os.listdir(out):
            remove_path(os.path.join(out, x))
        if os.path.islink(out):                          # a link to the folder: empty it, keep the link and its target
            print(f'Removed {human(total)}. The folder is empty (it is a link, so it stays): /claude-usage:report starts a new '
                  'report in it.')
        else:
            os.rmdir(out)
            print(f'Removed {human(total)}. The folder is gone: /claude-usage:report starts a new one.')
    else:
        print(f'Removed {human(total - sum(size_of(p) for p, _ in failed))}. The folder stays for what is kept; '
              '/claude-usage:report builds a new report in it.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
