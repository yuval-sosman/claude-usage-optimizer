# Catalog of optimizations

Each entry: **when** it applies (read the named questions in digest.md), **saving** (where the number comes from), the
**apply** template for optimizations.json, the **manual** steps, the **tradeoff**, and **related**: how it interacts with other
entries. Paths use `~`; apply.py expands them and refuses anything outside the home directory. Never overwrite an existing
`statusLine`, never add a second copy of a hook, skip settings already at the proposed value.

Every entry follows Claude Code's documented best practice ([best-practices.md](best-practices.md)): the report's numbers
decide whether an entry applies and how big it is, the guidance decides what the change is. That is why compaction is capped
at 400K (never under 300K) beside a notice, why effort goes back to the model's default rather than below it, and why a skill
used rarely is listed by name rather than switched off. validate.py (which assemble runs) refuses the changes that guidance
rules out: an auto-compact window under 300K, a saved `low` effort, thinking off, a context notice under 100K.

A draft with `first` (the cache-lifetime pin, whenever SV6 supports it) leads the Optimizations tab in **Start here**, ahead
of the categories. At most 3 optimizations carry it: keep it for quick, low-risk changes worth making before the rest.

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
- **When**: SV7 "Main threads on <current model>" saving > $10. When the `model` setting already names that model or its
  family's alias (`opus`, `opus[1m]`, …), it is a habit (draft `main-model-habit`); when it names another model, or none,
  **(judgment)**: the draft `main-model-<alias>` sets it, or keep it a habit. The problem names only the models pricier than
  the current one (prices.json), and says OV2's split includes subagents.
- **Saving**: SV7 main-thread figure (`theoretical`; same tokens at list prices: a ceiling, not a forecast).
- **Apply** (only if `model` in `~/.claude/settings.json` isn't already that model): `set_json` `~/.claude/settings.json`
  pointer `/model` value `"<alias>"` (e.g. `"opus[1m]"`, `"sonnet"`). Often this is a **habit** instead: the default is already
  right and the spend came from switching up (`/model`) for whole sessions.
- **Manual**: `/model` at the start of a session; switch up only for the hard part, then back at a natural break (a switch
  re-writes the cache); consider `opusplan`. The docs: "Sonnet handles most coding tasks well … Reserve Opus for complex
  architectural decisions or multi-step reasoning."
- **Tradeoff**: a cheaper model can take more turns or do the work worse; judge by task.
- **Related**: effort-default `overlaps` (both cut the same main-thread output cost: SV7 reprices the tokens you had, a lower
  effort cuts the tokens).

### subagent-model: run subagents that name no model on Sonnet by default
- **When**: SV7 "Via CLAUDE_CODE_SUBAGENT_MODEL" > $5 and `env.CLAUDE_CODE_SUBAGENT_MODEL` isn't set (id
  `subagent-model-<alias>`). That figure counts only the subagents the variable moves: no model passed for the call and none
  in their definition (general-purpose, agent files without `model`). Explore and Plan are `model: inherit` and keep the main
  model; SV7 "Subagents on <model>" (every subagent) is the ceiling, reachable only with the force switch below.
- **Saving**: SV7 "Via CLAUDE_CODE_SUBAGENT_MODEL" (`theoretical`).
- **Apply**: `merge_json` `~/.claude/settings.json` value `{"env": {"CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"}}`.
- **Manual**: add the same to settings.json; for Explore, a user agent file named `Explore` with `model: haiku` replaces the
  built-in (prompt and tools too: the docs' way to keep exploration cheap; SV7 "Explore subagents on Haiku"). To move every
  subagent, `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` by hand, as a stated choice, offered only when it would move pricier
  subagents and Explore isn't already on a cheaper model: Claude can then no longer pick a stronger model for one call, and
  agent files' own models are ignored, so an agent pinned to a cheaper model moves up too.
- **Tradeoff**: subagents that plan or review hard code may need the stronger model: ask for it in the request (a per-call
  model still wins).
- **Related**: subagent-briefs and cache-ttl-fit (when it changes the subagent lifetime) `overlaps`: SV4's resume misses and
  SV6's subagent cache writes are priced at the model the subagents ran on, so on Sonnet both are worth less.

### effort-default: effort at the model's documented default
- **When** (draft, id `effort-<model>-default`): the level settings keep for the model you use now (SV7's current model: its
  `modelSettings` entry, else `effortLevel`) is above that model's documented default (candidates.EFFORT_DEFAULT: `medium` on
  Opus 5.5 and Sonnet 5.5, `xhigh` on Opus 4.7, `high` on others). The docs: "When you move from Opus 5 to Opus 5.5, start at
  `medium` rather than carrying over the level you used on Opus 5." The draft removes those keys (`unset_json`), so every
  model starts at its own default; no saving claimed.
