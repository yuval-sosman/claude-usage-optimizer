# Writing the Insights tab

The report answers its questions (IDs like `OV3`, `CX8`, `SV4`) with numbers. The Insights tab is where you (Claude) say what those numbers **mean for this
user**: the bottom line, why it happens, what it cost, what applying a change from day one would have saved, and what to do.
Each insight links back to the questions that support it; the report opens filtered to them, with a way back.

You write notes, not the file: `assemble.py insights` builds insights.json from them (SKILL.md step 3 has the format). The
savings levers come from `candidates.py --show levers` with their numbers, evidence, questions and overlaps already filled in;
your part is choosing what to say, connecting the questions, and the words.

The numbers say where the money went; what to do about it follows Claude Code's documented best practice
([../../optimize/reference/best-practices.md](../../optimize/reference/best-practices.md)). A good insight has both: this user's
figures, and the way of working the docs recommend, aimed at the project, file or habit the figures point to.

## Categories (field `category`)

Listed in display order. **Cost management comes first and matters most.**

| id | Title | What belongs here | Usually cites |
|---|---|---|---|
| `cost` | Cost management | Every lever that would have saved real money, stated as "$X all time (≈ $Z per 30 days, Y% of spend)": model choice, compaction point, avoidable misses, fresh starts after breaks, unused context, large reads, automation that triggers extra work. Also where the money goes (token types, effort level, concentration) when that changes what to do. | SV1–SV9, OV2–OV5, CX8 |
| `context` | Token & context efficiency | What fills the context and what carrying it costs: start-up overhead, big tool outputs, re-reads, growth rate, context size at /clear, what a new session starts with. | CX1–CX6, EX6, EX8, OUT1 |
| `cache` | Caching | Hit rate, misses and their causes, cache lifetime fit, cold starts. | CX6–CX12, SV3, SV5, ME3 |
| `workflow` | Sessions & habits | Session length, prompting, reply times, parallel sessions, interruptions, when you work. | SE1–SE4, ME1–ME3, ME6, ME7 |
| `delegation` | Subagents & models | Whether subagents pay off, duplicate work, resumes, which model does what, effort. | SE5, SE6, OV4, SV6, CX8 |
| `tooling` | Setup: skills, MCP, hooks & plugins | What the configuration loads and triggers: unused skills/MCP, tool-list changes, hooks (cost, failures, follow-up work), plugins. | EX1–EX5, SV1, SV2 |
| `reliability` | Errors & friction | API errors, tool failures, fix-and-retry loops, rework, waste. | ME7, OV8, EX7 |

## Anatomy of a good insight

- **title** (≤ 90 chars): the finding, not the topic. "Fable 5 work cost 2.5× what Opus 5.5 would have", not "Model usage".
  When the insight has `savings`, keep the amount out of the title: the card already shows it beside the title, all time and
  per 30 days. "Compacting at ~150K tokens pays for itself", not "Compacting at ~150K would have saved $67 (≈ $111 per 30 days)".
- **bottom_line** (one sentence, ≤ 280 chars): what is true, the key number, and why it matters.
- **detail** (≤ 1,400 chars): the reasoning. Connect two or three questions: cause → effect → cost. Name the mechanism
  ("every later call re-reads it", "resuming rebuilds the subagent's history"). No filler.
- **questions**: the 1–6 question IDs that together support it, main one first. Only IDs that exist in the digest.
- **evidence**: 2–4 facts, each tied to one question ID, copied from the digest (numbers verbatim). A lever's evidence is
  already quoted that way; `evidence_add` appends one of its `facts` or a line of your own.
