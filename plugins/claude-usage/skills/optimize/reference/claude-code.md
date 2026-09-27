# Claude Code: what can be configured (verified reference)

Checked against Claude Code **2.1.281**: every settings key below appears in that version's settings schema with the
description quoted; hook output fields were checked in its hook parser. Anything marked *(docs)* comes from the official
documentation. For anything else, fetch the docs page (URLs at the end) before proposing it.

## Where settings live

- `~/.claude/settings.json` (user, all projects) < `<project>/.claude/settings.json` (checked in) <
  `<project>/.claude/settings.local.json` (personal, not checked in). Managed policy overrides everything. Later wins, so a
  project file can override a user setting.
- `~/.claude.json` holds app state, including user- and local-scope MCP servers and per-project MCP toggles. Don't edit it by
  hand while Claude Code runs; use the commands that write it (`claude mcp add/remove -s <scope>`, `/mcp`).
- Settings and hooks are read when a session starts: changes apply to new sessions.

## Settings that affect cost, context and caching

| Key | Values | What it does (schema description) |
|---|---|---|
| `model` | alias (`opus`, `sonnet`, `haiku`, `opus[1m]`, `opusplan`) or model id | Default model. |
| `effortLevel` | `low` `medium` `high` `xhigh` `max` | "Persisted effort level for supported models." Higher effort → more thinking tokens (output price). |
| `alwaysThinkingEnabled` | bool | "When false, thinking is disabled. When absent or true, thinking is enabled automatically for supported models." |
| `promptCacheTtl` | `"5m"` \| `"1h"` | "Prompt cache TTL for the main conversation … Unset = automatic: 1 hour on a Claude subscription within its usage limits, 5 minutes on an API key, Bedrock, Vertex or Foundry. 1-hour cache writes are billed at a higher rate; the cache stays warm across longer breaks." Env `CLAUDE_CODE_PROMPT_CACHE_TTL` takes precedence. |
| `subagentPromptCacheTtl` | `"5m"` \| `"1h"` | "Prompt cache TTL for everything outside the main conversation — subagents, workflows, background and helper requests … Unset = automatic (5 minutes unless ENABLE_PROMPT_CACHING_1H=1)." Env `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL` takes precedence. |
| `autoCompactEnabled` | bool | "Automatically compact conversation when context fills." |
| `autoCompactWindow` | tokens, e.g. `200000` (also `"200k"`), or unset/auto | "Auto-compact summarizes the conversation when context usage approaches this limit. The actual threshold is the minimum of this setting and your model's maximum context window." Claude Code's own note: "The auto setting picks a window tuned for your model and is strongly recommended … Overriding auto may result in high token usage, especially when resuming long sessions." Env `CLAUDE_CODE_AUTO_COMPACT_WINDOW` takes precedence. |
| `skillOverrides` | `{"<skill name>": "on" \| "name-only" \| "user-invocable-only" \| "off"}` | "Per-skill listing overrides … "name-only" lists the skill without its description; "user-invocable-only" hides it from the model but keeps /name; "off" hides it from both." |
| `disableBundledSkills` | bool | Removes the skills and workflows that ship with Claude Code. Don't propose it (see disable rules). |
| `skillListingBudgetFraction` | 0–1 (default 0.01) | Share of the context window reserved for the skill listing; descriptions are shortened past it. |
| `enabledPlugins` | `{"<plugin>@<marketplace>": false}` | Enable/disable an installed plugin. |
| `disableClaudeAiConnectors` | bool | "When true in any settings source, claude.ai MCP cloud connectors are not auto-fetched or connected." |
| `enabledMcpjsonServers` / `disabledMcpjsonServers` / `enableAllProjectMcpServers` | names / bool | Approve or reject the servers of a project's `.mcp.json`. |
| `bashOutputMaxChars` | 4000–128000 (default 30000) | "How many characters of a successful Bash or PowerShell command's output Claude receives inline … Output past this is saved to a file and Claude receives a short preview plus the path." |
| `cleanupPeriodDays` | days (default 30) | Transcript retention. The usage report can only see what is retained: raise it (e.g. 90) for longer history. |
| `statusLine` | `{"type": "command", "command": "…", "padding": 0, "refreshInterval": N}` | Command whose stdout is the status line. |
| `hooks` | see below | Hook definitions. |
| `disableAllHooks` | bool | Turns off all hooks and the status line. |
| `env` | `{"NAME": "value"}` | Environment variables for every session. |