- **Otherwise (judgment)**: OV4 thinking share > 35% of output, a level set to `high`/`xhigh` at or below the model's
  default, and OV3 shows output is at least 10% of cost. Going below the default trades quality for cost: propose it only
  with strong evidence. When `modelSettings` raise one model above the global level, the draft removes that override
  (`unset_json` `/modelSettings/<model>/effortLevel`, id `effort-<model>-<global level>`); otherwise it sets medium on the
  global level and on each model's own level that is high or above.
- **Saving**: none claimed for the default; for going lower, a stated fraction of thinking cost (OV3 output cost × OV4
  thinking share), `upper_bound`. OV4's per-call cost by level describes which tasks ran at each level: never cite it as a saving.
- **Manual**: `/effort high` (or `xhigh`) for a hard task, back after. Never a saved `low` (validate.py refuses it).
- **Tradeoff**: less thinking on hard problems unless raised for them. Changing effort mid-session re-writes the cache,
  except on Opus 5.5, Sonnet 5.5 and Fable 5.1 (API key or subscription).
- **Related**: main-model `overlaps`.

### stop-hook-followup: make Stop-hook follow-up work cheaper (judgment)
- **When**: SV2's "Stop-hook follow-up work" lever > $5. The lever counts only the work a Stop hook set off: EX5's "What Stop
  hooks set off" gives, per hook, its runs, how often Claude went on working afterwards without a new prompt, and that
  work's cost. A hook that sends nothing back (a notification) scores $0, so it never reaches this entry: nothing to read or
  review for it. The draft is generic: make it name the hook's real source and change.
- **Saving**: that lever (`upper_bound`: some of that work is wanted).
- **Apply**: none generic (it's the user's own automation). **Manual**: show which hook (EX5, config hooks) and options:
  run it only when the turn was long or changed files; check `stop_hook_active` so it can't loop; move summarisation out of
  the session (a script calling a cheaper model); lower what it asks Claude to write.

---

## Context

### auto-compact-window: cap auto-compact on a 1M-context model
- **When**: the model's window (CX5) is over 300K: a native 1M model compacts at about 967K by default. SV5 best threshold net
  saving > $5, and SV5's 400K row still saves money (else its 300K row); `autoCompactWindow` (or its env variable) isn't set
  at or below it, and auto-compact isn't off. **Never on a 200K model**: auto already compacts near 200K, the window Claude
  Code tunes and "strongly recommended for the best cost and performance"; there SV5 is acted on with /compact at natural
  breaks and context-guard.
- **Saving**: the net saving of SV5's row at that window (`theoretical`).
- **Apply**: `merge_json` `~/.claude/settings.json` value `{"autoCompactWindow": 400000}` (300000 when SV5's 400K row saves
  nothing): a cap high enough that a long task keeps its room, whatever SV5's best threshold is (compaction triggers as usage
  *approaches* the window). Never under 300K: validate.py refuses less. Id `auto-compact-<W>k`.
- **Manual**: `/autocompact 400k` (saves the setting and applies to the current session; `/autocompact auto` resets), and keep
  compacting yourself at natural breaks with what to keep.
- **Tradeoff**: Claude Code recommends its auto window and warns "Overriding auto may result in high token usage, especially
  when resuming long sessions"; a compaction drops detail and can land mid-task. Risk `medium`.
- **Related**: context-guard `overlaps` (the notice asks for a /compact at a natural break from SV5's threshold; the cap is
  the backstop for runs no one watches, such as a /goal: both cut big-context re-reads, so never add their savings).
  stale-cache-guard and big-read-guard `overlaps` (see them).

