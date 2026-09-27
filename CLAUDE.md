# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

It is about working on the claude-usage plugin: it explains how the pieces depend on each other, so that a change in one place is followed through everywhere it
matters. User-facing docs are in the root README.md; every question's definition is in docs/QUESTIONS.md.

The repo is a marketplace (`.claude-plugin/marketplace.json`, name `claude-usage-optimizer`) with one plugin in
`plugins/claude-usage/`. Paths below (`scripts/…`, `skills/…`, `docs/…`, `.claude-plugin/plugin.json`) are relative to that folder.

## What it is

A plugin, `claude-usage@claude-usage-optimizer` (users: `/plugin marketplace add yuval-sosman/claude-usage-optimizer`, then
`/plugin install claude-usage@claude-usage-optimizer`; a working copy: `claude --plugin-dir plugins/claude-usage`). A deterministic script counts
the user's Claude Code transcripts and renders an HTML report. Claude then writes two tabs from what the script extracted:
- **Insights**, written by `/claude-usage:report`.
- **Optimizations**, written by `/claude-usage:optimize`.

A third skill, `/claude-usage:brainstorm`, talks the data through with the user and can edit insights.json. `optimize` and
`brainstorm` are manual-only (`disable-model-invocation: true`); `report` can also be triggered by the model.

`docs/CATALOG.md` and `docs/lib.sh` hold the original jq extraction commands; QUESTIONS.md cites them by catalog id (e.g. `[B06]`).
The engine doesn't use them. The gotchas table at the top of CATALOG.md (G1–G9) still applies to any new counting. The main one:
Claude Code writes one line per content block, so assistant lines must be deduplicated on `message.id + requestId`.

## Data flow

```
<claude dir>/projects/**/*.jsonl        transcripts (<claude dir> = --claude-dir, $CLAUDE_CONFIG_DIR, else ~/.claude)
        │  scripts/usage_report.py      stdlib Python 3.8+, no LLM
        ▼
<OUT> = $CLAUDE_USAGE_OUT or <claude dir>-usage (~/.claude-usage): outside .claude/, which Claude Code write-protects (skill Writes there prompt, and fail headless)
  (paths: scripts/layout.py; layout.migrate() moves an older flat <OUT> into place on the next run)
  data/metrics.json   every card, per scope (all projects, repo groups, dirs) + meta (generated, range, notes, claude_dir)
  data/digest.md      the same, compact: what the skills read (never the transcripts)
  data/config.json    current settings/hooks/MCP/skills, secrets removed
  data/*.csv          calls, tool_calls, sessions, subagents, cache_misses
  report.html         scripts/report_template.html with metrics + insights.json + optimizations.json embedded
        │  skills/report     → insights.json       (schema: schemas/insights.schema.json)
        │  skills/optimize   → optimizations.json  (schema: schemas/optimizations.schema.json)
        │  usage_report.py --render   re-embeds the two JSON files, no recomputing
        ▼
  apply.py       applies one optimization: preview, backup to <OUT>/applied/backups, record in <OUT>/applied/applied.json (written before the
                 first change, so a failure partway can be undone), undo. Undo reverts only that change (a 3-way JSON
                 revert, or removing the appended CLAUDE.md block), keeping later changes; shared helper files stay until
                 the last optimization using them is undone. Re-applying a changed optimization undoes the old one first.
                 Hooks are installed as copies in <claude dir>/hooks/claude-usage/ (plus _session.py and prices.json).
```

## Invariants (don't break these)

- **Skills never read transcripts.** They use only `<OUT>` files. If a skill needs a number, add it to the script's output.
- **The script stays stdlib-only and portable:**
  - Python 3.8 syntax: no nested same-quote f-strings, no `str.removeprefix`, no `dict | dict`.
  - Works on macOS, Linux and Windows: paths go through `slash()`/`tilde()`, file URLs through `pathlib`, and stdout is safe via `safe_console()`.
- **Any Claude model:** model ids go through `canon_model()`. Prices come from `scripts/prices.json`, with a nearest-family
  fallback flagged as "estimated". SV6 compares against `_compare`. Never hardcode a model name in card text; use
  `model_name()` and `prices.pick(family)`.
- **Dollars are list-price equivalents.** Every SV card is "if applied from day one, same work". Savings overlap; say so.
- **Every figure in insights/optimizations is traceable** to the digest. `scripts/validate.py` enforces:
  - the schema;
  - cited question ids exist and are shown;
  - no saving exceeds spend;
  - apply paths stay under home or the Claude folder;
  - optimizations' `related` links are two-sided (except `requires`). Two optimizations that change the same settings key,
    or act on the same cost (`SAME_LEVER`: the main-thread cache lifetime vs the stale-cache guard, auto-compact vs the
    context guard), must be linked.

## The card model (scripts/usage_report.py)

- **Cards:** each question is a function `xxN(m, g)` returning `card(id, question, scope_letters, blocks, why=, insight=, note=, empty=)`.
  - `m` is the `Model` for one scope, `g` holds shared things (prices, source, model colour slots).
  - `CARDS` lists the functions; the report sorts cards by the number in their id.
  - A card's section is its id's letter prefix, one of `SECTIONS`: OV, CX, SE, EX, OUT, ME, SV, TR. There is no CA section.
    The `_CA_*` ids are internal cards, merged away before output.
