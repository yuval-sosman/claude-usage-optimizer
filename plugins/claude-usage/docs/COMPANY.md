# The company report

`/claude-usage:company <folder>` (`scripts/company.py build <folder>`) combines the share files people send
(`/claude-usage:share`) into one report page. This file is to the company cards what QUESTIONS.md is to the personal
report: why each card is there, how it is counted, and how to check it. The company cards are not part of the personal
report's question count.

## Where the numbers come from

- **Rows first.** Everything except the savings levers comes from each file's API-call rows (`tables.calls`, the personal
  report's calls.csv), its sessions and its cache misses, not from card labels, which change between plugin versions.
- **One price table.** Each call is re-priced from its tokens at this plugin's `prices.json`, times the call's premium
  (`Prices.mult(model, where, fast)`: a regional Bedrock profile, a pinned location, fast mode), so everyone is priced
  the same way. A file packed before calls.csv had `where` and `fast` keeps its own call costs, split by token type as
  the list prices split them, and is flagged.
- **Price check.** Re-priced total ÷ the file's own total. A difference over 0.5% is flagged: the sender's prices.json
  differs.
- **Periods.** Each file covers the days its sender's report covered (`headline.days`, the basis of the report's "per 30
  days"). People are compared per 30 days: spend × 30 ÷ days. `--since`/`--until` keeps the calls inside the window and
  each person's days inside it; someone with no days inside is left out.
- **Levers** come from each person's report: SV1's row for each lever (by `id`; older reports by card and label,
  `candidates.lever_row`), except unused listings, which use SV2's "Of which you can switch off" (what the person can act
  on). They are scaled by the price check's ratio and capped at the person's spend. Levers overlap within a person (a
  smaller context also makes misses cheaper), so they are never added across levers; one lever added across people is
  fine. With a window, levers still cover each person's whole period.
- **Who is who.** Newest file first. A file that shares session ids with a kept one is an older copy of that history:
  left out. A file of a kept person (same name, else same account) with no sessions in common is that person on another
  machine: merged (additive figures added, days over the span both cover). Two people shown alike get their account (or
  file) added to the name. Every decision is printed and listed in the page's notes.
- **Left out, with the reason:** not a share file, a newer share format, a damaged file, no API-call rows (`--no-csv`),
  a missing calls.csv column.
- **Privacy.** Only numbers leave the files, plus each person's name or account: no project names, session titles or
  prompt text.

## Scopes

`Company` (everyone) and one scope per person, most spend per 30 days first. A person's scope shows CO1–CO4 and LV1 for
them alone, with the company median beside their rates, and names the file that opens their full report.

## CO: Company

**CO1. How much does the company spend on Claude Code, and how is it trending?**
- Tiles: people (and files), spend per 30 days (Σ each person's rate), spend over all periods, sessions, API calls, active
  hours, cache hit rate (Σ cache reads ÷ Σ context), subagent share of spend.
- Weekly spend stacked by the 8 people with the most spend per 30 days (the rest as "Others"), and spend per week per
  person whose period covers that week: periods end on different days, so the edge weeks are partial.
- Check: a company of one person equals that person's report (hero, sessions, hit rate, subagent share).

**CO2. Where does the money go: models, platforms and token types?**
- Spend by model; by platform (Claude API global or pinned to one location, Bedrock global, regional or not recorded,
  subscription or not recorded: from each call's `where`); by token type.

**CO3. How well is the prompt cache used, and what do misses cost?**
- Hit rate; misses, tokens re-written and their extra cost, re-priced (tokens re-written × (write − read price) × the
  call's premium; write price from the thread's cache lifetime); their share of spend; cost by cause.
- Avoidable per 30 days: Σ each person's "Avoid the avoidable cache misses" lever (the rows can't tell avoidable from not).
- A file without cache-miss rows is left out of the miss figures and said in the card's note.

**CO4. How big do contexts get?**
- Calls and spend by context size (the report's buckets); spend above 200K and its share; the median person's median
  session peak.

## PE: People

**PE1. How do people compare?** One row per person: days, spend, per 30 days, sessions, active hours per 30 days, hit
rate, median peak context, subagent share, miss-cost share, biggest lever and its saving per 30 days, and notes (short
period, prices differ, priced at its own prices, misses unknown, merged). Also written to `data/people.csv`, with every
lever's saving per 30 days.

**PE2. Who spends the most per 30 days, and on which models?** Per person, stacked by the 5 models with the most spend.

**PE3. Does spend go with cache hit rate?** One point per person; short periods drawn apart.

**PE4. Who stands out, and why?** Among people with 7+ days (shorter periods make per 30 days a projection of very
little), and only with at least 3 of them: spend per 30 days over 2× the median; hit rate 10+ points under the median;
median peak context over 2× the median; miss-cost share over 2× the median (and at least 2%).

## LV: Savings levers

**LV1. Which savings levers are worth the most across everyone?** Per lever: saving per 30 days summed over people, the
people it applies to (saves at least $1 or 1% of their spend per 30 days), the people for whom it is the biggest lever,
the median saving among the people it applies to, and the all-time sum.
- Check: a company of one person's LV1 equals their SV1's per-30-days figures (unused listings: their SV2's switch-off-able
  part).

**LV2. Who would gain the most from each lever?** Heatmap of the 20 people with the most spend per 30 days × levers, as
a share of each person's spend per 30 days (so one heavy user doesn't wash out the rest); the $ per 30 days for everyone
is in the card's data (and the digest).

## Output

`<out>/company/` (or `--to DIR`, which must be empty or hold an earlier company report): `report.html`,
`data/metrics.json` (`meta.shared.company` marks it: no apply commands, and apply.py refuses it; `meta.views` shows only
the Report tab; `meta.scope_label` names the selector "Person"), `data/digest.md` (the skill reads it: headline, notes,
everyone's row, every card with its full tables) and `data/people.csv`.
