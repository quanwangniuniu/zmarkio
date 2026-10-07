"""
Fail CI when production code swallows an exception silently (MED-401).

A handler is "silent" when it catches broadly (`except:`, `except Exception`,
`except BaseException`, or a tuple containing one of them) and its body is only
`pass` or `continue`.

What to do instead:
- If the exception is expected and the failure leaves nothing behind, catch only
  that type and say why in a short comment, for example `ObjectDoesNotExist` for
  a related object that may have been deleted.
- Otherwise log it with the exception attached (`logger.exception(...)` or
  `exc_info=True`): `error` if it leaves something someone has to fix, such as an
  orphaned file or a row stuck in an intermediate status; `warning` if the code
  falls back to a worse result.
- Do not log tokens, request bodies, user text or `request.path` (some routes
  carry a share token); log object IDs and `request.resolver_match.route`.

The scan reads each file's syntax tree with `ast`, so comments, strings and
formatting cannot cause false matches.
"""
import ast
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
SKIP_DIRS = {"tests", "test", "migrations", "node_modules", "venv", ".venv", "__pycache__", "media"}
BROAD = {"Exception", "BaseException"}

# Known silent handlers that need a behaviour change and are not fixed yet; each
# is a follow-up issue. Keyed by (file, enclosing function) with the number of
# handlers in it, so line changes do not break the list. Remove an entry when you
# fix it; the test fails if an entry no longer matches. Do not add new code here.
KNOWN_ISSUES = Counter({
    # Found by MED-401; fixing them changes behaviour, so they are follow-ups.
    ("authentication/views.py", "DeleteAccountView.delete"): 1,
    ("budget_approval/permissions.py", "BudgetRequestPermission.has_object_permission"): 1,
    ("budget_approval/permissions.py", "BudgetPoolPermission.has_object_permission"): 1,
    ("chat/services.py", "OnlineStatusService.set_offline"): 1,
    ("chat/tasks.py", "send_scheduled_message"): 1,
    ("core/middleware/tenant_schema.py", "TenantSchemaMiddleware._authenticate_jwt"): 1,
    ("core/views.py", "ProjectViewSet.perform_destroy"): 2,
    ("slack_integration/signals.py", "notify_on_project_creation"): 1,
    ("task/views.py", "_debug_log"): 1,
    # Added by MED-299 (#834), which merged after this test was written.
    ("access_control/middleware/authorization.py", "AuthorizationMiddleware.process_view"): 1,
    ("access_control/services.py", "invalidate_user_permission_cache"): 1,
    ("access_control/services.py", "_set_permission_cache"): 1,
})


def _is_broad(exc_type):
    if exc_type is None:
        return True
    if isinstance(exc_type, ast.Name):
        return exc_type.id in BROAD
    if isinstance(exc_type, ast.Attribute):
        return exc_type.attr in BROAD
    if isinstance(exc_type, ast.Tuple):
        return any(_is_broad(e) for e in exc_type.elts)
    return False


def _is_silent(body):
    # Only pass, continue, and docstrings or other bare constants.
    return all(
        isinstance(s, (ast.Pass, ast.Continue))
        or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
        for s in body
    )


class _Finder(ast.NodeVisitor):
    """Collect silent handlers with the qualified name of the function they are in."""

    def __init__(self):
        self.scope = []
        self.found = []

    def _visit_scope(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _visit_scope

    def visit_ExceptHandler(self, node):
        if _is_broad(node.type) and _is_silent(node.body):
            self.found.append((".".join(self.scope) or "<module>", node.lineno))
        self.generic_visit(node)


def _silent_handlers():
    found = []
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if SKIP_DIRS.intersection(rel.parts[:-1]) or rel.name.startswith("test") or rel.name == "conftest.py":
            continue
        finder = _Finder()
        finder.visit(ast.parse(path.read_text(encoding="utf-8")))
        found += [(rel.as_posix(), scope, line) for scope, line in finder.found]
    return found


def test_no_new_silent_exception_handlers():
    found = _silent_handlers()
    counts = Counter((path, scope) for path, scope, _ in found)

    new = [
        f"{path}:{line} in {scope}"
        for path, scope, line in found
        if counts[(path, scope)] > KNOWN_ISSUES[(path, scope)]
    ]
    assert not new, (
        "Broad except with only pass/continue found. Catch a narrower exception or log it "
        "(see the docstring of core/tests/test_no_silent_except.py):\n  " + "\n  ".join(new)
    )

    stale = [f"{path} in {scope}" for (path, scope), n in KNOWN_ISSUES.items() if counts[(path, scope)] < n]
    assert not stale, (
        "These known issues are fixed or moved; remove them from KNOWN_ISSUES:\n  " + "\n  ".join(stale)
    )
