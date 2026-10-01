# Questions the usage script should answer

63 questions about your Claude Code usage. The claude-usage plugin (`/claude-usage:report`, or `usage_report.py --open` for the numbers alone) answers all of them in an HTML report, one card per question with the same IDs. Claude then writes the report's Insights and Optimizations tabs from those answers. Each one was tested against your own logs (2026-09-06 → 09-24,
91 main sessions, 64 subagent transcripts). Every question passed three tests:

1. **It can be answered by counting.** Only `jq`, `awk`, `grep` or plain code, no LLM judgment.
2. **It's worth answering.** It says something about cost, efficiency or habits that you could act on or wouldn't otherwise know.
3. **It isn't a duplicate.** Questions that overlapped were merged.

The **Check** line is what that test found on your data, so you can see each question gives a real answer.
Dollar figures are *API list-price equivalent*: tokens × `prices.json`. On a subscription that's a yardstick for
comparing things, not your bill. Catalog IDs in brackets (e.g. `[B06]`) point to extraction commands in `CATALOG.md`.

**Scope tags** show which views the script should produce: `T` total · `P` per project · `S` per session ·
`A` per subagent · `W` per day/week. Per-project views should offer two groupings: the raw `projects/` directories, and
repos with their `--claude-worktrees-*` dirs folded in.

---

## Shared definitions (implement once, reuse everywhere)

These were settled while testing the questions. Several are traps if done naively.

| Term | Definition |
|---|---|
| **API call** | Unique `message.id + requestId`. Keep the line with the most `output_tokens`, and drop model `<synthetic>`, which is a client-side error message. 8,939 assistant lines collapse to about 4,660 calls. |
| **Thread** | `(sessionId, agentId)`. The main thread has no `agentId`. Order calls by the timestamp of their first line. |
| **Human prompt** | A `user` line with `promptSource` = `typed` or `queued`, plus `queued_command` attachments where `commandMode` = `prompt`. Count these separately: slash commands (`<command-name>`), `<task-notification>`s, `sdk` prompts, and `isMeta` lines. |
| **Turn** | From one human prompt or slash command to the next. `system/turn_duration` gives Claude's busy time for the turn. |
| **Context (ctx)** | `input + cache_read + cache_creation` for one call. |
| **Request start / gap** | Start = the later of (the previous call's last line, the last user/attachment line before this call). Gap = time from one call's start to the next call's start in the same thread. Idle = the previous call's last line → this call's start; the cache TTL is counted from there (see SV6). |
| **Cache hit rate** | Σ cache_read ÷ Σ ctx. |
| **Re-written tokens** | `max(0, min(ctx(previous call) − cache_read − input, cache_creation))` in the same thread, and 0 when a `compact_boundary` falls between the two calls (compaction replaces the conversation, so nothing after it is a re-write). A **miss** is any call where this is above 1,000. Label each miss with `diagnostics.cache_miss_reason.type` when present, but take the token count from this formula. Checked against the 25 logged diagnostics: their own token figure runs 10–25% lower. |
| **Cold start** | The first call in a thread. Its cache write is the thread's start-up baseline. |
| **TTL policy (observed)** | Main threads write only 1-hour cache entries; subagents write only 5-minute ones. Checked on every call. |
| **Added tokens** | `ctx(call) − ctx(previous call) − output(previous call)`, divided by character share among the items that arrived in between (tool results, prompts, attachments). Checked on image prompts: gives 500–3,000 tokens per screenshot, where a characters ÷ 4 estimate is off by about 50×. Summed re-reads match the logged cache reads within about 3%. |
| **Carry cost** | Added tokens × the number of later calls in the thread. Every later call re-reads the item. |
| **Waiting-on-me tools** | `AskUserQuestion`, `ExitPlanMode`, `EnterPlanMode`. Their latency is your reply time, not tool time. |
| **Active time** | Gaps between consecutive events in a session, each capped at 5 minutes. |
| **Session chain** | Sessions that share a `bridgeSessionId` belong to one terminal chain. The session before a `/clear` session is its predecessor. This linked all 17 of your `/clear`s. |
| **Tool-call class** | `explore` (Read, search, inspect), `build` (Edit, Write, Bash heredoc/`sed -i`/`tee`), `verify` (build/test commands), `git`, `coordinate` (Agent, SendMessage, questions, plans, Skill, ToolSearch). |

---

## Headline questions: the report's first page

Of the questions below, these gave the biggest or most surprising answers on your data:

0. **SV1** How efficient was my use? → 70/100 (B+): the documented practices would have saved 30% of spend; Context lost the most points (C+).
1. **SV2** Which change would have saved the most? → main-thread work on Opus 5.5 (the current default) instead of Opus 5 / Fable 5: $230 (36% of spend).
2. **OV3** What dominates the bill? → cache reads 55%, output only 17%.
3. **CX9** Tokens re-read per token written → 163×.
4. **CX8** Why the cache missed → resuming finished subagents with `SendMessage` and returning after long breaks re-wrote the most; its step-by-step section shows each costly miss on a timeline.
5. **CX3** The costliest things to carry in context → the start-up baseline, Claude's own earlier output, then Bash output. The costliest single item was a 33K-token file read, re-read 96 times.
6. **OV5** How concentrated spend is → the top 10% of sessions are 47% of spend.
7. **EX2** Loaded but never used → 33 of 41 skills, and 2 MCP servers with zero calls.
8. **CX4** Context size when you `/clear` → median 172k.
9. **EX9** Exploring before the first edit → a median 31% of a session's cost.
10. **OV8** Total waste budget.

---

## OV: Overview & cost

**OV1. How much did I use Claude Code, in total and per project?** `T P W`
Sessions, human prompts, API calls, tool calls, subagents, active hours, and tokens by type (input, output, thinking, cache read, 5m and 1h cache write).
- Why: the baseline every other number is read against.
- How: `ASST` [B01–B03], plus the prompt and active-time definitions.
- Check: ✓ 88 sessions (62 made API calls), 143 prompts, about 4,660 calls, about 4,900 tool calls, 63 subagents; 3.79M output tokens vs 620M cache-read tokens.

**OV2. What would my usage cost at API list prices, per project, model and day?** `T P S W`
- Why: raw token totals mislead. Within one model an output token costs 50× a cache-read token.
- How: `prices.json` × token types [B16], times what the call paid over list price. Global routing is list price on both
  platforms (a `global.` Bedrock profile, `inference_geo: "global"`). 1.1× through any other Bedrock inference profile
  (`us.`, `eu.`, `apac.`, `jp.`, `au.`, `us-gov.`… before `anthropic.` in the model id; Claude 4.5 and later) or with the
  Claude API's inference pinned to one location (an `inference_geo` other than `"global"`, e.g. `"us"`; Claude 4.6 and
  later), and the model's fast-mode premium (usage `speed: "fast"`, `"fast"` in `prices.json`). A native call (no location
  recorded, or global) is never changed. The notes say where the calls ran whenever any ran off the native path. A Bedrock application inference profile ARN names no model: `_aliases` in `prices.json`, then
  `modelOverrides` in the settings map it to one; `ANTHROPIC_DEFAULT_<FAMILY>_MODEL` set to the ARN gives only its family, so
  it is priced as that family's comparison model and the notes say so. An unmapped ARN costs $0 and is named in the notes.
