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
- **Optimizations**, written by `/claude-usage:report` too (its step 4 follows the optimize skill's SKILL.md, which it
  reads), so the report opens complete; `/claude-usage:optimize` redoes them on its own.

A third skill, `/claude-usage:brainstorm`, talks the data through with the user and can edit insights.json. A fourth,
`/claude-usage:video`, turns the report into a 30–60 s video of the user's highlights to share (an MP4 and a self-playing
HTML page, in the promo videos' style). A fifth, `/claude-usage:share`, packs the whole report folder into one JSON file
to send to whoever collects and compares usage, and (`open FILE`) unpacks one someone sent back into a report folder. A
sixth, `/claude-usage:company <folder>`, combines many people's share files into one company report and summarises it. A
seventh, `/claude-usage:clear`, removes the plugin's files from the report folder to start from scratch (the setup stays).
An eighth, `/claude-usage:open`, opens a report that already exists (yours, the company report, or a received one) without
counting again. `optimize`, `brainstorm`, `video`, `share`, `company`, `clear` and `open` are manual-only
(`disable-model-invocation: true`); `report` can also be triggered by the model, and when it is asked in words only to
open the report, it runs open.py instead of building.

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
  data/candidates.json  scripts/candidates.py (called at the end of a full run; reads only metrics.json + config.json):
                      the optimize catalog evaluated in code (complete, validated optimization drafts, skipped entries
                      with reasons, judgment calls with their facts, links) and one savings bundle per lever for insights
  data/*.csv          calls, tool_calls, sessions, subagents, cache_misses
  report.html         scripts/report_template.html with metrics + insights.json + optimizations.json embedded
        │  skills/report     → data/notes-insights.json      → assemble.py insights       → insights.json       (schemas/insights.schema.json)
        │  skills/optimize   → data/notes-optimizations.json → assemble.py optimizations  → optimizations.json  (schemas/optimizations.schema.json)
        │      (skills/report runs both: insights, then the optimize steps; skills/optimize alone redoes the second)
        │      Claude writes only its picks, prose and extra items; assemble.py merges them with candidates.json, fills
        │      stamps, figures and two-sided links, keeps ids stable, and writes the file only when validate.py passes
        │  usage_report.py --render   re-embeds the two JSON files, no recomputing
        ▼
  apply.py       applies one optimization: preview, backup to <OUT>/applied/backups, record in <OUT>/applied/applied.json (written before the
                 first change, so a failure partway can be undone), undo. Undo reverts only that change (a 3-way JSON
                 revert, or removing the appended CLAUDE.md block), keeping later changes. Whole files (hook scripts, the
                 shared _session.py and prices.json) have one origin each in applied.json's `_origins` (created, or the
                 backup of what was there): they stay while any applied optimization uses them, and the last one undone
                 puts the origin back (older applied.json files are converted on load). A change that leaves a JSON file
                 equal in content (layout only) writes nothing. Re-applying a changed optimization undoes the old one first.
                 Hooks are installed as copies in <claude dir>/hooks/claude-usage/ (plus _session.py and prices.json).
                 `apply.py check` previews every optimization in one call, one line each (the optimize skill uses it).
                 `apply` and `undo` write only after `confirm()` reads y from the user's own terminal (/dev/tty; on
                 Windows a console stdin): without one (Claude Code's tools, a pipe) they change nothing and exit 3.
                 Every step, and the settings change it makes, must pass scripts/policy.py (see Invariants).

  video.py plan     → <OUT>/video/storyboard.json (schema: schemas/video.schema.json): which scenes, tiles, trace, insight /
                      optimization ids and levers to show, never the figures; skills/video edits the words and the choice
  video.py check    the schema, 30–60 s, the ids exist, every number in the words is one the data has, no private names
  video.py render   → <OUT>/video/video.html (video_template.html + the figures from metrics/insights/optimizations + the
                      fonts in scripts/fonts/, embedded) → <OUT>/video/claude-usage-video.mp4 (video_capture.py: a headless
                      Chromium browser over the DevTools protocol draws every frame; ffmpeg encodes H.264)

  share.py pack     → <OUT>/share/claude-usage-share-<who>-<date>.json (schema: schemas/share.schema.json), made on demand
                      by skills/share: metrics.json whole, insights/optimizations (with a status: current / out of date),
                      candidates.json, config.json, applied.json's records (id, when, files) and every CSV row (numeric
                      columns typed), plus who (account, optional name/team) and the UTC offset. Names are kept: it holds
                      what report.html holds.
  share.py unpack   → <OUT>/received/<file name>/: the same files back (CSVs byte-identical), a digest, and report.html
                      rendered with meta.shared set, so render() leaves out the apply commands. Refuses a folder
                      holding the user's own report.

  open.py           opens <OUT>/report.html (or company/, or a received/<name>/ by part of its name or the sender's) in
                    the browser, at --tab or --show ID (a card: #q=, an insight or optimization: #tab=…&focus=), and prints
                    when the numbers were counted and whether insights/optimizations are current (their source stamps).
                    Writes nothing, except report.html rebuilt by render() when it is older than metrics.json,
                    insights.json, optimizations.json or the template (a plugin update), in a folder unsafe_out() accepts.

  history/scores.json  usage_report.py's one memory between runs: each run's high-level scores (run_snapshot: score, grade,
                    areas, spend per 30 days, hit rate, peak context), one entry a day (save_history replaces the same day;
                    --until runs neither read nor save it). read_history feeds SV9's progress (all projects) and the
                    headline's change since the last report. Not packed by share.py: the progress is already in metrics.json.

  clear.py          removes <OUT>'s own files by name (FILES/FOLDERS/LEGACY: report.html, insights.json, optimizations.json,
                    data/, video/, share/, received/, company/, history/, flat files from older versions), then the folder when only
                    its marker is left. A preview by default; --yes removes; --keep spares video/share/received/company/history.
                    applied/ stays while apply.py has any optimization on record. skills/clear previews, asks once, removes.

  company.py build <folder>  → <OUT>/company/ (or --to): report.html (report_template.html with company cards: CO company,
                      PE people, LV levers; scopes = Company + one per person; meta.views ['report'], scope_label, and
                      shared.company, so no apply commands and apply.py refuses it), data/metrics.json, data/digest.md
                      (skills/company reads it), data/people.csv. It reads share files one at a time (share.load), re-prices
                      every calls.csv row at this prices.json (Prices.mult from its where/fast columns), takes levers from
                      each report's SV1 (lever_row, by id) and SV2, and dedupes people by session ids. docs/COMPANY.md
                      documents every card.
```

## Invariants (don't break these)

- **Nothing changes the user's setup without them, enforced in code, not in prompts.**
  - `apply.py apply|undo` ask through `confirm()` (the controlling terminal), so only a person can say yes. Never add a
    `--yes`, an environment switch or any other bypass, and never make a skill run `apply`/`undo` (they aren't in any
    skill's `allowed-tools`; the optimize skill previews with `show` and gives the user the command).
  - `scripts/policy.py` is the one list of what an optimization may change: bundled files copied to
    `<claude dir>/hooks/claude-usage/<same name>`, a marked block (≤ 8 lines) appended to `<claude dir>/CLAUDE.md`, and the
    settings keys in `SETTINGS`/`MAPS`/`ENV` in `<claude dir>/settings.json` or a project's `.claude/settings.local.json`,
    with hooks and the status line only running bundled scripts. No step runs a command. apply.py checks each step
    (`check_step`) and the resulting settings diff (`settings_problems`) before previewing or writing; undo touches only
    policy files and restores only from `<OUT>/applied/backups/` (applied.json is a plain file). validate.py applies the
    same checks at assemble time. A new catalog entry that needs another key or file: widen policy.py deliberately, never
    around it.
- **clear.py removes only this plugin's own files.** By name (FILES/FOLDERS/LEGACY), only in a folder that
  `layout.unsafe_out()` and `is_report_dir()` accept (never home, the root or the Claude folder), links removed as links
  and never followed, anything unknown left in place. `applied/` stays while any optimization is applied (or its record
  can't be read), so undo keeps working. It previews unless `--yes`, and it never touches the setup, installed hooks or
  transcripts; a legacy report inside the Claude folder is only pointed out. A new file or folder in `<OUT>` goes into
  clear.py's lists too.
- **Least privilege for skills.** `allowed-tools` pre-approves each script by its full path
  (`Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/x.py" *)`; `${CLAUDE_PLUGIN_ROOT}` expands only in Bash rules), reads only
  `~/.claude-usage/**` (and the plugin's installed reference files), and edits only the notes files the skill owns (report:
  both, since it writes both tabs; optimize: its own). Never add
  bare `Read`, `Write`, `Edit`, `Bash(python3 *)`, `open`, `ls`, an install command or an unrestricted `WebFetch`:
  anything outside the list must reach the user as a permission prompt. Only `report` is model-invocable.
- **Nothing is installed.** The plugin ships no hooks, MCP servers or background processes of its own, and no skill runs
  a package manager: `video.py tools` prints install commands for the user to run.
- **Private and local.** No network in the scripts; report.html and video.html carry a CSP (`default-src 'none'`); the
  video's browser runs with `PRIVATE` flags (no network) over `--remote-debugging-pipe` (a 127.0.0.1 port on Windows only).
  Every script calls `layout.private()`/`os.umask(0o077)`; `layout.prepare()` makes `<OUT>` 0700 and refuses home, the
  filesystem root, the Claude folder and non-report folders; scripts that add to a report call `layout.require_report()`;
  `migrate()` only touches report folders. Transcript text goes through `clip()`, which `scrub()`s secrets
  (`SECRET_IN_TEXT`); config.json goes through `redact()` (env values outside `ENV_KEEP` become `<set>`).
- **Received files are untrusted.** `share.load()` caps size and nesting and rejects unwritable text; `plain()` strips
  control and bidi characters from anything printed or shown; `render()` drops any `meta.apply` that came with the data
  (the template also hides apply commands when `meta.shared`); unpack and `company.py --to` write only into a new folder
  or one they made; `company.py` skips a file it can't summarise.
- **Skills never read transcripts.** They use only `<OUT>` files. If a skill needs a number, add it to the script's output.
- **The scripts stay stdlib-only and portable** (the video's MP4 also needs a Chromium-based browser and ffmpeg; without
  them `video.py render` still writes the HTML page and says what to install):
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
  - apply steps pass scripts/policy.py (the same check apply.py makes);
  - optimizations' `related` links are two-sided (except `requires`). Two optimizations that change the same settings key,
    or act on the same cost (`SAME_LEVER`: the main-thread cache lifetime vs the stale-cache guard, auto-compact vs the
    context guard), must be linked;
  - no change that Claude Code's best practice rules out (`practice_errs()`): an auto-compact window under 300K (setting or
    env), a saved `low` effort, thinking off, a context notice under 100K;
  - at most 3 optimizations marked `first` (the tab's Start here section; the cache-lifetime pin comes with it).
- **Recommendations follow Claude Code's documented best practice; the data aims and sizes them.** A replay's saving never
  makes a change good practice (the case that started this: SV4's replay started at 60K, so the catalog could propose a 60K
  context notice and an 80K auto-compact window, below what Claude Code even accepts).
  `skills/optimize/reference/best-practices.md` holds the verbatim guidance, the "never recommend" list and the digest
  signals that point to a documented practice; the report, optimize and brainstorm skills read it, and catalog entries
  encode it (auto-compact only caps a 1M model, at 400K and never below 300K; the cache lifetimes are pinned at main 1 hour ·
  subagents 5 minutes whenever SV5 favours that mix, as the first recommendation; effort goes back to the model's documented default,
  not below; a rarely used skill is listed `name-only`; SV4's thresholds start at 100K). Keep code guards, not prompts,
  for the hard lines (`practice_errs()`, and policy.py's bounds, which match what Claude Code accepts). When the docs or
  Claude Code change, update best-practices.md, `claude-code.md`, the catalog and these guards together.
- **A share file round-trips.** `share.py unpack` of a `pack` gives back metrics.json, insights, optimizations,
  candidates and config equal, and the CSVs byte-identical. It never reads transcripts, and a received report never shows
  apply commands (`meta.shared`; `apply.py apply` refuses one too).
- **The company report reads share files only** (never transcripts), aggregates from their rows rather than card labels,
  and writes no project names, session titles or prompt text: only numbers and each person's name or account. A company
  of one person equals that person's report (spend, sessions, hit rate, levers).
- **The video quotes only the data.** Its storyboard names what to show; `video.py` takes every figure from metrics.json,
  insights.json and optimizations.json, and `check` rejects a number in a headline or title that none of them has (`facts()`),
  and project names, session titles and paths unless `allow_names` is set. The video never shows prompt text.

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
  - `{'kind': 'score'}`: SV9's efficiency score (value, grade, `scale`, `areas`), drawn by the template's `scoreBlock()`;
    `md_card()` writes it as one line even in a short digest.
- **Layout flags on a block:**
  - `width: 'half'|'third'` puts consecutive blocks side by side, with dividers.
  - `hidden: True` keeps a block in metrics/digest but doesn't draw it.
  - Chart options: `minBand` + `scrollX` (scrollable bars), `labelUnit`. The template also honours `pointColors`, which it sets itself on the insights chart.
- **Card-level switches:**
  - `HIDDEN_CARDS` (currently OV6, ME4, ME5, EX10–EX15): computed and kept in the data/digest, not shown, can't be cited.
  - `CARD_ORDER`: a display position that differs from the id (SV9 leads its section at 0.5).
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
  - Its `score` is SV9's (`m.facts['score']`), drawn beside the cost by `scoreSummary()`; `add_refs()` gives each scope's
    score (headline and SV9 block) the all-projects value, and `add_scores()` adds All projects' "By project" table after
    every scope has run.
- **Efficiency score (SV9):** `efficiency()` combines SV1's levers (`sv_levers()`, by id) into a 1–100 score. `SCORE_AREAS`
  maps each area to its levers (the largest counts: they act on the same cost) and its points; areas combine as
  Π(1 − share), like the tabs and the video combine savings; `GRADES` holds the cut-offs (A+ 88 … C- below 40; generous on purpose: a C means over 42% avoidable). The score block and the
  headline carry them as `scale`, which the (i) beside the score (`scoreHelp()`) shows as a table, so it always matches. The
  main-thread model lever is left out on purpose (it compares with the model in use now, so moving to a cheaper model would
  lower the score). A new SV1 lever goes into an area, or is left out, deliberately: update `SCORE_AREAS`, SV9's note and
  QUESTIONS.md's SV9 together.
- **Across scopes:** `where_used()` runs once on all projects in `build()` and reaches every scope as `g.where`: what a
  user-level setting loads everywhere but only some projects use (SV2's "used in some projects" table, `item_origin()`'s
  advice, which never says "switch it off in each project").
- **Stop hooks:** `_post_stop()` charges a Stop hook only the calls that descend from its `stop_hook_summary` record
  (`parentUuid`) before a `new_input()`; EX5's "What Stop hooks set off" and SV1's lever both read it. Don't go back to a
  time window: work started by another input (another session's message, a `/loop` wake-up) would be charged to the hook.
- **Digest:** `md_card()` writes every card into digest.md, including hidden blocks and the blocks inside tabs.
- **Loading and speed** (≈5 s and ≈320 MB for ~300 MB of transcripts; memory grows with the records kept):
  - `slim()` runs on every record as it is read and drops what no card reads (file contents of Read results, stdout,
    image data, thinking text and signatures, Edit results' file copies); a tool result keeps only its size, first 600
    characters, image count and failure flag (`_res`). A new card that reads a raw transcript field must check that
    `slim()` keeps it. `json_decoder()` shares repeated strings between records.
  - With a start date (the 60-day default, `--since` or `--days`; not with `--all`), a file is skipped when its mtime is
    more than a day before the start and the last timestamp in its tail (`last_time()`) is too, so its lines (and any
    unreadable ones) aren't read or counted; the subtitle counts the transcripts in range.
  - Pure per-command helpers are cached (`bash_class`, `bash_file_ops` with its `FILE_OP_WORDS` pre-filter, dates):
    every scope asks again for the same commands.
  - Output is deterministic: never let set order reach the output (iterate sorted or first-seen with `uniq()`); check
    with two different `PYTHONHASHSEED` values.
  - Scopes run one after another on purpose: parallel scopes nearly tripled memory, and forked processes on macOS make
    every local-time conversion ~130× slower.

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
- Run the full `usage_report.py`. This regenerates metrics.json with a new `generated` stamp, so insights.json/optimizations.json become **out of date** (the tabs show a banner) until `/claude-usage:report` runs again (it rewrites both).
- Update the question's entry in `docs/QUESTIONS.md` ("How" / "Check").
- If an SV card changed, the savings quoted in insights/optimizations change too: regenerate them rather than hand-editing.
- `candidates.py` reads SV, CX, EX, OV and TR cards by id, label, table key and chart title (like video.py). After renaming
  any of those, run `python3 scripts/candidates.py --out <scratch out>`: an entry whose figure it can't find is skipped
  with a reason, so a rename shows up there, not as a crash.

**Adding, removing, hiding or renumbering questions (the ripple list):**
1. The engine:
   - the function and `card(id…)`;
   - `CARDS`;
   - any hardcoded ids in strings (card notes and insights such as "see CX8", `HEADLINE_ORDER`, the digest's per-scope `cid` list, `merge_misses()` and the digest's CX8 trace section).
2. `docs/QUESTIONS.md`: the entry, the summary list at the top, and cross-references.
3. `skills/report/reference/insights-guide.md`: the "Usually cites" column and the checklist.
4. `skills/optimize/reference/catalog.md` and `skills/optimize/reference/claude-code.md`: the `When`/`Saving` rules cite ids.
5. The examples in `skills/report/SKILL.md` and `skills/brainstorm/SKILL.md`, and the video: `scripts/video.py` reads OV2
   (the "By model" chart), OV3, CX8 (its KPI labels and traces), SV1, SV3 and the headline tiles by label. And
   `scripts/candidates.py`: its catalog entries and lever bundles cite and read cards by id.
6. The question count ("63 questions" = every card id in metrics.json, hidden ones included) in the root `README.md`, the plugin's `README.md`, `.claude-plugin/plugin.json`, QUESTIONS.md and the promo videos (`promo/*.html`; re-render their MP4s with `promo/render.mjs`, then the GIFs in both READMEs as its header says). Keep it out of the skills: the report skill's description loads in every session.
7. The user's existing `<OUT>/insights.json` and `optimizations.json`: remap cited ids (`questions`, `evidence[].question`, ids inside text), or regenerate them.
8. Ids are link targets, so renumbering breaks old links. Prefer hiding (`HIDDEN_CARDS`), folding (`alias`), `CARD_ORDER`
   or, to move a card to another section, `CARD_SECTION` when a stable id matters.

**The insights/optimizations shape:**
- The schema in `schemas/`.
- The checks in `validate.py`.
- The renderers in the template (`insightCard`, `optCard`, `renderInsights`, `renderOpts`).
- The instructions in the matching `SKILL.md` and the insights guide.
- `assemble.py` (the notes format and how it merges them) and `candidates.py` (the drafts it produces).
- `apply.py` and `policy.py` if you add or change a step action, a target file or a settings key (see Invariants).
- A catalog entry lives in two places that change together: its description in `skills/optimize/reference/catalog.md`
  and its rule, figures, apply template and links in `scripts/candidates.py`. Entries marked (judgment) there are left to
  Claude with their facts. Every entry follows `skills/optimize/reference/best-practices.md` (the report and brainstorm
  skills link to it there, so moving it means updating their links): the data decides whether it applies and how much it
  saves, the guidance what the change is. Quote the docs only verbatim, and check the quote against the page.
- A catalog entry that goes after a cost another entry already targets: give both a **Related** line in
  `skills/optimize/reference/catalog.md` (and the link in candidates.py). If the pair is a setting or bundled hook the
  validator can see, add it to `SAME_LEVER` in validate.py.

**Prices or models:**
- Edit `scripts/prices.json` (canonical ids; `_compare` sets SV6's comparison set).
- What a call paid over list price is its `pm` (`Prices.mult()`, set once per call from its raw model id and usage):
  `_modifiers` (`bedrock_regional`: any Bedrock inference profile but `global.`; `api_regional`: an `inference_geo` other
  than `"global"`; both from `where_of()`) from a model version on, times the model's own `"fast"` when `usage.speed` is `"fast"`. Everything that prices a call's own tokens
  passes it (`cost(model, u, c['pm'])`, `per_token(…, c['pm'])`, `w_rate`/`r_rate`, items carry `pm`); a what-if on another
  model (SV6) passes `mult(other, c['where'])`, without fast mode. With every `pm` at 1 the numbers match the list-price
  engine exactly: keep the arithmetic order (`… / 1e6 * mult`).
- Ids that name no model (a Bedrock application inference profile ARN) resolve through `ALIASES` (`model_aliases()`:
  `_aliases`, then settings' `modelOverrides`, then `ANTHROPIC_DEFAULT_<FAMILY>_MODEL` as the family's comparison model,
  noted as guessed); `canon_model()` checks it first. Only an id with no `claude-` in it can be an alias (`names_no_model()`),
  so a pinned native model (`ANTHROPIC_DEFAULT_OPUS_MODEL=claude-opus-4-8`) is never re-priced. Test with a fake Claude
  folder whose transcripts use those ids, and with those variables set to native ids.
- Installed hooks carry their own copy of prices.json and `_session.py`. They pick up changes only when re-applied (`apply.py undo <id>` then `apply <id>`).
- A new model or a change in the docs' model tables: `candidates.EFFORT_DEFAULT` (each model's default effort, from
  model-config), `EFFORT_NOTE`, `EFFORT_CACHE_SAFE` (models whose effort changes keep the cache), `LSP_PLUGINS` (the official
  code intelligence plugins), the engine's `SUBAGENT_DEFAULT_TYPES` (built-in agent types with no model of their own), and
  best-practices.md's effort table.

**The video (`skills/video`, `scripts/video.py`, `video_capture.py`, `video_template.html`, `scripts/fonts/`):**
- A scene type lives in four places: `SECONDS`/`FIELDS`/`resolve()`/`plan()` in video.py, `BUILD` in the template, the
  `type` enum in `schemas/video.schema.json`, and the scene list in `skills/video/SKILL.md`.
- The template keeps the promo's look and its one-timeline engine (`render(t)`, `window.__seek`, `?record`); no CSS
  animations or timers, or frames stop being exact. Look at a change with `video.py render --stills 3,12,20`.
- The fonts are OFL (licenses beside them), Latin subsets, embedded into each video.html, so the page makes no network requests.
- The prerequisites: `video.py tools` reports the browser and ffmpeg and, for a missing one, the install command from `INSTALL`
  in video_capture.py (per package manager), for the user to run: the skill never installs anything. `find_ffmpeg()` also
  looks where installers put ffmpeg, since a fresh install isn't on the shell's PATH yet. `find_browser()`/`find_ffmpeg()`
  accept only programs named like a browser / ffmpeg (`_named()`), so `--browser`/`--ffmpeg` can't launch anything else.

**The share file (`skills/share`, `scripts/share.py`, `schemas/share.schema.json`):**
- It embeds the report folder's files as they are, so a new card, field or CSV column needs nothing here. A new file in
  `<OUT>` that the report depends on goes into `pack`, `unpack` and the schema together (and clear.py's lists); a new CSV
  only into `layout.DATA_FILES` (share.py and clear.py read it from there).
- Changing the share file's own shape (top-level fields, status values, how tables are stored) raises `format_version`
  (`VERSION` in share.py and the schema's `const`); `unpack` must keep reading older versions and refuses newer ones.
- Check a change with the round trip: `share.py pack --out <copy of OUT>`, then `unpack` it, compare the files, and
  render the received report.html (all three tabs).

**The company report (`skills/company`, `scripts/company.py`, `docs/COMPANY.md`):**
- It reads calls.csv, sessions.csv and cache_misses.csv by column name (`NEED` lists the calls columns it requires), SV1
  rows by lever `id` (`candidates.lever_row`) and SV2's "Of which you can switch off". Renaming any of those, or a lever
  id, needs company.py (and `candidates.LEVER_TITLES`, one model-free title per lever) to follow. A new lever: add it to
  `LEVER_ROWS` and `LEVER_TITLES`; company.py picks it up.
- Cards are built with the engine's block builders and rendered by `usage_report.render()`; the template needs only the
  metrics shape. `meta.views` (the tabs shown) and `meta.scope_label` (the selector's name) are generic template options.
- Check a change: a company of one (your own share file) must equal your report; the synthetic set (copies with new
  session ids, names, scaled tokens, shifted dates, plus duplicates and bad files) must give Σ people = company, the right
  merges and skips; render every scope with `#render=all`; two `PYTHONHASHSEED` values give identical output.

**Hooks (`scripts/hooks/`):**
- Hooks must fail open: never block on an error. That includes `import _session` (wrapped in try; a missing helper makes the hook a no-op).
- apply.py installs `_session.py` and `prices.json` only beside hooks that import `_session`.
- Keep `_session.py`'s `canon_model`/price fallback in step with the engine's, `_aliases` and `_modifiers` included (the
  hooks pass the call from `last_usage()` to `price()`; they don't read `modelOverrides`).
- Changes reach users only when they re-apply the optimization.
- Hooks run on every Read, prompt or status-line refresh, so keep them cheap (~25 ms, most of it Python starting):
  no argparse (`S.arg()`), imports only where needed, and `last_usage()` reads the transcript's last 64 KB first (growing
  to 3 MB at most). Measure against the old hook with the same payloads before and after a change.
- The catalog writes hook commands as `python3 "$HOME/.claude/hooks/claude-usage/x.py" …`; apply.py installs them as
  `h="…/x.py"; [ -f "$h" ] || exit 0; p="<its own interpreter>"; [ -x "$p" ] || p=python3; exec "$p" -S "$h" …` (POSIX,
  not venvs): it skips interpreter shims such as Apple's /usr/bin/python3, and a deleted script exits 0 instead of
  Python's exit 2, which would block every prompt or Read. `plain_command()` maps it back, so re-applying never adds a
  duplicate, and the engine's `hook_script()` still finds the script name (the script path comes first on purpose). The
  digest's setup lines show installed hooks in the plain form too (`md_config()` imports `plain_command`), so the
  arguments aren't lost to its 160-character clip.
- A guard blocks or denies only when `S.state_put()` could record the warning, so the retry always goes through.
- Undoing an optimization never restores an older `_session.py` under hooks that other applied optimizations still use.

**Paths or portability:**
- `claude_dir()`/`default_out()` in the engine, `layout.py` (every file's place inside `<OUT>`), `CLAUDE_DIR`/`expand()` in apply.py, `STATE` in `_session.py`, and the `<OUT>` wording in the SKILL.md files and README.
- The optimize skill always writes `~/.claude/…` paths; apply.py maps them to a custom Claude folder.

**Skills:**
- Reference scripts as `"${CLAUDE_PLUGIN_ROOT}/scripts/…"`, exactly as in the skill's `allowed-tools` rule (the rule
  matches the command text, quotes included).
- Refer to the output folder as `<OUT>`: the script prints report.html's path, and `--where` prints the resolved folders.
- `report` passes `$ARGUMENTS` (`--days`, `--since/--until`, `--all`, `--claude-dir`) to the script. Without `--days`,
  `--since` or `--all` the script covers the last `DEFAULT_DAYS` (60) days, counted back from `--until` when given, and
  says so in `meta.notes`; the skill turns a period asked for in words into those flags.
- `report` and `optimize` never Write insights.json or optimizations.json themselves. Claude Writes a small notes file
  (`<OUT>/data/notes-insights.json` / `notes-optimizations.json`) and runs a plain `python3 …/assemble.py insights|optimizations
  --out <OUT>`, which reads it and writes the final file when it validates. The notes stay in `data/` so Claude can Edit
  and re-run; the next full run removes notes older than metrics.json. So Claude writes only
  judgment and prose, and every figure, link and stamp comes from the scripts. Keep new mechanical work in the scripts,
  not in the SKILL.md steps.
- Skill Bash commands must start with `python3` and have no `cd`, `&&`, variable assignments, pipes, redirection or
  heredocs: Claude Code's permission checks refuse those (a JSON heredoc trips "brace with quote character"), which in a
  headless run fails the step and interactively asks every time. Test a skill change with a real headless run (a
  scratch `CLAUDE_USAGE_OUT`, `--setting-sources project --no-session-persistence`, `--output-format stream-json --verbose`,
  run from a folder outside the repo so reads outside `<OUT>` are really refused). The Edit/Read rules name `~/.claude-usage`,
  so with a scratch `<OUT>` add the same rules for it, and for the working copy the rule an installed plugin gets for its
  own reference files (`~/.claude/plugins/cache/claude-usage-optimizer/claude-usage/**`):
  `--allowedTools "Edit(//<scratch>/data/notes-insights.json)" "Edit(//<scratch>/data/notes-optimizations.json)" "Read(//<scratch>/**)" "Read(//<repo>/plugins/claude-usage/**)"`
  (the report skill writes both notes files; it reads the optimize skill's SKILL.md and references from the plugin folder).
  Check `permission_denials` in the result: only what the skill shouldn't do on its own may be there. A skill the model
  starts itself (from plain words, not a typed slash command) gets no pre-approval from its `allowed-tools` in a headless
  run (2.1.286: even `usage_report.py --where` is denied, and the Skill call needs `--allowedTools "Skill(claude-usage:report)"`),
  so test a skill's commands through its slash command, and a plain-words route only for which command it picks.

## Staleness rules

- `insights.json.source.metrics_generated` and `optimizations.json.source.metrics_generated` must equal `metrics.json.meta.generated`, and `optimizations.json.source.insights_generated` must equal `insights.json.generated` (validate.py checks all three). Otherwise the tab shows "out of date", and the optimize skill asks for the report skill first.
- A full script run always changes the stamp (`YYYY-MM-DD HH:MM:SS`, so two runs in the same minute differ). `--render` never does.
- The live session is itself in the transcripts, so numbers drift a little between runs of the same day; that's expected.

## Verifying a change

There is no test suite; these commands are the checks.

```bash
S=plugins/claude-usage/scripts; O=$(python3 $S/usage_report.py --where | sed -n 's/^out=//p')   # from the repo root
python3 $S/usage_report.py --out /tmp/usage-check            # full run (≈5 s) into a scratch folder; prints report.html
python3 $S/candidates.py --out /tmp/usage-check                # one line per catalog entry; "problems" must stay empty
python3 $S/apply.py check --dir $O                             # every optimization's apply steps, one line each
python3 $S/validate.py insights $O/insights.json --metrics $O/data/metrics.json
python3 $S/validate.py optimizations $O/optimizations.json --metrics $O/data/metrics.json --insights $O/insights.json
python3 -c "import ast,sys; [ast.parse(open(f).read(), feature_version=(3,8)) for f in sys.argv[1:]]" $S/*.py $S/hooks/*.py
claude plugin validate --strict . && claude plugin validate --strict plugins/claude-usage   # marketplace, then plugin
python3 $S/video.py tools                                          # browser and ffmpeg found? if not, how to install them here
python3 $S/video.py plan --out /tmp/usage-check && python3 $S/video.py check --out /tmp/usage-check   # after the full run
python3 $S/video.py render --out /tmp/usage-check --stills 3,12,20   # PNG frames in video/stills/; without --stills, the MP4
python3 $S/share.py pack --out /tmp/usage-check                   # the share file; then unpack it:
python3 $S/share.py unpack /tmp/usage-check/share/<file>.json --out /tmp/usage-check   # → received/<file>/report.html
python3 $S/company.py build /tmp/usage-check/share --to /tmp/usage-company   # a company of one: must equal your report
BROWSER=true python3 $S/open.py --out /tmp/usage-check --tab insights   # status and the page it opens (no browser window)
python3 $S/clear.py --out /tmp/usage-check                        # what it would remove (preview); --yes removes it
```

- A full run on `<OUT>` itself changes the `generated` stamp. After that, both `validate.py` commands fail on
  `source.metrics_generated` until `/claude-usage:report` runs again (it rewrites both). That is expected, so run
  it on `<OUT>` only when you mean to regenerate the insights.
- Rendering (macOS): `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --virtual-time-budget=5000 --dump-dom "file://$O/report.html#tab=report" | grep -o '<body[^>]*>'`
  (also `tab=insights`, `tab=optimizations`, `q=<ID>`). Expect `data-render-status="ok"` and `data-render-errors="0"`.
- Portability: build a fake Claude folder with odd model ids and a Windows-style `cwd`, and run with `--claude-dir` and with `HOME` pointing somewhere without `.claude`. The script must stop with the "No Claude Code transcripts folder" message.
- Engine changes that should not change numbers: run the old and new engine on the same input (`--until` a past day,
  `PYTHONHASHSEED=0`, two `--out` scratch folders) and compare metrics.json (minus `meta.generated`), digest.md (minus
  its Generated line), config.json and the CSVs; then run the new one under a second seed and compare again.
- assemble.py: put a notes file at `<a copy of OUT>/data/notes-insights.json` (or `--notes FILE`), run
  `assemble.py insights|optimizations --out <that copy>`, check it prints OK, then render that copy.
- Hooks: pipe a JSON payload with `transcript_path` into the installed script.
- Shell: guard variable paths in `rm` (`"${D:?}"/…`).
- Keep `<OUT>` clean: no `*.bak`/`*.prev` copies next to the outputs. Compare runs with `--out` to a scratch folder instead
  (e.g. `--out /tmp/usage-before`), and keep old code in git, not in `<OUT>`.
