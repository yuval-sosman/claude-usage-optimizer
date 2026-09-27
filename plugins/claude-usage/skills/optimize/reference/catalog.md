# Catalog of optimizations

Each entry: **when** it applies (read the named questions in digest.md), **saving** (where the number comes from), the
**apply** template for optimizations.json (adapt names, paths and thresholds to the data), the **manual** steps, and the
**tradeoff**. Paths use `~`; apply.py expands them and refuses anything outside the home directory. Check config.json first:
never overwrite an existing `statusLine`, never add a second copy of a hook, skip settings already at the proposed value.

---

## Cost & model choice

### main-model: keep main-thread work on the cheaper model you already use
- **When**: SV6 "Main threads on <current model>" saving > $10, or OV2's by-model split shows a pricier model doing most of the work.
- **Saving**: SV6 main-thread figure (`theoretical`; same tokens at list prices).
- **Apply** (only if `model` in `~/.claude/settings.json` isn't already that model): `set_json` `~/.claude/settings.json`
  pointer `/model` value `"<alias>"` (e.g. `"opus[1m]"`, `"sonnet"`). Often this is a **habit** instead: the default is already
  right and the spend came from switching up (`/model`) for whole sessions.
- **Manual**: `/model` at the start of a session; switch up only for the hard part, then back; consider `opusplan`.
- **Tradeoff**: a cheaper model can take more turns or do the work worse; judge by task.

### subagent-model: run subagents on Sonnet by default
- **When**: SV6 "Subagents on Sonnet 5" > $5 and `env.CLAUDE_CODE_SUBAGENT_MODEL` isn't set.
- **Saving**: SV6 subagent figure (`theoretical`).
- **Apply**: `merge_json` `~/.claude/settings.json` value `{"env": {"CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"}}`.
- **Manual**: add the same to settings.json; or give specific agents `model: sonnet` / `model: haiku` in their agent files
  (`~/.claude/agents/<name>.md`), e.g. read-only research agents on Haiku (SV6 "Explore subagents on Haiku").
- **Tradeoff**: subagents that plan or review hard code may need the stronger model; an agent's own `model` field and a
  per-call model still win.

### effort-default: lower the default effort (only with strong evidence)
- **When**: OV4 thinking share > 35% of output and `effortLevel` is `high`/`xhigh`/`max`, and OV3 shows output is a big part
  of cost. Confidence low: effort changes quality.
- **Saving**: a stated fraction of thinking cost (OV3 output cost × OV4 thinking share); mark `upper_bound` and explain.
- **Apply**: `set_json` `~/.claude/settings.json` pointer `/effortLevel` value `"medium"`.
- **Manual**: `/effort high` for hard tasks, back to medium after.
- **Tradeoff**: less thinking on hard problems. Changing effort mid-session can also invalidate the cache.

### stop-hook-followup: make Stop-hook follow-up work cheaper
- **When**: SV1's "Stop-hook follow-up work" lever > $5.
- **Saving**: that lever (`upper_bound`: some of that work is wanted).
- **Apply**: none generic (it's the user's own automation). **Manual**: show which hook (EX5, config hooks) and options:
  run it only when the turn was long or changed files; check `stop_hook_active` so it can't loop; move summarisation out of
  the session (a script calling a cheaper model); lower what it asks Claude to write.

---

## Context

### auto-compact-window: let Claude Code compact at your break-even size
- **When**: SV4 best threshold net saving > $5 and the threshold is well below the model's window (1M models especially).
- **Saving**: SV4 net saving (`theoretical`).
- **Apply**: `merge_json` `~/.claude/settings.json` value `{"autoCompactWindow": <SV4 best threshold, rounded, e.g. 200000>}`.
  Round up (compaction triggers as usage *approaches* the window).
- **Manual**: `/config` → auto-compact window, or the settings line above.
- **Tradeoff**: Claude Code recommends its automatic window; compaction drops detail and costs one summary. Risk `medium`.
  Offer **context-guard** as the gentle alternative.

### context-guard: a notice when the context passes the break-even size
- **When**: same data as auto-compact-window; preferred when the user wants to decide when to compact.
- **Saving**: SV4 (`theoretical`, if acted on).
- **Apply**: `write_file` `~/.claude/hooks/claude-usage/context_guard.py` from `hooks/context_guard.py` (mode 755), then
  `merge_json` `~/.claude/settings.json` value
  `{"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 \"$HOME/.claude/hooks/claude-usage/context_guard.py\" --threshold <T>", "timeout": 10}]}]}}`.
- **Tradeoff**: none beyond a line of text; it never blocks.

### big-read-guard: steer Claude to targeted reads of large files
- **When**: SV8 saving > $3 (large whole-file reads re-read for the rest of the session; CX3's costliest-item insight and EX8 show which files).
- **Saving**: SV8 (`upper_bound`, assumes a range keeps half).
- **Apply**: `write_file` `~/.claude/hooks/claude-usage/big_read_guard.py` from `hooks/big_read_guard.py` (mode 755);
  `merge_json` `~/.claude/settings.json` value `{"hooks": {"PreToolUse": [{"matcher": "Read", "hooks": [{"type": "command", "command": "python3 \"$HOME/.claude/hooks/claude-usage/big_read_guard.py\" --max-kb 60", "timeout": 10}]}]}}`.
- **Tradeoff**: one extra round-trip the first time a big file is needed whole (the retry goes through).

### bash-output-cap: smaller inline Bash output
- **When**: CX3 shows Bash results among the top context sources and the digest's EX10 lists very large Bash outputs (EX10 is
  hidden: cite CX3 and EX6).
- **Apply**: `merge_json` `~/.claude/settings.json` value `{"bashOutputMaxChars": 15000}` (default 30000; the rest goes to a file
  Claude can read on demand).
- **Tradeoff**: Claude sees less of long logs inline; risk `medium`.

### transcript-retention: keep more history for the next report
- **When**: the digest's period is close to 30 days (the default `cleanupPeriodDays`) and it isn't set higher.
- **Apply**: `merge_json` `~/.claude/settings.json` value `{"cleanupPeriodDays": 90}`. No saving; better analysis.

---

## Caching

### stale-cache-guard: stop the first prompt into an expired, big session
- **When**: SV7 saving > $2, or CX8's costliest misses are "came back after a break".
- **Saving**: SV7 (`upper_bound`) or the "You came back after a break" row of SV3 (`measured` extra cost).
- **Apply**: `write_file` `~/.claude/hooks/claude-usage/stale_cache_guard.py` from `hooks/stale_cache_guard.py` (mode 755);
  `merge_json` `~/.claude/settings.json` value
  `{"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 \"$HOME/.claude/hooks/claude-usage/stale_cache_guard.py\" --min-context 60000", "timeout": 10}]}]}}`.
  Set `--min-context` well above what a new session starts with (CX5): a fresh start only saves once the context is past
  start-up + 15K (SV7), so use about twice the start-up size (60000 suits a ~30K start-up) and small sessions are never stopped.
- **Manual**: after a break over the cache lifetime with a big context, `/clear` and paste a short summary (or `/compact` first).
- **Tradeoff**: one extra Enter when you really do want to continue.

### statusline-cache: see context size and cache warmth all the time
- **When**: no `statusLine` in any settings file (config.json).
- **Apply**: `write_file` `~/.claude/hooks/claude-usage/statusline.py` from `hooks/statusline.py` (mode 755); `merge_json`
  `~/.claude/settings.json` value `{"statusLine": {"type": "command", "command": "python3 \"$HOME/.claude/hooks/claude-usage/statusline.py\""}}`.
- **Saving**: none directly (awareness); omit `savings`.

### cache-ttl-fit: choose the cache lifetime per thread kind
- **When**: SV5's "Cheapest mix" differs from the actual mix and saves > $3 (current: main 1h on a subscription, else 5m;
  subagents 5m unless configured). SV5 replays the whole history under each of the 4 main × subagent combinations and prints
  the total bill for each. Cite its break-even line as the evidence: pauses of 5–60 min per 100 calls against the user's rate.
  When the margin is marked "a close call", or the last 7 days favour the other lifetime, say so and don't push a change.