- **Block builders:**
  - `K(kpi…)`: tiles; 6+ tiles lay out in even rows.
  - `BAR`, `LINE`, `SCATTER`, `HEAT`, `PIE` (donut; `slots=` fixes colours, `total=` the centre value).
  - `TABLE`.
  - `TABS([(label, block or [blocks])], title, sub, desc, collapsed)`: a collapsible section, one tab shown at a time. With a single tab it is just a collapsible section.
  - `{'kind': 'traces'}`: the step-by-step miss timelines.
- **Layout flags on a block:**
  - `width: 'half'|'third'` puts consecutive blocks side by side, with dividers.
  - `hidden: True` keeps a block in metrics/digest but doesn't draw it.
  - Chart options: `minBand` + `scrollX` (scrollable bars), `labelUnit`. The template also honours `pointColors`, which it sets itself on the insights chart.
- **Card-level switches:**
  - `HIDDEN_CARDS` (currently OV6, ME4, ME5, EX10–EX15): computed and kept in the data/digest, not shown, can't be cited.
  - `CARD_ORDER`: a display position that differs from the id.
  - `CARD_SECTION`: the section a card shows in when it differs from its id's letters (the page's `sectionOf()` and the
    digest's `section_of()` read the card's `section`).
  - `alias`: a hidden card with `alias: '<ID>'` opens that card when it is cited or linked. The template, `validate.py`
    (which allows citing it) and `md_card()` all honour it. Nothing in the engine sets it at the moment: to fold a card, set it on the card dict.
  - `merge_misses()` folds the internal cards `_CA_COST`, `_CA_CAUSES` and `_CA_TRACE` into **CX8**. They are built by
    `ca_miss_cost`, `ca_miss_causes` and `ca_miss_traces`, which are in `CARDS`. The cost tiles join CX8's KPIs, the cause
    blocks are added hidden, and the traces become CX8's "Step by step" `TABS` section.
- **Headline:** `headline()` builds the hero (its `spark` is the daily spend drawn under the number) and the 10 tiles.
  - Most tiles come from `m.facts`, which cards fill in as a side effect (OV1, OV7, CX1, CX7, CX8, CX9…), so a card that sets a fact must run.
  - The two median tiles are read by label from the KPI block of hidden OV6, so renaming those labels blanks the tiles.
  - The headline insights come from the cards listed in `HEADLINE_ORDER`.
- **Digest:** `md_card()` writes every card into digest.md, including hidden blocks and the blocks inside tabs.

## When you change…

**Only the look (template, CSS, JS):**
- Edit `scripts/report_template.html`, then run `usage_report.py --render --out <OUT>`. This doesn't recompute, and insights stay current.
- Keep to the design tokens at the top of the CSS:
  - type 12 / 13 / 14 / 16 / 20 / 28 / 48 px, weights 400 / 500 / 600;
  - spacing in multiples of 4 px; corners 6 px (controls, inset boxes), 10 px (cards), round (pills);
  - one button style, `btn()` / `.btn` (`ghost`, `sm`, `icon`), and the 16 px line icons from `icon()`;
  - savings in the one `--save` green, and colour only in dots, marks and chart series.
  All three tabs share the header, the sticky tab row and the sidebar (`renderToc()` lists the report's sections, or the
  `.vcat` groups of the Insights / Optimizations view).
- Check that the page renders: headless Chrome `--dump-dom` and look for `data-render-status="ok" data-render-errors="0"` on `<body>`.
- The README screenshots (`docs/images/`) show made-up data. After a visible change, regenerate them as the docstring of
  `docs/screenshots/make_demo.py` says; never screenshot a real report.

**A card's numbers or blocks (usage_report.py):**
- Run the full `usage_report.py`. This regenerates metrics.json with a new `generated` stamp, so insights.json/optimizations.json become **out of date** (the tabs show a banner) until `/claude-usage:report` (and `/claude-usage:optimize`) run again.
- Update the question's entry in `docs/QUESTIONS.md` ("How" / "Check").
- If an SV card changed, the savings quoted in insights/optimizations change too: regenerate them rather than hand-editing.

**Adding, removing, hiding or renumbering questions (the ripple list):**
1. The engine:
   - the function and `card(id…)`;
   - `CARDS`;
   - any hardcoded ids in strings (card notes and insights such as "see CX8", `HEADLINE_ORDER`, the digest's per-scope `cid` list, `merge_misses()` and the digest's CX8 trace section).
