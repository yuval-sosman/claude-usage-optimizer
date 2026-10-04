# usage-optimizer

**See where your Claude Code money goes, and what would have kept it.**

[![A 25-second tour: what 62 days of Claude Code cost, the report command, one costly cache miss traced step by step, Claude's insights with what each fix would have saved, applying a fix, and what the fixes together would have saved](../../promo/usage-optimizer-short.gif)](../../promo/usage-optimizer-short.mp4)

<sub>Demo data. Watch as video: [25-second cut](../../promo/usage-optimizer-short.mp4) · [full 53-second tour](../../promo/usage-optimizer-video.mp4).</sub>

Reads your local Claude Code transcripts, answers 63 questions about cost, caching, context and habits, scores how
efficiently you work (1 to 100, with a grade from A+ to C- for each area, and your progress since the last report), and has Claude
write what to change, with the dollars each change would have saved so far. The changes follow Claude Code's documented best
practice; your numbers decide which apply and how much they are worth. Nothing leaves your machine.

```text
/usage-optimizer:report        build the report, write the Insights and Optimizations tabs, open it
/usage-optimizer:open          open the report you already have, without building it again
/usage-optimizer:optimize      redo the optimizations (e.g. with a focus), or preview one to apply
/usage-optimizer:brainstorm    dig into the numbers with Claude and test what-ifs
/usage-optimizer:video         a 30–60 second video of your own highlights, to share
/usage-optimizer:share         the whole report as one file, to send to whoever compares usage
/usage-optimizer:company       many people's share files combined: totals, people compared, levers company-wide
/usage-optimizer:clear         remove the report files to start from scratch (your setup stays)
```

Install, screenshots, options and privacy: the [repository README](../../README.md). Every question, how it's counted
and how to check it: [docs/QUESTIONS.md](docs/QUESTIONS.md).
