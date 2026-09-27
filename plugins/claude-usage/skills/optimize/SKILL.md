---
name: optimize
description: Turn the usage report's insights into concrete Claude Code optimizations (settings, hooks, status line, CLAUDE.md, agent and habit changes), each with its theoretical saving (all time and per 30 days), a one-command apply (preview, backup, undo) where possible and exact manual steps otherwise, shown in the report's Optimizations tab. Run after /claude-usage:report.
disable-model-invocation: true
argument-hint: "[focus: cost | cache | context | hooks | <anything>] | apply <id>"
allowed-tools: Read, Write, Edit, Bash(python3 *), Bash(ls *), Bash(open *), Bash(xdg-open *), Bash(claude --version), WebFetch
---

# Optimizations: from insights to changes

You read what the usage report extracted and the insights written from it, pick the changes that would have saved the most
(or fix something broken), prepare each one so it can be applied **change by change**, and publish them as the report's
**Optimizations** tab.

If `$ARGUMENTS` starts with `apply`, skip to "Applying on request" below.

## Ground rules

- Work only from the script's output in `<OUT>`, the output folder (`~/.claude-usage` by default; `usage_report.py --where
  [--claude-dir …]` prints it): `insights.json`, and in `<OUT>/data/`: `digest.md`, `config.json` (current setup, secrets
  removed), `metrics.json`, the CSVs. **Never read session transcripts** (`<claude dir>/projects/**/*.jsonl`).
- Recommend only settings, hooks and features that exist in the user's Claude Code version. The verified reference is
  [reference/claude-code.md](reference/claude-code.md); if you need something it doesn't cover, check the official docs
  (`https://code.claude.com/docs/en/<page>.md`, e.g. settings, hooks, statusline, sub-agents, mcp, costs) and `claude --version`.
  Don't guess key names.
- Never change anything yourself unless the user asks you to apply a specific optimization (see the end).
- Never propose disabling Claude Code's bundled skills or anything enabled by managed policy; never `claude mcp remove` to
  disable a server (it deletes its config and tokens); keep every change reversible.

Scripts live in `${CLAUDE_SKILL_DIR}/../../scripts` (or two levels up from this skill's base directory).

## 1. Load the inputs

1. If `<OUT>/insights.json` is missing, or its `source.metrics_generated` differs from the "Generated …"
   line of `<OUT>/data/digest.md`, run the report skill's steps first (`/claude-usage:report`), or tell the user to.
2. Read `data/digest.md` (numbers; in parts if it is too big for one read), `insights.json` (what matters), `data/config.json` (what is already set: settings per file, hooks,
   MCP servers, installed plugins, a skills inventory telling user/project/synced/plugin skills apart from bundled ones).
3. Read [reference/claude-code.md](reference/claude-code.md) and [reference/catalog.md](reference/catalog.md).

## 2. Find the optimizations

Two passes:

**Catalog pass.** Go through every entry of the catalog. For each: evaluate its "when" rule against the digest and config;
if it applies, quantify it with the named questions, check the current config so you don't duplicate, conflict with or
overwrite something (an existing `statusLine`, an existing hook on the same event, a setting already at that value), and adapt
the apply template (paths, thresholds from the data, names from the inventory). Skip entries whose rule doesn't hold; don't pad.

**Research pass.** Then look for what the catalog doesn't cover, specific to this user: a failing hook and its cause, a
project whose settings differ, a slash command or prompt repeated often enough to become a skill, a subagent type that
could run on a cheaper model through its own agent file, a noisy tool output a PostToolUse hook could trim, a Claude Code
feature the data shows they'd benefit from (plan mode, /rewind, /context, output styles, background tasks). Check anything
new against the docs before proposing it. Prefer fewer, stronger changes.

For every optimization decide: `kind` (hook, setting, statusline, claude_md, agent, command, habit), `effort` (`one-click`
only when apply.py can make the whole change; `minutes` when the user must act, `habit` for behaviour), `risk`, `tradeoffs`,
and `savings` (from the SV questions or a measured cost; same rules as insights: `usd_so_far` (all time), `usd_per_month` (required: per 30 days),
`basis`, `kind`). Habits and manual changes still get exact steps.

## 3. Write optimizations.json

Follow `${CLAUDE_SKILL_DIR}/../../schemas/optimizations.schema.json`:

- `source.metrics_generated` = digest's "Generated" timestamp; `source.insights_generated` = insights.json `generated`;
  `source.claude_code_version` from `claude --version`.
- `apply.steps` use these actions (apply.py executes them; everything must stay under the home directory):
  - `write_file` with `source` = a bundled file under scripts/ (e.g. `hooks/stale_cache_guard.py`) or literal `content`, plus
    `mode` for scripts. Installing a bundled hook also installs its helper `_session.py` and `prices.json` next to it.
  - `merge_json` (deep-merge an object into a JSON file; arrays gain missing items, so hook groups are appended, never replaced),
    `set_json` / `unset_json` with a JSON `pointer` (array indices allowed, e.g. `/hooks/UserPromptSubmit/0/hooks/0/command`).
  - `append_text` with a `marker` (idempotent; for CLAUDE.md snippets; keep them to 1–3 lines, they load in every session).
  - `run` for a shell command (shown and confirmed before running; prefer settings over commands).
- Install hooks as copies under `~/.claude/hooks/claude-usage/` and reference them as
  `python3 "$HOME/.claude/hooks/claude-usage/<name>.py" <args>` (plugin paths can change on update). Always write `~/.claude/…`
  paths: when the report was built from another Claude folder (`$CLAUDE_CONFIG_DIR` or `--claude-dir`), apply.py maps them
  there, hook commands included (it reads that folder from `<OUT>/data/config.json`). User settings are
  `~/.claude/settings.json` in that same sense.
- `manual`: numbered steps a person can follow without this plugin. `undo`: how to revert by hand (apply.py also has `undo <id>`).
  `verify`: how to see it working. `docs`: the official doc pages.
- Link both ways: `insights` (ids) on each optimization, and add the optimization ids to the matching insights'
  `optimizations` arrays in insights.json.

## 4. Validate, render, open

Replace `<OUT>` with the real output folder (see the ground rules).

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/validate.py" optimizations "<OUT>/optimizations.json" --metrics "<OUT>/data/metrics.json" --insights "<OUT>/insights.json"
python3 "${CLAUDE_SKILL_DIR}/../../scripts/validate.py" insights "<OUT>/insights.json" --metrics "<OUT>/data/metrics.json"
python3 "${CLAUDE_SKILL_DIR}/../../scripts/apply.py" list --dir "<OUT>"
python3 "${CLAUDE_SKILL_DIR}/../../scripts/usage_report.py" --render --out "<OUT>" --open --tab optimizations
```

Also run `apply.py show <id> --dir "<OUT>"` for each one-click optimization and read the preview: it must change exactly what you intended.

## 5. Reply

List the optimizations by saving (title, saving all time and per 30 days, effort), say how to apply them (the tab's copy button runs apply.py:
preview, confirm, backup, undo; or ask you "apply <id>"), and that settings and hooks take effect in new sessions.

## Applying on request

When the user asks to apply specific optimizations (by id or title): run `apply.py show <id> --dir "<OUT>"`, summarise what will change,
and apply with `apply.py apply <id> --yes --dir "<OUT>"` only after they confirm in the conversation. Afterwards give the undo command.
One optimization at a time.