- **actions**: up to 4 short imperatives the user can do. Concrete ("Start a fresh session after breaks longer than an hour
  when the context is over 150K"), not generic ("be mindful of context"), and the way the docs recommend it: "/compact with what
  to keep at a natural break once past ~150K", not "compact at 150K"; "raise effort with /effort for the hard task", not "set
  effort to low". Never an action best-practices.md's "Never recommend" list rules out, whatever an SV figure says.
- **priority**: `high` if it is worth ≥ 5% of spend, or is a quick fix with a clear payoff, or a correctness problem (e.g. a hook
  that keeps failing); `medium` for 1–5% or a solid habit; `low` for small or informational.
- **confidence**: `high` when the number is measured directly (money spent, counts); `medium` for SV scenarios and other
  modelled figures; `low` when attribution is heuristic (keyword classification, inferred causes).
- **scope**: only when the insight is about one project; use the scope id from the digest's Scopes list. Omit for all projects.

## Savings (field `savings`) — required for every `cost` insight

"What would it have saved if applied from day one" means: the same work over the analysed period, with the change in place from
the first day. Every saving is given twice, so it is easy to read: **all time** (the analysed days) and **per 30 days** (the same
pace projected to 30 days). Say both wherever a saving appears in text (bottom_line, detail, evidence; not the title), e.g.
"$67 all time (≈ $111 per 30 days)".

**A lever's `savings` is ready**: `usd_so_far`, `usd_per_month`, `pct_of_spend`, `kind`, `basis` and `overlaps_with` (the other
levers it shares cost with) come from the SV cards. Use them as they are; override a key only to say something the bundle
can't (e.g. name the part two levers share in `basis`, or add a whole insight to `overlaps_with`).

For a whole insight with a saving, give:

- `usd_so_far`: the saving over the whole period, "all time" (an SV card's saving tile or "all time" column, SV1's table, or a
  measured cost such as CX8's "Extra cost of misses", or another scope's lever in the digest's "Other scopes").
- `basis`: one or two sentences: which question, and the assumption. E.g. "SV4: replaying every main thread with /compact at
  150K; summary 20K + 10K re-read detail per compaction; net of the compactions' own cost."
- `kind`: `theoretical` for SV replays; `upper_bound` when the SV card or question says so (SV7, SV1's Stop-hook follow-up lever, SV8's
  assumed share); `measured` when it is money actually spent that the change would have removed (e.g. avoidable miss re-writes).
- `overlaps_with`: ids of other insights whose savings overlap (smaller contexts make misses and re-reads cheaper; compaction
  and fresh starts after breaks overlap; model choice multiplies everything). The tab says levers don't add up; this makes it
  specific.
- `usd_per_month` and `pct_of_spend` are computed from `usd_so_far` when you leave them out (the validator checks
  `usd_per_month` = `usd_so_far` × 30 / days covered).

Non-cost insights may carry `savings` too when a number exists.

## What to look for (a checklist, not a template)

0. **The efficiency score** (SV9): the overall score and grade, and the area that lost the most points. It is SV1's levers
   combined with overlaps removed (the main-thread model is not graded), so it adds no saving of its own: cite it in the
   summary or in the insight on that area's lever, not as a separate saving.
1. **Model mix** (SV6, OV2): which model did most of the work; what the same tokens cost on the model the user uses now;
   subagents on a cheaper model (SV6's "Via" figure is what the default subagent model reaches: Explore and Plan follow the
   main model); Explore agents on Haiku. SV6 is a price ceiling: the docs' split is Sonnet for most coding, Opus for complex
   reasoning, switching at the start of a session.
2. **Context size** (SV4, CX1–CX4, CX6): where compaction pays off; how big contexts get before /clear. The docs' habits come
   first: /clear between unrelated tasks, /compact with what to keep at natural breaks; SV4's threshold says when, it isn't a
   window to force (on a 1M model a 400K cap, never under 300K, is the setting-level option).
3. **Cache misses** (SV3, CX8): avoidable vs not; the top causes; the costliest single misses and their story (CX8 traces them step by step).
   Cache lifetimes (SV5): when SV5 favours main 1 hour · subagents 5 minutes and they aren't pinned in settings, the `ttl`
   bundle says to pin them; keep that action (its optimization leads the tab).
4. **Breaks** (SV7, CX8, ME3): returns to expired sessions; fresh start vs resume.
5. **Automation** (EX5, SV1): hook failures, Stop-hook follow-up work (SV1's lever; EX5's "What Stop hooks set off" says
   which hook and how often Claude went on working after it. A hook that sends nothing back sets off nothing: no insight).
6. **Setup overhead** (SV2, EX2, CX5): unused skills/MCP/agents loaded every session; what a new session starts with and its biggest parts.
7. **Big reads and outputs** (SV8, CX3, EX6, EX8): files read whole and re-read for the rest of the session.
8. **Subagents** (SE5, SE6, CX8): do they return small results for big internal work; duplicate reads; resumes.
9. **Output** (OUT1, OV4): thinking share, effort level against the model's documented default (a persisted level above it,
   e.g. Opus 5.5 kept at high or xhigh where the docs make medium the default), what Claude writes.
