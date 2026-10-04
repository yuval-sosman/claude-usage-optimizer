#!/bin/bash
# UserPromptSubmit hook (macOS): keep the Mac from idle-sleeping for a while after each prompt, so a long run is not cut
# off mid-response ("API Error: Your computer went to sleep mid-response"), which also throws away the prompt cache.
# One caffeinate at a time; each prompt restarts the timer. Closing the lid on battery still sleeps.
#   keep_awake.sh [seconds, default 7200]
cat >/dev/null
[ "$(uname)" = "Darwin" ] || exit 0
command -v caffeinate >/dev/null 2>&1 || exit 0
state="$(cd "$(dirname "$0")" && pwd)/state"            # beside the installed hook (never a shared /tmp: another user could plant a link)
mkdir -p "$state" 2>/dev/null && chmod 700 "$state" 2>/dev/null || exit 0
pidf="$state/caffeinate.pid"
[ -L "$pidf" ] && exit 0
pid="$(cat "$pidf" 2>/dev/null)"
# caffeinate may have timed out and its PID been reused: only stop that PID if it is still caffeinate
case "$pid" in ''|*[!0-9]*) ;; *) [ "$(ps -p "$pid" -o comm= 2>/dev/null)" = caffeinate ] && kill "$pid" 2>/dev/null ;; esac
nohup caffeinate -i -t "${1:-7200}" >/dev/null 2>&1 &
echo $! > "$pidf"
exit 0
