---
name: optimize
description: Turn the usage report's insights into concrete Claude Code optimizations (settings, hooks, status line, CLAUDE.md, agent and habit changes), each with its theoretical saving (all time and per 30 days), a one-command apply (preview, backup, undo) where possible and exact manual steps otherwise, shown in the report's Optimizations tab. Run after /claude-usage:report.
disable-model-invocation: true
argument-hint: "[focus: cost | cache | context | hooks | <anything>] | apply <id>"
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/candidates.py" *), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/assemble.py" *), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apply.py" check *), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apply.py" show *), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apply.py" list *), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/usage_report.py" *), Bash(claude --version), WebFetch(domain:code.claude.com), Read(~/.claude-usage/**), Read(~/.claude/plugins/cache/claude-usage-optimizer/claude-usage/**), Edit(~/.claude-usage/data/notes-optimizations.json)
---

# Optimizations: from insights to changes

The script has already evaluated the catalog of known optimizations against this report and drafted every one whose rule
holds: numbers, apply steps, manual/undo/verify, docs and the links between them. You review those drafts, decide what needs
judgment, look for what the catalog doesn't cover, weigh the pairs, and publish the result as the report's **Optimizations**
tab, where each change can be applied on its own.

If `$ARGUMENTS` starts with `apply`, skip to "Applying on request" below.

## Ground rules

- Work only from the script's output in `<OUT>`, the output folder (`~/.claude-usage` by default; `usage_report.py --where
  [--claude-dir …]` prints it): `insights.json`, and in `<OUT>/data/`: `candidates.json`, `digest.md`, `config.json` (current
  setup, secrets removed), `metrics.json`, the CSVs. **Never read session transcripts** (`<claude dir>/projects/**/*.jsonl`).
  For a number the digest cuts short (it shows the first rows of a table),
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/candidates.py" --out "<OUT>" --show card:EX1` prints every figure of one card.
- Recommend only settings, hooks and features that exist in the user's Claude Code version. The verified reference is
  [reference/claude-code.md](reference/claude-code.md); if you need something it doesn't cover, check the official docs
  (`https://code.claude.com/docs/en/<page>.md`, e.g. settings, hooks, statusline, sub-agents, mcp, costs) and `claude --version`.
  Don't guess key names.
- You never change the user's setup. apply.py writes only after the user types y at their own terminal (Claude Code's
  tools have none, so from here it can only preview); your part is to prepare, preview and explain (see the end).
- Never propose disabling Claude Code's bundled skills or anything enabled by managed policy; never `claude mcp remove` to
  disable a server (it deletes its config and tokens); keep every change reversible.
- Never write `optimizations.json` yourself: `assemble.py` writes it from your notes (step 3).
- Run every command exactly as shown: one `python3 …` command, without `cd`, pipes, redirection or variables, so it matches
  the allowed tools and needs no permission prompt. The skill is allowed nothing else without the user's say-so.

Scripts: the plugin's scripts are in `${CLAUDE_PLUGIN_ROOT}/scripts`. Exactly those commands are pre-approved (each script by its full path); anything else, such as another program, `python3 -c`, a `cd` or a pipe, makes Claude Code ask the user first, so don't work around a refusal.

## 1. Load the inputs

The optimizations cover the report's period (the last 60 days unless the report was run with `--days`, `--since` or
`--all`). If the user asks for another period, run `/claude-usage:report` with it first (or tell them to).

