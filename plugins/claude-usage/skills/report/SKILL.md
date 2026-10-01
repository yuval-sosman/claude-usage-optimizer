---
name: report
description: Build and open the Claude Code usage report from the local transcripts (cost, caching, context, sessions, tools, hooks), with Claude-written insights on what each change would have saved. Use when the user asks to analyze, audit or explain their Claude Code usage, cost, cache hits or context size, or to run, refresh or open the usage report.
argument-hint: "[--days N (default 60) | --since YYYY-MM-DD --until YYYY-MM-DD | --all] [--claude-dir DIR] [--no-insights] [--no-open]"
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/usage_report.py" *), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/candidates.py" *), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/assemble.py" *), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/open.py" *), Read(~/.claude-usage/**), Read(~/.claude/plugins/cache/claude-usage-optimizer/claude-usage/**), Edit(~/.claude-usage/data/notes-insights.json)
---

# Usage report + insights

You run a deterministic script that counts everything in the user's Claude Code transcripts and renders an HTML report,
then you write the **Insights** tab: the bottom lines, grouped by category, cost first. The scripts produce every figure and
every structure (savings, evidence, question ids, links); you bring the judgment and the prose.

## Ground rules (read first)

- **Only use what the script extracted**: the files in `<OUT>/data/` (`digest.md`, `candidates.json`, `metrics.json`,
  `config.json`, the CSVs). **Never read session transcripts** (`<claude dir>/projects/**/*.jsonl`) or any other
  conversation content, not even to check a number. If a number you need isn't in the digest (it shows the first rows of a
  table), `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/candidates.py" --out "<OUT>" --show card:CX3` prints every figure of one card
  (all KPIs, every table row and column, every chart entry; `card:CX3@<scope id>` for one project's scope).
- **Never invent numbers.** Every figure in an insight must appear in the digest or be a simple, stated derivation of figures
  that do (put the derivation in `savings.basis`).
- **Advice follows Claude Code's best practice; the numbers aim it.** The SV scenarios say what a change would have saved, not
  whether it is a good way to work. Every action you write is one the official guidance recommends
  ([../optimize/reference/best-practices.md](../optimize/reference/best-practices.md)): e.g. SV4's threshold becomes "/compact
  with what to keep at a natural break once past ~150K" and "/clear before unrelated work", never "compact at 60K". When the
  data favours something the guidance advises against, say what the guidance recommends instead, with the numbers.
- Dollars are **API list-price equivalents** (tokens × prices.json). On a subscription they are a yardstick, not a bill. Say so
  once in the summary.
- Never write `insights.json` yourself: `assemble.py` writes it from your notes (step 3).
- Run every command exactly as shown: one `python3 …` command, without `cd`, pipes, redirection or variables, so it matches
  the allowed tools and needs no permission prompt. The skill is allowed nothing else without the user's say-so.

Paths used below:

- Scripts: the plugin's scripts are in `${CLAUDE_PLUGIN_ROOT}/scripts`. Exactly those commands are pre-approved (each script by its full path); anything else, such as another program, `python3 -c`, a `cd` or a pipe, makes Claude Code ask the user first, so don't work around a refusal.
- `<OUT>`: the output folder, `~/.claude-usage` by default. Step 1 prints it (so does `usage_report.py --where [--claude-dir …]`);
  substitute its real path. It holds `report.html`, the extracted data in `data/` and the files built from your notes:
  `insights.json`, `optimizations.json`.

## Only opening it?

When the user asked in words only to open or look at the report they already have (not to run, refresh or rebuild it),
don't build it: a build counts everything again, which puts the current insights and optimizations out of date until
they are written again. Run

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/open.py"
```

(with `--tab insights` or `--tab optimizations` when they named a tab), reply in two lines with what it printed, and stop.
If it says there is no report yet, go on with step 1. Typed as `/claude-usage:report`, with or without options, it builds:
go to step 1. (`/claude-usage:open` opens without building too.)

## 1. Build the report

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/usage_report.py" $ARGUMENTS
```

The script ignores `--no-insights` and `--no-open` (they are for you). It takes ~5 seconds and prints the report path on its
last line: `<OUT>` is that file's folder. With `--no-insights`, skip to step 4.

Without `--days`, `--since` or `--all` it covers the last 60 days. When the user asks for another period in words ("the
last 3 months", "since September", "everything"), add `--days 90`, `--since 2026-09-01` or `--all` to the command.

If it stops with "No Claude Code transcripts folder", Claude Code keeps its data somewhere else on this machine. Show the
message, ask the user for that folder (the one with `projects/` inside), and run again with `--claude-dir "<that folder>"`
(the later steps only need `<OUT>`). Any other error: show it and stop.

## 2. Read what the script prepared

1. The savings levers, ready to use:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/candidates.py" --out "<OUT>" --show levers
   ```
   One line per lever (SV1's rows, largest first): the insight `id` to use, `category`, `questions`, a `savings` object
   (all time, per 30 days, share of spend, kind, basis, which other levers it overlaps), `evidence` with every number exactly
   as the digest writes it, more `facts` you may cite, a draft `title`, `bottom_line` and `actions`, and the `optimizations`
   drafted for it.
   A lever without `savings` is a positive finding (e.g. the cache lifetimes already fit).
2. `<OUT>/data/digest.md`, completely (in parts with offset/limit if it is too big for one read): every question of the report
   (IDs like `OV3`, `CX8`, `SV4`) with its numbers, top table rows and one-line insight, then the costliest cache misses, the
   other project scopes and the current setup. The levers already carry the SV numbers; the digest is where everything else
   comes from: context, caching, sessions, subagents, tools, hooks, errors, and the story behind each lever.
3. [reference/insights-guide.md](reference/insights-guide.md): the categories, what makes a good insight, how savings are
   stated, the checklist of what to look for. Then
   [../optimize/reference/best-practices.md](../optimize/reference/best-practices.md): the official guidance each action
   follows, what never to recommend, and the digest signals that point to a documented practice.

## 3. Write the notes, assemble

Decide the 12–20 insights (at least 4 in `cost`; every category that has something real gets one). Write your notes with
the Write tool to `<OUT>/data/notes-insights.json` (if Write refuses because the file exists, Read it first), then run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/assemble.py" insights --out "<OUT>"
```

It builds `insights.json`: fills in each lever's numbers, evidence and overlaps, the source stamps, `generated` and
`author`, links the optimizations of this same report, validates, and writes only when everything passes. The notes file
stays either way: if it prints problems (nothing was written), or `note:` lines you want to act on (a big lever left out,
the count outside 12–20), Edit the notes file (don't write it again) and run the same command. The next full report run
clears it.

The notes, for example:

```json
{
  "summary": "Big contexts drive your bill: … (3–5 sentences, cost first; the first sentence is the lead, under ~160 characters)",
  "insights": [
    {"lever": "cost-compact-150k",
     "detail": "Cache reads are 53% of spend (OV3), and they scale with context size: …",
     "questions_add": ["CX2"]},
    {"lever": "cost-model-mix",
     "title": "Opus 5 and Fable 5 did most of the main-thread work that Opus 5.5 now does cheaper",
     "detail": "…"},
    {"id": "context-bash-output", "category": "context",
     "title": "Bash output is the largest thing tools add to your context",
     "bottom_line": "Bash results added 5.6M tokens that cost $122 to re-read (CX3), more than file reads.",
     "detail": "…", "questions": ["CX3", "EX6"],
     "evidence": [{"question": "CX3", "fact": "Bash results: 5.6M / $122"}],
     "priority": "medium", "confidence": "medium", "actions": ["…"]}
  ]
}
```

- `summary` (required): 3–5 sentences, cost first: total spend, the two or three biggest levers with their savings (all time and
  per 30 days), one sentence on what already works well, and that dollars are list-price equivalents. At most 900
  characters (the schema's limit; aim for ~600).
- Length limits the schema enforces (characters): `title` 90, `bottom_line` 280, `detail` 1,400, an evidence `fact` 240,
  `savings.basis` 400, an action 240; at most 6 `questions`, 6 `evidence` and 4 `actions`.
- `insights`: in display order. Each item is either
  - **a lever** (`"lever": "<its id from step 2>"`): it starts as the bundle (id, category, questions, evidence, savings,
    priority, confidence, title, bottom line, actions). Write `detail` (required: the reasoning, 40–1,400 characters). Any
    other field you give replaces the bundle's (`null` removes it; `savings` is merged key by key). `evidence_add` and
    `questions_add` append to the bundle's lists (cut to 6, keeping the lever's main one and yours first). Keep the draft
    title and bottom line when they already say it; replace them only when you have a sharper finding specific to this user.
    The bundle's actions already follow the guidance: make them name this user's project, file or model, not weaker.
  - **a whole insight** (no `lever`): every field of the schema's insight (`id`, `category`, `title`, `bottom_line`, `detail`,
    `questions`, `evidence`, `priority`, `confidence`, optional `scope`, `savings`, `actions`). In `savings`, leave out
    `pct_of_spend` and `usd_per_month` to have them computed from `usd_so_far`.
- Cite questions the report shows (the digest marks hidden ones "not shown"): a hidden id in `questions` or `questions_add`
  is left out with a `note:`, and evidence must come from a shown question. Quote a hidden card's numbers in the text instead.
- Don't write `optimizations` links: `/claude-usage:optimize` adds them.

## 4. Render and open

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/usage_report.py" --render --out "<OUT>" --open --tab insights
```

With `--no-insights`, use `--tab report`; with `--no-open`, leave out `--open --tab …` (it opens the default browser on macOS,
Linux and Windows). The render step only embeds the JSON files into report.html (no recomputing), so re-run it whenever
insights.json changes.

## 5. Reply

Keep it short: the report path, the 3 biggest cost levers (one line each, with the saving all time and per 30 days), one thing
that is going well, and the next steps: `/claude-usage:optimize` turns these into changes you can apply one by one,
`/claude-usage:brainstorm` to dig into the data together. Don't paste the whole insights file. With `--no-insights`, give just
the report path.