Environment variables (set in `env`): `CLAUDE_CODE_SUBAGENT_MODEL` (default model for subagents; an agent's own `model`
field or a per-call model wins over it), `ENABLE_PROMPT_CACHING_1H`, `FORCE_PROMPT_CACHING_5M`, `DISABLE_PROMPT_CACHING`
(and per-family `_HAIKU`, `_SONNET`, `_OPUS`), `BASH_MAX_OUTPUT_LENGTH`, `MAX_MCP_OUTPUT_TOKENS`, `MAX_THINKING_TOKENS`,
`ENABLE_TOOL_SEARCH` (deferred MCP tool loading).

## Prompt caching *(docs + schema)*

- Main conversation: 1-hour entries on a subscription within limits, else 5 minutes; subagents and helpers: 5 minutes
  unless configured. A 1-hour write costs 2× the input price, a 5-minute write 1.25×, a read 0.1×.
- Invalidates the cache (full re-write): switching model, changing tool definitions that sit in the prompt prefix (MCP
  servers connecting/disconnecting without tool search, enabling a plugin with MCP servers), effort changes on some
  models, compaction, a Claude Code upgrade.
- Keeps the cache: editing files, editing CLAUDE.md mid-session, changing permission mode, invoking skills, `/rewind`.
- The report measures what actually happened (CX8–CX12, SV3, SV5): prefer its numbers over these rules.

## Hooks (checked in the 2.1.281 hook parser)

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
  "…"}}` (`allow` / `deny` / `ask`); the reason is shown to Claude on deny.
- Exit code 2 = blocking error: stderr is fed back (to Claude for tool events, to the user for UserPromptSubmit). Other non-zero
  codes are non-blocking errors. A hook that errors must never block by accident: catch everything and exit 0.

Bundled hook scripts (scripts/hooks, stdlib Python, fail open):

| Script | Event | What it does | Args |
|---|---|---|---|
| `stale_cache_guard.py` | UserPromptSubmit | Blocks once when the last call is older than the cache lifetime and the context is big; shows the re-write cost and the options; sending again within the grace period goes through. | `--min-context 60000 --grace 180 [--ttl 3600]` |
| `context_guard.py` | UserPromptSubmit | A notice (systemMessage) when the context passes the compaction threshold; repeats every `--step` tokens. | `--threshold 150000 --step 50000` |
| `keep_awake.sh` | UserPromptSubmit | macOS: `caffeinate -i` for N seconds after each prompt, one process at a time. | `[seconds=7200]` |
| `big_read_guard.py` | PreToolUse, matcher `Read` | Denies the first whole-file Read of a file over N KB with a reason telling Claude to grep and read a range; a repeat goes through. | `--max-kb 60` |
| `statusline.py` | statusLine | model · context size · cache warm Xm / cold: next message re-writes N · session cost. | none |

## Status line input *(docs)*

JSON on stdin with (fields may be absent): `model.display_name`, `session_id`, `transcript_path`, `cost.total_cost_usd`,
`context_window.used_percentage` and `context_window.current_usage` (input/cache read/cache creation tokens),
`prompt_cache` (`warm`, `ttl`, `expires_at`, `recache_tokens_if_cold`, `hit_ratio`), `rate_limits`, `effort.level`.

## Subagents *(docs)*

Agent files: `~/.claude/agents/<name>.md` or `<project>/.claude/agents/<name>.md`, frontmatter `name`, `description`,
`tools`, `model` (`haiku`, `sonnet`, `opus`, `inherit` or an id), `experimental.cacheTtl` (`"5m"`/`"1h"`). Model precedence:
per-call model > agent `model` > `CLAUDE_CODE_SUBAGENT_MODEL` > the main model. Resuming a finished subagent (SendMessage)
re-sends its history; the report (CX8) shows whether that hit the cache for this user.

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

`/context` (what fills the context now), `/usage` or `/cost` (session tokens and cost), `/compact [what to keep]`, `/clear`,
`/rewind` (go back without losing the cached prefix), `/resume`, `/model` (incl. `opusplan`: Opus in plan mode, Sonnet
otherwise), `/effort`, `/mcp`, `/hooks`, `/statusline`, plan mode (Shift+Tab), `@file` mentions instead of asking Claude to
search, background tasks for long commands.

## Docs

- Settings: https://code.claude.com/docs/en/settings
- Hooks: https://code.claude.com/docs/en/hooks · guide: https://code.claude.com/docs/en/hooks-guide
- Status line: https://code.claude.com/docs/en/statusline
- Costs: https://code.claude.com/docs/en/costs
- Prompt caching: https://code.claude.com/docs/en/prompt-caching
- Subagents: https://code.claude.com/docs/en/sub-agents
- MCP: https://code.claude.com/docs/en/mcp
- Skills: https://code.claude.com/docs/en/skills
- Memory (CLAUDE.md): https://code.claude.com/docs/en/memory
- Model configuration: https://code.claude.com/docs/en/model-config