1. What the report and its insights hold, in one call:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/candidates.py" --out "<OUT>" --show insights,optimizations,judgment,links,skipped,problems --brief
   ```
   - `insights`: its first line says whether `insights.json` is current for this report. If it says stale or missing, run the
     report skill's steps first (`/claude-usage:report`), or tell the user to. Then one line per insight: id (what
     optimizations link to), category, title, bottom line, saving per 30 days. Read `<OUT>/insights.json` itself only when
     you need an insight's detail.
   - `optimizations`: one draft per catalog entry whose rule holds and isn't in place yet, with the `rule` (why, with the
     numbers) and `notes` (what the script left for you to decide, e.g. project skills it left out).
   - `judgment`: entries whose call is yours (a habit or a setting, effort, a Stop hook's real source, a close call, a path to
     check), with the facts and, where one can be written, a `draft` you can adopt.
   - `links`: how the drafts relate (the catalog's pairs, with a note from each side).
   - `skipped`: entries whose rule doesn't hold, or that are already in place, and why.
   - `problems`: what validate.py says about the drafts taken together; normally empty. A problem there follows the draft
     into assemble: fix it in the notes (`edit`) or `drop` the draft.
   Without `--brief` the drafts are shown whole (manual, verify, undo, docs, apply steps).
2. `digest.md`, for the research pass (if you already read it in this conversation, don't read it again), and `config.json`
   when you need the setup's details.
3. [reference/catalog.md](reference/catalog.md): its "How optimizations interact" rules, and the entries named in `judgment`.
   [reference/claude-code.md](reference/claude-code.md) before proposing anything the drafts don't already cover.

## 2. Decide

**Review the drafts.** Their numbers come straight from the cards, and the script already checked the current setup (no
duplicate hook, no setting already at that value, no existing status line). Read each one as the user will: keep it as is,
drop it when something the rule can't see argues against it, or edit the prose where it should be specific to this user
(name the project, the file, the real cause). Don't rewrite a draft that already reads right.

**Make the judgment calls.** For each `judgment` entry: adopt its draft (with edits), write your own version, or leave it out
(an insight can say it instead). Say why in the summary when you leave out something with a real saving.

**Research pass.** Then look for what the catalog doesn't cover, specific to this user: a failing hook and its cause, a
project whose settings differ, a slash command or prompt repeated often enough to become a skill, a subagent type that
could run on a cheaper model through its own agent file, a noisy tool output a PostToolUse hook could trim, a Claude Code
feature the data shows they'd benefit from (plan mode, /rewind, /context, output styles, background tasks). Check anything
new against the docs before proposing it. Prefer fewer, stronger changes. For each, decide `kind` (hook, setting,
statusline, claude_md, agent, command, habit), `effort` (`one-click` only when apply.py can make the whole change;
`minutes` when the user must act, `habit` for behaviour), `risk`, `tradeoffs`, and `savings` (from the SV questions or a
measured cost: `usd_so_far` all time, `usd_per_month` per 30 days, `basis`, `kind`). Habits and manual changes still get
exact steps.

**Weigh them against each other.** Each saving is measured alone, as if that change were the only one. So go through the
optimizations in pairs and ask whether one changes the case for the other. Do they fix the same cost (the same misses, the
same big contexts, the same calls repriced)? Change the same setting? Does one only pay off once the other is in place?
`links` already carries the catalog's pairs between the drafts; check each note holds for this user, and add the pairs your
own items create (use the catalog's "How optimizations interact" rules). For example, a guard that warns before prompting
into an expired cache and a switch of the main thread to the 1h cache go after the same misses: with one in place the
other saves little, so they are an `alternative`. For each pair that interacts:
- `alternative` or `conflicts`: recommend one. Lead with the one the data favours and keep the other only as a stated
  choice (or drop it when the data clearly favours the first). Never present both as things to do.
- `overlaps`: keep both, name the shared part in each `savings.basis`, and never add them up in the summary.
- `complements` / `requires`: say so. For `requires`, the prerequisite comes first.

## 3. Write the notes, assemble

Write your notes with the Write tool to `<OUT>/data/notes-optimizations.json` (if Write refuses because the file exists,
Read it first), then run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/assemble.py" optimizations --out "<OUT>"
```

