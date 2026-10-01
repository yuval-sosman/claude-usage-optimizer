# Claude Code best practice: what every recommendation follows

The report's numbers say where this user's money went and what a change would have saved. They don't say whether the change
is a good way to work. A replay can favour something Claude Code's own guidance advises against: compacting every few
turns, a lower effort than the model was tuned for, switching off skills that cost cents. The rule for insights and
optimizations alike: **the data picks the lever, the target and the size; the guidance picks the fix.** Recommend what the
guidance recommends, aimed with the user's numbers (which project, file, hook, model, how much).

Quotes are from the official docs (checked 2026-09-30, Claude Code 2.1.285). Cite the page in `docs` when you rely on one.
When you need something not covered here, fetch the page (`https://code.claude.com/docs/en/<page>.md`) before proposing it.

## How the data and the guidance combine

- **They agree**: say both. "Your sessions ran past 300K (CX1); the docs advise /clear between unrelated tasks" is stronger than
  either half.
- **The data favours what the guidance warns against**: recommend what the guidance recommends instead, and say why in one
  sentence with the numbers. Example: SV5 finds compacting at 150K pays; the fix is /compact at natural breaks once past about
  150K (and, on a 1M model only, an auto-compact cap at 400K, never under 300K), never a low forced threshold.
- **The guidance has a default the user overrode**: going back to the tuned default is a recommendation even without a
  measured saving (a persisted effort above the model's default; an auto-compact window below 300K).
- **The guidance applies but the data is silent**: don't recommend it. Generic advice that would be true of anyone is not an
  insight ("write specific prompts") unless a card points at it (EX9's exploring share, EX8's re-reads).
- **Personal**: name the project, file, hook, agent type or model, and quote the user's figures. A recommendation that would
  read the same for every user is not finished.

## Context and compaction

> Most best practices are based on one constraint: Claude's context window fills up fast, and performance degrades as it fills.
> — [Best practices](https://code.claude.com/docs/en/best-practices)

> **Clear between tasks**: Use `/clear` to start fresh when switching to unrelated work. Stale context wastes tokens on every
> subsequent message. — [Manage costs](https://code.claude.com/docs/en/costs)

> To choose when its overhead happens, run `/compact` at a natural break in your work, such as between tasks, instead of waiting
> for auto-compaction to trigger mid-task. If you've gone down a path you want to abandon entirely, `/rewind` to an earlier turn
> instead. — [Prompt caching](https://code.claude.com/docs/en/prompt-caching)

> `/compact` reads the conversation it summarizes, so compacting a large context is itself a large request. When you want a
> fresh start instead of continuity, `/clear` costs nothing. — [Manage costs](https://code.claude.com/docs/en/costs)

> Customize compaction behavior in CLAUDE.md with instructions like "When compacting, always preserve the full list of modified
> files and any test commands" to ensure critical context survives summarization. — [Best practices](https://code.claude.com/docs/en/best-practices)

> Sometimes you *should* let context accumulate because you're deep in one complex problem and the history is valuable.
> — [Best practices](https://code.claude.com/docs/en/best-practices)

Auto-compact window: `/autocompact` accepts 100K–1M; models with a native 1M window compact "at about 967K tokens by default"
([Model configuration](https://code.claude.com/docs/en/model-config)). Claude Code itself says: "The auto setting picks a window
tuned for your model and is strongly recommended for the best cost and performance", and when it is overridden: "Overriding
auto may result in high token usage, especially when resuming long sessions."

How the plugin applies it:
- SV5's threshold is where compacting at a natural break pays off, not a switch to flip. Its replay starts at 100K; below that
  you would compact every few turns.
- A forced window (`autoCompactWindow`) only caps a model whose window is over 300K (a 1M model), at 400K: high enough that
  a long task keeps its room, low enough to stop re-reading huge contexts. 300K at the lowest, when SV5 finds nothing to save
  past 400K; never less (validate.py refuses it). It comes next to the context notice (the natural-break habit) and with
  Claude Code's own recommendation in the tradeoffs. On a 200K model the plugin never overrides auto.
- After a break past the cache lifetime, cheapest first: `/clear` (free), `/compact` (reads the whole context once, uncached,
  then carries only the summary), continuing (re-writes it all, then re-reads it on every call). Before a long break, a
  `/compact` while the cache is warm is cheap. On Pro and Max, resuming a session idle over about an hour and over 100K offers
  "Resume from summary" ([Sessions](https://code.claude.com/docs/en/sessions)).
- Delegate investigation: "Delegate research with "use subagents to investigate X". They explore in a separate context,
  keeping your main conversation clean for implementation." For side questions, `/btw` keeps the answer out of the history.

## Model choice

> Sonnet handles most coding tasks well and costs less than Opus. Reserve Opus for complex architectural decisions or
> multi-step reasoning. — [Manage costs](https://code.claude.com/docs/en/costs)

> Unexpectedly high spend on an API or cloud-provider plan: usually traces back to long sessions that were never cleared or to
> Opus left as the default model. — [Manage costs](https://code.claude.com/docs/en/costs)

> Pick your model and effort level at the top of a session, then save `/compact` for natural breaks between tasks.
> — [Prompt caching](https://code.claude.com/docs/en/prompt-caching)

- `opusplan` runs Opus in plan mode and Sonnet otherwise; each plan-mode toggle is a model switch and starts a fresh cache.
- SV7 re-prices the same tokens: a price ceiling, not a forecast. A cheaper main model is the user's call. Recommend it as a
  habit (the default for day-to-day work, the stronger model for the hard step) and switching at a natural break or a new
  session, since a switch re-writes the cache.

## Subagents

> Claude Code resolves the subagent's model in this order: 1. The per-invocation `model` parameter 2. The subagent definition's
> `model` frontmatter, where `inherit` selects the main conversation's model 3. The `CLAUDE_CODE_SUBAGENT_MODEL` environment
> variable … 4. The main conversation's model. — [Subagents](https://code.claude.com/docs/en/sub-agents)

> Setting `CLAUDE_CODE_SUBAGENT_MODEL` by itself doesn't change the model the built-in Explore and Plan subagents run on.

> A user or project subagent named `Explore` overrides the built-in and keeps its own `model` field, so define one with
> `model: haiku` to keep exploration on a lower-cost model.

- `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` moves every subagent (not forks), but Claude can then no longer pass a model for one
  call and agent files' own models are ignored: offer it only as a stated choice.
- "For simple subagent tasks, specify `model: haiku` in your subagent configuration" (costs).
- A resumed subagent "can keep reading the prompt cache the original run warmed"; subagents cache for 5 minutes by default,
  so a resume after that re-writes its history. A fork reuses the parent's cache.
- Verbose work (tests, logs, docs lookups) belongs in a subagent "so the verbose output stays in the subagent's context while
  only a summary returns to your main conversation."

## Effort and thinking

| Level | The docs ([Model configuration](https://code.claude.com/docs/en/model-config)) |
|---|---|
| `low` | Quick exchanges where you review each result: brainstorming, a first sketch, a rename |
| `medium` | The default on Opus 5.5 and Sonnet 5.5, fitting day-to-day engineering work with a clear scope; on other models, trades some intelligence for fewer tokens |
| `high` | Work where verification matters or edge cases are likely; the default on every other model except Opus 4.7 |
| `xhigh` | Deeper reasoning at higher token spend; the default on Opus 4.7 |
| `max` | The hardest problems; "may show diminishing returns and is prone to overthinking" (and not a saved effortLevel) |

> In Anthropic's testing, Opus 5.5 at `medium` matches or exceeds Opus 5 at `high` on coding and knowledge-work evaluations.
> … When you move from Opus 5 to Opus 5.5, start at `medium` rather than carrying over the level you used on Opus 5.

> You can't turn thinking off on Opus 5.5, Sonnet 5.5, or the Fable models.

- A persisted level above the model's default is worth undoing (the catalog's effort-default does it). Going below the
  default is a quality trade: per task with `/effort`, not as a saved default (validate.py refuses a saved `low`).
- On Opus 5.5, Sonnet 5.5 and Fable 5.1 (API key or subscription) changing effort keeps the cache; on other models it
  re-writes it, so change it at a natural break.

## Caching

- Main conversation: 1 hour on a subscription within its usage, else 5 minutes; subagents and helpers: 5 minutes unless set.
  "The longer TTL helps when you leave a session idle and come back to it … It costs more on short bursts of work that never
  idle past five minutes." SV6 prices both on the user's own pauses: follow it.
- The plugin's standing advice, whenever SV6 favours it (the usual result: people pause between prompts, subagents don't):
  1 hour for the main thread and 5 minutes for subagents, pinned in settings (`promptCacheTtl` `"1h"`,
  `subagentPromptCacheTtl` `"5m"`), even when the history already ran that way, since the automatic main lifetime is 1 hour
  only on a subscription within its usage limits. It leads the Optimizations tab (Start here). Moving to any other mix needs
  a margin over 5%.
- Invalidates: switching model, effort (on most models), fast mode on, MCP servers connecting or removed when their tools load
  upfront (with tool search, the default, a late connection doesn't), enabling a plugin with MCP servers, compaction, many
  images, a Claude Code upgrade. Keeps: editing files or CLAUDE.md, permission mode, output style, skills, `/recap`, `/rewind`.
- "For normal use, leave caching enabled." Never propose disabling it.

## Setup: CLAUDE.md, skills, MCP, plugins, hooks

- CLAUDE.md: "Aim to keep CLAUDE.md under 200 lines by including only essentials." "For each line, ask: *'Would removing this
  cause Claude to make mistakes?'* If not, cut it." Workflow-specific instructions belong in skills, which load on demand.
  Anything this plugin adds to CLAUDE.md stays at 1–3 lines.
- MCP: "MCP tool definitions are deferred by default, so only tool names and server instructions enter context until Claude
  uses a specific tool." Prefer CLI tools (`gh`, `aws`, `gcloud`) when available; set a server up only where it is used.
- Skills: `skillOverrides` `"name-only"` keeps a rarely used skill's name listed without its description; `"off"` hides it
  from the model and from `/name`. Never disable bundled skills or anything managed policy sets.
- Code intelligence plugins "give Claude precise symbol navigation instead of text-based search, reducing unnecessary file
  reads when exploring unfamiliar code" ([Manage costs](https://code.claude.com/docs/en/costs); setup in
  [Code intelligence](https://code.claude.com/docs/en/plugins/code-intelligence)).
- Hooks "can preprocess data before Claude sees it", e.g. a test run filtered to its failures. Hooks the plugin installs
  must fail open and never block without a way through.
- Track context with a status line ("Track context usage continuously with a custom status line") and `/context`, `/usage`.

## Never recommend

validate.py refuses the first four in optimizations.json; the rest are rules for you.
1. An auto-compact window below 300K (setting or `CLAUDE_CODE_AUTO_COMPACT_WINDOW`), or any override on a 200K model.
2. A saved `low` effort (global or per model).
3. Thinking off (`alwaysThinkingEnabled: false`, `MAX_THINKING_TOKENS=0`).
4. A context notice (context_guard.py) below 100K.
5. Disabling prompt caching, bundled skills, or anything managed policy sets.
6. Switching off a capability worth under $1 (the catalog's bar); prefer `"name-only"` for a skill used rarely.
7. `/mcp disable` in every project where a server is unused, or `claude mcp remove` without keeping the definition first.
8. A model or effort switch mid-task as a cost habit (it re-writes the cache on most models); at session start or a break.
9. More than a few lines of CLAUDE.md for one habit, or a rule Claude already follows.
10. A forced change where a notice or a habit gets the same saving with less risk, without offering that choice.

## Signals worth a recommendation (for the research pass and the insights)

| What the digest shows | What the docs recommend | Page |
|---|---|---|
| Long sessions, /clear late (CX1, CX4), mixed work in one session (SE3) | /clear between unrelated tasks; /rename then /resume to come back | best-practices, costs |
| Contexts past the SV5 threshold (CX1, CX6) | /compact with what to keep at natural breaks; CLAUDE.md compaction instructions | best-practices |
| A large share of cost before the first edit (EX9), many reads (EX6, EX8) | Subagents for investigation; plan mode for multi-file changes; specific prompts | best-practices, costs |
| Files read again and again (EX8), a typed language (OUT4), no language server (EX4) | A code intelligence plugin | plugins/code-intelligence |
| Large Bash output (CX3, EX6) | A hook that filters it; verbose runs in a subagent | costs |
| CLAUDE.md over 200 lines (CX5's memory files) | Trim to essentials; workflows into skills; `/doctor` for a checked-in file | memory, best-practices |
| Fix-and-retry loops, repeated corrections (EX7, the digest's EX15) | After two failed corrections, /clear and a better prompt; give Claude a check it can run | best-practices |
| Opus or Fable doing routine work (OV2, SV7) | Sonnet for day-to-day, Opus for hard reasoning, or opusplan | costs, model-config |
| Effort above the model's default (OV4, config) | The model's default; /effort per task | model-config |
| Explore or Plan on an expensive main model (SE5, SV7) | An Explore agent file with `model: haiku`, or a cheaper main model | sub-agents |
| Unused MCP servers or skills (SV3, EX2) | Remove or scope them; CLI tools over MCP | costs, mcp |
