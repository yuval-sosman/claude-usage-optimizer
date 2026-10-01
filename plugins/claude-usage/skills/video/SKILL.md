---
name: video
description: Make a 30–60 second video of your own Claude Code usage highlights (cost, a costly cache miss, insights, optimizations and what they would save) to share, as an MP4 and a self-playing HTML page in the report's style. Run after /claude-usage:report.
disable-model-invocation: true
argument-hint: "[what to highlight, e.g. \"cache misses and savings\"] [--seconds 30-60] [--no-mp4]"
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/video.py" *), Read(~/.claude-usage/**), Read(~/.claude/plugins/cache/claude-usage-optimizer/claude-usage/**), Edit(~/.claude-usage/video/storyboard.json)
---

# A video of the user's usage highlights

You turn the user's report into a short square video they can post (LinkedIn, Slack, a team update): their numbers,
told in a few scenes, in the report's dark style. It is a summary of their usage, not an advert: plain, factual,
in the first person ("my", "I"), because the user is sharing their own numbers.

A script does the work: `tools` checks what the MP4 needs, `plan` drafts a storyboard from the report's data, you shape
it to what the user wants to show, `render` draws it. The storyboard only names *what* to show; every figure comes from
the data files.

## Ground rules

- Use only `<OUT>` files (`data/metrics.json`, `insights.json`, `optimizations.json`, through the script's output).
  **Never read session transcripts.**
- **Never invent a number.** Headlines may quote only figures that `plan` prints; `check` rejects any other number.
- **Private by default.** The video will be shared, so it shows no project names, session titles, file paths or prompt
  text. `check` refuses them. Include them only if the user asks, and then set `"allow_names": true`.
- Dollars are API list-price equivalents; the video says so in its last frame.

Paths: the plugin's scripts are in `${CLAUDE_PLUGIN_ROOT}/scripts`. Exactly those commands are pre-approved (each script by its full path); anything else, such as another program, `python3 -c`, a `cd` or a pipe, makes Claude Code ask the user first, so don't work around a refusal. `<OUT>` is the report folder, `~/.claude-usage` by default;
`plan` prints the storyboard's path inside it.

## 1. Prerequisites

Skip this step if the user asked for the page only (`--no-mp4`): that needs nothing but Python.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/video.py" tools
```

The MP4 needs two programs besides Python: a Chromium-based browser (Chrome, Edge, Chromium or Brave), which draws each
frame, and ffmpeg, which encodes the frames into the video. `tools` finds them, or prints for each missing one the
`install:` command for this machine and a `by hand:` link, and ends with `ready: yes` or `ready: no`.

**Never install anything.** This skill can't (no install command is allowed to it) and must not try another way. When
something is missing:

1. Tell the user, in a sentence or two, what is missing and what it's for, and give them the `install:` command (or the
   `by hand:` link) to run themselves if they want the MP4, in their own terminal or here with a `!` in front
   (e.g. `! brew install ffmpeg`). It is their decision and their command.
2. Go on without waiting: `render` writes the HTML page, which plays the same video and can be screen-recorded (H hides
   the controls). Say so. Once they have installed it, `video.py tools` finds it (it also looks where installers put
   these programs, before the shell's PATH knows about them) and `render` makes the MP4.

## 2. Plan

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/video.py" plan [--seconds N]
```

Pass `--seconds` only if the user asked for a length (30–60). If it stops with "No report data", tell the user to run
`/claude-usage:report` first and stop.

It writes `<OUT>/video/storyboard.json` and prints the draft scenes, everything each scene could show instead (ids and
their figures), and notes (for example: no optimizations yet, or insights written from an older report and so left out).

## 3. Shape the story

Read `<OUT>/video/storyboard.json` (the Read is required before editing it). The draft already works; change it only to
serve what the user asked for in `$ARGUMENTS`, or to make the headlines better:

- **Scenes** (the contract is `${CLAUDE_PLUGIN_ROOT}/schemas/video.schema.json`): `intro` (total cost and daily
  spend), `numbers` (`tiles`), `models` (cost by model and token type), `miss` (`trace`: one costly cache miss, step by
  step), `insights` (`items`: 1–3 insight ids), `levers` (`items`: 2–5 SV2 lever names; use when there are no
  insights), `optimizations` (`items`: 1–4 ids; applied ones show a check), `savings` (the changes together, overlaps
  removed). Each type at most once. Drop or reorder scenes to match the focus; keep `intro` first and end on
  `savings` when it is there.
- **Length**: 30–60 s in total; each scene 3.5–12 s. The defaults (5–7.5 s) are tuned for reading; give extra time to a
  scene with more text rather than shortening others below their default.
- **Headlines**: one short sentence (up to 64 characters, better under 45), plain and specific, first person, the key
  number in it when there is one: "My costliest cache miss: $7.30." "22 days of Claude Code, by the numbers." Quote
  figures exactly as `plan` prints them (rounding to fewer digits is fine). No hype words, no emoji, no questions.
- **Titles**: an item may get a shorter title (`{"id": "…", "title": "…"}`, up to 70 characters) when the insight's or
  optimization's own title is long or names something private.
- `eyebrow` (small label above a headline) is optional; the defaults are fine.

Then check it, fix what it lists, and repeat until it prints `OK`:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/video.py" check
```

## 4. Render

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/video.py" render --open
```

Run it with a long timeout (up to 10 minutes): recording draws every frame, about a minute or two for a 45 s video.
Add `--no-mp4` if the user only wants the HTML page. It prints the page's path and the MP4's. If step 1 ended without
the browser or ffmpeg, it writes only the page and says what is missing; offer to render again once it is installed.

Optional, when a title or headline is long: look at a frame or two before calling it done:
`video.py render --stills 8,20` writes PNGs of those seconds to `<OUT>/video/stills/`; Read them, and shorten
anything that is cut off (`…`) or crowded.

## 5. Reply

Short: where the MP4 and the page are, how long it is, and the scenes in one line each. Remind the user that it shows
their real numbers, so watch it before posting. Mention that re-running `/claude-usage:video` after a new report makes
a fresh one (the storyboard is re-planned from the new data).