It builds `optimizations.json` from the drafts and your notes: source stamps (`insights_generated` included), `generated`,
`author`, `related` on both sides, links to the insights (and back, in insights.json). An optimization that is already
applied keeps its id when a newer version of the same change replaces it, so apply.py undoes the old one first instead of
adding a second copy; you don't need to read the old file. It validates both files and writes only when everything passes.
The notes file stays either way: after problems (nothing was written), `note:` lines, or an `apply.py check` ERROR (step 4),
Edit the notes file (don't write it again) and run the same command. The next full report run clears it. When the problems are in insights.json itself (for example
after a hand edit in `/claude-usage:brainstorm`), fix them in insights.json with Edit (Read it first, keep its `generated`),
or run `/claude-usage:report` again.

The notes, for example:

```json
{
  "summary": "Start with … For context size, pick one: auto-compact at 200K ($119 all time, ≈ $173 per 30 days) or a notice at 150K. …",
  "claude_code_version": "2.1.283",
  "drop": {"keep-awake": "the user works on a desktop that never sleeps"},
  "edit": {
    "effort-opus55-high": {},
    "stop-hook-followup": {"id": "memory-hook-turn-scope", "title": "…", "problem": "…", "manual": ["…"]},
    "mcp-off-where-unused": {"insights": ["tooling-unused-listings", "cache-tool-list-changes"]}
  },
  "add": [{"id": "claudepit-phase-inputs", "title": "…", "category": "workflow", "kind": "command", "problem": "…",
           "what_it_does": "…", "questions": ["EX1", "CX11"], "effort": "minutes", "risk": "low", "manual": ["…"],
           "undo": "…", "docs": [{"title": "Skills", "url": "https://code.claude.com/docs/en/skills"}]}],
  "relate": [{"a": "claudepit-phase-inputs", "b": "big-read-guard", "relation": "overlaps",
              "note_a": "…from this one's side", "note_b": "…from big-read-guard's side"}]
}
```

