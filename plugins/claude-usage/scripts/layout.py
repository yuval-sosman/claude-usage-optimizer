"""Where each file lives in the output folder <out> (default $CLAUDE_USAGE_OUT, else <claude dir>-usage, e.g. ~/.claude-usage).

  <out>/report.html            the report: open this
  <out>/insights.json          written by /claude-usage:report (Claude), yours to edit
  <out>/optimizations.json     written by /claude-usage:report, or redone by /claude-usage:optimize (Claude)
  <out>/data/                  rebuilt by usage_report.py on every run: metrics.json, digest.md, config.json, candidates.json, *.csv
  <out>/applied/               apply.py's record (applied.json) and backups/, once you apply an optimization
  <out>/video/                 /claude-usage:video: storyboard.json (Claude), video.html and claude-usage-video.mp4 (video.py)
  <out>/share/                 /claude-usage:share: the one-file copies of the report you made to send (share.py pack)
  <out>/received/<name>/       share files others sent you, unpacked into report folders (share.py unpack)
  <out>/company/               /claude-usage:company: many people's share files combined (company.py build): report.html, data/

Older versions wrote everything flat into <out>; migrate() moves those files into place (once, on the next run).
The folder holds private data (prompt snippets, paths, your setup): prepare() refuses one that isn't this plugin's (home,
the Claude folder, a folder holding other things) and keeps it readable by you alone.
Standard library only; shared by usage_report.py, apply.py, video.py, share.py, company.py, open.py (which opens a report
that is already here) and clear.py (which removes these files, by these names, to start from scratch).
"""
import json
import os
import shutil

DATA = 'data'
APPLIED = 'applied'
VIDEO = 'video'
SHARE = 'share'
RECEIVED = 'received'
COMPANY = 'company'
DATA_FILES = ('metrics.json', 'digest.md', 'config.json',
              'calls.csv', 'tool_calls.csv', 'sessions.csv', 'subagents.csv', 'cache_misses.csv')
MARKER = '.claude-usage'         # in every folder this plugin writes a report into: it is safe to write and tidy there


def private():
    """Every file and folder the scripts create from here on is readable by the user alone (they hold prompt snippets,
    paths and the setup)."""
    os.umask(0o077)


def _norm(p):
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def _meta_ok(p):
    try:
        with open(p, encoding='utf-8') as fh:
            meta = json.load(fh).get('meta')
        return isinstance(meta, dict) and 'generated' in meta
    except (OSError, ValueError, AttributeError):
        return False


def is_report_dir(out):
    """Whether out already holds this plugin's output: its marker, or a report an older version wrote (a metrics.json with
    its meta, in data/ or flat)."""
    return os.path.isfile(os.path.join(out, MARKER)) or _meta_ok(data(out, 'metrics.json')) or _meta_ok(os.path.join(out, 'metrics.json'))


def unsafe_out(out, claude_dir=None):
    """Why out must not be written into, or None: home, the filesystem root, the Claude folder (or a folder holding it,
    or inside it), or a folder that already holds other things."""
    o = _norm(out)
    home = _norm(os.path.expanduser('~'))
    if o in (home, _norm(os.path.abspath(os.sep))):
        return f'{out} is your home folder or the filesystem root'
    if home.startswith(o.rstrip(os.sep) + os.sep):
        return f'{out} holds your home folder'
    if claude_dir:
        c = _norm(claude_dir)
        if o == c or c.startswith(o.rstrip(os.sep) + os.sep) or o.startswith(c.rstrip(os.sep) + os.sep):
            return f'{out} is, holds or is inside your Claude folder ({claude_dir})'
    if os.path.isfile(out):
        return f'{out} is a file'
    if os.path.isdir(out) and os.listdir(out) and not is_report_dir(out):
        return f'{out} already holds other files (it is not a claude-usage report folder)'
    return None


def prepare(out, claude_dir=None):
    """Make out ready for this plugin's files, or stop: see unsafe_out(). Creates it readable by you alone, marks it as a
    report folder, and makes an existing one private too."""
    why = unsafe_out(out, claude_dir)
    if why:
        raise SystemExit(f'Refusing to write the report there: {why}. Choose a new or empty folder (--out), or the default.')
    os.makedirs(out, mode=0o700, exist_ok=True)
    try:
        os.chmod(out, 0o700)
    except OSError:
        pass
    mark = os.path.join(out, MARKER)
    if not os.path.exists(mark):
        with open(mark, 'w', encoding='utf-8') as fh:
            fh.write('This folder holds claude-usage reports (private data: prompt snippets, paths, your setup).\n')


def data_dir(out):
    return os.path.join(out, DATA)


def data(out, name):
    return os.path.join(out, DATA, name)


def applied_dir(out):
    return os.path.join(out, APPLIED)


def video(out, name):
    return os.path.join(out, VIDEO, name)


def share(out, name):
    return os.path.join(out, SHARE, name)


def received(out, name):
    return os.path.join(out, RECEIVED, name)


def company(out):
    return os.path.join(out, COMPANY)


def require_report(out):
    """Stop unless out is a report folder (see is_report_dir): the scripts that only add to a report never write elsewhere."""
    if not is_report_dir(out):
        raise SystemExit(f'No claude-usage report in {out}: run /claude-usage:report first (or usage_report.py), or pass --out '
                         'with the report folder.')


def migrate(out, log=None):
    """Move files an older version left flat in <out> into data/ and applied/. Never overwrites a newer file, and does
    nothing in a folder that isn't a report folder (generic names like config.json may belong to something else)."""
    if not os.path.isdir(out) or not is_report_dir(out):
        return
    moved = []
    for name in DATA_FILES:
        old, new = os.path.join(out, name), data(out, name)
        if os.path.isfile(old):
            os.makedirs(data_dir(out), exist_ok=True)
            if os.path.exists(new):
                os.remove(old)                       # a newer copy is already in data/
            else:
                os.replace(old, new)
            moved.append(name)
    old_state, old_backups = os.path.join(out, 'applied.json'), os.path.join(out, 'backups')
    new_state, new_backups = os.path.join(applied_dir(out), 'applied.json'), os.path.join(applied_dir(out), 'backups')
    if os.path.isfile(old_state) and not os.path.exists(new_state):
        os.makedirs(applied_dir(out), exist_ok=True)
        if os.path.isdir(old_backups) and not os.path.exists(new_backups):
            shutil.move(old_backups, new_backups)
            moved.append('backups/')
        with open(old_state, encoding='utf-8') as fh:
            state = json.load(fh)
        # the record names each backup by its absolute path: point those at the new folder
        pre = old_backups + os.sep
        for rec in state.values():
            for f in (rec.get('files') or []) if isinstance(rec, dict) else []:
                for k in ('backup', 'after'):
                    if isinstance(f.get(k), str) and f[k].startswith(pre):
                        f[k] = os.path.join(new_backups, f[k][len(pre):])
        tmp = new_state + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            fh.write(json.dumps(state, indent=2, ensure_ascii=False) + '\n')
        os.replace(tmp, new_state)
        os.remove(old_state)
        moved.append('applied.json')
    if moved and log:
        log(f'Tidied {out} into the new layout: moved {", ".join(moved)} (see data/ and applied/).')
