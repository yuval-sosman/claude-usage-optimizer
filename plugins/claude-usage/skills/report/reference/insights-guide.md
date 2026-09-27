# Writing the Insights tab

The report answers its questions (IDs like `OV3`, `CX8`, `SV4`) with numbers. The Insights tab is where you (Claude) say what those numbers **mean for this
user**: the bottom line, why it happens, what it cost, what applying a change from day one would have saved, and what to do.
Each insight links back to the questions that support it; the report opens filtered to them, with a way back.

## Categories (field `category`)

Listed in display order. **Cost management comes first and matters most.**

| id | Title | What belongs here | Usually cites |
|---|---|---|---|
| `cost` | Cost management | Every lever that would have saved real money, stated as "$X all time (≈ $Z per 30 days, Y% of spend)": model choice, compaction point, avoidable misses, fresh starts after breaks, unused context, large reads, automation that triggers extra work. Also where the money goes (token types, effort level, concentration) when that changes what to do. | SV1–SV8, OV2–OV5, CX8 |
| `context` | Token & context efficiency | What fills the context and what carrying it costs: start-up overhead, big tool outputs, re-reads, growth rate, context size at /clear, what a new session starts with. | CX1–CX6, EX6, EX8, OUT1 |
| `cache` | Caching | Hit rate, misses and their causes, cache lifetime fit, cold starts. | CX6–CX12, SV3, SV5, ME3 |
| `workflow` | Sessions & habits | Session length, prompting, reply times, parallel sessions, interruptions, when you work. | SE1–SE4, ME1–ME3, ME6, ME7 |
| `delegation` | Subagents & models | Whether subagents pay off, duplicate work, resumes, which model does what, effort. | SE5, SE6, OV4, SV6, CX8 |
| `tooling` | Setup: skills, MCP, hooks & plugins | What the configuration loads and triggers: unused skills/MCP, tool-list changes, hooks (cost, failures, follow-up work), plugins. | EX1–EX5, SV1, SV2 |
| `reliability` | Errors & friction | API errors, tool failures, fix-and-retry loops, rework, waste. | ME7, OV8, EX7 |

## Anatomy of a good insight

- **title** (≤ 90 chars): the finding, not the topic. "Fable 5 work cost 2.5× what Opus 5.5 would have", not "Model usage".
- **bottom_line** (one sentence, ≤ 280 chars): what is true, the key number, and why it matters.
- **detail** (≤ 1,400 chars): the reasoning. Connect two or three questions: cause → effect → cost. Name the mechanism
  ("every later call re-reads it", "resuming rebuilds the subagent's history"). No filler.
