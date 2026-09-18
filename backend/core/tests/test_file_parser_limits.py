"""Hard-cap + structured-error behaviour of core.services.file_parser."""
import csv
import os
import tempfile

from django.test import SimpleTestCase, override_settings

from core.services.file_parser import FileParseError, parse_file_to_json


def _write_csv(rows, headers):
    handle, path = tempfile.mkstemp(suffix=".csv")
    os.close(handle)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)
    return path


class FileParserLimitsTests(SimpleTestCase):
    def _run(self, path, **kw):
        try:
            return parse_file_to_json(path, os.path.basename(path), **kw)
        finally:
            os.remove(path)

    @override_settings(SPREADSHEET_AI_MAX_ROWS=10)
    def test_row_cap(self):
        path = _write_csv([("a", i) for i in range(50)], ["x", "y"])
        parsed = self._run(path)
        self.assertEqual(len(parsed["sheets"][0]["rows"]), 10)
        self.assertTrue(parsed["limits_hit"]["rows"])

    @override_settings(SPREADSHEET_AI_MAX_COLS=3)
    def test_col_cap(self):
        headers = [f"c{i}" for i in range(10)]
        path = _write_csv([tuple(range(10))], headers)
        parsed = self._run(path)
        self.assertEqual(len(parsed["sheets"][0]["columns"]), 3)
        self.assertTrue(parsed["limits_hit"]["cols"])

    @override_settings(SPREADSHEET_AI_MAX_CELLS=5)
    def test_cell_budget_cap(self):
        path = _write_csv([("a", "b") for _ in range(50)], ["x", "y"])
        parsed = self._run(path)
        self.assertLessEqual(
            sum(len(r) for r in parsed["sheets"][0]["rows"]), 5 + 2
        )
        self.assertTrue(parsed["limits_hit"]["cells"])

    @override_settings(SPREADSHEET_AI_MAX_CELL_CHARS=4)
    def test_cell_char_clip(self):
        path = _write_csv([("abcdefghij", "ok")], ["x", "y"])
        parsed = self._run(path)
        self.assertEqual(parsed["sheets"][0]["rows"][0]["x"], "abcd")
        self.assertEqual(parsed["limits_hit"]["cell_chars_truncated"], 1)

    def test_unsupported_extension_raises(self):
        handle, path = tempfile.mkstemp(suffix=".pdf")
        os.close(handle)
        with self.assertRaises(FileParseError):
            self._run(path)

    def test_corrupt_xlsx_raises_not_empty(self):
        handle, path = tempfile.mkstemp(suffix=".xlsx")
        os.write(handle, b"not really a zip / xlsx")
        os.close(handle)
        with self.assertRaises(FileParseError):
            self._run(path)

    def test_missing_file_raises(self):
        with self.assertRaises(FileParseError):
            parse_file_to_json("/no/such/file.csv", "file.csv")

    def test_clean_file_no_limits_hit(self):
        path = _write_csv([("a", "1"), ("b", "2")], ["name", "n"])
        parsed = self._run(path)
        self.assertEqual(
            parsed["limits_hit"],
            {"rows": False, "cols": False, "cells": False, "cell_chars_truncated": 0},
        )
