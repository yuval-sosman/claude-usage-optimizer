#!/usr/bin/env bash
# Claude Code usage report (claude-usage plugin): answers the questions in docs/QUESTIONS.md from your local transcripts
# (<claude dir>/projects) by plain counting, and writes a self-contained HTML report. In Claude Code, /claude-usage:report
# also has Claude write the Insights tab; /claude-usage:optimize the Optimizations tab.
#
#   ./usage-report.sh                   # the last 60 days, report in ~/.claude-usage/report.html
#   ./usage-report.sh --claude-dir /data/claude   # Claude Code keeps its data elsewhere (or set CLAUDE_CONFIG_DIR)
#   ./usage-report.sh --where           # print the Claude folder and output folder it would use
#   ./usage-report.sh --open            # ...and open it in your browser
#   ./usage-report.sh --days 30         # only the last 30 days
#   ./usage-report.sh --all             # every transcript on disk, however old
#   ./usage-report.sh --since 2026-09-01 --until 2026-09-15
#   ./usage-report.sh --out ~/reports/claude --no-csv
#   ./usage-report.sh --render          # re-embed insights.json / optimizations.json without recomputing
#   ./usage-report.sh --help            # all options
#
# Outputs: report.html (Report, Insights and Optimizations tabs), and in data/: metrics.json, digest.md (for Claude),
# config.json (your setup, secrets removed), calls.csv, tool_calls.csv, sessions.csv, subagents.csv, cache_misses.csv.
# Requires only python3 (3.8+, standard library). Dollar figures use prices.json (API list prices).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
py="${PYTHON:-}"
if [ -z "$py" ]; then
  for c in python3 python py; do                 # python/py: Windows (Git Bash) installs often have no python3
    if command -v "$c" >/dev/null 2>&1; then py="$c"; break; fi
  done
fi
if [ -z "$py" ] || ! command -v "$py" >/dev/null 2>&1; then
  echo "usage-report: Python 3.8+ is required (standard library only). Set PYTHON=/path/to/python if it is not on PATH." >&2
  exit 1
fi
if ! "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
  echo "usage-report: Python 3.8 or newer is required." >&2
  exit 1
fi

exec "$py" "$here/usage_report.py" "$@"
