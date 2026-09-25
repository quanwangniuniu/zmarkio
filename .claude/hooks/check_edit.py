#!/usr/bin/env python3
"""PostToolUse hook for Edit / Write / MultiEdit (wired in .claude/settings.json).

Runs the fast, regex-only pass of scripts/conventions/check_conventions.py on the file Claude
just edited — added lines only. On findings it exits 2 so stderr is shown to Claude and it fixes
them in the same turn. Tolerant by design: anything unexpected (bad payload, git error, file out
of scope) exits 0 and the edit stands. The full pass runs in check_on_stop.py and CI.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
CHECKER = HOOK_DIR.parents[1] / 'scripts' / 'conventions' / 'check_conventions.py'
IN_SCOPE = ('.py', '.ts', '.tsx')


def main():
    if not CHECKER.is_file():
        return 0
    try:
        file_path = (json.load(sys.stdin).get('tool_input') or {}).get('file_path') or ''
    except (json.JSONDecodeError, AttributeError):
        return 0
    if not file_path.endswith(IN_SCOPE):
        return 0

    try:
        os.chdir(os.environ.get('CLAUDE_PROJECT_DIR') or HOOK_DIR.parents[1])
    except OSError:
        return 0

    result = subprocess.run(
        [sys.executable, str(CHECKER), '--mode', 'fast', '--flag-suppressions', '--file', file_path],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 1:
        return 0

    print('Convention check (.claude/rules) flagged your last edit:', file=sys.stderr)
    print(result.stdout.rstrip('\n'), file=sys.stderr)
    print('Project policy (CLAUDE.md, "Rule enforcement"): fix these now, without asking the user, then continue and mention the fixes in one line of your reply. Code the user pasted, copied, or got from a teammate is NOT a request for the pattern: fix it. Never add \'conventions: ignore\' yourself; keep a flagged pattern only if the user explicitly said to keep that exact pattern despite the rule.', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
