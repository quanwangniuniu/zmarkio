#!/usr/bin/env python3
"""Diff-aware checker for the conventions in .claude/rules/*.md.

One rule set, two callers:
  - .claude/hooks/check_edit.py     PostToolUse hook, `--mode fast --file <path>` after every edit
  - .claude/hooks/check_on_stop.py  Stop hook, `--mode full --files <paths Claude edited>` once an
                                    approved plan is implemented
Not wired into CI. `--base <sha>` checks a whole branch by hand; `--format github` / `--advisory`
are there for a future CI job.

Only *added* lines (and classes/functions/handlers whose first line was added) are checked,
because most rules say "never newly introduce" — legacy code stays quiet until it is touched.
Stdlib only, no install step. Suppress a single line with a trailing `conventions: ignore`
comment. With --flag-suppressions (used by the Claude hooks) every newly added suppression
is itself reported as `suppression-added`, so Claude can't silently opt out of a rule and
reviewers see each one.

Exit codes: 0 clean, 1 findings (0 with --advisory), 2 usage / git error.

To add a rule: append a LineRule to LINE_RULES (regex, fast + full) or a check to
_check_python_ast / _check_repo (full only), and cite the rule file.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SUPPRESS_MARKER = 'conventions: ignore'


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule_id: str
    message: str
    rule_file: str

    def as_text(self) -> str:
        return f'{self.path}:{self.line} [{self.rule_id}] {self.message} (see {self.rule_file})'

    def as_github(self) -> str:
        msg = f'{self.message} (see {self.rule_file})'.replace('%', '%25').replace('\n', '%0A')
        return f'::warning file={self.path},line={self.line},title={self.rule_id}::{msg}'


# --- file scopes -----------------------------------------------------------------------------

def _is_backend_py(path: str) -> bool:
    return path.startswith('backend/') and path.endswith('.py')


def _is_frontend_ts(path: str) -> bool:
    return path.startswith('frontend/src/') and path.endswith(('.ts', '.tsx'))


def _is_backend_test_or_script(path: str) -> bool:
    name = path.rsplit('/', 1)[-1]
    return (
        '/tests/' in path
        or name.startswith('test_')
        or name == 'tests.py'
        or name == 'conftest.py'
        or '/management/commands/' in path
        or '/migrations/' in path
    )


def _is_frontend_test(path: str) -> bool:
    name = path.rsplit('/', 1)[-1]
    return '/__tests__/' in path or '.test.' in name or '.spec.' in name


def _in_scope(path: str) -> bool:
    return _is_backend_py(path) or _is_frontend_ts(path)


# --- line rules (fast + full) ----------------------------------------------------------------

@dataclass(frozen=True)
class LineRule:
    rule_id: str
    pattern: re.Pattern
    applies: Callable[[str], bool]
    message: str
    rule_file: str


LINE_RULES = [
    LineRule(
        'serializer-all-fields',
        re.compile(r'''\bfields\s*=\s*['"]__all__['"]'''),
        _is_backend_py,
        "Serializer uses fields = '__all__' — list the fields explicitly.",
        '.claude/rules/models-conventions.md',
    ),
    LineRule(
        'no-print',
        re.compile(r'^\s*print\('),
        lambda p: _is_backend_py(p) and not _is_backend_test_or_script(p),
        'print() in backend code — use logger = logging.getLogger(__name__).',
        '.claude/rules/migrations-gotchas.md',
    ),
    LineRule(
        'no-header-rbac',
        re.compile(r'''x-user-role|x-team-id|HTTP_X_USER_ROLE|HTTP_X_TEAM_ID|\bhas_rbac_permission\(''', re.I),
        lambda p: _is_backend_py(p) and not _is_backend_test_or_script(p),
        'Header-driven RBAC is legacy — use permission_classes = [IsAuthenticated, <AppPermission>].',
        '.claude/rules/permissions-security.md',
    ),
    LineRule(
        'no-app-task',
        re.compile(r'^\s*@app\.task\b'),
        _is_backend_py,
        'Define Celery tasks with @shared_task, never @app.task.',
        '.claude/rules/celery-async.md',
    ),
    LineRule(
        'no-raw-search-path',
        re.compile(r'SET\s+search_path', re.I),
        lambda p: _is_backend_py(p) and not p.startswith('backend/core/') and '/middleware/' not in p
        and not _is_backend_test_or_script(p),
        'Raw SET search_path in app code — use tenant_schema_context.',
        '.claude/rules/multi-tenancy.md',
    ),
    LineRule(
        'no-direct-axios',
        re.compile(r'''^\s*import\s+axios\b|\baxios\.create\(|require\(\s*['"]axios['"]\s*\)'''),
        lambda p: _is_frontend_ts(p) and not p.startswith('frontend/src/lib/') and not _is_frontend_test(p),
        "Direct axios use outside src/lib — import api from '@/lib/api' via a <domain>Api.ts module.",
        '.claude/rules/frontend-api-conventions.md',
    ),
    LineRule(
        'no-hardcoded-hex',
        re.compile(r'\b(?:bg|text|from|via|to|border|ring|fill|stroke)-\[#[0-9a-fA-F]{3,8}\]'),
        _is_frontend_ts,
        'Hardcoded hex in a Tailwind class — use a theme token.',
        '.claude/rules/frontend-components.md',
    ),
    LineRule(
        'no-deep-relative-import',
        re.compile(r'''(?:from\s+|import\s*\(\s*|require\(\s*)['"](?:\.\./){3,}'''),
        lambda p: _is_frontend_ts(p) and not _is_frontend_test(p),
        "Deep relative import — use the '@/…' alias.",
        '.claude/rules/code-style.md',
    ),
]


def _check_lines(path: str, lines: list[str], added: set[int], flag_suppressions: bool) -> list[Finding]:
    rules = [r for r in LINE_RULES if r.applies(path)]
    findings = []
    for lineno in sorted(added):
        if lineno < 1 or lineno > len(lines):
            continue
        text = lines[lineno - 1]
        if SUPPRESS_MARKER in text:
            if flag_suppressions:
                findings.append(Finding(
                    path, lineno, 'suppression-added',
                    "New 'conventions: ignore' — remove it and fix the code, unless the user "
                    'explicitly told you to keep this exact pattern.',
                    'CLAUDE.md',
                ))
            continue
        for rule in rules:
            if rule.pattern.search(text):
                findings.append(Finding(path, lineno, rule.rule_id, rule.message, rule.rule_file))
    return findings


# --- python AST rules (full only) ------------------------------------------------------------

DRF_VIEW_BASES = {
    'APIView', 'GenericAPIView', 'ViewSet', 'GenericViewSet', 'ModelViewSet', 'ReadOnlyModelViewSet',
    'CreateAPIView', 'ListAPIView', 'RetrieveAPIView', 'DestroyAPIView', 'UpdateAPIView',
    'ListCreateAPIView', 'RetrieveUpdateAPIView', 'RetrieveDestroyAPIView',
    'RetrieveUpdateDestroyAPIView',
}
DRF_MODULES = {'viewsets', 'generics', 'views', 'mixins'}
DRF_HTTP_IMPORTS = {
    'rest_framework.response': None,     # any name
    'rest_framework.status': None,
    'rest_framework.exceptions': {'ValidationError'},
    'rest_framework': {'status', 'response'},
}


def _base_name(node: ast.expr) -> tuple[str, str | None]:
    """Return (name, module) for `Foo` -> ('Foo', None), `viewsets.Foo` -> ('Foo', 'viewsets')."""
    if isinstance(node, ast.Name):
        return node.id, None
    if isinstance(node, ast.Attribute):
        module = node.value.id if isinstance(node.value, ast.Name) else None
        return node.attr, module
    return '', None


def _sets_permissions(cls: ast.ClassDef) -> bool:
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == 'permission_classes' for t in stmt.targets
        ):
            return True
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) \
                and stmt.target.id == 'permission_classes':
            return True
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == 'get_permissions':
            return True
    return False


def _is_drf_view_class(cls: ast.ClassDef, local_with_perms: set[str]) -> bool:
    """True when the class directly subclasses a DRF view and no other base could supply perms."""
    has_drf = False
    for base in cls.bases:
        name, module = _base_name(base)
        if name in DRF_VIEW_BASES and (module is None or module in DRF_MODULES):
            has_drf = True
        elif module in DRF_MODULES:
            continue  # e.g. mixins.ListModelMixin
        elif name.endswith('Mixin') and name not in local_with_perms:
            continue  # mixins don't grant permissions (unless defined here with permission_classes)
        else:
            return False  # custom base class — it may set permission_classes; stay quiet
    return has_drf


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        node = node.func
    return _base_name(node)[0]


def _is_500(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant) and node.value == 500:
        return True
    return isinstance(node, ast.Attribute) and node.attr.startswith('HTTP_500')


def _returns_str_e_500(handler: ast.ExceptHandler) -> ast.Return | None:
    for node in ast.walk(handler):
        if not (isinstance(node, ast.Return) and isinstance(node.value, ast.Call)):
            continue
        call = node.value
        if _base_name(call.func)[0] not in {'Response', 'JsonResponse'}:
            continue
        status_500 = any(kw.arg == 'status' and _is_500(kw.value) for kw in call.keywords) \
            or any(_is_500(arg) for arg in call.args[1:])
        uses_str_e = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'str'
            and n.args and isinstance(n.args[0], ast.Name) and n.args[0].id == handler.name
            for n in ast.walk(call)
        )
        if status_500 and uses_str_e:
            return node
    return None


def _check_python_ast(path: str, source: str, added: set[int]) -> list[Finding]:
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return [Finding(path, exc.lineno or 1, 'python-syntax', f'SyntaxError: {exc.msg}', 'backend')]

    findings = []
    is_service = path.endswith('/services.py') or '/services/' in path
    local_with_perms = {
        n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and _sets_permissions(n)
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.lineno in added:
            if _is_drf_view_class(node, local_with_perms) and not _sets_permissions(node):
                findings.append(Finding(
                    path, node.lineno, 'view-permission-classes',
                    f'{node.name} does not set permission_classes (DRF default is AllowAny).',
                    '.claude/rules/permissions-security.md',
                ))

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
            names = [_decorator_name(d) for d in node.decorator_list]
            first_line = node.decorator_list[0].lineno
            if 'api_view' in names and 'permission_classes' not in names and \
                    (first_line in added or node.lineno in added):
                findings.append(Finding(
                    path, node.lineno, 'view-permission-classes',
                    f'@api_view function {node.name} has no @permission_classes (DRF default is AllowAny).',
                    '.claude/rules/permissions-security.md',
                ))

        elif isinstance(node, ast.ExceptHandler) and node.lineno in added and node.name:
            exc_name = _base_name(node.type)[0] if node.type is not None else ''
            ret = _returns_str_e_500(node) if exc_name in {'Exception', 'BaseException'} else None
            if ret is not None:
                findings.append(Finding(
                    path, ret.lineno, 'except-str-e-500',
                    'except Exception returning a 500 with str(e) leaks internals — let it raise or return a generic error.',
                    '.claude/rules/permissions-security.md',
                ))

        elif is_service and isinstance(node, ast.ImportFrom) and node.lineno in added:
            allowed = DRF_HTTP_IMPORTS.get(node.module or '', False)
            if allowed is False:
                continue
            bad = [a.name for a in node.names if allowed is None or a.name in allowed]
            if bad:
                findings.append(Finding(
                    path, node.lineno, 'service-imports-drf',
                    f'Service module imports DRF HTTP objects ({", ".join(bad)}) — raise domain errors, let the view map them.',
                    '.claude/rules/architecture.md',
                ))
    return findings


# --- git plumbing ----------------------------------------------------------------------------

def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ['git', *args], cwd=root, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


HUNK_RE = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@')


def _parse_added_lines(diff: str) -> dict[str, set[int]]:
    added: dict[str, set[int]] = {}
    current = None
    for line in diff.splitlines():
        if line.startswith('+++ '):
            target = line[4:]
            current = target[2:] if target.startswith('b/') else None
            if current is not None:
                added.setdefault(current, set())
        elif line.startswith('@@') and current is not None:
            match = HUNK_RE.match(line)
            if match:
                start, count = int(match.group(1)), int(match.group(2) or 1)
                added[current].update(range(start, start + count))
    return added


def _diff_range(base: str | None) -> list[str]:
    return [f'{base}...HEAD'] if base else ['HEAD']


def _collect_changes(root: Path, base: str | None, only: list[str] | None) -> dict[str, set[int] | None]:
    """Map changed in-scope path -> added line numbers (None = whole file is new)."""
    pathspec = ['--', *only] if only else []
    diff = _git(root, 'diff', '-U0', '--no-color', '--no-ext-diff', '--no-renames',
                *_diff_range(base), *pathspec)
    changes: dict[str, set[int] | None] = dict(_parse_added_lines(diff))
    if base is None:
        untracked = _git(root, 'ls-files', '--others', '--exclude-standard', *pathspec)
        for path in untracked.splitlines():
            changes[path] = None
    return {p: lines for p, lines in changes.items() if _in_scope(p)}


def _check_repo(root: Path, base: str | None, only: list[str] | None) -> list[Finding]:
    """Repo-level checks. compose-edited only applies to the working tree (i.e. Claude's edits) —
    humans legitimately change compose files in reviewed PRs — and, with `only`, to those paths.
    migration-deleted stays repo-wide: a migration can be removed with `rm`, which no edit list sees."""
    findings = []
    status = _git(root, 'diff', '--name-status', '-M', *_diff_range(base))
    for row in status.splitlines():
        parts = row.split('\t')
        code, old = parts[0], parts[1] if len(parts) > 1 else ''
        new = parts[2] if len(parts) > 2 else old
        if code[:1] in {'D', 'R'} and re.match(r'backend/.+/migrations/[^/]+\.py$', old):
            findings.append(Finding(
                old, 1, 'migration-deleted',
                'Existing migration deleted or renamed — CI migration_guard will block this.',
                '.claude/rules/migrations-gotchas.md',
            ))
        for path in {old, new} if base is None else ():
            if only is not None and path not in only:
                continue
            if re.match(r'(?:.*/)?docker-compose[^/]*\.ya?ml$', path):
                findings.append(Finding(
                    path, 1, 'compose-edited',
                    'docker-compose*.yml changed — tooling must not edit compose files.',
                    'CLAUDE.md',
                ))
    return findings


# --- entry point -----------------------------------------------------------------------------

def _repo_relative(root: Path, files: list[str]) -> list[str]:
    """Resolve paths (absolute or cwd-relative) to repo-relative POSIX paths; drop outside ones."""
    resolved = []
    for file in files:
        file_path = Path(file)
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path
        try:
            resolved.append(file_path.resolve().relative_to(root.resolve()).as_posix())
        except ValueError:
            continue  # file outside the repo
    return resolved


def run(
    root: Path, mode: str, base: str | None, files: list[str] | None, flag_suppressions: bool = False,
) -> list[Finding]:
    """Check the diff. `files` limits the check to those paths; None means every changed file."""
    only = None
    if files is not None:
        only = _repo_relative(root, files)
        if not only:
            return []

    findings: list[Finding] = []
    scoped = [p for p in only if _in_scope(p)] if only is not None else None
    changes = _collect_changes(root, base, scoped) if scoped != [] else {}
    for path, added in sorted(changes.items()):
        full_path = root / path
        if not full_path.is_file():
            continue
        try:
            source = full_path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        lines = source.splitlines()
        added_lines = set(range(1, len(lines) + 1)) if added is None else added
        if not added_lines:
            continue
        findings.extend(_check_lines(path, lines, added_lines, flag_suppressions))
        if mode == 'full' and path.endswith('.py'):
            ast_findings = _check_python_ast(path, source, added_lines)
            findings.extend(
                f for f in ast_findings
                if SUPPRESS_MARKER not in (lines[f.line - 1] if 0 < f.line <= len(lines) else '')
            )

    if mode == 'full':
        findings.extend(_check_repo(root, base, only))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('--mode', choices=['fast', 'full'], default='full')
    parser.add_argument('--file', help='fast mode: the single file to check')
    parser.add_argument('--files', nargs='*', help='full mode: only check these paths')
    parser.add_argument('--base', help='compare <base>...HEAD instead of the working tree vs HEAD')
    parser.add_argument('--format', choices=['text', 'github'], default='text')
    parser.add_argument('--advisory', action='store_true', help='always exit 0')
    parser.add_argument('--flag-suppressions', action='store_true',
                        help="report newly added 'conventions: ignore' comments")
    args = parser.parse_args(argv)

    if args.mode == 'fast' and not args.file:
        parser.error('--mode fast requires --file')
    files = [args.file] if args.mode == 'fast' else args.files

    try:
        root = Path(_git(Path.cwd(), 'rev-parse', '--show-toplevel').strip())
        findings = run(root, args.mode, args.base, files, args.flag_suppressions)
    except RuntimeError as exc:
        print(f'check_conventions: {exc}', file=sys.stderr)
        return 2

    for finding in findings:
        print(finding.as_github() if args.format == 'github' else finding.as_text())
    if findings and args.format == 'text':
        print(f'{len(findings)} convention finding(s).')
    if args.advisory:
        return 0
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