2. `docs/QUESTIONS.md`: the entry, the summary list at the top, and cross-references.
3. `skills/report/reference/insights-guide.md`: the "Usually cites" column and the checklist.
4. `skills/optimize/reference/catalog.md` and `skills/optimize/reference/claude-code.md`: the `When`/`Saving` rules cite ids.
5. The examples in `skills/report/SKILL.md` and `skills/brainstorm/SKILL.md`.
6. The question count ("62 questions" = every card id in metrics.json, hidden ones included) in the root `README.md`, the plugin's `README.md`, `.claude-plugin/plugin.json` and QUESTIONS.md. Keep it out of the skills: the report skill's description loads in every session.
7. The user's existing `<OUT>/insights.json` and `optimizations.json`: remap cited ids (`questions`, `evidence[].question`, ids inside text), or regenerate them.
8. Ids are link targets, so renumbering breaks old links. Prefer hiding (`HIDDEN_CARDS`), folding (`alias`), `CARD_ORDER`
   or, to move a card to another section, `CARD_SECTION` when a stable id matters.

**The insights/optimizations shape:**
- The schema in `schemas/`.
- The checks in `validate.py`.
- The renderers in the template (`insightCard`, `optCard`, `renderInsights`, `renderOpts`).
- The instructions in the matching `SKILL.md` and the insights guide.
- `apply.py` if you add or change a step action.
- A catalog entry that goes after a cost another entry already targets: give both a **Related** line in
  `skills/optimize/reference/catalog.md`. If the pair is a setting or bundled hook the validator can see, add it to
  `SAME_LEVER` in validate.py.

**Prices or models:**
- Edit `scripts/prices.json` (canonical ids; `_compare` sets SV6's comparison set).
- Installed hooks carry their own copy of prices.json and `_session.py`. They pick up changes only when re-applied (`apply.py undo <id>` then `apply <id>`).

**Hooks (`scripts/hooks/`):**
- Hooks must fail open: never block on an error. That includes `import _session` (wrapped in try; a missing helper makes the hook a no-op).
- apply.py installs `_session.py` and `prices.json` only beside hooks that import `_session`.
- Keep `_session.py`'s `canon_model`/price fallback in step with the engine's.
- Changes reach users only when they re-apply the optimization.

**Paths or portability:**
- `claude_dir()`/`default_out()` in the engine, `layout.py` (every file's place inside `<OUT>`), `CLAUDE_DIR`/`expand()` in apply.py, `STATE` in `_session.py`, and the `<OUT>` wording in the three SKILL.md files and README.
- The optimize skill always writes `~/.claude/…` paths; apply.py maps them to a custom Claude folder.

**Skills:**
- Reference scripts as `${CLAUDE_SKILL_DIR}/../../scripts/…`.
- Refer to the output folder as `<OUT>`: the script prints report.html's path, and `--where` prints the resolved folders.
- `report` passes `$ARGUMENTS` (`--days`, `--since/--until`, `--claude-dir`) to the script.

## Staleness rules

- `insights.json.source.metrics_generated` and `optimizations.json.source.metrics_generated` must equal `metrics.json.meta.generated`, and `optimizations.json.source.insights_generated` must equal `insights.json.generated` (validate.py checks all three). Otherwise the tab shows "out of date", and the optimize skill asks for the report skill first.
- A full script run always changes the stamp (`YYYY-MM-DD HH:MM:SS`, so two runs in the same minute differ). `--render` never does.
- The live session is itself in the transcripts, so numbers drift a little between runs of the same day; that's expected.

## Verifying a change

There is no test suite; these commands are the checks.

```bash
S=plugins/claude-usage/scripts; O=$(python3 $S/usage_report.py --where | sed -n 's/^out=//p')   # from the repo root
python3 $S/usage_report.py --out /tmp/usage-check            # full run (≈10 s) into a scratch folder; prints report.html
python3 $S/validate.py insights $O/insights.json --metrics $O/data/metrics.json
python3 $S/validate.py optimizations $O/optimizations.json --metrics $O/data/metrics.json --insights $O/insights.json
python3 -c "import ast,sys; [ast.parse(open(f).read(), feature_version=(3,8)) for f in sys.argv[1:]]" $S/*.py $S/hooks/*.py
claude plugin validate --strict . && claude plugin validate --strict plugins/claude-usage   # marketplace, then plugin
```

- A full run on `<OUT>` itself changes the `generated` stamp. After that, both `validate.py` commands fail on
  `source.metrics_generated` until `/claude-usage:report` (and `/claude-usage:optimize`) run again. That is expected, so run
  it on `<OUT>` only when you mean to regenerate the insights.
- Rendering (macOS): `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --virtual-time-budget=5000 --dump-dom "file://$O/report.html#tab=report" | grep -o '<body[^>]*>'`
  (also `tab=insights`, `tab=optimizations`, `q=<ID>`). Expect `data-render-status="ok"` and `data-render-errors="0"`.
- Portability: build a fake Claude folder with odd model ids and a Windows-style `cwd`, and run with `--claude-dir` and with `HOME` pointing somewhere without `.claude`. The script must stop with the "No Claude Code transcripts folder" message.
- Hooks: pipe a JSON payload with `transcript_path` into the installed script.
- Shell: guard variable paths in `rm` (`"${D:?}"/…`).
- Keep `<OUT>` clean: no `*.bak`/`*.prev` copies next to the outputs. Compare runs with `--out` to a scratch folder instead
  (e.g. `--out /tmp/usage-before`), and keep old code in git, not in `<OUT>`.