### context-guard: a notice when the context passes the break-even size
- **When**: SV5 best threshold net saving > $5 (the threshold below the model's window; SV5 starts at 100K, so the notice
  never fires below it, and validate.py refuses less); preferred when the user wants to decide when to compact, and the only
  setting-level way to act on SV5 for a 200K model. Id `context-guard-<T>k`, T = SV5's best threshold. When the user's own
  `autoCompactWindow` already sits at or below the next SV5 row, **(judgment)**, with no saving claimed: the notice fires just
  before a compaction that happens anyway.
- **Saving**: SV5 (`theoretical`, if acted on).
- **Apply**: `write_file` `~/.claude/hooks/claude-usage/context_guard.py` from `hooks/context_guard.py` (mode 700), then
  `merge_json` `~/.claude/settings.json` value
  `{"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 \"$HOME/.claude/hooks/claude-usage/context_guard.py\" --threshold <T> --step 50000", "timeout": 10}]}]}}`.
- **Tradeoff**: none beyond a line of text; it never blocks. The notice says what the docs advise: at the next natural break,
  /clear before unrelated work, /compact with what to keep otherwise.
- **Related**: auto-compact-window `overlaps` (see it). statusline-cache `complements` (the status line shows the context size
  all the time; the guard speaks up at the threshold). stale-cache-guard and big-read-guard `overlaps` (see them).

