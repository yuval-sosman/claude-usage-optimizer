---
name: report
description: Build and open the Claude Code usage report from the local transcripts (cost, caching, context, sessions, tools, hooks), with Claude-written insights on what each change would have saved. Use when the user asks to analyze, audit or explain their Claude Code usage, cost, cache hits or context size, or to run, refresh or open the usage report.
argument-hint: "[--days N | --since YYYY-MM-DD --until YYYY-MM-DD] [--claude-dir DIR] [--no-insights] [--no-open]"
allowed-tools: Read, Write, Edit, Bash(python3 *), Bash(open *), Bash(xdg-open *)
---

# Usage report + insights

You run a deterministic script that counts everything in the user's Claude Code transcripts and renders an HTML report,
then you read what it extracted and write the **Insights** tab: the bottom lines, grouped by category, cost first.

## Ground rules (read first)

- **Only use what the script extracted**: `digest.md`, `metrics.json`, `config.json` and the CSVs in `<OUT>/data/`.
  **Never read session transcripts** (`<claude dir>/projects/**/*.jsonl`) or any other conversation content, not even
  to check a number. If a number you need isn't in the digest, compute it from `metrics.json` or the CSVs with a short
  `python3 -c` snippet.
- **Never invent numbers.** Every figure in an insight must appear in the digest or be a simple, stated derivation of figures
  that do (put the derivation in `savings.basis`).
- Dollars are **API list-price equivalents** (tokens × prices.json). On a subscription they are a yardstick, not a bill. Say so
  once in the summary.

Paths used below:

- Scripts: `${CLAUDE_SKILL_DIR}/../../scripts` (if that variable isn't substituted, use the "Base directory for this skill" shown
  above: scripts are two levels up, in `scripts/`).
- `<OUT>`: the output folder, `~/.claude-usage` by default. Step 1 prints it (so does `usage_report.py --where [--claude-dir …]`);
  substitute its real path. It holds `report.html`, the extracted data in `data/` (`digest.md`, `metrics.json`, `config.json`,
  the CSVs) and the files Claude writes: `insights.json`, `optimizations.json`.

## 1. Build the report

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/usage_report.py" $ARGUMENTS
```

The script ignores `--no-insights` and `--no-open` (they are for you). It takes ~10 seconds and prints the report path on its
last line: `<OUT>` is that file's folder. With `--no-insights`, skip to step 5.

If it stops with "No Claude Code transcripts folder", Claude Code keeps its data somewhere else on this machine. Show the
message, ask the user for that folder (the one with `projects/` inside), and run again with `--claude-dir "<that folder>"`
(the later steps only need `<OUT>`). Any other error: show it and stop.

## 2. Read the extracted data

Read `<OUT>/data/digest.md` completely (in parts with offset/limit if it is too big for one read). It lists every question of
the report (IDs like `OV3`, `CX8`, `SV4`) with its numbers, top table rows and one-line insight, then the costliest cache
misses, the other project scopes and the current setup (settings, hooks, MCP servers, skills; secrets removed). The `SV`
questions are the savings scenarios: what each lever would have saved if applied from the first day, all time and per 30
days. They are your source for every saving.

Then read [reference/insights-guide.md](reference/insights-guide.md): the categories, what makes a good insight, how to state
savings, and a worked example. The output contract is `${CLAUDE_SKILL_DIR}/../../schemas/insights.schema.json`.

## 3. Write the insights

Write `<OUT>/insights.json`:

- `source.metrics_generated` = the "Generated …" timestamp at the top of digest.md (exactly as written there, `YYYY-MM-DD HH:MM:SS`);
  `source.range` and `source.spend_usd` from the digest; `generated` = now; `author` = `"Claude (claude-usage:report)"`.
- `summary`: 3–5 sentences, cost first: total spend, the two or three biggest levers with their savings, and one sentence on what
  is already working well.
- The insights themselves, following the guide's anatomy and quality bar (12–20 of them, at least 4 in `cost`, each of those
  with `savings`).

## 4. Validate, fix, repeat

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/validate.py" insights "<OUT>/insights.json" --metrics "<OUT>/data/metrics.json"
```

Fix every problem it lists and run it again until it prints `OK`.

## 5. Render and open

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/usage_report.py" --render --out "<OUT>" --open --tab insights
```

With `--no-insights`, use `--tab report`; with `--no-open`, leave out `--open --tab …` (it opens the default browser on macOS,
Linux and Windows). The render step only embeds the JSON files into report.html (no recomputing), so re-run it whenever you
change insights.json.

## 6. Reply

Keep it short: the report path, the 3 biggest cost levers (one line each, with the saving all time and per 30 days), one thing
that is going well, and the next steps: `/claude-usage:optimize` turns these into changes you can apply one by one,
`/claude-usage:brainstorm` to dig into the data together. Don't paste the whole insights file. With `--no-insights`, give just
the report path.
