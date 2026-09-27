---
name: brainstorm
description: Think through your Claude Code usage with Claude — explore the report's numbers, challenge or extend the insights, test what-if ideas on the extracted data, and turn what you agree on into updated insights or optimizations.
disable-model-invocation: true
argument-hint: "[a question or topic, e.g. \"why are subagents so expensive?\"]"
allowed-tools: Read, Write, Edit, Bash(python3 *), Bash(open *)
---

# Brainstorm on the usage data

A conversation, not a pipeline. You and the user look at their Claude Code usage together: what the numbers say, why, what
to try, and what is worth changing.

## Ground rules

- Use only what the usage report extracted, in `<OUT>`, the output folder (`~/.claude-usage` by default;
  `python3 "${CLAUDE_SKILL_DIR}/../../scripts/usage_report.py" --where [--claude-dir …]` prints it): `insights.json`,
  `optimizations.json`, and in `<OUT>/data/`: `digest.md`, `config.json`, `metrics.json` and the CSVs (`calls.csv`,
  `tool_calls.csv`, `sessions.csv`, `subagents.csv`, `cache_misses.csv`). **Never open session transcripts** (`<claude dir>/projects/**/*.jsonl`), even when a
  question seems to need one; say what the extracted data can and can't answer instead.
- Show your working: when you compute something new, use a short `python3` snippet over the CSVs or metrics.json and state
  the result with its assumptions. Keep measured facts, modelled estimates (the SV questions) and your own guesses clearly
  apart.
- Dollars are API list-price equivalents.
- Cite report question IDs (e.g. `CX8`) so the user can open them in the report.

## Start

1. If `<OUT>/data/digest.md` doesn't exist, say so and offer to run `/claude-usage:report` first (it takes ~5 s
   for the numbers, a few minutes with insights).
2. Read `data/digest.md` (in parts if it is too big for one read), and `insights.json` / `optimizations.json` if present. Note their `source.metrics_generated`: if it
   differs from the digest's "Generated" line, the insights are out of date; mention it once.
3. If `$ARGUMENTS` has a question, start there. Otherwise open with a short orientation: the three findings that matter most
   (with their saving all time and per 30 days), and three open questions worth exploring together (things the insights don't settle yet).
   Then let the user lead.

## Useful moves

- **Drill down**: from a total to the sessions, days, threads or tools behind it (CSV filters and group-bys).
- **Compare**: two projects (the digest's scopes), two periods (re-run the report into a separate directory:
  `python3 "${CLAUDE_SKILL_DIR}/../../scripts/usage_report.py" --since … --until … --out /tmp/usage-a --no-csv`), main vs subagents,
  models.
- **What-if**: re-price calls under another assumption (a model, a compaction threshold, a cache lifetime) the way the SV
  questions do, and say how it differs from theirs.
- **Explain a miss or a spike**: `cache_misses.csv` has every miss with its timestamps, cause, idle time, cache state, trigger
  and extra cost; the report's CX8 card shows the timeline of the costliest ones.
- **Challenge an insight**: look for the counter-evidence; if it weakens, say so.

Recipes (adapt freely; replace `<OUT>` with the real output folder):

```bash
# cost by project and day
python3 -c "import csv,collections as C; d=C.defaultdict(float)
for r in csv.DictReader(open('<OUT>/data/calls.csv')): d[(r['project'][-30:], r['time'][:10])]+=float(r['usd'])
[print(f'{k[1]}  {k[0]:<30} \${v:8.2f}') for k,v in sorted(d.items())]"
# the 10 costliest sessions with their kind and peak context
python3 -c "import csv; rows=sorted(csv.DictReader(open('<OUT>/data/sessions.csv')), key=lambda r:-float(r['usd']))[:10]
[print(f\"\${float(r['usd']):7.2f}  {r['kind']:<22} peak {int(r['peak_context'] or 0):>7}  {r['title'][:60]}\") for r in rows]"
# slowest tools
python3 -c "import csv,statistics as S,collections as C; d=C.defaultdict(list)
for r in csv.DictReader(open('<OUT>/data/tool_calls.csv')):
  if r['latency_s']: d[r['tool']].append(float(r['latency_s']))
[print(f'{k:<18} n={len(v):>5} median={S.median(v):6.1f}s max={max(v):7.1f}s') for k,v in sorted(d.items(), key=lambda kv:-sum(kv[1]))[:12]]"
```

## Closing the loop

When the conversation lands on something new or changes a conclusion:

- **An insight**: offer to add or edit it in `insights.json` (same schema and rules as the report skill: cite questions,
  savings with basis for cost insights). Then validate and re-render:
  `python3 "${CLAUDE_SKILL_DIR}/../../scripts/validate.py" insights "<OUT>/insights.json" --metrics "<OUT>/data/metrics.json"`
  and `python3 "${CLAUDE_SKILL_DIR}/../../scripts/usage_report.py" --render --out "<OUT>"`.
- **A change to try**: point to `/claude-usage:optimize` (it prepares changes you can apply one by one), or, if the user
  wants it now, describe exactly what to change and where.
- Only write files when the user agrees.
