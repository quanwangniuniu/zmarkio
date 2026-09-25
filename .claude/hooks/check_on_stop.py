#!/usr/bin/env python3
"""Stop hook (wired in .claude/settings.json).

After Claude implements an approved plan, runs the full pass of
scripts/conventions/check_conventions.py over the files *this session* edited since the plan was
approved (their diff vs HEAD). Other uncommitted work in the checkout — the developer's own edits,
another session's — is never flagged. On findings it exits 2 so stderr is shown to Claude, which
fixes them without asking (policy: CLAUDE.md → "Rule enforcement").

Gates, cheapest first — any miss exits 0 immediately:
  1. plan gate   — the session transcript contains an approved ExitPlanMode call
  2. file gate   — Claude edited in-scope files after that approval (Edit / Write / MultiEdit)
  3. change gate — those files differ from the last state that passed in this session
Loop guard: if Claude is already continuing because of this hook (stop_hook_active) and the
findings are unchanged, let it stop and surface the findings to the user instead.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
CHECKER = HOOK_DIR.parents[1] / 'scripts' / 'conventions' / 'check_conventions.py'
EDIT_TOOLS = {'Edit', 'Write', 'MultiEdit'}


def files_edited_after_plan(transcript_path):
    """Paths Claude edited after an ExitPlanMode call was approved; None if no plan was approved."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return None
    plan_ids, approved, edited = set(), False, []
    with open(transcript_path, encoding='utf-8', errors='replace') as fh:
        for raw in fh:
            if 'tool_use' not in raw and 'tool_result' not in raw:
                continue
            try:
                content = (json.loads(raw).get('message') or {}).get('content')
            except (json.JSONDecodeError, AttributeError):
                continue
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get('type') == 'tool_use' and item.get('name') == 'ExitPlanMode':
                    plan_ids.add(item.get('id'))
                elif item.get('type') == 'tool_result' and item.get('tool_use_id') in plan_ids \
                        and not item.get('is_error'):
                    approved = True
                elif approved and item.get('type') == 'tool_use' and item.get('name') in EDIT_TOOLS:
                    path = (item.get('input') or {}).get('file_path')
                    if path and path not in edited:
                        edited.append(path)
    return edited if approved else None


def git(*args):
    return subprocess.run(['git', *args], capture_output=True, check=False).stdout


def fingerprint(files):
    digest = hashlib.sha256('\0'.join(files).encode())
    digest.update(git('diff', 'HEAD', '--no-color', '--no-ext-diff', '--binary', '--', *files))
    for name in sorted(git('ls-files', '--others', '--exclude-standard', '--', *files).decode().splitlines()):
        digest.update(name.encode())
        try:
            if os.path.getsize(name) < 1_000_000:
                digest.update(Path(name).read_bytes())
        except OSError:
            pass
    return digest.hexdigest()


def main():
    if not CHECKER.is_file():
        return 0
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        os.chdir(os.environ.get('CLAUDE_PROJECT_DIR') or HOOK_DIR.parents[1])
    except OSError:
        return 0

    edited = files_edited_after_plan(payload.get('transcript_path'))
    if not edited:
        return 0

    session = ''.join(c for c in str(payload.get('session_id') or 'default') if c.isalnum() or c in '-_')
    state_dir = Path(os.environ.get('TMPDIR') or tempfile.gettempdir()) / 'claude-conventions'
    state_dir.mkdir(parents=True, exist_ok=True)
    ok_file, blocked_file = state_dir / f'{session}.ok', state_dir / f'{session}.blocked'

    state = fingerprint(edited)
    if ok_file.exists() and ok_file.read_text() == state:
        return 0

    result = subprocess.run(
        [sys.executable, str(CHECKER), '--mode', 'full', '--flag-suppressions', '--files', *edited],
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        ok_file.write_text(state)
        blocked_file.unlink(missing_ok=True)
        return 0
    if result.returncode != 1:
        return 0  # checker error — don't block on tooling problems

    findings = result.stdout.strip()
    findings_hash = hashlib.sha256(findings.encode()).hexdigest()
    if payload.get('stop_hook_active') and blocked_file.exists() and blocked_file.read_text() == findings_hash:
        print(json.dumps({
            'systemMessage': 'Convention check still reports findings Claude did not resolve '
                             '(possible false positives) — please review:\n' + findings,
        }))
        return 0

    blocked_file.write_text(findings_hash)
    print('Convention check (.claude/rules) found issues in the files you edited for this plan:', file=sys.stderr)
    print(findings, file=sys.stderr)
    print('Project policy (CLAUDE.md, "Rule enforcement"): fix these now, without asking the user, then continue and mention the fixes in one line of your reply. Code the user pasted, copied, or got from a teammate is NOT a request for the pattern: fix it. Never add \'conventions: ignore\' yourself; keep a flagged pattern only if the user explicitly said to keep that exact pattern despite the rule.', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