- **questions**: the 1–6 question IDs that together support it, main one first. Only IDs that exist in the digest.
- **evidence**: 2–4 facts, each tied to one question ID, copied from the digest (numbers verbatim).
- **actions**: up to 4 short imperatives the user can do. Concrete ("Start a fresh session after breaks longer than an hour
  when the context is over 150K"), not generic ("be mindful of context").
- **priority**: `high` if it is worth ≥ 5% of spend, or is a quick fix with a clear payoff, or a correctness problem (e.g. a hook
  that keeps failing); `medium` for 1–5% or a solid habit; `low` for small or informational.
- **confidence**: `high` when the number is measured directly (money spent, counts); `medium` for SV scenarios and other
  modelled figures; `low` when attribution is heuristic (keyword classification, inferred causes).
- **scope**: only when the insight is about one project; use the scope id from the digest's Scopes list. Omit for all projects.

## Savings (field `savings`) — required for every `cost` insight

"What would it have saved if applied from day one" means: the same work over the analysed period, with the change in place from
the first day. Every saving is given twice, so it is easy to read: **all time** (the analysed days) and **per 30 days** (the same
pace projected to 30 days). Say both wherever a saving appears in text (title, bottom_line, detail, evidence), e.g. "$67 all time
(≈ $111 per 30 days)". Take the numbers from the SV questions; don't recompute them unless you must (then show how in `basis`).

- `usd_so_far`: the saving over the whole period, "all time" (an SV card's saving tile or "all time" column, SV1's table, or a
  measured cost such as CX8's "Extra cost of misses").
- `pct_of_spend`: `usd_so_far / total spend × 100`.
- `usd_per_month` (required): the same saving per 30 days: the SV card's "per 30 days" figure (the digest writes it as
  "≈ $X per 30 days"), or `usd_so_far × 30 / days covered` (SV1 "Days covered"). The validator checks it matches.
- `basis`: one or two sentences: which question, and the assumption. E.g. "SV4: replaying every main thread with /compact at
  150K; summary 20K + 10K re-read detail per compaction; net of the compactions' own cost."
- `kind`: `theoretical` for SV replays; `upper_bound` when the SV card or question says so (SV7, SV1's Stop-hook follow-up lever, SV8's
  assumed share); `measured` when it is money actually spent that the change would have removed (e.g. avoidable miss re-writes).
- `overlaps_with`: ids of other insights whose savings overlap (smaller contexts make misses and re-reads cheaper; compaction
  and fresh starts after breaks overlap; model choice multiplies everything). The tab says levers don't add up; this makes it
  specific.

Non-cost insights may carry `savings` too when a number exists.

## What to look for (a checklist, not a template)

1. **Model mix** (SV6, OV2): which model did most of the work; what the same tokens cost on the model the user uses now;
   subagents on a cheaper model; Explore agents on Haiku.
2. **Context size** (SV4, CX1–CX4, CX6): where compaction pays off; how big contexts get before /clear.
3. **Cache misses** (SV3, CX8): avoidable vs not; the top causes; the costliest single misses and their story (CX8 traces them step by step).
4. **Breaks** (SV7, CX8, ME3): returns to expired sessions; fresh start vs resume.
5. **Automation** (EX5, SV1): hook failures, Stop-hook follow-up work (SV1's lever).
6. **Setup overhead** (SV2, EX2, CX5): unused skills/MCP/agents loaded every session; what a new session starts with and its biggest parts.
7. **Big reads and outputs** (SV8, CX3, EX6, EX8): files read whole and re-read for the rest of the session.
8. **Subagents** (SE5, SE6, CX8): do they return small results for big internal work; duplicate reads; resumes.
9. **Output** (OUT1, OV4): thinking share, effort level, what Claude writes.
10. **Positives**: what already works (hit rate, cache lifetime fit, cheap sessions). One or two insights in the relevant
    category; the user needs to know what not to change.

## Quality bar

- Specific to this user's numbers; nothing that would be true of anyone.
- Each insight says something the question cards don't already say on their own: combine, explain, recommend.
- No double counting inside one insight; overlaps between insights are declared.
- Neutral, plain language. Name tradeoffs (a cheaper model can do the work worse; compaction drops detail).
- 12–20 insights total; ≥ 4 cost insights; every category that has something real gets at least one.

## Example

```json
{
  "id": "cost-compact-150k",
  "category": "cost",
  "title": "Compacting at ~150K tokens would have saved about $67 (≈ $111 per 30 days)",
  "bottom_line": "Your main threads often run past 150K tokens; every call above that re-reads the extra context, so compacting there would have saved about $67 all time (≈ $111 per 30 days, 11% of spend) after paying for the compactions.",
  "detail": "Cache reads are 54% of spend (OV3), and they scale with context size: the median session peaks at 131K and many run far past it (CX1). Replaying every main thread with a /compact whenever it was about to pass 150K (SV4) cuts the re-read context on 1,515 calls, for 65 compactions. Below ~100K compaction costs more than it saves; above 200K the saving shrinks. Compaction drops detail, so do it at natural breaks between subtasks.",
  "questions": ["SV4", "CX1", "OV3"],
  "evidence": [
    {"question": "SV4", "fact": "Best threshold 150K: 65 compactions, net saving $66.93 all time (≈ $111 per 30 days)"},
    {"question": "OV3", "fact": "Cache reads are 54% of your cost"},
    {"question": "CX1", "fact": "Median peak context 131K"}
  ],
  "priority": "high",
  "confidence": "medium",
  "savings": {"usd_so_far": 66.93, "pct_of_spend": 10.7, "usd_per_month": 111, "kind": "theoretical",
              "basis": "SV4: every main thread replayed with /compact at 150K (20K summary + 10K re-read detail each), net of the compactions' own cost.",
              "overlaps_with": ["cost-fresh-after-breaks"]},
  "actions": ["Run /compact (with what to keep) when a task is done and the context is past ~150K", "Or let Claude Code do it: see the auto-compact optimization"]
}
```