- **Saving**: actual total − the cheapest mix's total (`theoretical`).
- **Apply**: `merge_json` `~/.claude/settings.json` value `{"promptCacheTtl": "5m"}` or `{"subagentPromptCacheTtl": "1h"}`; a single subagent type
  can differ with `experimental.cacheTtl` in its agent file (SV5 lists each type).
- If SV5 agrees with the current defaults, don't propose a change: write an insight that the lifetimes already fit.

### keep-awake: don't let the Mac sleep mid-run
- **When**: SV3 has a "Computer went to sleep" row (ME7 shows the errors) and the user is on macOS.
- **Saving**: that row's extra cost (`measured`), plus the cut-off work.
- **Apply**: `write_file` `~/.claude/hooks/claude-usage/keep_awake.sh` from `hooks/keep_awake.sh` (mode 755); `merge_json`
  `~/.claude/settings.json` value `{"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "bash \"$HOME/.claude/hooks/claude-usage/keep_awake.sh\" 7200", "timeout": 10}]}]}}`.
- **Tradeoff**: the Mac stays awake up to 2 h after each prompt (closing the lid on battery still sleeps).

### mcp-connect-first: don't send the first prompt while MCP servers connect
- **When**: CX8/EX3 show a "tool list changed" miss right at session start.
- **Kind**: habit. **Manual**: wait until the startup MCP line settles (or check `/mcp`) before the first prompt; disable
  servers the project doesn't use (unused-mcp-off).

