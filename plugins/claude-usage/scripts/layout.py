"""Where each file lives in the output folder <out> (default $CLAUDE_USAGE_OUT, else <claude dir>-usage, e.g. ~/.claude-usage).

  <out>/report.html            the report: open this
  <out>/insights.json          written by /claude-usage:report (Claude), yours to edit
  <out>/optimizations.json     written by /claude-usage:optimize (Claude)
  <out>/data/                  rebuilt by usage_report.py on every run: metrics.json, digest.md, config.json, candidates.json, *.csv
  <out>/applied/               apply.py's record (applied.json) and backups/, once you apply an optimization
  <out>/video/                 /claude-usage:video: storyboard.json (Claude), video.html and claude-usage-video.mp4 (video.py)

Older versions wrote everything flat into <out>; migrate() moves those files into place (once, on the next run).
Standard library only; shared by usage_report.py, apply.py and video.py.
"""
import json
import os
import shutil

DATA = 'data'
APPLIED = 'applied'
VIDEO = 'video'
DATA_FILES = ('metrics.json', 'digest.md', 'config.json',
              'calls.csv', 'tool_calls.csv', 'sessions.csv', 'subagents.csv', 'cache_misses.csv')


def data_dir(out):
    return os.path.join(out, DATA)


def data(out, name):
    return os.path.join(out, DATA, name)


def applied_dir(out):
    return os.path.join(out, APPLIED)


def video(out, name):
    return os.path.join(out, VIDEO, name)


def migrate(out, log=None):
    """Move files an older version left flat in <out> into data/ and applied/. Never overwrites a newer file."""
    if not os.path.isdir(out):
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
