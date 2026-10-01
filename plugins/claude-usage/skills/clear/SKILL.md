---
name: clear
description: Remove this plugin's report files (the report and its data, the insights and optimizations, videos, share files, reports others sent you, the company report) to start from scratch. Your Claude Code setup and any applied optimizations stay as they are.
disable-model-invocation: true
argument-hint: "[--yes] [--keep video,share,received,company] [--out DIR]"
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/clear.py" *)
---

# Start from scratch

The user wants this plugin's files gone, to start again with a new report. A script does it: in the report folder only,
and only this plugin's own files, by name. You show what will go, confirm, and run it.

## Ground rules

- Remove files only through the script: never `rm` or any other command, and nothing outside the report folder. The
  script refuses a folder that isn't a claude-usage report folder (and home, the filesystem root, the Claude folder), and
  leaves every file that isn't this plugin's.
- The user's setup stays as it is: settings, the hooks and CLAUDE.md lines an applied optimization installed, and the
  transcripts. While any optimization is applied, `applied/` (apply.py's record and backups) stays too, so
  `apply.py undo <id>` keeps working. To have it go as well, the user undoes those optimizations first, in their own
  terminal (each undo asks there), then clears again. Never run apply.py yourself.
- Run every command exactly as shown: one `python3 …` command, without `cd`, pipes, redirection or variables, so it matches
  the allowed tools and needs no permission prompt. The skill is allowed nothing else without the user's say-so.

Scripts: the plugin's scripts are in `${CLAUDE_PLUGIN_ROOT}/scripts`. Exactly this command is pre-approved (the script by its full path); anything else makes Claude Code ask the user first, so don't work around a refusal.

## 1. Preview

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/clear.py"
```

- Add `--out "DIR"` only when the user named another report folder, or `--claude-dir "DIR"` for another Claude folder.
- Add `--keep` with any of `video`, `share`, `received`, `company` when the user wants those kept ("clear it but keep my
  video": `--keep video`).

It changes nothing. It prints what it would remove (each item and its size), what it keeps and why (optimizations still
applied, files that aren't this plugin's, what `--keep` spared), and the command that removes it. If it finds nothing to
clear, or refuses the folder, tell the user what it said and stop.

## 2. Confirm

If `$ARGUMENTS` contains `--yes`, the user has confirmed already: go on to step 3.

Otherwise ask once, with the AskUserQuestion tool: say how many items and how much will go, and, when the preview lists
them, that `received/` holds reports other people sent (gone for good unless they still have the share files) and that
`insights.json` and `optimizations.json` come back only when Claude writes them again. Options: remove them, or keep
everything. If they choose to keep everything, or the tool isn't available (a non-interactive run), stop here and give the
command the preview printed, for them to run when they want.

## 3. Remove

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/clear.py" --yes
```

With the same `--out`, `--claude-dir` and `--keep` as the preview. It removes the items, then the folder itself when
nothing else is left in it, and names anything it couldn't remove.

## 4. Reply

Short: what was removed (items and size), what was kept and why, and the way back: `/claude-usage:report` builds a new
report (and writes new insights), then `/claude-usage:optimize` new optimizations.
