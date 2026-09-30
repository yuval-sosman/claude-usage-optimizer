# Catalog of optimizations

Each entry: **when** it applies (read the named questions in digest.md), **saving** (where the number comes from), the
**apply** template for optimizations.json, the **manual** steps, the **tradeoff**, and **related**: how it interacts with other
entries. Paths use `~`; apply.py expands them and refuses anything outside the home directory. Never overwrite an existing
`statusLine`, never add a second copy of a hook, skip settings already at the proposed value.

`scripts/candidates.py` evaluates every entry below, in this order (one function per entry, its rule in the docstring;
mcp-connect-first is part of mcp-off-where-unused), against metrics.json and config.json, and writes
`<OUT>/data/candidates.json`: a finished draft for each entry whose rule holds and isn't in place, with the numbers, apply
steps and the **Related** links filled in, and the reason for each entry it skips. Entries marked **(judgment)** come back
with their facts (and a draft when one can be written) for Claude to decide. This file stays the documentation: change a
rule here and in candidates.py together.

## How optimizations interact

Every saving here is measured alone, as if that change were the only one. When you propose two entries whose **Related**
line names each other, link them in `related` on both (same relation) and write the note from each one's side. The same
rules cover pairs the catalog doesn't list:
- **Same cost, two fixes** → `alternative`: the one applied first takes most of the saving. Lead with the one the data
  favours, say in the note when the other is the better pick, and never add their savings.
- **Same setting, different values**, or one undoes what the other relies on → `conflicts`: keep one, or offer them as an
  explicit choice. Two hooks on the same event are fine together (hook groups are appended).