- `summary` (required): name the choices ("pick one: auto-compact at 200K or a notice at 150K"), give overlapping savings as a
  range or the larger one, never a sum, and say which savings are upper bounds. The tab shows the first sentence larger, as
  the lead: make it short and the one to remember. At most 900 characters (the schema's limit; aim for ~600).
- Length limits the schema enforces (characters): `title` 90, `problem` 500, `what_it_does` 700, `tradeoffs` 500, a
  `related` note 300, a `manual` step 400, `verify` and `undo` 300; at most 6 `questions` and 6 `related`.
- `claude_code_version`: from `claude --version`.
- `drop`: candidates to leave out (by id, or by catalog entry name), with a reason for yourself; it isn't written anywhere.
  Every draft in `optimizations` is kept unless dropped.
- `edit`: fields to change, per candidate id (or entry name). A value replaces the field, `null` removes it, `savings` is
  merged key by key, `"id"` renames it. A `judgment` draft is included only when it appears here (`{}` keeps it as drafted).
  `insights` replaces the links the script would add (the insights of the lever each draft acts on).
- `add`: your own optimizations, whole. Every field the schema requires: `id`, `title` (the change, without its saving),
  `category`, `kind`, `problem` (what it fixes, with the report's numbers), `what_it_does`, `questions`, `effort`, `risk`,
  `manual` (numbered steps a person can follow without this plugin), `undo` (how to revert by hand; apply.py also has
  `undo <id>`), `docs` (the official doc pages); plus as needed `insights`, `savings`, `tradeoffs`, `apply`, and `verify`
  (how to see it working).
- `relate`: pairs to add or change, set on both sides at once: `a`, `b`, `relation` (`alternative`, `conflicts`, `overlaps`,
  `complements`, or `requires` = a needs b first, with `note_a` only; `none` removes a pair), and a one-sentence note from
  each side (`note_a`, `note_b`; for `alternative`: when to pick which, the tab shows it as "Why this one").

Apply steps (for your own items) use only these actions. apply.py refuses anything else, and so does assemble (both check
`scripts/policy.py`, the one list of what this plugin may change), so a change outside it goes in `manual` instead:
- `write_file` with `source` = a file bundled with the plugin (`hooks/<name>.py` or `.sh`), to
  `~/.claude/hooks/claude-usage/<the same name>`, `mode` `"700"`. Installing a bundled hook also installs its helper
  `_session.py` and `prices.json` next to it. No other file and no literal content.
- `merge_json` (deep-merge an object; arrays gain missing items, so hook groups are appended, never replaced),
  `set_json` / `unset_json` with a JSON `pointer`, on `~/.claude/settings.json` or a project's `.claude/settings.local.json`.
  Only these keys: `model`, `effortLevel`, `modelSettings.<model>.effortLevel`, `alwaysThinkingEnabled`,
  `promptCacheTtl`, `subagentPromptCacheTtl`, `autoCompactEnabled`, `autoCompactWindow`, `skillOverrides`,
  `skillListingBudgetFraction`, `enabledPlugins` (only `false`), `disabledMcpjsonServers` (only adding),
  `disableClaudeAiConnectors`, `bashOutputMaxChars`, `cleanupPeriodDays` (only raising), `env` for the model, cache,
  compaction and output-limit variables in claude-code.md, a `statusLine` running the bundled `statusline.py`, and new
  `hooks` groups whose commands run bundled hooks as `python3 "$HOME/.claude/hooks/claude-usage/<name>.py" <args>` (bash
  for `.sh`). Never permissions, API keys or endpoints, MCP definitions, or a user's own hook.
- `append_text` to `~/.claude/CLAUDE.md` with a `marker` (idempotent; at most 8 lines and 800 characters: it loads in every
  session, so keep it to 1–3 lines).
There is no action that runs a command: commands (`claude mcp …`, installing something) are manual steps for the user.
Always write `~/.claude/…` paths: apply.py maps them to the Claude folder the report was built from.

validate.py (which assemble runs) rejects one-sided links, and two optimizations that change the same setting or act on the
same cost without a link. `note:` lines name judgment drafts you left out and applied changes no longer in the file.

## 4. Check, render, open

Replace `<OUT>` with the real output folder.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apply.py" check --dir "<OUT>"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/usage_report.py" --render --out "<OUT>" --open --tab optimizations
```

`check` previews every one-click optimization against the user's current files in one call, one line each. `ok` (with
the change it would make), `in place` (the setup already has it), `applied` (applied earlier with apply.py) and `update`
(applied earlier, this version differs) are all fine: just make sure each describes the change you intended. An `ERROR`
line needs action: when it is about the optimization (a path, a step, a value), fix the notes and assemble again; when
it names one of the user's own files (e.g. "is not valid JSON: fix it by hand first"), leave the notes and tell the user
in the reply. Relation warnings describe what the user already applied; mention them in the reply, don't drop items
because of them.

## 5. Reply

List the optimizations by saving (title, saving all time and per 30 days, effort), say how to apply them (the tab's copy
button gives the apply.py command to run in a terminal: it previews, asks, backs up, and can be undone; or ask you to preview
one with "apply <id>"), and that settings and hooks take effect in new sessions. Give
alternatives and conflicts as one choice ("X or Y: I'd pick X because …"), not as two items, and say which savings overlap.

## Applying on request

You can't apply anything: apply.py writes only after the user types y at their own terminal, and Claude Code's tools have
none (from here `apply` stops with "Nothing was changed" and exit code 3; don't try to get around that). When the user asks
to apply specific optimizations (by id or title), preview it:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apply.py" show <id> --dir "<OUT>"
```

Summarise what will change (files, keys, hooks), then give the command it prints under "To apply it" for the user to run
in their own terminal. It shows the same preview and asks them there; afterwards it prints the undo command
(`apply.py undo <id>`, which also asks). One optimization at a time. `show` also lists how it relates to the others. It
warns when an alternative or a conflicting one is already applied, or when one it needs isn't: tell the user, and let
them choose (undo the other first, apply the prerequisite, or skip).
