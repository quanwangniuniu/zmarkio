"""
Generic file parser: CSV + Excel -> standardised JSON.

Output format:
  {"name": "filename",
   "sheets": [{"name": "Sheet1", "columns": [...], "rows": [{...}, ...]}],
   "limits_hit": {"rows": bool, "cols": bool, "cells": bool, "cell_chars_truncated": int}}

Hard caps (rows / columns / total cells / per-cell chars) are enforced while
streaming — the whole file is never materialised first. A file that cannot be
read raises ``FileParseError`` instead of silently yielding an empty sheet.
"""
import csv
import logging
import os
import zipfile

from django.conf import settings

logger = logging.getLogger(__name__)

# Historical default, kept for callers/tests that import it directly.
MAX_ROWS = 200

_DEFAULTS = {
    "rows": ("SPREADSHEET_AI_MAX_ROWS", 500),
    "cols": ("SPREADSHEET_AI_MAX_COLS", 50),
    "cells": ("SPREADSHEET_AI_MAX_CELLS", 20000),
    "cell_chars": ("SPREADSHEET_AI_MAX_CELL_CHARS", 2000),
}


class FileParseError(Exception):
    """An uploaded file could not be read (unsupported, corrupt, unreadable).

    Callers surface this to the user instead of silently importing an empty sheet.
    """


def _limit(value, key):
    if value is not None:
        return value
    name, default = _DEFAULTS[key]
    return getattr(settings, name, default)


def _try_number(value):
    """Convert a string to int/float if possible, else return the original string."""
    if value is None or value == '' or value == '-':
        return value
    if isinstance(value, (int, float)):
        return value
    try:
        cleaned = str(value).replace(',', '')
        if '.' in cleaned:
            return float(cleaned)
        return int(cleaned)
    except (ValueError, TypeError):
        return value


class _Caps:
    """Mutable running state for the hard caps during a parse."""

    def __init__(self, max_rows, max_cols, max_cells, max_cell_chars):
        self.max_rows = _limit(max_rows, "rows")
        self.max_cols = _limit(max_cols, "cols")
        self.max_cells = _limit(max_cells, "cells")
        self.max_cell_chars = _limit(max_cell_chars, "cell_chars")
        self.cells_used = 0
        self.hit = {"rows": False, "cols": False, "cells": False,
                    "cell_chars_truncated": 0}

    def clip_columns(self, columns):
        if len(columns) > self.max_cols:
            self.hit["cols"] = True
            return columns[: self.max_cols]
        return columns

    def clip_cell(self, value):
        if isinstance(value, str) and len(value) > self.max_cell_chars:
            self.hit["cell_chars_truncated"] += 1
            return value[: self.max_cell_chars]
        return value

    def cell_budget_left(self):
        return self.cells_used < self.max_cells


def _parse_csv(filepath, caps):
    rows = []
    with open(filepath, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        columns = caps.clip_columns(list(reader.fieldnames or []))
        for i, row in enumerate(reader):
            if i >= caps.max_rows:
                caps.hit["rows"] = True
                break
            if not caps.cell_budget_left():
                caps.hit["cells"] = True
                break
            parsed = {}
            for col in columns:
                raw = row.get(col, '')
                num = _try_number(raw)
                parsed[col] = caps.clip_cell(num if num is not None else raw)
                caps.cells_used += 1
            rows.append(parsed)
    return [{"name": "Sheet1", "columns": columns, "rows": rows}]


def _parse_excel(filepath, caps):
    try:
        import openpyxl
        from openpyxl.utils.exceptions import InvalidFileException
    except ImportError as exc:  # pragma: no cover - openpyxl is a hard dependency
        raise FileParseError("openpyxl is not installed") from exc

    sheets = []
    try:
        wb = openpyxl.load_workbook(filepath, data_only=True, read_only=True)
    except (InvalidFileException, zipfile.BadZipFile, OSError, KeyError, ValueError) as exc:
        raise FileParseError(f"could not open workbook: {exc}") from exc

    try:
        for ws in wb.worksheets:
            rows = []
            row_iter = ws.iter_rows(values_only=True)
            header = next(row_iter, None)
            if header is None:
                sheets.append({"name": ws.title, "columns": [], "rows": []})
                continue
            columns = caps.clip_columns(
                [str(c) if c is not None else f"col_{j}" for j, c in enumerate(header)]
            )
            for i, row_vals in enumerate(row_iter):
                if i >= caps.max_rows:
                    caps.hit["rows"] = True
                    break
                if not caps.cell_budget_left():
                    caps.hit["cells"] = True
                    break
                row_dict = {}
                for j, val in enumerate(row_vals):
                    if j >= len(columns):
                        break
                    row_dict[columns[j]] = caps.clip_cell(_try_number(val))
                    caps.cells_used += 1
                rows.append(row_dict)
            sheets.append({"name": ws.title, "columns": columns, "rows": rows})
    finally:
        wb.close()
    return sheets


def parse_file_to_json(
    file_path,
    filename=None,
    *,
    max_rows=None,
    max_cols=None,
    max_cells=None,
    max_cell_chars=None,
):
    """Parse a CSV or Excel file on disk into the standardised JSON structure.

    ``max_rows`` / ``max_cols`` / ``max_cells`` / ``max_cell_chars`` default to
    the ``SPREADSHEET_AI_MAX_*`` settings when ``None``. There is no unbounded
    mode — a truly large import is the deferred background job (see
    backend/agent/README.md).

    Raises:
        FileParseError: the file type is unsupported or the file cannot be read.
    """
    if filename is None:
        filename = os.path.basename(file_path)

    ext = os.path.splitext(filename)[1].lower()
    caps = _Caps(max_rows, max_cols, max_cells, max_cell_chars)

    try:
        if ext == '.csv':
            sheets = _parse_csv(file_path, caps)
        elif ext in ('.xlsx', '.xls'):
            sheets = _parse_excel(file_path, caps)
        else:
            raise FileParseError(f"unsupported file type: {ext or '(none)'}")
    except FileParseError:
        raise
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        raise FileParseError(str(exc)) from exc

    return {
        "name": filename,
        "sheets": sheets,
        "limits_hit": caps.hit,
    }
