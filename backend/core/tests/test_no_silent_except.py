"""
Fail CI when production code swallows an exception silently (MED-401).

A handler is "silent" when it catches broadly (`except:`, `except Exception`,
`except BaseException`, or a tuple containing one of them) and its body is only
`pass` or `continue`.

What to do instead: log it with the exception attached (`logger.exception(...)`
or `exc_info=True`) and otherwise keep the current behaviour.
- Use `error` if the failure leaves something someone has to handle, such as an
  orphaned file or a row stuck in an intermediate status; otherwise `warning`.
- If the handler is cleanup that must not mask the original error or response
  (in a `finally`, or in an outer `except` after the original failure), add a
  one-line comment saying why it does not raise.
- Do not log tokens, request bodies, user text or `request.path` (some routes
  carry a share token); log object IDs and `request.resolver_match.route`.

The scan reads each file's syntax tree with `ast`, so comments, strings and
formatting cannot cause false matches.
"""
import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
SKIP_DIRS = {"tests", "test", "migrations", "node_modules", "venv", ".venv", "__pycache__", "media"}
BROAD = {"Exception", "BaseException"}


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


def test_no_silent_exception_handlers():
    found = [f"{path}:{line} in {scope}" for path, scope, line in _silent_handlers()]
    assert not found, (
        "Broad except with only pass/continue found. Log it with the exception attached "
        "(see the docstring of core/tests/test_no_silent_except.py):\n  " + "\n  ".join(found)
    )