10. **Documented practices the data points to** (best-practices.md's signals table): a CLAUDE.md over 200 lines (CX5's memory
    files), repeated reads of a typed language's files without a language server (EX8, EX4, OUT4), a large share of cost before
    the first edit (EX9: subagents for investigation, plan mode), fix-and-retry loops (EX7). Only where a card shows it.
11. **Positives**: what already works (hit rate, cache lifetime fit, cheap sessions). One or two insights in the relevant
    category; the user needs to know what not to change.

## Quality bar

- Specific to this user's numbers; nothing that would be true of anyone.
- Each insight says something the question cards don't already say on their own: combine, explain, recommend.
- No double counting inside one insight; overlaps between insights are declared.
- Neutral, plain language. Name tradeoffs (a cheaper model can do the work worse; compaction drops detail).
- Each action is a practice Claude Code's docs recommend, aimed with this user's numbers; when a saving points at something
  the docs advise against, the insight says what to do instead.
- 12–20 insights total; ≥ 4 cost insights; every category that has something real gets at least one.

## Example

Two items of the notes' `insights` list: a lever (the bundle supplies id, category, questions, evidence, savings, priority,
confidence; the title, detail and actions are yours) and a whole insight.

```json
{"lever": "cost-compact-150k",
 "title": "Compacting at ~150K tokens pays for itself",
 "bottom_line": "Your main threads often run past 150K tokens; every call above that re-reads the extra context, so compacting there would have saved about $127 all time (≈ $185 per 30 days, 15% of spend) after paying for the compactions.",
 "detail": "Cache reads are 53% of spend (OV3), and they scale with context size: the median session peaks at 138K and a third of calls run past 200K (CX1). Replaying every main thread with a /compact whenever it was about to pass 150K (SV4) cuts the re-read context on 2,551 calls, for 107 compactions. Below ~100K compaction costs more than it saves; above 200K the saving shrinks. Compaction drops detail, which is why the docs advise compacting at natural breaks between tasks rather than waiting for it mid-task, and /clear (free) when the next task is unrelated.",
 "actions": ["/clear before unrelated work; once past ~150K, /compact with what to keep at the next natural break", "See the context notice (and, on a 1M-context model, the auto-compact cap) in the optimizations"]}
```

```json
{"id": "cache-tool-list-changes", "category": "cache",
 "title": "MCP tools changing mid-session forced three full re-writes",
 "bottom_line": "Three times an MCP server's tools changed mid-session and the whole context was written again: $8.66 all time (≈ $12.59 per 30 days).",
 "detail": "Tools sit at the front of the prompt, so any change to them invalidates everything cached after it (EX3). …",
 "questions": ["SV3", "CX8", "EX3", "EX2"],
 "evidence": [{"question": "SV3", "fact": "Tool list changed (MCP/tools): 3 misses, $8.66 all time, $12.59 per 30 days"},
              {"question": "EX3", "fact": "Mid-session tool-list changes: 22"}],
 "priority": "medium", "confidence": "high",
 "savings": {"usd_so_far": 8.66, "kind": "measured", "basis": "SV3: the extra cost of the three misses whose cause was a tool-list change.",
             "overlaps_with": ["cost-avoidable-misses", "tooling-unused-listings"]},
 "actions": ["Wait for MCP servers to finish connecting before the first prompt (/mcp)"]}
```