### big-read-guard: steer Claude to targeted reads of large files
- **When**: SV9 saving > $3 (large whole-file reads re-read for the rest of the session; CX3's costliest-item insight and EX8 show which files).
- **Saving**: SV9 (`upper_bound`, assumes a range keeps half).
- **Apply**: `write_file` `~/.claude/hooks/claude-usage/big_read_guard.py` from `hooks/big_read_guard.py` (mode 700);
  `merge_json` `~/.claude/settings.json` value `{"hooks": {"PreToolUse": [{"matcher": "Read", "hooks": [{"type": "command", "command": "python3 \"$HOME/.claude/hooks/claude-usage/big_read_guard.py\" --max-kb <KB>", "timeout": 10}]}]}}`,
  KB = SV9's size threshold at ~4 bytes a token, rounded up to 10 KB (8K tokens → 40), so the guard catches the reads SV9 counts.
- **Tradeoff**: one extra round-trip the first time a big file is needed whole (the retry goes through).
- **Related**: auto-compact-window / context-guard `overlaps` (SV9 counts carrying each big read for the rest of the session;
  compacting earlier drops it from the context too). code-intelligence `complements`.

### code-intelligence: a language server for the language Claude reads most
- **When**: EX8's most-read files include 30 or more reads of one language that has an official code intelligence plugin
  (candidates.LSP_PLUGINS, from the docs' table: C/C++, C#, Go, Java, Kotlin, Lua, PHP, Python, Ruby, Rust, Swift,
  TypeScript/JavaScript), and no plugin of that name is installed (EX4) or enabled in a settings file. Id `install-<plugin>`.
- **Saving**: none measured. The docs list it under reducing token usage: "precise symbol navigation instead of text-based
  search, reducing unnecessary file reads".
- **Apply**: none (a plugin install and a language server binary are the user's to run). **Manual**: install the language
  server binary (the plugin's README), then `/plugin install <plugin>@claude-plugins-official`, then a new session.
- **Tradeoff**: the server uses memory while it indexes; diagnostics after edits add a little context (EX4).
- **Related**: big-read-guard `complements`.

### bash-output-cap: smaller inline Bash output (judgment)
- **When**: Bash results are among CX3's three biggest context sources and the digest's EX10 lists Bash outputs over 15,000
  characters (EX10 is hidden: cite CX3 and EX6). No saving is measured, so whether it is worth a setting is a call. The docs'
  first answers are narrower: a hook that filters a noisy command's output (a test run down to its failures), or running
  verbose commands in a subagent.
- **Apply**: `merge_json` `~/.claude/settings.json` value `{"bashOutputMaxChars": 15000}` (default 30000, clamped to
  4000–128000; the rest goes to a file Claude can read on demand).
- **Tradeoff**: Claude sees less of long logs inline; risk `medium`.

### transcript-retention: keep more history for the next report
- **When**: the digest's period is 25 days or more, close to the 30-day default `cleanupPeriodDays`, and it isn't set higher.
- **Apply**: `merge_json` `~/.claude/settings.json` value `{"cleanupPeriodDays": 90}`. No saving; better analysis.
- **Tradeoff**: more disk, and prompts, code and command output stay on the machine longer (`claude project purge` deletes a
  project's early).

---

## Caching

### stale-cache-guard: stop the first prompt into an expired, big session
- **When**: SV8 saving > $2, or CX8's costliest misses are "came back after a break".
- **Saving**: SV8 (`upper_bound`) or the "You came back after a break" row of SV4 (`measured` extra cost).
- **Apply**: `write_file` `~/.claude/hooks/claude-usage/stale_cache_guard.py` from `hooks/stale_cache_guard.py` (mode 700);
  `merge_json` `~/.claude/settings.json` value
  `{"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 \"$HOME/.claude/hooks/claude-usage/stale_cache_guard.py\" --min-context <N> --grace 180", "timeout": 10}]}]}}`.
  N = SV8's "A fresh session starts at" rounded up to 10K, at least 60000: a fresh start only saves once the context is past
  that size (start-up + 15K), so smaller sessions are never stopped.
- **Manual**: before a long break, `/compact` while the cache is warm (cheap then). Back at a big, expired session, cheapest
  first: `/clear` (free) and start from a short note; `/compact` (reads the whole context once, uncached, then carries only
  the summary); continuing (re-writes it all). On Pro and Max, `--resume` of a session idle over an hour and over 100K offers
  "Resume from summary" by itself.
- **Tradeoff**: one extra Enter when you really do want to continue.
- **Related**:
  - cache-ttl-fit, when it moves the main thread to 1h: `alternative`. Both go after the same misses, the returns after a
    break. The 1h lifetime keeps the cache warm through pauses of up to an hour but makes every cache write cost 2× input
    instead of 1.25×. The guard lets the cache expire and stops the one prompt that would re-write a big context. With 1h
    in place the guard only fires after pauses over an hour (it reads the lifetime from the transcript), so SV8's saving,
    computed at today's lifetime, mostly goes. With the guard in place, the 5–60 min returns that SV6 counts as 1h hits
    become fresh starts. Pick by SV6's break-even line: pauses you mostly continue through → 1h; longer breaks, or the user
    would rather restart small → the guard. Propose one; mention the other in the note.
  - cache-ttl-fit, when it moves the main thread to 5m: `complements` (5m lets more returns expire; the guard stops the
    costly ones, which softens the switch's downside).
  - cache-ttl-fit, when it only pins the 1h the main thread already ran on: `complements` (the pin keeps pauses under an
    hour warm; the guard stops the costly first prompt after longer ones).
  - statusline-cache `complements` (the countdown warns before you type; the guard catches it when you don't look).
  - auto-compact-window / context-guard `overlaps` (SV8 is priced at the context sizes you had; compacting shrinks them).

### statusline-cache: see context size and cache warmth all the time
- **When**: no `statusLine` in any settings file (config.json). The docs: "Track context usage continuously with a custom
  status line."
- **Apply**: `write_file` `~/.claude/hooks/claude-usage/statusline.py` from `hooks/statusline.py` (mode 700); `merge_json`
  `~/.claude/settings.json` value `{"statusLine": {"type": "command", "command": "python3 \"$HOME/.claude/hooks/claude-usage/statusline.py\""}}`.
- **Saving**: none directly (awareness); omit `savings`.
- **Related**: stale-cache-guard and context-guard `complements`.

### cache-ttl-fit: 1 hour for the main thread, 5 minutes for subagents
- **When SV6 favours main 1 hour and subagents 5 minutes** (its per-kind cheaper lifetime; the usual result for interactive
  work): pin that mix, `promptCacheTtl` `"1h"` and `subagentPromptCacheTtl` `"5m"`, whichever isn't already set (in settings,
  or by `CLAUDE_CODE_PROMPT_CACHE_TTL` / `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`, which win over them). A standard early
  recommendation, marked `first`: unset, the main lifetime is automatic, 1 hour only on a subscription within its usage
  limits and 5 minutes on an API key, Bedrock, Vertex or Foundry, and `ENABLE_PROMPT_CACHING_1H` would move subagents to 1
  hour. A close margin is fine: SV6 still favours it. An env variable that overrides a setting, or
  `FORCE_PROMPT_CACHING_5M`, goes into the notes and a manual step to remove it.
- **When SV6 favours another mix**: its cheapest mix differs from the actual one and saves > $3. When the margin is under 5%
  ("a close call"), or the last 7 days favour the other lifetime, it is **(judgment)**: say so and don't push a change.
- **Saving**: actual total − SV6's cheapest mix (`theoretical`) when the actual mix differs; none when the history already
  ran on the pinned mix (the problem gives SV6's per-kind margins instead).
- **Tradeoff**: a 1-hour write costs 2× input instead of 1.25×, and pinned, the main thread writes 1-hour entries past a
  subscription's usage limits too. Low risk, not free: never call it "no downside".
- **Apply**: `merge_json` `~/.claude/settings.json` with the keys to set, plus (as in unused-listings-off) the other lifetime
  key when an applied earlier version set it; a single subagent type can differ with `experimental.cacheTtl` in its agent
  file (SV6 lists each type).
- **Related**: stale-cache-guard: `alternative` when the main thread moves to 1h, `complements` when it moves to 5m or is only
  pinned at the 1h it already had (see stale-cache-guard). subagent-model `overlaps` when the subagent lifetime changes.

### keep-awake: don't let the Mac sleep mid-run
- **When**: SV4 has a "Computer went to sleep" row (ME7 shows the errors) and the user is on macOS (config.json's home is
  under `/Users/`).
- **Saving**: that row's extra cost (`measured`), plus the cut-off work.
- **Apply**: `write_file` `~/.claude/hooks/claude-usage/keep_awake.sh` from `hooks/keep_awake.sh` (mode 700); `merge_json`
  `~/.claude/settings.json` value `{"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "bash \"$HOME/.claude/hooks/claude-usage/keep_awake.sh\" 7200", "timeout": 10}]}]}}`.
- **Tradeoff**: the Mac stays awake up to 2 h after each prompt (closing the lid on battery still sleeps).

### mcp-connect-first: don't send the first prompt while MCP servers connect
- **When**: SV4 has "Tool list changed (MCP/tools)" misses. Folded into mcp-off-where-unused as a manual step when that entry
  applies; alone (a habit) only when no MCP server is unused, and then **(judgment)**: EX3 shows whether the changes came right
  at session start. With MCP tool search (the default on supported models) a late connection no longer changes the cached
  tool list, so check that the misses are recent.
- **Kind**: habit. **Manual**: wait until the startup MCP line settles (or check `/mcp`) before the first prompt; keep
  servers a project doesn't use out of it (mcp-off-where-unused, scope-where-used).

---

## Setup

### unused-listings-off: list unused skills by name only, stop unused connectors
- **When**: SV3's table "Every unused item" has rows a user setting switches off, worth $1 or more together: skills whose
  "How to switch it off" is a `skillOverrides` entry (personal, or synced `anthropic-skills:<name>`), and
  `"disableClaudeAiConnectors": true` rows (only when EX2 shows no claude.ai connector used at all). Leave items marked built
  in. Project skills are left out (the draft's notes name them): they belong to the project; add one more `merge_json` into
  that project's `.claude/settings.local.json` only when the user doesn't use them there.
- **Saving**: the sum of those rows' "Saved, all time", less the few tokens each name still takes (SV3 splits its total by
  each item's size; `theoretical`).
- **Apply**: `merge_json` `~/.claude/settings.json` value `{"skillOverrides": {"<name>": "name-only", ...}, "disableClaudeAiConnectors": true}`
  with the names exactly as in SV3 (synced skills keep their `anthropic-skills:` prefix). `"name-only"` is the lighter option
  for skills used rarely: the name stays listed (Claude can still use it when asked, `/name` still runs it), the description
  goes. Use `"off"` only for a skill the user never wants offered. When a newer version replaces an applied one (apply.py
  undoes the old version first), assemble.py adds back what the applied version switched off (read from apply.py's record,
  and only where settings.json still holds it), so nothing comes back on; the user's own entries are never copied into the step.
- **Verify**: the next session's `/context` lists them at a few tokens each.

### mcp-off-where-unused: stop loading MCP servers you never call
- **When**: SV3's "Every unused item" table (all projects) has MCP servers no project uses: one in the user MCP config, or the
  Chrome extension's (`/chrome`).
- **Apply**: none: `claude mcp get <server>` and keep what it prints, then `claude mcp remove <server> -s user`; a project that
  later needs it gets it there alone (`claude mcp add … -s local`, or its `.mcp.json`). The Chrome extension's server:
  `/chrome`, turn off "enabled by default". Never "`/mcp disable` in each project": switching a server off and on per
  project is the chore this avoids. When SV4 has tool-list-change misses, add mcp-connect-first's habit as a step. The
  draft's notes name servers EX2 shows nearly unused.
- **Saving**: those rows' "Saved, all time" (SV3). The tool-list-change misses (SV4) could shrink too, but the transcripts don't
  say which server changed, so they are not counted.

### scope-where-used: load an MCP server, skill or plugin only where it is used (judgment)
- **When**: SV3's "Used in some projects, loaded in every one" table (all projects) adds up to $1 or more: a user-level MCP
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
- **Verify**: the next report's SV3 no longer lists them in that table.

### claude-md-length: a CLAUDE.md over the docs' 200 lines (judgment)
- **When**: CX5's memory files (what a new session loads now, with each file's lines) include a CLAUDE.md over 200 lines. The
  docs: "Aim to keep CLAUDE.md under 200 lines by including only essentials", and each line should pass "Would removing this
  cause Claude to make mistakes?"
- **Apply**: none (the user edits the file). **Manual**: cut what Claude can learn from the code; move one workflow's
  instructions into a skill (loads on demand); reference material Claude needs only sometimes can go in a file CLAUDE.md names
  by path (an `@import` still loads at start); `/doctor` proposes cuts for a checked-in CLAUDE.md.
- **Judgment**: propose it when the file mixes rarely needed detail with essentials; when it is a deliberately detailed guide
  the user keeps for Claude, say so in an insight instead. The skill never reads the file (it is outside the report folder):
  say what kind of content to move and let the user pick the sections. No saving claimed (the facts give the file's share of
  the start-up context and its cost per 100 calls).

### fix-broken-hook: a hook that fails every time (judgment)
- **When**: EX5 lists failures with "No such file or directory" (or a path from another machine) and config.json shows the
  hook command in a settings file (with `~` and `$HOME` spelled out as the report's home; by the script's name only when the
  command builds its path from another variable). The facts give each file and the JSON pointer of the command; which path
  is right is yours to check.
- **Apply**: `set_json` on that settings file with the pointer to the command, e.g. `/hooks/UserPromptSubmit/0/hooks/0/command`,
  value = the same command with the correct path. Only if the corrected script exists (check with `ls`). Worktree copies of a
  project often carry their own `.claude/settings.json`: fix each file that has the bad path.
- **Category**: reliability; no saving, but the hook's intended work (e.g. summaries) starts happening again.

### subagent-briefs: prefer a fresh subagent over a late resume of a big one
- **When**: SV4's "Subagent resumed via SendMessage" row is worth $1 or more.
- **Saving**: that SV4 row (`upper_bound`: some follow-ups need the old context, and the fresh subagent's own start, CX11, is
  not subtracted).
- **Apply**: `append_text` `~/.claude/CLAUDE.md` marker `subagent-briefs` content (1 line): "- To follow up on a finished
  subagent that is large (over ~100K tokens) and has sat idle for more than a few minutes, start a fresh subagent with a short
  brief of what changed and what to check instead of resuming it with SendMessage: its cache has expired, so a resume
  re-writes its whole history." (A resume within the subagent's cache lifetime, 5 minutes by default, reads the cache the
  original run warmed.)
- **Tradeoff**: CLAUDE.md loads in every session; keep it to this one line. The fresh subagent may need to re-read some files.
- **Related**: subagent-model `overlaps`.
