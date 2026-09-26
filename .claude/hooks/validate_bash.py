#!/usr/bin/env python3
"""PreToolUse hook for the Bash tool (wired in .claude/settings.json).

Reads the hook payload as JSON on stdin, pulls out tool_input.command, and blocks (exit 2 —
stderr is shown to Claude) when the command matches a dangerous pattern. Anything else exits 0
and the command runs normally. Stdlib only, so it runs the same on macOS, Linux and Windows.

To add a rule: append a (regex, reason) pair to RULES. Regexes are Python `re`, matched
case-insensitively.
"""

import json
import re
import sys

RULES = [
    # recursive-force rm aimed at a root-ish or system path
    (r'rm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r|-r\s+-f|-f\s+-r|--recursive\s+--force)[a-z]*(\s+-[a-z]+)*\s+(-[a-z]+\s+)*'
     r'((/|~|\$HOME|\*|\.)([\s/*.]|$)|/(etc|usr|var|bin|sbin|lib|opt|home|Users|System|Library|boot|root|dev|private)([\s/]|$))',
     'recursive-force rm targeting a root, home, or system path — refusing. Target a specific project subdirectory instead.'),
    # destructive SQL through a DB client (DELETE only flagged when it has no WHERE)
    (r'(psql|mysql|mariadb|dbshell|sqlite3|mongosh?)\b[^|]*\b(DROP\s+(TABLE|DATABASE|SCHEMA)|TRUNCATE\s|DELETE\s+FROM\s+[a-z0-9_."]+\s*;)',
     'destructive SQL via a DB client — run schema/data changes through a Django migration, not ad hoc.'),
    # destructive SQL statement terminated with a semicolon
    (r'\b(DROP\s+(TABLE|DATABASE|SCHEMA)\s+[a-z_]|TRUNCATE\s+(TABLE\s+)?[a-z_])[a-z0-9_." ]*;',
     'destructive SQL statement (DROP / TRUNCATE) — refusing.'),
    # DELETE FROM with no WHERE
    (r'\bDELETE\s+FROM\s+[a-z_][a-z0-9_."]*\s*;',
     'DELETE FROM with no WHERE clause — refusing.'),
    # force push
    (r'git\s+push\b[^|]*(--force[a-z-]*|--hard|\s-f)(\s|=|$)',
     'git push --force — refusing. Claude does not push; a human handles force pushes.'),
    # pipe a downloaded script into a shell
    (r'(curl|wget)\s+[^|]*\|\s*(sudo\s+)?(sh|bash|zsh)\b',
     'piping a downloaded script into a shell — refusing. Download, inspect, then run.'),
    # world-writable chmod
    (r'chmod\s+(-[a-z]+\s+)*(777|0777|a\+rwx)',
     'chmod 777 — refusing. Grant the narrowest permission that works.'),
    # editing a compose file via the shell
    (r'(>>?|\stee\s|sed\s+-i)[^|;&]*docker-compose\S*\.ya?ml',
     'writing to a docker-compose*.yml file — these must not be edited by tooling.'),
    # bypassing git hooks
    (r'\s--no-verify(\s|$)',
     '--no-verify bypasses the git hooks (lint / coverage gate) — refusing.'),
]


def main():
    raw = sys.stdin.read()
    try:
        command = (json.loads(raw).get('tool_input') or {}).get('command') or ''
    except (json.JSONDecodeError, AttributeError):
        command = raw
    if not command:
        return 0
    for regex, reason in RULES:
        if re.search(regex, command, re.IGNORECASE | re.MULTILINE):
            print(f'BLOCKED by .claude/hooks/validate_bash.py: {reason}', file=sys.stderr)
            print(f'Command: {command}', file=sys.stderr)
            return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
