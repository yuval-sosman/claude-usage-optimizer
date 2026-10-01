#!/usr/bin/env python3
"""What an optimization may change on this machine, and nothing else: the one place that says it.

apply.py checks every step against it before it previews, applies or undoes anything, and validate.py (which assemble.py
runs) rejects an optimization that breaks it, so a step outside it never reaches the Optimizations tab. Whatever
optimizations.json says (Claude writes part of it), apply.py can only:

  - copy a file bundled with this plugin (scripts/hooks/*, prices.json) to <claude dir>/hooks/claude-usage/, under its
    own name;
  - append a short marked block to <claude dir>/CLAUDE.md;
  - change these keys in <claude dir>/settings.json or a project's .claude/settings.local.json (SETTINGS below): model,
    effort, prompt-cache lifetimes, auto-compact, skill listing, disabling plugins, MCP servers or connectors, output
    limits, raising transcript retention, a few model/cache environment variables, and adding hooks or a status line
    that run the bundled scripts above (never another command, and never removing one of the user's own hooks).

It never runs a command, never writes anything else, and never touches permissions, API keys or endpoints, proxies,
credentials helpers, MCP server definitions or anything outside those files. Standard library only.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.realpath(__file__))
HOOKS_SRC = os.path.join(HERE, 'hooks')

ACTIONS = {                  # action: the keys a step may have besides "action" (required ones first)
    'write_file': (('path', 'source'), ('mode',)),
    'append_text': (('path', 'marker', 'content'), ()),
    'merge_json': (('path', 'value'), ()),
    'set_json': (('path', 'pointer', 'value'), ()),
    'unset_json': (('path', 'pointer'), ()),
}
JSON_ACTIONS = ('merge_json', 'set_json', 'unset_json')
MODES = ('700', '755', '600', '644')
MARKER = re.compile(r'[a-z0-9][a-z0-9-]{0,40}$')
APPEND_MAX = (800, 8)        # characters, lines: a CLAUDE.md block loads into every session, so it stays small
MISSING = object()


def bundled_sources():
    """The files write_file may copy: the bundled hooks (and their helper) and the price table."""
    try:
        names = sorted(n for n in os.listdir(HOOKS_SRC) if re.fullmatch(r'[A-Za-z0-9_]+\.(?:py|sh)', n))
    except OSError:
        names = []
    return ['hooks/' + n for n in names] + ['prices.json']


def hook_scripts():
    """The bundled scripts a hook or status line command may run (not the shared helper)."""
    return [s[len('hooks/'):] for s in bundled_sources() if s.startswith('hooks/') and s != 'hooks/_session.py']


# ---- files -------------------------------------------------------------------------------------------------------

def _norm(p):
    return os.path.normcase(os.path.abspath(p))


def under(p, root):
    p, root = _norm(p), _norm(root)
    return p == root or p.startswith(root.rstrip(os.sep) + os.sep)


def hooks_dir(claude_dir):
    return os.path.join(claude_dir, 'hooks', 'claude-usage')


def _local_settings(p, claude_dir, home):
    return (os.path.basename(p) == 'settings.local.json' and os.path.basename(os.path.dirname(p)) == '.claude'
            and (under(p, home) or under(p, claude_dir)))


def target_kind(p, claude_dir, home):
    """What an absolute path is to this policy: 'hook' (<claude dir>/hooks/claude-usage/<bundled name>), 'claude_md'
    (<claude dir>/CLAUDE.md), 'settings' (<claude dir>/settings.json, or a project's .claude/settings.local.json under
    home or the Claude folder), else None. Files in the Claude folder may be links (a dotfiles repo); a project's file,
    which may come from a cloned repository, only where it really is one (so a link in a repo can't send a write elsewhere)."""
    p = os.path.abspath(p)
    if _norm(os.path.dirname(p)) == _norm(hooks_dir(claude_dir)) and os.path.basename(p) in [os.path.basename(s) for s in bundled_sources()]:
        return 'hook'
    if _norm(p) == _norm(os.path.join(claude_dir, 'CLAUDE.md')):
        return 'claude_md'
    if _norm(p) == _norm(os.path.join(claude_dir, 'settings.json')):
        return 'settings'
    if _local_settings(p, claude_dir, home) and _local_settings(os.path.realpath(p), claude_dir, home):
        return 'settings'
    return None


def file_ok(p, claude_dir, home):
    """Whether apply.py may write, restore or remove p: one of the files target_kind() names."""
    return target_kind(p, claude_dir, home) is not None


# ---- settings ----------------------------------------------------------------------------------------------------

EFFORTS = ('low', 'medium', 'high', 'xhigh')     # what effortLevel accepts ("max" is only a maxEffortLevel; Claude Code ignores it here)
TTLS = ('5m', '1h')
WORD = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:@\[\]/-]{0,150}$')         # a model alias or id, a Bedrock profile id, a name
NUMBER = re.compile(r'\d{1,9}[kKmM]?$')
ENV = {          # the environment variables an optimization may set, and what their values may look like
    'CLAUDE_CODE_SUBAGENT_MODEL': WORD,
    'CLAUDE_CODE_AUTO_COMPACT_WINDOW': re.compile(r'(?:[1-9]\d{5}|1000000)$'),   # a plain token count, 100K–1M
    'CLAUDE_CODE_PROMPT_CACHE_TTL': re.compile(r'(?:5m|1h)$'),
    'CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL': re.compile(r'(?:5m|1h)$'),
    'ENABLE_PROMPT_CACHING_1H': re.compile(r'[01]$'),
    'FORCE_PROMPT_CACHING_5M': re.compile(r'[01]$'),
    'BASH_MAX_OUTPUT_LENGTH': NUMBER,
    'MAX_MCP_OUTPUT_TOKENS': NUMBER,
    'MAX_THINKING_TOKENS': NUMBER,
    'ENABLE_TOOL_SEARCH': re.compile(r'(?:true|false|auto|auto:\d{1,3}|[01])$'),
}