---

## Setup

### unused-skills-off: stop listing skills you never use
- **When**: SV2's table "Every unused item" has skills whose "How to switch it off" is a `skillOverrides` entry (personal,
  synced `anthropic-skills:<name>`, or project skills). Leave items marked built in.
- **Saving**: the sum of those rows' "Saved, all time" and "Per 30 days" (SV2 splits its total by each item's size; `theoretical`).
- **Apply**: `merge_json` `~/.claude/settings.json` value `{"skillOverrides": {"<name>": "off", ...}}` with the names exactly as in
  SV2 (synced skills keep their `anthropic-skills:` prefix). Project skills: one more `merge_json` step into that project's
  `.claude/settings.local.json`. Use `"name-only"` instead of `"off"` for skills used rarely.
- **Verify**: the next session's skill listing (or `/skills`) no longer shows them.

### unused-mcp-off: disconnect MCP servers you never call
- **When**: SV2's table has MCP servers.
- **Apply**: rows whose way to switch off is `"disableClaudeAiConnectors": true` → `merge_json` that into `~/.claude/settings.json`
  (only when no claude.ai connector is used at all). Everything else is manual: `/mcp disable <server>` in each project where
  it's unused (per project, reversible with `/mcp enable`); the Chrome extension's server from `/chrome` (turn off "enabled by
  default"). Never `claude mcp remove`.
- **Saving**: those rows' "Saved, all time" and "Per 30 days" (SV2), plus a tool-list-change miss at session start if EX3/CX8 show one.

### fix-broken-hook: a hook that fails every time
- **When**: EX5 lists failures with "No such file or directory" (or a path from another machine) and config.json shows the
  hook command in a settings file.
- **Apply**: `set_json` on that settings file with the pointer to the command, e.g. `/hooks/UserPromptSubmit/0/hooks/0/command`,
  value = the same command with the correct path. Only if the corrected script exists (check with `ls`). Worktree copies of a
  project often carry their own `.claude/settings.json`: fix each file that has the bad path.
- **Category**: reliability; no saving, but the hook's intended work (e.g. summaries) starts happening again.

### subagent-briefs: prefer a fresh subagent over resuming a big one
- **When**: CX8/SV3 show "Subagent resumed via SendMessage" misses.
- **Saving**: that SV3 row (`measured` extra cost; `upper_bound` of what a fresh brief saves).
- **Apply**: `append_text` `~/.claude/CLAUDE.md` marker `subagent-briefs` content (≤ 3 lines), e.g. "- When a subagent has
  finished and its context is large, start a fresh subagent with a short brief of what changed instead of resuming it with
  SendMessage (resuming re-writes its whole history to the cache)."
- **Tradeoff**: CLAUDE.md loads in every session; keep it short. The fresh subagent may need to re-read some files.