- **Part of the same cost** (one shrinks the contexts, calls or prices the other's saving was computed on) → `overlaps`:
  both are worth doing; name the overlap in each `savings.basis` and don't sum them in the summary.
- **One makes the other work better** → `complements`. **One only works or pays off after the other** → `requires`.

---

## Cost & model choice

### main-model: keep main-thread work on the cheaper model you already use
- **When**: SV6 "Main threads on <current model>" saving > $10. When the `model` setting already names that model or its
  family's alias (`opus`, `opus[1m]`, …), it is a habit (draft `main-model-habit`); when it names another model, or none,
  **(judgment)**: the draft `main-model-<alias>` sets it, or keep it a habit. The problem names only the models pricier than
  the current one (prices.json), and says OV2's split includes subagents.
- **Saving**: SV6 main-thread figure (`theoretical`; same tokens at list prices).
- **Apply** (only if `model` in `~/.claude/settings.json` isn't already that model): `set_json` `~/.claude/settings.json`
  pointer `/model` value `"<alias>"` (e.g. `"opus[1m]"`, `"sonnet"`). Often this is a **habit** instead: the default is already
  right and the spend came from switching up (`/model`) for whole sessions.
- **Manual**: `/model` at the start of a session; switch up only for the hard part, then back; consider `opusplan`.
- **Tradeoff**: a cheaper model can take more turns or do the work worse; judge by task.
- **Related**: effort-default `overlaps` (both cut the same main-thread output cost: SV6 reprices the tokens you had, a lower
  effort cuts the tokens).

### subagent-model: run subagents on Sonnet by default
- **When**: SV6 "Subagents on <model>" > $5 and `env.CLAUDE_CODE_SUBAGENT_MODEL` isn't set (id `subagent-model-<alias>`).
- **Saving**: SV6 subagent figure (`theoretical`).
- **Apply**: `merge_json` `~/.claude/settings.json` value `{"env": {"CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"}}`.
- **Manual**: add the same to settings.json; or give specific agents `model: sonnet` / `model: haiku` in their agent files
  (`~/.claude/agents/<name>.md`), e.g. read-only research agents on Haiku (SV6 "Explore subagents on Haiku").
- **Tradeoff**: subagents that plan or review hard code may need the stronger model; an agent's own `model` field and a
  per-call model still win.
- **Related**: subagent-briefs and cache-ttl-fit (when it changes the subagent lifetime) `overlaps`: SV3's resume misses and
  SV5's subagent cache writes are priced at the model the subagents ran on, so on Sonnet both are worth less.

### effort-default: lower the default effort (only with strong evidence) (judgment)
- **When**: OV4 thinking share > 35% of output, an effort level (`effortLevel`, or a model's in `modelSettings`) is
  `high`/`xhigh`/`max`, and OV3 shows output is at least 10% of cost. Confidence low: effort changes quality. When
  `modelSettings` raise one model above the global level, the draft removes that override (`unset_json`
  `/modelSettings/<model>/effortLevel`, id `effort-<model>-<global level>`, no saving claimed); otherwise it sets medium on
  the global level and on each model's own level that is high or above (a model's level beats the global one).
- **Saving**: a stated fraction of thinking cost (OV3 output cost × OV4 thinking share); mark `upper_bound` and explain.
- **Apply**: `set_json` `~/.claude/settings.json` pointer `/effortLevel` value `"medium"`.
- **Manual**: `/effort high` for hard tasks, back to medium after.
- **Tradeoff**: less thinking on hard problems. Changing effort mid-session can also invalidate the cache.
- **Related**: main-model `overlaps`.

### stop-hook-followup: make Stop-hook follow-up work cheaper (judgment)
- **When**: SV1's "Stop-hook follow-up work" lever > $5. The lever counts only the work a Stop hook set off: EX5's "What Stop
  hooks set off" gives, per hook, its runs, how often Claude went on working afterwards without a new prompt, and that
  work's cost. A hook that sends nothing back (a notification) scores $0, so it never reaches this entry: nothing to read or
  review for it. The draft is generic: make it name the hook's real source and change.
- **Saving**: that lever (`upper_bound`: some of that work is wanted).
- **Apply**: none generic (it's the user's own automation). **Manual**: show which hook (EX5, config hooks) and options:
  run it only when the turn was long or changed files; check `stop_hook_active` so it can't loop; move summarisation out of
  the session (a script calling a cheaper model); lower what it asks Claude to write.

---

## Context

### auto-compact-window: let Claude Code compact at your break-even size
- **When**: SV4 best threshold net saving > $5, and the window below is under ¾ of the model's window (CX5; 1M models
  especially); `autoCompactWindow` (or its env variable) isn't set at or below it, and auto-compact isn't off.
- **Saving**: the net saving of SV4's row at that window (`theoretical`).
- **Apply**: `merge_json` `~/.claude/settings.json` value `{"autoCompactWindow": <W>}`, W = the first SV4 threshold above the best
  one that still saves money (e.g. best 150K → 200000): compaction triggers as usage *approaches* the window, so it lands
  between the two. Id `auto-compact-<W>k`.
- **Manual**: `/config` → auto-compact window, or the settings line above.
- **Tradeoff**: Claude Code recommends its automatic window; compaction drops detail and costs one summary. Risk `medium`.
  Offer **context-guard** as the gentle alternative.
- **Related**: context-guard `alternative` (the same SV4 saving, forced vs a notice: with both, the notice fires just before
  a compaction that happens anyway; pick context-guard when the user wants to decide). stale-cache-guard and big-read-guard
  `overlaps` (see them).

### context-guard: a notice when the context passes the break-even size
- **When**: SV4 best threshold net saving > $5 (the threshold below the model's window); preferred when the user wants to
  decide when to compact. Id `context-guard-<T>k`, T = SV4's best threshold. When the user's own `autoCompactWindow` already
  sits at or below the next SV4 row, **(judgment)**, with no saving claimed: the notice fires just before a compaction that
  happens anyway.
- **Saving**: SV4 (`theoretical`, if acted on).
- **Apply**: `write_file` `~/.claude/hooks/claude-usage/context_guard.py` from `hooks/context_guard.py` (mode 700), then
  `merge_json` `~/.claude/settings.json` value
  `{"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 \"$HOME/.claude/hooks/claude-usage/context_guard.py\" --threshold <T> --step 50000", "timeout": 10}]}]}}`.
- **Tradeoff**: none beyond a line of text; it never blocks.
- **Related**: auto-compact-window `alternative`. statusline-cache `complements` (the status line shows the context size all
  the time; the guard speaks up at the threshold). stale-cache-guard and big-read-guard `overlaps` (see them).

### big-read-guard: steer Claude to targeted reads of large files
- **When**: SV8 saving > $3 (large whole-file reads re-read for the rest of the session; CX3's costliest-item insight and EX8 show which files).
- **Saving**: SV8 (`upper_bound`, assumes a range keeps half).
- **Apply**: `write_file` `~/.claude/hooks/claude-usage/big_read_guard.py` from `hooks/big_read_guard.py` (mode 700);
  `merge_json` `~/.claude/settings.json` value `{"hooks": {"PreToolUse": [{"matcher": "Read", "hooks": [{"type": "command", "command": "python3 \"$HOME/.claude/hooks/claude-usage/big_read_guard.py\" --max-kb <KB>", "timeout": 10}]}]}}`,
  KB = SV8's size threshold at ~4 bytes a token, rounded up to 10 KB (8K tokens → 40), so the guard catches the reads SV8 counts.
- **Tradeoff**: one extra round-trip the first time a big file is needed whole (the retry goes through).
- **Related**: auto-compact-window / context-guard `overlaps` (SV8 counts carrying each big read for the rest of the session;
  compacting earlier drops it from the context too).

### bash-output-cap: smaller inline Bash output (judgment)
- **When**: Bash results are among CX3's three biggest context sources and the digest's EX10 lists Bash outputs over 15,000
  characters (EX10 is hidden: cite CX3 and EX6). No saving is measured, so whether it is worth a setting is a call.
- **Apply**: `merge_json` `~/.claude/settings.json` value `{"bashOutputMaxChars": 15000}` (default 30000; the rest goes to a file
  Claude can read on demand).
- **Tradeoff**: Claude sees less of long logs inline; risk `medium`.

### transcript-retention: keep more history for the next report
- **When**: the digest's period is 25 days or more, close to the 30-day default `cleanupPeriodDays`, and it isn't set higher.
- **Apply**: `merge_json` `~/.claude/settings.json` value `{"cleanupPeriodDays": 90}`. No saving; better analysis.

---

## Caching

### stale-cache-guard: stop the first prompt into an expired, big session
- **When**: SV7 saving > $2, or CX8's costliest misses are "came back after a break".
- **Saving**: SV7 (`upper_bound`) or the "You came back after a break" row of SV3 (`measured` extra cost).
- **Apply**: `write_file` `~/.claude/hooks/claude-usage/stale_cache_guard.py` from `hooks/stale_cache_guard.py` (mode 700);
  `merge_json` `~/.claude/settings.json` value
  `{"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 \"$HOME/.claude/hooks/claude-usage/stale_cache_guard.py\" --min-context <N> --grace 180", "timeout": 10}]}]}}`.
  N = SV7's "A fresh session starts at" rounded up to 10K, at least 60000: a fresh start only saves once the context is past
  that size (start-up + 15K), so smaller sessions are never stopped.
- **Manual**: after a break over the cache lifetime with a big context, `/clear` and paste a short summary (or `/compact` first).
- **Tradeoff**: one extra Enter when you really do want to continue.
- **Related**:
  - cache-ttl-fit, when it moves the main thread to 1h: `alternative`. Both go after the same misses, the returns after a
    break. The 1h lifetime keeps the cache warm through pauses of up to an hour but makes every cache write cost 2× input
    instead of 1.25×. The guard lets the cache expire and stops the one prompt that would re-write a big context. With 1h
    in place the guard only fires after pauses over an hour (it reads the lifetime from the transcript), so SV7's saving,
    computed at today's lifetime, mostly goes. With the guard in place, the 5–60 min returns that SV5 counts as 1h hits
    become fresh starts. Pick by SV5's break-even line: pauses you mostly continue through → 1h; longer breaks, or the user
    would rather restart small → the guard. Propose one; mention the other in the note.
  - cache-ttl-fit, when it moves the main thread to 5m: `complements` (5m lets more returns expire; the guard stops the
    costly ones, which softens the switch's downside).
  - statusline-cache `complements` (the countdown warns before you type; the guard catches it when you don't look).
  - auto-compact-window / context-guard `overlaps` (SV7 is priced at the context sizes you had; compacting shrinks them).

### statusline-cache: see context size and cache warmth all the time
- **When**: no `statusLine` in any settings file (config.json).
- **Apply**: `write_file` `~/.claude/hooks/claude-usage/statusline.py` from `hooks/statusline.py` (mode 700); `merge_json`
  `~/.claude/settings.json` value `{"statusLine": {"type": "command", "command": "python3 \"$HOME/.claude/hooks/claude-usage/statusline.py\""}}`.
- **Saving**: none directly (awareness); omit `savings`.
- **Related**: stale-cache-guard and context-guard `complements`.

### cache-ttl-fit: choose the cache lifetime per thread kind
- **When**: SV5's "Cheapest mix" differs from the actual mix and saves > $3 (current: main 1h on a subscription, else 5m;
  subagents 5m unless configured). SV5 replays the whole history under each of the 4 main × subagent combinations and prints
  the total bill for each. Cite its break-even line as the evidence: pauses of 5–60 min per 100 calls against the user's rate.
  When the margin is under 5% ("a close call"), or the last 7 days favour the other lifetime, it is **(judgment)**: say so
  and don't push a change.
- **Saving**: actual total − the cheapest mix's total (`theoretical`).
- **Apply**: `merge_json` `~/.claude/settings.json` value `{"promptCacheTtl": "5m"}` or `{"subagentPromptCacheTtl": "1h"}`, plus
  (as in unused-listings-off) the other lifetime key when an applied earlier version set it; a single subagent type
  can differ with `experimental.cacheTtl` in its agent file (SV5 lists each type).
- If SV5 agrees with the current defaults, don't propose a change: write an insight that the lifetimes already fit.
- **Related**: stale-cache-guard: `alternative` when the main thread goes to 1h, `complements` when it goes to 5m (see
  stale-cache-guard). subagent-model `overlaps` when the subagent lifetime changes.

### keep-awake: don't let the Mac sleep mid-run
- **When**: SV3 has a "Computer went to sleep" row (ME7 shows the errors) and the user is on macOS (config.json's home is
  under `/Users/`).
- **Saving**: that row's extra cost (`measured`), plus the cut-off work.
- **Apply**: `write_file` `~/.claude/hooks/claude-usage/keep_awake.sh` from `hooks/keep_awake.sh` (mode 700); `merge_json`
  `~/.claude/settings.json` value `{"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "bash \"$HOME/.claude/hooks/claude-usage/keep_awake.sh\" 7200", "timeout": 10}]}]}}`.
- **Tradeoff**: the Mac stays awake up to 2 h after each prompt (closing the lid on battery still sleeps).

### mcp-connect-first: don't send the first prompt while MCP servers connect
- **When**: SV3 has "Tool list changed (MCP/tools)" misses. Folded into mcp-off-where-unused as a manual step when that entry
  applies; alone (a habit) only when no MCP server is unused, and then **(judgment)**: EX3 shows whether the changes came right
  at session start.
- **Kind**: habit. **Manual**: wait until the startup MCP line settles (or check `/mcp`) before the first prompt; keep
  servers a project doesn't use out of it (mcp-off-where-unused, scope-where-used).

---

## Setup

### unused-listings-off: stop listing skills and connectors you never use
- **When**: SV2's table "Every unused item" has rows a user setting switches off: skills whose "How to switch it off" is a
  `skillOverrides` entry (personal, or synced `anthropic-skills:<name>`), and `"disableClaudeAiConnectors": true` rows (only
  when EX2 shows no claude.ai connector used at all). Leave items marked built in. Project skills are left out (the draft's
  notes name them): they belong to the project; add one more `merge_json` into that project's `.claude/settings.local.json`
  only when the user doesn't use them there.
- **Saving**: the sum of those rows' "Saved, all time" (SV2 splits its total by each item's size; `theoretical`).
- **Apply**: `merge_json` `~/.claude/settings.json` value `{"skillOverrides": {"<name>": "off", ...}, "disableClaudeAiConnectors": true}`
  with the names exactly as in SV2 (synced skills keep their `anthropic-skills:` prefix). Use `"name-only"` instead of `"off"`
  for skills used rarely. When a newer version replaces an applied one (apply.py undoes the old version first), assemble.py
  adds back what the applied version switched off (read from apply.py's record, and only where settings.json still holds
  it), so nothing comes back on; the user's own entries are never copied into the step.
- **Verify**: the next session's skill listing (or `/skills`) no longer shows them.

### mcp-off-where-unused: stop loading MCP servers you never call
- **When**: SV2's "Every unused item" table (all projects) has MCP servers no project uses: one in the user MCP config, or the
  Chrome extension's (`/chrome`).
- **Apply**: none: `claude mcp get <server>` and keep what it prints, then `claude mcp remove <server> -s user`; a project that
  later needs it gets it there alone (`claude mcp add … -s local`, or its `.mcp.json`). The Chrome extension's server:
  `/chrome`, turn off "enabled by default". Never "`/mcp disable` in each project": switching a server off and on per
  project is the chore this avoids. When SV3 has tool-list-change misses, add mcp-connect-first's habit as a step. The
  draft's notes name servers EX2 shows nearly unused.
- **Saving**: those rows' "Saved, all time" (SV2). The tool-list-change misses (SV3) could shrink too, but the transcripts don't
  say which server changed, so they are not counted.

### scope-where-used: load an MCP server, skill or plugin only where it is used (judgment)
- **When**: SV2's "Used in some projects, loaded in every one" table (all projects) adds up to $1 or more: a user-level MCP
  server, personal skill (`~/.claude/skills`) or plugin enabled in `~/.claude/settings.json` that some projects use and
  others only load.
- **Apply**: none (by hand), a one-time move instead of switching it off and on per project:
  - MCP server: `claude mcp get <server>` (keep the definition); in each project that uses it, `claude mcp add … -s local`
    (just you) or its `.mcp.json` (the team too); then `claude mcp remove <server> -s user`.
  - Plugin: `"enabledPlugins": {"<plugin>@<marketplace>": true}` in those projects' `.claude/settings.local.json` (just you)
    or `.claude/settings.json` (the team too), then `false` in `~/.claude/settings.json` (a project-level true wins).
  - Personal skill: move `~/.claude/skills/<name>` into each using project's `.claude/skills/`.
- **Judgment**: where each goes. The local scope and `settings.local.json` are personal but reach neither teammates nor task
  worktrees (`.claude/worktrees/…`, a fact names projects that have them); the shared `.mcp.json` / `.claude/settings.json`
  do, and are checked in.
- **Saving**: those rows' "Saved, all time" (`theoretical`: each item's listing, re-read on every main-thread call of the
  sessions in projects that never used it).
- **Verify**: the next report's SV2 no longer lists them in that table.

### fix-broken-hook: a hook that fails every time (judgment)
- **When**: EX5 lists failures with "No such file or directory" (or a path from another machine) and config.json shows the
  hook command in a settings file (with `~` and `$HOME` spelled out as the report's home; by the script's name only when the
  command builds its path from another variable). The facts give each file and the JSON pointer of the command; which path
  is right is yours to check.
- **Apply**: `set_json` on that settings file with the pointer to the command, e.g. `/hooks/UserPromptSubmit/0/hooks/0/command`,
  value = the same command with the correct path. Only if the corrected script exists (check with `ls`). Worktree copies of a
  project often carry their own `.claude/settings.json`: fix each file that has the bad path.
- **Category**: reliability; no saving, but the hook's intended work (e.g. summaries) starts happening again.

### subagent-briefs: prefer a fresh subagent over resuming a big one
- **When**: SV3's "Subagent resumed via SendMessage" row is worth $1 or more.
- **Saving**: that SV3 row (`measured` extra cost; `upper_bound` of what a fresh brief saves).
- **Apply**: `append_text` `~/.claude/CLAUDE.md` marker `subagent-briefs` content (≤ 3 lines): "- When a finished subagent has
  a large context (over ~100K), start a fresh subagent with a short brief of what changed and what to check instead of
  resuming it with SendMessage: resuming re-writes its whole history to the cache."
- **Tradeoff**: CLAUDE.md loads in every session; keep it short. The fresh subagent may need to re-read some files.
- **Related**: subagent-model `overlaps`.