- Check: ✓ about $590. Opus 5 $375, Fable 5 $171, Sonnet 5 $39. claudepit (including worktrees) is 98%.

**OV3. Which token type dominates the bill: output, cache reads, or 5m vs 1h cache writes?** `T P S`
- Why: it tells you which lever matters: smaller contexts (reads), fewer misses (writes), or less wordy work (output).
- Check: ✓ cache reads 55%, 1h writes 20%, output 17%, 5m writes 9%, uncached input under 1%.

**OV4. How does effort level change cost and time?** `T P`
Cost, API calls and model time per effort level, all models together, shown as pies; plus the thinking share of output.
- Check: ✓ effort is logged on every call (medium, high, xhigh, max). Thinking is 37% of all output.

**OV5. Which sessions, days, prompts and single calls cost the most, and what share do the top 10% take?** `T P`
- Why: fixing the top few sessions matters more than the median. Show titles (`ai-title`) and prompt text so the list is readable.
- Check: ✓ top 10% of sessions = 47% of spend; costliest session $73.

**OV6. What does a typical session, prompt, turn and active hour cost (median and 90th percentile)?** `T P W`
- Why: gives you an intuitive price for "one more prompt".
- Check: ✓ session median $4.15, 90th percentile $22.57.

**OV7. How is usage split: main thread vs subagents, repo checkout vs task worktrees, interactive vs automated?** `T P`
Automated means named or auto-run sessions and SDK calls.
- Check: ✓ subagents 23% of spend; worktrees 22% of calls; 12 named sessions (e.g. `auto-run-code-review`); only 6 SDK calls.

**OV8. What's my total waste in tokens and dollars?** `T P W`
Parts: miss re-writes, error-recovery calls, interrupted turns, redundant reads and unused-extension overhead.
- Check: ✓ each part is computable (CX8, EX7, EX8, EX2; interrupted turns from the turn log). Miss re-writes alone are $38.

## CX: Context & Caching

Context questions first (CX1–CX5), then caching (CX6–CX12).

**CX1. How big does my context get?** `T P S`
Peak and average per session, and the share of calls above 100k / 200k / 400k tokens.
- Check: ✓ peak per session: median 102k, 90th percentile 255k, max 381k. 60% of calls are above 100k and 21% above 200k.

**CX2. How fast does context grow in my biggest sessions?** `S`
Context per call for the four largest sessions (main thread).
- Check: ✓ from the added-tokens formula.

