# Claude Code: what can be configured (verified reference)

Checked against Claude Code **2.1.285**: every settings key below appears in that version's settings schema with the
description quoted; hook output fields were checked in its hook parser. Anything marked *(docs)* comes from the official
documentation. For anything else, fetch the docs page (URLs at the end) before proposing it. What to recommend, and what
never to, is in [best-practices.md](best-practices.md).

## Where settings live

- `~/.claude/settings.json` (user, all projects) < `<project>/.claude/settings.json` (checked in) <
  `<project>/.claude/settings.local.json` (personal, not checked in). Managed policy overrides everything. Later wins, so a
  project file can override a user setting.
- `~/.claude.json` holds app state, including user- and local-scope MCP servers and per-project MCP toggles. Don't edit it by
  hand while Claude Code runs; use the commands that write it (`claude mcp add/remove -s <scope>`, `/mcp`).
- Settings and hooks are read when a session starts: changes apply to new sessions (`/autocompact` also applies to the current one).
- A value outside a key's schema is dropped silently: the setting then falls back to its default, so write exactly the values below.

## Settings that affect cost, context and caching

| Key | Values | What it does (schema description) |
|---|---|---|
| `model` | alias (`opus`, `sonnet`, `haiku`, `fable`, `opus[1m]`, `opusplan`) or model id | Default model. |
| `effortLevel` | `low` `medium` `high` `xhigh` | "Persisted effort level for supported models." `max` is not a saved level (it is dropped; `maxEffortLevel` caps effort instead). Unset, each model uses its own default *(docs)*: `medium` on Opus 5.5 and Sonnet 5.5, `xhigh` on Opus 4.7, `high` on every other model. |
| `modelSettings` | `{"<canonical model id>": {"effortLevel": …}}` | "Per-model settings keyed by canonical model name." A model's own `effortLevel` beats the global one. |
| `alwaysThinkingEnabled` | bool | "When false, thinking is disabled. When absent or true, thinking is enabled automatically for supported models." *(docs)* It has no effect on Opus 5.5, Sonnet 5.5 or the Fable models: their thinking can't be turned off. |
| `promptCacheTtl` | `"5m"` \| `"1h"` | "Prompt cache TTL for the main conversation … Unset = automatic: 1 hour on a Claude subscription within its usage limits, 5 minutes on an API key, Bedrock, Vertex or Foundry. 1-hour cache writes are billed at a higher rate; the cache stays warm across longer breaks." Env `CLAUDE_CODE_PROMPT_CACHE_TTL` takes precedence. |
| `subagentPromptCacheTtl` | `"5m"` \| `"1h"` | "Prompt cache TTL for everything outside the main conversation — subagents, workflows, background and helper requests … Unset = automatic (5 minutes unless ENABLE_PROMPT_CACHING_1H=1)." Env `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL` takes precedence. |
| `autoCompactEnabled` | bool | "Automatically compact conversation when context fills." |
| `autoCompactWindow` | an integer from `100000` to `1000000` | "Auto-compact summarizes the conversation when context usage approaches this limit. The actual threshold is the minimum of this setting and your model's maximum context window." Unset = auto: *(docs)* a native 1M-window model compacts "at about 967K tokens by default", a 200K model near 200K. Claude Code's own note: "The auto setting picks a window tuned for your model and is strongly recommended for the best cost and performance", and, when overridden, "Overriding auto may result in high token usage, especially when resuming long sessions." `/autocompact 500k` (100K–1M, `auto` to reset) saves it for you; env `CLAUDE_CODE_AUTO_COMPACT_WINDOW` (a plain token count) takes precedence. |
| `precomputeCompactionEnabled` | bool | "Precompute the compaction summary in the background before it is needed. Only applies when auto-compact is on." (Latency, not cost; not something the plugin sets.) |
| `skillOverrides` | `{"<skill name>": "on" \| "name-only" \| "user-invocable-only" \| "off"}` | "Per-skill listing overrides … "name-only" lists the skill without its description; "user-invocable-only" hides it from the model but keeps /name; "off" hides it from both." |
| `disableBundledSkills` | bool | Removes the skills and workflows that ship with Claude Code. Don't propose it (see disable rules). |
| `skillListingBudgetFraction` | 0–1 (default 0.01) | "Fraction of the context window (in characters) reserved for the skill listing … When the listing exceeds this, descriptions are shortened to fit." |
| `enabledPlugins` | `{"<plugin>@<marketplace>": false}` | Enable/disable an installed plugin. |
| `disableClaudeAiConnectors` | bool | "When true in any settings source, claude.ai MCP cloud connectors are not auto-fetched or connected." |
| `enabledMcpjsonServers` / `disabledMcpjsonServers` / `enableAllProjectMcpServers` | names / bool | Approve or reject the servers of a project's `.mcp.json`. |
| `bashOutputMaxChars` | integer (default 30000; clamped to 4000–128000) | "How many characters of a successful Bash or PowerShell command's output Claude receives inline … Output past this is saved to a file and Claude receives a short preview plus the path." |
| `cleanupPeriodDays` | days (default 30) | Transcript retention. The usage report can only see what is retained: raise it (e.g. 90) for longer history. |
| `statusLine` | `{"type": "command", "command": "…", "padding": 0, "refreshInterval": N}` | Command whose stdout is the status line. |
| `hooks` | see below | Hook definitions. |
| `disableAllHooks` | bool | Turns off all hooks and the status line. |
| `env` | `{"NAME": "value"}` | Environment variables for every session. |