def _int(lo, hi):
    return lambda v: isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _in(*vals):
    return lambda v: isinstance(v, str) and v in vals


def _bool(v):
    return isinstance(v, bool)


SETTINGS = {     # top-level key: a check of its new value (a removed key is allowed where REMOVABLE says so)
    'model': lambda v: isinstance(v, str) and bool(WORD.match(v)),
    'effortLevel': _in(*EFFORTS),
    'alwaysThinkingEnabled': _bool,
    'promptCacheTtl': _in(*TTLS),
    'subagentPromptCacheTtl': _in(*TTLS),
    'autoCompactEnabled': _bool,
    'autoCompactWindow': _int(100000, 1000000),        # Claude Code ignores anything but an integer from 100K to 1M
    'skillListingBudgetFraction': lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= 1,
    'disableClaudeAiConnectors': _bool,
    'bashOutputMaxChars': _int(4000, 128000),          # Claude Code clamps it to this range
}
REMOVABLE = set(SETTINGS)          # removing one of these goes back to Claude Code's default
MAPS = {         # object keys whose entries are checked one by one: {name: check of the entry's new value}
    'skillOverrides': _in('on', 'name-only', 'user-invocable-only', 'off'),
    'enabledPlugins': lambda v: v is False,                       # only ever switching a plugin off
    'env': None,                                                  # ENV above
    'modelSettings': None,                                        # {model: {"effortLevel": …}} only
}


def _same(a, b):
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _cmd_script(cmd, dirs):
    """The bundled script a hook / status line command runs, or None. The plain form is the catalog's:
    python3 "<hooks dir>/<name>.py" [args] (bash for .sh); dirs are the ways the hooks folder may be written."""
    m = re.fullmatch(r'(python3|bash) "([^"\n]+)/([A-Za-z0-9_]+\.(?:py|sh))"((?: [A-Za-z0-9_.=:-]+)*)', cmd or '')
    if not m or m.group(2) not in dirs or m.group(3) not in hook_scripts():
        return None
    if (m.group(1) == 'bash') != m.group(3).endswith('.sh'):
        return None
    return m.group(3)


def command_ok(cmd, dirs, known=(), fast=None):
    """A hook or status line command apply.py may add: the plain bundled form, the fast form apply.py itself makes of
    it (fast(plain) must give exactly this command), or one already in the file (known)."""
    if not isinstance(cmd, str):
        return False
    if cmd in known or _cmd_script(cmd, dirs):
        return True
    m = re.fullmatch(r'h="([^"]*)"; \[ -f "\$h" \] \|\| exit 0; p="[^"]*"; \[ -x "\$p" \] \|\| p=python3; exec "\$p" -S "\$h"(.*)', cmd, re.S)
    plain = f'python3 "{m.group(1)}"{m.group(2)}' if m else None
    return bool(plain and fast and _cmd_script(plain, dirs) and fast(plain) == cmd)


HOOK_EVENTS = ('PreToolUse', 'PostToolUse', 'UserPromptSubmit', 'Stop', 'SubagentStop', 'SessionStart', 'SessionEnd',
               'PreCompact', 'Notification')


