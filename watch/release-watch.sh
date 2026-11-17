#!/usr/bin/env bash
# Example of watching an EXTERNAL deploy target (here: a Heroku app). Every 30 s it reads the
# latest release number and appends a "release" event to the log when it changes.
# Uses only `heroku releases -n 1` (never releases:info / config -- they print secrets).
# Swap the `v=` line for whatever tells you "the change is live" in your stack.
OUT="${OUT:-$HOME/.claude/loop-events.jsonl}"
APP="${1:?usage: release-watch.sh <heroku-app>}"
last=""
while true; do
  v=$(heroku releases -a "$APP" -n 1 2>/dev/null | grep -oE '^ +v[0-9]+' | tr -d ' ' | head -1)
  if [ -n "$v" ] && [ "$v" != "$last" ]; then
    if [ -n "$last" ]; then
      printf '{"t":"%s","agent":"release","kind":"release","label":"%s %s"}\n' "$(date +%Y-%m-%dT%H:%M:%S%z | sed 's/\(..\)$/:\1/')" "$APP" "$v" >> "$OUT"
    fi
    last="$v"
  fi
  sleep 30
done
