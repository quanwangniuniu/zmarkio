#!/usr/bin/env bash
#
# PreToolUse hook for the Bash tool (wired in .claude/settings.json).
#
# Reads the hook payload as JSON on stdin, pulls out .tool_input.command, and blocks
# (exit 2 — stderr is shown to Claude) when the command matches a dangerous pattern.
# Anything else exits 0 and the command runs normally.
#
# To add a rule: add a "regex<TAB>reason" line to the here-doc at the bottom.
# Regexes are POSIX extended (grep -E) and matched case-insensitively.

set -euo pipefail

payload="$(cat)"

# --- extract the command string (jq if available, else python3, else raw) -------------
if command -v jq >/dev/null 2>&1; then
  command_str="$(printf '%s' "$payload" | jq -r '.tool_input.command // ""')"
elif command -v python3 >/dev/null 2>&1; then
  command_str="$(printf '%s' "$payload" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))')"
else
  command_str="$payload"
fi

[ -z "$command_str" ] && exit 0

check() {
  local regex="$1" reason="$2"
  if printf '%s' "$command_str" | grep -qiE -- "$regex"; then
    echo "BLOCKED by .claude/hooks/validate-bash.sh: $reason" >&2
    echo "Command: $command_str" >&2
    exit 2
  fi
}

# --- rules --------------------------------------------------------------------------
# recursive-force rm aimed at a root-ish or system path
check 'rm[[:space:]]+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r|-r[[:space:]]+-f|-f[[:space:]]+-r|--recursive[[:space:]]+--force)[a-z]*([[:space:]]+-[a-z]+)*[[:space:]]+(-[a-z]+[[:space:]]+)*((/|~|\$HOME|\*|\.)([[:space:]/*.]|$)|/(etc|usr|var|bin|sbin|lib|opt|home|Users|System|Library|boot|root|dev|private)([[:space:]/]|$))' \
  'recursive-force rm targeting a root, home, or system path — refusing. Target a specific project subdirectory instead.'

# destructive SQL through a DB client (DELETE only flagged when it has no WHERE)
check '(psql|mysql|mariadb|dbshell|sqlite3|mongosh?)\b[^|]*\b(DROP[[:space:]]+(TABLE|DATABASE|SCHEMA)|TRUNCATE[[:space:]]|DELETE[[:space:]]+FROM[[:space:]]+[a-z0-9_."]+[[:space:]]*;)' \
  'destructive SQL via a DB client — run schema/data changes through a Django migration, not ad hoc.'

# destructive SQL statement terminated with a semicolon
check '\b(DROP[[:space:]]+(TABLE|DATABASE|SCHEMA)[[:space:]]+[a-z_]|TRUNCATE[[:space:]]+(TABLE[[:space:]]+)?[a-z_])[a-z0-9_." ]*;' \
  'destructive SQL statement (DROP / TRUNCATE) — refusing.'

# DELETE FROM with no WHERE
check '\bDELETE[[:space:]]+FROM[[:space:]]+[a-z_][a-z0-9_."]*[[:space:]]*;' \
  'DELETE FROM with no WHERE clause — refusing.'

# force push
check 'git[[:space:]]+push\b[^|]*(--force[a-z-]*|--hard|[[:space:]]-f)([[:space:]]|=|$)' \
  'git push --force — refusing. Claude does not push; a human handles force pushes.'

# pipe a downloaded script into a shell
check '(curl|wget)[[:space:]]+[^|]*\|[[:space:]]*(sudo[[:space:]]+)?(sh|bash|zsh)\b' \
  'piping a downloaded script into a shell — refusing. Download, inspect, then run.'

# world-writable chmod
check 'chmod[[:space:]]+(-[a-z]+[[:space:]]+)*(777|0777|a\+rwx)' \
  'chmod 777 — refusing. Grant the narrowest permission that works.'

# editing a compose file via the shell
check '(>>?|[[:space:]]tee[[:space:]]|sed[[:space:]]+-i)[^|;&]*docker-compose[^[:space:]]*\.ya?ml' \
  'writing to a docker-compose*.yml file — these must not be edited by tooling.'

# bypassing git hooks
check '[[:space:]]--no-verify([[:space:]]|$)' \
  '--no-verify bypasses the git hooks (lint / coverage gate) — refusing.'

exit 0