def _hook_group_problem(g, dirs, known, fast):
    if not isinstance(g, dict) or set(g) - {'matcher', 'hooks'} or not isinstance(g.get('hooks'), list) or not g['hooks']:
        return 'a hook group must be {"matcher"?, "hooks": [...]} and nothing else'
    if 'matcher' in g and not (isinstance(g['matcher'], str) and len(g['matcher']) <= 200 and '\n' not in g['matcher']):
        return 'a hook matcher must be one short line'
    for h in g['hooks']:
        if not isinstance(h, dict) or set(h) - {'type', 'command', 'timeout'} or h.get('type') != 'command':
            return 'a hook must be {"type": "command", "command": …, "timeout"?} and nothing else'
        if 'timeout' in h and not _int(1, 120)(h['timeout']):
            return 'a hook timeout must be 1–120 seconds'
        if not command_ok(h.get('command'), dirs, known, fast):
            return (f'hook command {str(h.get("command"))[:80]!r} is not one of the bundled hooks '
                    f'({", ".join(hook_scripts())}) under the Claude folder\'s hooks/claude-usage/')
    return None


def _list(v):
    return v if isinstance(v, list) else []


def _dict(v):
    return v if isinstance(v, dict) else {}


def settings_problems(before, after, dirs, known=(), fast=None):
    """Why changing a settings file from before to after isn't allowed ([] when it is): every key that differs must be
    one SETTINGS/MAPS/ENV lists with an allowed value, hooks may only gain groups that run bundled scripts, and the status
    line may only become the bundled one. dirs: the ways the hooks folder may be written in a command."""
    b, a, out = _dict(before), _dict(after), []
    if not isinstance(after, dict):
        return ['a settings file must stay a JSON object']
    known = set(known)
    for k in sorted(set(b) | set(a)):
        bv, av = b.get(k, MISSING), a.get(k, MISSING)
        if bv is not MISSING and av is not MISSING and _same(bv, av):
            continue
        if k in SETTINGS:
            if av is MISSING:
                if k not in REMOVABLE:
                    out.append(f'removes "{k}"')
            elif not SETTINGS[k](av):
                out.append(f'"{k}": {json.dumps(av)[:60]} is not an allowed value')
        elif k == 'cleanupPeriodDays':           # only ever keeping transcripts longer
            old = bv if _int(1, 100000)(bv) else 30
            if av is MISSING or not _int(max(30, old), 3650)(av):
                out.append(f'"cleanupPeriodDays" may only be raised (from {old}, at most 3650), never lowered or removed')
        elif k == 'disabledMcpjsonServers':      # only ever adding names (switching servers off)
            if not isinstance(av, list) or any(x not in av for x in _list(bv)) or \
                    not all(isinstance(x, str) and WORD.match(x) for x in av if x not in _list(bv)):
                out.append('"disabledMcpjsonServers" may only gain server names')
        elif k in MAPS:
            bd, ad = _dict(bv), _dict(av)
            if av is not MISSING and not isinstance(av, dict):
                out.append(f'"{k}" must stay an object')
                continue
            for n in sorted(set(bd) | set(ad)):
                nb, na = bd.get(n, MISSING), ad.get(n, MISSING)
                if nb is not MISSING and na is not MISSING and _same(nb, na):
                    continue
                if k == 'env':
                    rule = ENV.get(n)
                    if rule is None:
                        out.append(f'"env.{n}" is not an environment variable this plugin sets')
                    elif na is not MISSING and not (isinstance(na, str) and rule.match(na)):
                        out.append(f'"env.{n}": {json.dumps(na)[:60]} is not an allowed value')
                elif k == 'modelSettings':
                    if not WORD.match(n):
                        out.append(f'"modelSettings.{n[:40]}" is not a model name')
                        continue
                    mb, ma = _dict(nb), _dict(na)
                    if na is not MISSING and not isinstance(na, dict):
                        out.append(f'"modelSettings.{n}" must be an object')
                    for kk in sorted(set(mb) | set(ma)):
                        if kk in mb and kk in ma and _same(mb[kk], ma[kk]):
                            continue
                        if kk != 'effortLevel':
                            out.append(f'"modelSettings.{n}.{kk}" is not a setting this plugin changes')
                        elif kk in ma and ma[kk] not in EFFORTS:
                            out.append(f'"modelSettings.{n}.effortLevel": {json.dumps(ma[kk])[:40]} is not an effort level')
                elif na is not MISSING and not MAPS[k](na):
                    out.append(f'"{k}.{n}": {json.dumps(na)[:60]} is not an allowed value')
                elif na is MISSING and k == 'enabledPlugins' and nb is not False:
                    out.append(f'"enabledPlugins.{n}": only switching a plugin off is allowed')
        elif k == 'statusLine':
            if av is MISSING:
                if not (isinstance(bv, dict) and command_ok(bv.get('command'), dirs, (), fast)):
                    out.append('removes your own status line')
            elif not (isinstance(av, dict) and av.get('type') == 'command' and not set(av) - {'type', 'command', 'padding', 'refreshInterval'}
                      and command_ok(av.get('command'), dirs, known, fast)
                      and all(_int(0, 3600)(av[x]) for x in ('padding', 'refreshInterval') if x in av)):
                out.append('"statusLine" may only become the bundled statusline.py')
            elif isinstance(bv, dict) and not command_ok(bv.get('command'), dirs, (), fast):
                out.append('replaces your own status line')
        elif k == 'hooks':
            bh, ah = _dict(bv), _dict(av)
            if av is not MISSING and not isinstance(av, dict):
                out.append('"hooks" must stay an object')
                continue
            for ev in sorted(set(bh) | set(ah)):
                bl, al = _list(bh.get(ev)), _list(ah.get(ev))
                if ev in ah and not isinstance(ah[ev], list):
                    out.append(f'"hooks.{ev}" must stay a list')
                    continue
                if any(g not in al for g in bl):
                    out.append(f'removes or changes one of your own hooks under "hooks.{ev}"')
                    continue
                added = [g for g in al if g not in bl]
                if added and ev not in HOOK_EVENTS:
                    out.append(f'"hooks.{ev}" is not a hook event this plugin uses')
                    continue
                for g in added:
                    p = _hook_group_problem(g, dirs, known, fast)
                    if p:
                        out.append(p)
                        break
        else:
            out.append(f'changes "{k}", which this plugin never changes')
    return out


