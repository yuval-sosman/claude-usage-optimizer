#!/usr/bin/env python3
"""PreToolUse hook (matcher: Read): the first time Claude reads a large file whole, ask it to read the part it needs.

A whole-file read stays in the context and is re-read by every later request of the session. When a text file is over
--max-kb and the Read has no offset/limit, this denies that first attempt with a reason Claude sees (use Grep, then Read
with offset/limit). Reading the same file whole again goes through, so nothing is ever blocked twice.

  python3 big_read_guard.py [--max-kb 60]
"""
import hashlib
import os
import sys

try:
    import _session as S
except Exception:       # a missing or broken helper must never break Claude Code: exit and do nothing
    sys.exit(0)

BINARY = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.pdf', '.ipynb', '.heic', '.bmp', '.tiff', '.ico'}


def main():
    max_kb = S.arg('--max-kb', 60)
    inp = S.read_stdin()
    if inp.get('tool_name') != 'Read':
        return
    ti = inp.get('tool_input') or {}
    path = ti.get('file_path') or ''
    if not path or ti.get('offset') or ti.get('limit') or os.path.splitext(path)[1].lower() in BINARY:
        return
    try:
        if not os.path.isfile(path):                           # a folder has a size too; Claude Code refuses it anyway
            return
        size = os.path.getsize(path)
    except OSError:
        return
    if size < max_kb * 1024:
        return
    sid = inp.get('session_id') or 'unknown'
    name = 'read-' + sid + '-' + hashlib.sha1(path.encode('utf-8', 'replace')).hexdigest()[:16]
    if S.state_get(name):                                      # one file per (session, path): parallel Reads of two big
        return                                                 # files each record their own, so neither retry is denied
    if not S.state_put(name, {'path': path}):
        return                                                 # unrecorded, the second Read would be denied again
    lines, read, cap = 0, 0, 8 * 1024 * 1024                   # counted on at most 8 MB, then scaled: not a whole log file
    try:
        with open(path, 'rb') as fh:
            while read < cap:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                lines += chunk.count(b'\n')
                read += len(chunk)
    except OSError:
        pass
    about = f"{lines:,} lines" if read >= size or not read else f"about {int(lines * size / read):,} lines"
    reason = (f"{os.path.basename(path)} is {size // 1024} KB ({about}, about {S.tok(size / 4)} tokens). Read whole, it "
              f"stays in the context and every later request re-reads it. Find the part you need first (Grep, or the "
              f"file's structure), then Read it with offset/limit. If you really need the whole file, read it again and "
              f"it will go through.")
    S.emit({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                   'permissionDecisionReason': reason}})


if __name__ == '__main__':
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