Environment variables (set in `env`): `CLAUDE_CODE_SUBAGENT_MODEL` (the default model for subagents that name none, see
Subagents), `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` (`1`: that model for every subagent), `CLAUDE_CODE_AUTO_COMPACT_WINDOW`,
`ENABLE_PROMPT_CACHING_1H`, `FORCE_PROMPT_CACHING_5M`, `DISABLE_PROMPT_CACHING` (and per-family `_HAIKU`, `_SONNET`, `_OPUS`,
`_FABLE`; never propose them), `BASH_MAX_OUTPUT_LENGTH`, `MAX_MCP_OUTPUT_TOKENS`, `MAX_THINKING_TOKENS` (only models with a
fixed thinking budget; adaptive models ignore it), `ENABLE_TOOL_SEARCH` (deferred MCP tool loading, the default on supported models).

## Prompt caching *(docs + schema)*

- Main conversation: 1-hour entries on a subscription within its plan usage (5 minutes once it draws on usage credits), else
  5 minutes; subagents and helpers: 5 minutes unless configured. A 1-hour write costs 2× the input price, a 5-minute write
  1.25×, a read 0.1×.
- Invalidates the cache (full re-write): switching model (also `opusplan`'s plan-mode toggle, and a skill whose frontmatter
  names another model), changing effort (except on Opus 5.5, Sonnet 5.5 and Fable 5.1 with an API key or subscription),
  turning fast mode on, MCP tool definitions changing when they load upfront (with tool search, the default, a server that
  connects mid-session doesn't), enabling or disabling a plugin that has MCP servers, denying a whole tool without tool
  search, compaction, many accumulated images, a Claude Code upgrade.
- Keeps the cache: editing files, editing CLAUDE.md mid-session (it applies after `/clear`, `/compact` or a restart), changing
  permission mode or output style, invoking skills and commands, `/recap`, `/rewind`, spawning a subagent (its own cache), a
  resumed subagent within its cache lifetime, a fork (reads the parent's cache).
- Compaction while the cache is warm reads the history from the cache ("a mid-session `/compact` costs a fraction of what the
  context size suggests"); after the cache expired it reprocesses the whole history uncached.
- The report measures what actually happened (CX8–CX12, SV4, SV6): prefer its numbers over these rules.

## Hooks (checked in the hook parser)

Config (in any settings file, or a plugin's `hooks/hooks.json`):

```json
{"hooks": {"<Event>": [{"matcher": "<tool regex, for tool events>", "hooks": [
  {"type": "command", "command": "python3 \"$HOME/.claude/hooks/claude-usage/x.py\" --flag 1", "timeout": 10}]}]}}
```

`timeout` is in seconds. The command runs in a shell (`$HOME`, `$CLAUDE_PROJECT_DIR` expand) and gets a JSON object on
stdin: `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `permission_mode`, plus per event: `prompt`
(UserPromptSubmit), `tool_name`/`tool_input` (PreToolUse, PostToolUse; PostToolUse also the result), `source`
(SessionStart), `stop_hook_active` (Stop), `trigger` (PreCompact).

Events: `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `SubagentStop`, `StopFailure`,
`PreCompact`, `PostCompact`, `Notification`, `SessionEnd`, `PreModelSwitch` (and more; check the docs for others).

Output: exit 0 and optionally print one JSON object:

- `{"decision": "block", "reason": "…"}`: block. For UserPromptSubmit the prompt is not sent and the reason is shown to the
  user; for Stop it makes Claude continue with the reason.
- `{"continue": false, "stopReason": "…"}`: stop processing; the reason is shown to the user.
- `{"systemMessage": "…"}`: a notice shown to the user; nothing is blocked.
- `{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "…"}}`: text added to Claude's context
  (UserPromptSubmit, SessionStart, PostToolUse).
- PreToolUse: `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason":
  "…"}}` (`allow` / `deny` / `ask`); the reason is shown to Claude on deny. `updatedInput` rewrites the tool input (the docs'
  example filters a test run's output to its failures).
- Exit code 2 = blocking error: stderr is fed back (to Claude for tool events, to the user for UserPromptSubmit). Other non-zero
  codes are non-blocking errors. A hook that errors must never block by accident: catch everything and exit 0.

Bundled hook scripts (scripts/hooks, stdlib Python, fail open):

| Script | Event | What it does | Args |
|---|---|---|---|
| `stale_cache_guard.py` | UserPromptSubmit | Blocks once when the last call is older than the cache lifetime and the context is big; shows the re-write cost and the options (/clear, /compact, send again); sending again within the grace period goes through. | `--min-context 60000 --grace 180 [--ttl 3600]` |
| `context_guard.py` | UserPromptSubmit | A notice (systemMessage) when the context passes the threshold: /clear before unrelated work, /compact with what to keep at the next natural break; repeats every `--step` tokens. | `--threshold 150000 --step 50000` (never below 100000) |
| `keep_awake.sh` | UserPromptSubmit | macOS: `caffeinate -i` for N seconds after each prompt, one process at a time. | `[seconds=7200]` |
| `big_read_guard.py` | PreToolUse, matcher `Read` | Denies the first whole-file Read of a file over N KB with a reason telling Claude to grep and read a range; a repeat goes through. | `--max-kb 60` |
| `statusline.py` | statusLine | model · context size · cache warm Xm / cold: next message re-writes N · session cost. | none |

## Status line input *(docs)*

JSON on stdin with (fields may be absent): `model.display_name`, `session_id`, `transcript_path`, `cost.total_cost_usd`,
`context_window.used_percentage` and `context_window.current_usage` (input/cache read/cache creation tokens),
`prompt_cache` (`warm`, `ttl`, `expires_at`, `recache_tokens_if_cold`, `hit_ratio`), `rate_limits`, `effort.level`.

## Subagents *(docs)*

Agent files: `~/.claude/agents/<name>.md` or `<project>/.claude/agents/<name>.md`, frontmatter `name`, `description`,
`tools`, `model` (`haiku`, `sonnet`, `opus`, `fable`, `inherit` or an id), `experimental.cacheTtl` (`"5m"`/`"1h"`).

Model order: the model passed for the call > the definition's `model` (`inherit` = the main model) >
`CLAUDE_CODE_SUBAGENT_MODEL` > the main model. So the variable moves general-purpose subagents and agent files without a
`model`, but not the built-in Explore and Plan (both `inherit`; Explore is capped at Opus) nor forks (the parent's model).
`CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` applies the variable to every subagent except forks, and Claude can then no longer pass a
model for one call. A user or project agent named `Explore` replaces the built-in one, prompt and tools included, with its own
`model`. `/tasks` shows the model a running subagent uses.

Resuming a finished subagent (SendMessage) re-sends its history; within its cache lifetime (5 minutes by default) that reads
the cache the original run warmed, after it the history is written again. The report (CX8, SV4) shows what happened for this user.

## Disable rules (Claude Code's own guidance)

- Skill (user `~/.claude/skills`, synced, or plugin-provided): `"skillOverrides": {"<name>": "off"}` in `~/.claude/settings.json`;
  a project skill: in that project's `.claude/settings.local.json`. `"name-only"` is a lighter option for rarely used skills.
- Plugin: `"enabledPlugins": {"<name>@<marketplace>": false}` in the same scope that enabled it (a user-level false is
  overridden by a project-level true).
- MCP server (user/local scope): used nowhere, take it out of the user config (`claude mcp remove <server> -s user`, after
  `claude mcp get <server>` to keep its definition); used in some projects, set it up there alone (`-s local`, or the
  project's `.mcp.json`) and then remove it from the user scope. `/mcp disable <server>` switches one off in one project;
  don't make it the advice for "every project where it's unused": that is the per-project toggling to avoid. Project
  `.mcp.json` server: its name in `disabledMcpjsonServers` in `.claude/settings.local.json`. claude.ai connectors:
  `"disableClaudeAiConnectors": true`.
- Prefer setting a thing up where it is used over switching it off where it isn't: a personal skill used by one project
  goes into that project's `.claude/skills/`; a plugin one project uses is enabled in that project's settings and off in
  `~/.claude/settings.json`.
- Never propose disabling bundled skills (skills not in the config's skills inventory and without a plugin namespace) or
  anything set by managed policy.

## Commands and habits worth knowing

`/context` (what fills the context now), `/usage` (session tokens and cost, with a prompt-cache line: hit share, misses,
warm or cold), `/clear` (free; `/resume` brings the old session back), `/compact [what to keep]`, `/autocompact [auto|<tokens>]`,
`/rewind` or Esc Esc (go back to a cached point, or summarize from a message), `/btw` (a side question kept out of the
history), `/recap` (a summary that keeps the cache), `/rename` and `/resume`, `/model` (incl. `opusplan`: Opus in plan mode,
Sonnet otherwise), `/effort`, `/mcp`, `/hooks`, `/tasks`, `/statusline`, `/doctor` (proposes cuts to a checked-in CLAUDE.md),
plan mode (Shift+Tab), `@file` mentions instead of asking Claude to search, background tasks for long commands.

## Docs

- Best practices: https://code.claude.com/docs/en/best-practices
- Settings: https://code.claude.com/docs/en/settings
- Hooks: https://code.claude.com/docs/en/hooks · guide: https://code.claude.com/docs/en/hooks-guide
- Status line: https://code.claude.com/docs/en/statusline
- Costs: https://code.claude.com/docs/en/costs
- Prompt caching: https://code.claude.com/docs/en/prompt-caching
- Sessions: https://code.claude.com/docs/en/sessions
- Subagents: https://code.claude.com/docs/en/sub-agents
- MCP: https://code.claude.com/docs/en/mcp
- Skills: https://code.claude.com/docs/en/skills
- Memory (CLAUDE.md): https://code.claude.com/docs/en/memory
- Model configuration: https://code.claude.com/docs/en/model-config
- Code intelligence plugins: https://code.claude.com/docs/en/plugins/code-intelligence