# ---- steps -------------------------------------------------------------------------------------------------------

def step_problems(st, resolve, claude_dir, home):
    """Why a step (as written in optimizations.json) isn't allowed, before looking at any file: its shape, its target
    file and its source. resolve(path) gives the absolute file apply.py would write. The settings change itself is
    checked by settings_problems() against the file's content (apply.py) or an empty one (validate.py)."""
    if not isinstance(st, dict):
        return ['a step must be an object']
    act = st.get('action')
    if act not in ACTIONS:
        return [f'action {act!r} is not one apply.py performs (it performs {", ".join(ACTIONS)}; it never runs commands)']
    need, opt = ACTIONS[act]
    out = [f'{act} needs "{k}"' for k in need if k not in st]
    extra = sorted(set(st) - {'action'} - set(need) - set(opt))
    if extra:
        out.append(f'{act} takes no {", ".join(repr(x) for x in extra)}')
    if out:
        return out
    try:
        p = resolve(st['path'])
    except (TypeError, ValueError, AttributeError):
        return [f'path {st.get("path")!r} is not a file path']
    kind = target_kind(p, claude_dir, home)
    if act == 'write_file':
        src = st['source']
        if src not in bundled_sources():
            out.append(f'source {src!r} is not a file bundled with this plugin ({", ".join(bundled_sources())})')
        elif kind != 'hook' or os.path.basename(p) != os.path.basename(src):
            out.append(f'a bundled file is installed only as ~/.claude/hooks/claude-usage/{os.path.basename(src)}')
        if 'mode' in st and st['mode'] not in MODES:
            out.append(f'mode {st["mode"]!r} is not one of {", ".join(MODES)}')
    elif act == 'append_text':
        c = st['content']
        if kind != 'claude_md':
            out.append('append_text only adds to ~/.claude/CLAUDE.md')
        if not isinstance(st['marker'], str) or not MARKER.match(st['marker']):
            out.append('marker must be a short lower-case id (letters, digits, dashes)')
        if not isinstance(c, str) or not c.strip() or len(c) > APPEND_MAX[0] or len(c.splitlines()) > APPEND_MAX[1] or '<!--' in c:
            out.append(f'content must be 1–{APPEND_MAX[1]} lines, at most {APPEND_MAX[0]} characters, without "<!--"')
    else:
        if kind != 'settings':
            out.append(f'{act} only changes ~/.claude/settings.json or a project\'s .claude/settings.local.json')
        if act in ('set_json', 'unset_json') and not (isinstance(st['pointer'], str) and re.fullmatch(r'(/[^/]+)+', st['pointer'])):
            out.append('pointer must be a JSON pointer such as /env/CLAUDE_CODE_SUBAGENT_MODEL')
        if act == 'merge_json' and not isinstance(st['value'], dict):
            out.append('merge_json needs an object "value"')
    return out