**CX3. What is my context made of, and what does each part cost?** `T P S`
Added tokens by source (start-up baseline; tool results per tool; my prompts; Claude's own output; injected attachments such as hook context, skill listings, file-change notices, reminders, MCP and agent listings), and what re-reading each source cost over the rest of its session (carry cost). One vertical bar chart: tokens added on the left axis, re-read cost on the right. The insight names the costliest single item.
- Why: a big early tool output is paid for again on every later call. This is the most direct way to see what's worth trimming.
- Check: ✓ measured from context growth: summed re-reads (644M) match the logged cache reads (627M). Largest sources: the start-up baseline (~31%), Claude's own earlier output (~23%), Bash results (~20%), Read results (~11%). Costliest single item: a 33K-token file read, re-read 96 times.

**CX4. How big is my context when I `/clear`?** `T P`
- How: link each `/clear` session to the one before it through `bridgeSessionId`.
- Check: ✓ all 17 `/clear`s linked. Context at clear was 54k–345k, median 172k.

**CX5. What does a new session start with right now?** `T P`
The first request of the latest session in the scope, broken down like `/context` but in plain words: Claude Code's system prompt, built-in tools (and the biggest one), MCP tools, memory files (CLAUDE.md, MEMORY.md), the skills and agent lists, MCP instructions, the deferred-tool list, hook output, reminders and the first message. A pie shows the parts (the biggest seven, the rest grouped) beside a table that says what each part is and how to make it smaller; tiles give the share of the context window (on a 1M window, also of a 200K one), the cost to load it and what carrying it costs.
- How: logged parts are measured from the first call; the system prompt and tool definitions come from the `prompt_snapshot` Claude Code logs (the same session, else one on the same Claude Code version); memory files are read from disk, each with its tokens and lines (the catalog's claude-md-length compares a CLAUDE.md with the docs' 200-line target). Estimates (≈ 4 characters per token) are scaled so the parts add up to the measured request.
- Check: ✓ 39.2K at session start on 2.1.281: built-in tools 27.7K (the Artifact tool alone 13.3K), skills list 4.4K, system prompt 2.8K. ✓ 2.1.285: the repo's CLAUDE.md shows 388 lines (as `wc -l`) and 6.4K tokens (9.1K at 4 characters a token, scaled to the measured request).

**CX6. How much of my cache-read spend comes from big contexts?** `T P`
- Why: this is the price of long sessions, and it grows with every extra call.
- Check: ✓ 81% of cache reads come from calls above 100k, and 40% from calls above 200k.

**CX7. What's my cache hit rate overall, per project, model, session, and main vs subagents, and which sessions are worst?** `T P S A`
- Check: ✓ about 97% overall (Fable 97.8%, Sonnet 97.2%, Opus 96.7%, Haiku 67%).

**CX8. How many cache misses did I have, and how many tokens did each re-write?** `T P S A W`
One card for everything about misses: how many, how big, what they cost, why they happened, and each costly one step by step.
- How: the re-written tokens formula, per thread. Extra cost = re-written tokens × (write rate for the call's TTL − read rate).
- Tiles: misses, tokens re-written, share of all cache writes, extra cost of misses, share of spend, cost per miss.
- Insights: how the miss cost compares with total spend and with what caching cost overall (how much preventing misses matters); the cause behind most re-written tokens; the costliest miss.
- Collapsible sections: every miss (when, where, how big, cause, extra cost), and the step-by-step traces below.
- Causes (kept in the data and the digest, summarised in the insight): a subagent idle past its 5-minute TTL (waiting for SendMessage); a slow tool past the TTL; your reply or approval past the TTL; returning to a session after more than an hour; the tool list changing (an MCP server connecting, deferred tools loading); a model switch; an effort change; other. Found from the gap vs the thread's TTL, the slowest tool before the call, model/effort changes between calls, and `cache_miss_reason`.
- Check: ✓ 32 misses, 7.0M tokens re-written (30% of cache writes); $52 extra, 7.5% of spend and 9% of cache cost. Resuming finished subagents via SendMessage and returning after more than an hour are the two largest causes; model and effort switches: none mid-thread.

Step by step (the collapsible section; formerly its own card): the 15 costliest misses, each on a timeline that includes:
- the previous reply;
- what happened next: tool runs; your prompts, commands and answers; main-agent messages; other subagents' work; session events (compaction, MCP/tool changes, API errors, away recaps);
- the moment the cache expired;
- the request that paid to re-write it.

Long quiet stretches are drawn narrow with their real length printed. Each miss comes with the time from the previous reply to the next request, a plain-language account, cause-specific advice, and its place on the session's context-size curve.
- Why: the cause label says what kind of miss it was, while the timeline shows when and why. Comparing the idle time with the cache lifetime tells you whether being quicker would have helped.
- How: per miss, take every record of the thread (plus the main thread, for a subagent) from shortly before the previous call to the miss. Then:
  - idle = the previous call's last line → the next request's start;
  - expiry = the previous call's last line + the TTL (the same clock as SV6);
  - trigger = the first input after the previous reply.

  `cache_misses.csv` has one row per miss with these timestamps.

**CX9. For every token Claude writes, how many tokens does it re-read?** `T P S`
This is read amplification: cache_read ÷ output.
- Why: a high ratio means you mostly pay to re-send context rather than for new work. It's a single efficiency number.
- Check: ✓ 163×.

**CX10. Does each cache TTL fit my gap pattern?** `T P A`
The two options side by side, for main threads and for subagents apart. Every call is replayed under both lifetimes (SV6's method), and each option's cache cost is split into:
  - cache reads;
  - writing new content (1.25× input for 5 minutes, 2× for 1 hour);
  - re-writes after a pause of 5–60 minutes (5 minutes only: the one place the options differ);
  - re-writes that happen under both (pauses over 1 hour, a changed prompt, retries), at each option's write price.

The idle-time chart colors each pause by what the replay found: cached under both, cached only on 1 hour, or a miss under both.
- Why: it shows whether the 1-hour write premium is repaid by the re-writes it avoids, and which setting (`promptCacheTtl`, `subagentPromptCacheTtl`) fits.
- Check: ✓ main threads: 1 hour is $17 cheaper. The 2× write price costs $56 more but saves $72 of re-writes on 57 pauses of 5–60 min.
- Check: ✓ subagents: 5 minutes is $30 cheaper, with only 1 pause of 5–60 min. The other subagent pauses miss under both lifetimes (resumes with a changed prompt).

**CX11. How much do cold starts cost, by session type and subagent type?** `T P A`
Forks are left out: they start from the parent's context, not cold.
- Check: ✓ sessions average 51k (21k–66k). Subagents: general-purpose about 41k, `claude` about 26k, Plan about 12k, Explore about 10k.

**CX12. Where do my cache writes go: new content, cold starts, or miss re-writes?** `T P S`
- Check: ✓ 19.5M written: 53% new content, 21% cold starts, 27% re-writes.
- Check: ✓ 30 misses ($38):
  - 22 hit an expired cache ($27).
  - 8 happened while the cache was still alive ($11), so waiting less would not have helped them.
  - Costliest: $5.10, for coming back after 3h 31m to a 268K-token session. The 1-hour cache had expired 2h 31m earlier.
  - An overnight return was triggered by a background task's notification, not by you.
  - 4 subagent resumes ($4.15) followed "Your computer went to sleep mid-response" errors.

  Building the timelines exposed one false miss: a `/compact` had been counted as a 570K-token re-write. It is now excluded (see Re-written tokens).

## SE: Sessions

Sessions and prompts first (SE1–SE4), then subagents (SE5–SE6).

**SE1. How long are my sessions: wall time, active time, prompts and calls, per project?** `T P`
- Check: ✓ wall time median 7 min, 90th percentile 99 min. Active time median 7 min, 90th percentile 55 min.

**SE2. How much work does one prompt set off?** `T P S`
API calls, tool calls, subagents, and turn time per human prompt.
- Why: measures how much Claude does on its own per prompt, and how long you wait.
- Check: ✓ calls per prompt: median 16, 90th percentile 48, max 114. Turn time: median 108 s, 90th percentile 12 min, max 1 h 45 min.

**SE3. Which of my prompts were most expensive, and what did they ask?** `T P`
Each with its session ID, to pick it up again with `claude --resume`.
- Check: ✓ top three: $52 "Implement the plan with 2 subagents…", $25 "…lets plan a task…", $22 "continue".

**SE4. How often do I run sessions in parallel, and how much of my active time is multi-session?** `T W`
- Check: ✓ up to 3 at once; 27% of active time had 2 or more sessions running.

**SE5. How many subagents do I spawn, by type, model, project and foreground/background, and what share of spend do they take?** `T P S`
Pies in one row: subagents by type, cost by type, cost by model.
- Check: ✓ 63: Explore 27, general-purpose 26, Plan 6, `claude` 2, claude-code-guide 2. All ran in the background. 23% of spend.

**SE6. Do subagents pay off by keeping work out of the main context?** `T A`
Tokens processed inside the subagent vs tokens handed back (the `<result>` in its task notification), plus duplicated reads: files read by both a subagent and the main thread, or by sibling subagents.
- Check: ✓ the big ones processed 550× to 30,000× more than they returned. 29 of 242 subagent-read files were also read by the main thread (12%).

## EX: Plugins, MCP, Tools & Hooks

Skills, slash commands, MCP servers, plugins, agent types and hooks (EX1–EX5), then the tools Claude calls (EX6–EX15). EX1–EX9 are shown in the report. EX10–EX15 are computed for `metrics.json` and the digest, so Claude can use their numbers, but they are not shown.

**EX1. Which skills and slash commands do I use, how often, and what does each cost?** `T P`
Cost per skill comes from the tokens attributed to it (`attributionSkill`).
- Check: ✓ `/clear` 17, `/model` 15, `/rerun` 14, `/effort` 4. Cost is attributable to `rerun` and each `claudepit-task-*` phase.

**EX2. What loads into every session but never gets used, and what does that cost?** `T P`
Skills, MCP servers and agent types; cost per session and in total.
- How: each item's own line in the listing gives its size; a plugin's or synced skill's line is named `<namespace>:<skill>`
  (`- anthropic-skills:docx: …`), and the whole name is the key.
- Check: ✓ 33 of 41 skills never used. claude-in-chrome and pencil MCP servers loaded in 56–66 sessions with 0 calls. The statusline-setup agent was never used.

**EX3. How often does the tool list change mid-session, and what does each change cost?** `T S`
Changes come from MCP servers connecting late and from deferred tools loaded via ToolSearch.
- Check: ✓ 2 mid-session tool-list changes (one was claude-in-chrome connecting) re-wrote 54k and 267k tokens. 31 ToolSearch calls.

**EX4. What do plugins add, in value and overhead?** `T P`
E.g. swift-lsp diagnostics injected into context: count, tokens, errors surfaced.
- Check: ✓ 44 diagnostics injections, 149 issues, about 37k characters.

**EX5. What do my hooks cost: runs, time added, failures, context injected and its carry cost?** `T P`
- What Stop hooks set off: per Stop hook, its runs, how many times Claude went on working afterwards without a new prompt, the
  API calls that took and their cost. How: the calls that descend from a Stop run's `stop_hook_summary` record (`parentUuid`
  links) before any new input: a prompt, command or queued message, a task notification, or a message from another session.
  A hook that sends nothing back (a notification, a logger) sets off nothing and costs $0 here; one that adds context or
  blocks the stop makes Claude go on. `/goal` checks show as "Goal check (/goal)" and are not counted as a saving.
- Check: ✓ Stop hook: 377 runs, 45 s, 3 failures. The UserPromptSubmit summary hook failed 4 times with exit 127 (it points at a path from an old machine, `/Users/i501817`). Hooks injected about 178k tokens, re-read about 7.9M times (estimate).

**EX6. Which tools does Claude use, how often, and what do they cost in tokens?** `T P S`
Tool calls per tool and the output tokens Claude spent writing each tool's inputs, on one vertical bar chart with two axes. Tiles add the context that tool results added.
- Check: ✓ Bash about 3,350 (69%), Read 635, Edit 530, Write 105, Agent 63. Tool inputs are about 2.1M output tokens (52% of all output): Bash about 1.2M, Edit 0.37M, Write 0.33M. Each call's logged output is split by length; characters ÷ 4 undercounts code by about 1.5×.

**EX7. What fails, how often, and how many extra calls did recovery take?** `T P`
Failures by tool as a pie, beside the most common error messages.
- Check: ✓ Bash 70 of 3,381 (2%). Edit 4 of 531 ("string not found"). 4 blocked `sleep` commands; 3 worktree-isolation errors.

**EX8. Which files does Claude read most, and how often does it re-read one it already has?** `T P S`
One table: reads, repeat reads (lines the same thread already read, no edit in between; reading another range of the file is not a repeat), sessions and lines per file.
- Check: ✓ e.g. `AppState.swift` was read 30 times. 113 of 635 reads were repeats (18%); 68 of those had no edit in between.

**EX9. How much does Claude spend exploring before its first edit in a session?** `T P S`
- Why: high numbers suggest your CLAUDE.md, memory or task spec could say more up front.
- Check: ✓ a median 31% of session cost goes before the first edit (90th percentile 53%), across 33 sessions that made edits.

**EX10. Which tool calls return the biggest outputs?** `T P S`
Whole-file vs ranged reads, unfiltered commands, truncations, outputs saved to disk.
- Check: ✓ 344 whole-file reads vs 202 ranged; 8 whole reads of files over 800 lines; 2 token-cap truncations; 25 Bash outputs saved to disk.

**EX11. Which tools are slowest, and how much wall time did each take?** `T P`
Waiting-on-me tools are excluded [D04].
- Check: ✓ builds: 356 runs, 66 min (max 12.7 min). Tests: 142 runs, 24 min. Git: 314 runs, 7 min.

**EX12. How often does Claude batch several tool calls into one round-trip?** `T P`
- Why: every extra round-trip re-reads the whole context.
- Check: ✓ 86% of tool-using calls make just one tool call.

**EX13. What does Claude run in Bash: builds, tests, git, search and inspection, scripts, or file writing via heredoc?** `T P`
- Why: Bash is 69% of tool calls, and heredoc file writes are an expensive output pattern.
- Check: ✓ regex classes work: 356 builds, 142 tests, 314 git.

**EX14. How is Claude's effort split between exploring, building, verifying, git and coordinating?** `T P S`
- Check: ✓ explore 61%, build 20%, verify 9%, git 6%, coordinate 5% of tool calls.

**EX15. How often do build or test failures turn into fix-and-retry loops?** `T P S`
- Check: ✓ 499 build/test runs, 43 failed, 10 cycles of fail → edit → re-run.

## OUT: Output & outcomes

**OUT1. Where do output tokens go?** `T P S`
Thinking, text shown to me, and tool inputs (Bash scripts and heredocs, file contents, plans, subagent prompts).
- Check: ✓ thinking 38%, Bash commands and scripts 29%, file contents (Write/Edit) 17%, text shown to you 10%. Thinking text isn't logged, only its token count.

**OUT2. How much code did Claude change, and what does 100 changed lines cost?** `T P S`
Lines added and removed, and files touched.
- Check: ✓ +6,679 / −1,926 lines, from `structuredPatch`.

**OUT3. What does Claude write: code, tests, plans, memory, docs or config?** `T P`
Measured in calls, lines and output tokens, drawn as one vertical bar chart: each kind of file's share of each measure, side by side (the counts are in the tooltip).
- Check: ✓ code 353 edits (about 184k tokens), plans 48 (about 81k), tests 68 (about 52k), memory 131 (about 51k).

**OUT4. Which languages does Claude work in: files, lines and tokens per language?** `T P S`
Per language (from the file extension): distinct files created, edited, deleted, moved or renamed, and read; lines added and removed; tokens written (the edits' output tokens) and read (what Read results put in context), and what those tokens cost. Drawn as one vertical bar chart of each language's share of files touched, lines changed and tokens (counts in the tooltip), with a Files tab and a Lines-and-tokens tab.
- Check: ✓ created/edited from Write's create/update result and Edit calls; deleted and moved from `rm`, `git rm`, `mv`, `git mv` in Bash (globs, variables and folders skipped). Swift 128 files and 9,839 changed lines; 103 files created, 13 deleted.

## ME: My working patterns

**ME1. When do I work? Prompts, active time and spend by hour and weekday.** `T W`
- Check: ✓ local-time heatmap from timestamps.

**ME2. How do I prompt, and what do screenshots cost?** `T P`
Prompt length, screenshots, slash commands vs typed prompts, and messages queued while Claude works.
- Check: ✓ 41 prompts with images, each image about 500–3,000 tokens; 5 queued prompts; 51 slash-command lines.

**ME3. How long do I take to reply, and how often do my pauses outlast the cache?** `T P`
Reply time to Claude's answers, to its questions, and to plan approvals. Pauses are compared with this scope's main-thread cache lifetime (1 hour on a subscription, 5 minutes on an API key).
- Check: ✓ question and plan waits of up to 2.5 h. One 2.5 h wait re-wrote 70k tokens.

**ME4. Who's the bottleneck: Claude's busy time or my think time, per session and day?** `T S W`
Not shown in the report: still computed, and kept in the data file and the digest.
- Check: ✓ `turn_duration` gives Claude's time; the gap from turn end to the next prompt gives yours.

**ME5. How do I answer Claude's questions and plans?** `T`
First or recommended option vs a custom answer, and plans approved on the first try. Not shown in the report: still computed, and kept in the data file and the digest.
- Check: ✓ 55 answers: 56% the first option, 38% the "(Recommended)" one, 13% custom. 13 of 17 plans approved.

**ME6. Which modes do I work in, and how is spend split between planning and executing?** `T P S`
Modes: auto, plan, default, accept-edits. Drawn as one vertical bar chart: each mode's share of prompts and of the cost of the turns they started, side by side (the counts and dollars are in the tooltip). The count of sessions that used plan mode is kept in the data only.
- Check: ✓ `permissionMode` on prompts: auto 171, plan 26, default 23. Plan-mode entries and exits are logged.

**ME7. How often did sessions hit API errors, and what did they interrupt?** `T W`
Errors include being logged out, the machine sleeping mid-response, and server errors.
- Check: ✓ 17 "not logged in"; 4 "computer went to sleep mid-response".

## SV: What would it have saved?

Each scenario re-prices the logged calls as if one habit or setting had been different from the first day: the same work,
with the change in place from day one. They are theoretical, and they overlap (a smaller context also makes misses and
re-reads cheaper), so they don't add up. The Insights tab takes its savings from here.

Every saving in the report (these cards, OV8's waste, and the Insights and Optimizations tabs) is shown
twice so it is easy to read: **all time**, over the analysed days, and **per 30 days**, the same pace projected to 30 days
(all time × 30 ÷ days covered). Saving tiles show both side by side, tables add a "Per 30 days" column, and charts a second
series. Rows that each describe one event (SV8's returns, SV9's reads) keep a single figure.

**SV1. What is my efficiency score, and where did the points go?** `T P`
One number from 1 to 100 with a school grade, A+ down to C-, and the same for five areas, so it says where the points went.
The headline shows it beside the cost; the card leads the SV section.
- How:
  - Each area's share of spend is what the largest of its SV2 levers would have saved (they act on the same cost, so only
    one counts). Context: SV5's /compact and SV9's ranged reads, 35 points. Caching: SV4's avoidable misses, SV6's cache
    lifetime and SV8's fresh starts, 25. Subagents: SV7 via `CLAUDE_CODE_SUBAGENT_MODEL`, 15. Hooks: EX5's Stop-hook
    follow-up work (an upper bound), 15. Setup: SV3's unused listings, 10.
  - Together the areas keep Π(1 − share) of the spend, the way the tabs and the video combine savings (each change saves
    its share of what the others leave). The score is that kept share out of 100: 100 minus the share of spend the
    documented practices would have saved, overlaps removed, rounded half up and kept within 1–100.
  - The combined loss is split between the areas in proportion to their shares, so the points they lose add up to
    100 − score. An area's score is the points it kept out of its points; the points-weighted average of the area scores
    is the overall score, unless an area lost more than all its points (its score is then 1).
  - The main-thread model (SV2's model lever) is not graded: the right model depends on the work, and that lever compares
    with the model you use now, so moving to a cheaper model would lower the score.
  - Grades, generous on purpose (a C means more than 42% of spend was avoidable): A+ 88–100, A 82–87, A- 76–81, B+ 70–75,
    B 64–69, B- 58–63, C+ 50–57, C 40–49, C- below 40. An (i) beside the score shows this table.
  - All projects adds a row per project (each group scope): its score, grade, spend, and the area that lost the most points.
  - Progress: every full run that ends now (not `--until`) saves its high-level scores (score, grade, area scores, spend
    per 30 days, cache hit rate, median peak context) in `<OUT>/history/scores.json`, one entry a day (a second run the
    same day replaces it). With an earlier day there, All projects charts the score over the last 12 reports, lists them
    with the change from one to the next, and the headline shows the change since the last report beside the score.
    `/claude-usage:clear` removes the history unless `--keep history`.
- Check: ✓ 2026-10-01 (Sep 6 → 30): 70/100 (B+), 30% of spend avoidable with overlaps removed. Context 53 (C+, 16.4 points:
  /compact at about 150K, $212 = 18% of spend), Caching 77 (A-, 5.6: avoidable misses, $72.67), Subagents 80 (A-, 3.1),
  Hooks 73 (B+, 4.0, upper bound), Setup 88 (A+, 1.2). The points lost add up to 30.3 = 100 − 69.7. Projects range from
  44 (C) to 89 (A+). The engine's other cards, CSVs and drafts are unchanged by it (old and new engine on the same days).

**SV2. How much could I have saved so far, lever by lever?** `T P`
Every lever below side by side, with its share of spend and a 30-day projection.
- Why: the cost questions say where the money went; this says which change would have kept the most of it.
- Check: ✓ main-thread work on Opus 5.5 instead of Opus 5 / Fable 5: $230 (36%). Subagents on Sonnet 5: $75. /compact at ~150K: $75. Stop-hook follow-up work: $48 (upper bound; the work EX5's "What Stop hooks set off" charges to the memory hook). Avoidable misses: $34.

**SV3. What do unused skills, MCP servers and agent types cost me?** `T P`
Per item: tokens per session, its share of the saving, where it comes from (built in, personal, synced, project, plugin, MCP config, connector), and how to switch it off.
- How: listing sizes from `skill_listing`, `mcp_instructions_delta` and `agent_listing_delta` for items never used through Skill, a slash command, an MCP tool or Agent. Priced on every main-thread call of the sessions that loaded them.
- Used in some projects, loaded in every one (all projects only): an MCP server in the user config, a personal skill or a
  plugin enabled in `~/.claude/settings.json` that some projects use and others only load. Per item: the projects that use it,
  the other projects' sessions that loaded it, what that cost (priced the same way), and how to set it up only where it is
  used (`claude mcp add … -s local` or the project's `.mcp.json`, the project's `.claude/skills/`, `enabledPlugins` in the
  project's settings). The "how" never says to switch something off in each project: that is the toggling it replaces. An
  item no project uses keeps the first table's advice (off, or out of the user config).
- Check: ✓ 4.2K tokens per session, $11.79 so far. $7.88 of it can be switched off: the claude.ai Docs connector $1.32, the synced anthropic-skills (about 240 tokens each), and so on. The rest are built in. Nothing is used in some projects and loaded in others.

**SV4. Which cache misses were avoidable, and what would avoiding them have saved?** `T P S`
- How: the CX8 cause of each miss, plus "computer went to sleep" API errors, mapped to who can fix it: your habits, how Claude delegates, your setup, or not in your control.
- Check: ✓ $34 of the $37.85 miss cost was avoidable (90%).

**SV5. At what context size should I /compact, and what would it have saved?** `T P`
- How: replay every main thread. Whenever its context would pass a threshold, drop it to the start-up size + 20K summary + 10K re-read detail, and charge the compaction (a full read, 5K output, the new write). Net saving at thresholds from 100K to 500K: 100K is the smallest auto-compact window Claude Code accepts, and below it you would compact every few turns, which the replay can't price (lost detail, re-reads, mid-task summaries). The best threshold is a guide for compacting at natural breaks; the catalog turns it into a forced window only on a 1M model, as a 400K cap (300K when nothing is saved past 400K, never lower).
- Check: ✓ best at 150K: 70 compactions, $74.60 net. Below 100K, compaction costs more than it saves. ✓ 2026-09-30: best at 150K either way, 148 compactions, $163 net; the dropped 60K and 80K rows lost $1,911 and $1,310 on these sessions (a 44–55K start-up).

**SV6. 5-minute or 1-hour cache: which fits my sessions?** `T P A`
- How: replay every call under a 5-minute and a 1-hour lifetime, for main threads and subagents separately, and price the whole history for all 4 combinations (total bill = actual non-cache cost + simulated cache reads and writes).
  - The idle time is the time from the end of the previous response to the request; the cache clock runs from there. Measured start-to-start, 7 subagent hits seemed to come after more than 5 minutes; measured from the response's end, none do.
  - The replay is anchored. A longer lifetime than the actual one never adds a miss and a shorter one never removes one, so only the other direction is predicted: a hit becomes a miss when idle > τ, and an expiry miss becomes a hit when idle ≤ τ.
  - A miss from a changed prompt, or one within 5 minutes (retries, parallel requests), stays a miss under both lifetimes.
  - A simulated miss rewrites the whole context.
  - Decomposition: 5 min − 1 hour = the rewrites of pauses of 5–60 min − the 1-hour write premium. Break-even pauses per 100 calls = pauses × premium / (penalty × calls).
  - Tried and rejected:
    - Start-to-start gaps: +6.5% on subagents replaying the actual policy.
    - An event-driven replay with a shared prefix across threads: +9.5% on subagents, and the prefix is worth < $1.
- Whenever it favours main 1 hour · subagents 5 minutes, the catalog's first recommendation pins that mix in settings
  (`promptCacheTtl`, `subagentPromptCacheTtl`), even when it already is the actual mix: unset, the main lifetime is 1 hour
  only on a subscription within its usage limits.
- Check: ✓ replaying the actual lifetimes reproduces the cache cost exactly.
  - All 5 min: $677; all 1 hour: $697; actual (main 1 hour, subagents 5 min): $666, which is also the cheapest mix.
  - Main threads favour 1 hour by $11, a close call: 52 pauses of 5–60 min cost $63 against a $52 premium. Break-even is 1.2 pauses per 100 calls; yours is 1.4.

**SV7. What if another model had done the same work?** `T P A`
- How: the same tokens at each model's list prices. The levers count only calls on pricier models. "Via CLAUDE_CODE_SUBAGENT_MODEL" counts only the subagents that variable would move, following Claude Code's order (the model passed for the call, then the definition's `model`, where `inherit` is the main model, then the variable, then the main model): no model passed for the call, and a type with no model of its own (general-purpose, the built-in `claude`, or a user or project agent file without a `model` line; a file named like a built-in overrides it). Explore and Plan (`inherit`), forks and plugin agents are left out. SV2's subagent lever uses this figure.
- Check: ✓ main threads on Opus 5.5 (the model in use now): $230. Subagents on Sonnet 5: $75. Explore subagents on Haiku 4.5: $19. ✓ 2026-09-30: every subagent on Sonnet 5 $101, via the variable $37.03: general-purpose subagents without a per-call model ($122 of spend) are the only ones it moves; two `claude` agents launched with model opus ($44.61), Explore ($28.65), Plan ($15.28), general-purpose calls that passed a model ($28.09) and forks ($8.40) keep theirs.

**SV8. What would starting a fresh session after long breaks have saved?** `T P S`
- How: each return after the cache expired, priced as a fresh session (median start-up + 5K summary + 10K re-read), including every later call's smaller context until the next break or compaction.
- Check: ✓ 6 returns, $16.83 (upper bound).

**SV9. What would reading large files in ranges have saved?** `T P S`
- How: Read calls without offset/limit that returned ≥8K tokens; their cost to write and carry. Assumes a targeted read (or a grep first) keeps 50%.
- Check: ✓ 75 reads (1.2M tokens) cost $25.36; about $12.68 saved.

## TR: Trends & change detection

**TR1. What drove my cost from day to day (or week to week)?** `W`
Cost = prompts × API calls per prompt × cost per API call. One line chart draws all four, each as a multiple of an average active day, so the cost line is the other three multiplied and the driver furthest above 1× is the one that drove that day. The tooltip gives the real values; the table adds context per API call, hit rate, tool error rate and re-written tokens.
- Check: ✓ the product is exact (the averages are overall ratios): Sep 12 cost 2.2× = 2.9× prompts × 0.8× calls per prompt × 1.0× cost per call.

**TR2. What changed when my setup changed?** `W`
One line chart in tokens: main-thread context per API call (what each call re-reads, so it sets TR1's cost per API call) and start-up context (skills listed in the tooltip). Vertical lines mark a new Claude Code version (20+ API calls) or a new model taking over most main-thread calls. The insight compares context before and after the change with the biggest jump.
- Check: ✓ Sep 24 (v2.1.281 · Opus 5.5): main-thread context per call 148K → 377K, start-up context 51.6K → 27.2K.

---

## Considered and dropped

| Candidate | Why it was dropped |
|---|---|
| "How often do I have to correct Claude?" (keywords in prompts) | Tested: 4 matches in 143 prompts, 2 of them false. Too noisy. EX15 is a better proxy for rework. |
| "Does a big context slow Claude down?" | Tested: median tokens/s is flat across <100k, 100–200k and >200k for every model. |
| Cost of model or effort switches, as its own question | 0 mid-thread model switches, and the one effort switch kept its cache. Kept as a cause row in CX8. |
| Remote Control usage | Every session registers a bridge, and the logs don't show whether a remote client was attached. |
| Compaction cost | No compaction records yet (peak context 381k in a 1M window). Add it when `compact_boundary` appears. |
| "Does plan mode or a longer prompt give cheaper or better results?" | Confounded: harder tasks get more planning, and there's no quality label to control for it. |
| Quality comparison of models (e.g. your Opus vs Sonnet implementer race) | Needs judgment, not counting. |
| Exact size of the system prompt, CLAUDE.md and tool schemas | Not logged as their own items. CX5 estimates them from the `prompt_snapshot` Claude Code logs and the files on disk, scaled to the measured first request. |
| Rate-limit headroom and plan quota | Not in the logs. |
| What Claude was thinking | Thinking text is omitted (901 characters logged in total). Only token counts exist (OV4, OUT1). |
| "Subagents that inherit the parent's context cost more" | Tested: the one 145k start was a general-purpose agent, not an inherited context. Replaced by cold start by type (CX11). |
| SDK automation as its own section | Only 6 SDK calls. Folded into OV7. |
