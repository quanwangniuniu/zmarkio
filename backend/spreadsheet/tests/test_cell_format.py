from copy import copy
from types import SimpleNamespace

from django.test import TestCase
from openpyxl import Workbook
from openpyxl.styles import Font

from spreadsheet.cell_format import (
    FormatStrategy,
    font_name,
    number_pattern,
    percent_pattern,
)
from spreadsheet.xlsx_export import (
    XlsxPatchExportError,
    _excel_font,
    _excel_number_format,
    _excel_value,
    _patch_font,
    _patch_number_format,
)

def _fmt(**fields):
    base = {
        'bold': False,
        'italic': False,
        'strikethrough': False,
        'text_color': None,
        'font_family': None,
        'font_size': None,
    }
    base.update(fields)
    return SimpleNamespace(**base)


def _cell(font):
    worksheet = Workbook().active
    cell = worksheet['A1']
    cell.font = font
    return cell


class SharedHelperTests(TestCase):
    def test_font_name(self):
        self.assertEqual(font_name('Georgia, serif'), 'Georgia')
        self.assertEqual(font_name('  monospace  '), 'Consolas')
        self.assertIsNone(font_name('Comic Sans'))
        self.assertIsNone(font_name(None))

class ExcelRebuildFormatTests(TestCase):
    def test_all_default_format_gives_no_font(self):
        self.assertIsNone(_excel_font(_fmt()))

    def test_set_fields_are_written_and_unsupported_are_skipped(self):
        font = _excel_font(_fmt(
            bold=True,
            font_family='Comic Sans',
            font_size=0,
            text_color='#12',
            strikethrough=True,
        ))
        self.assertTrue(font.bold)
        self.assertTrue(font.strike)
        self.assertNotEqual(font.name, 'Comic Sans')
        self.assertIsNone(font.size)
        self.assertIsNone(font.color)

    def test_number_formats(self):
        self.assertIsNone(_excel_number_format(None))
        self.assertIsNone(_excel_number_format({'type': 'GENERAL'}))
        self.assertIsNone(_excel_number_format({'type': 'DATE'}))
        self.assertIsNone(_excel_number_format({'type': ['NUMBER']}))
        self.assertEqual(_excel_number_format({'type': 'NUMBER'}), '#,##0.00')
        self.assertEqual(
            _excel_number_format({'type': 'PERCENT', 'decimal_places': 0}), '0%'
        )
        self.assertEqual(
            _excel_number_format({'type': 'CURRENCY', 'decimal_places': 2}),
            '[$$-409]#,##0.00',
        )
        self.assertEqual(
            _excel_number_format({'type': 'CURRENCY', 'currency_code': 'XYZ', 'decimal_places': 0}),
            '#,##0',
        )

    def test_rebuild_with_only_unsupported_values_gives_no_font(self):
        self.assertIsNone(_excel_font(_fmt(font_family='Comic Sans', text_color='#12')))

    def test_excel_value_uses_formula_text_or_export_value(self):
        formula = SimpleNamespace(
            raw_input='  =A1*2 ', computed_type=None, computed_number=None,
            number_value=None, computed_string=None, string_value=None, boolean_value=None,
        )
        text = SimpleNamespace(
            raw_input='hello', computed_type=None, computed_number=None,
            number_value=None, computed_string=None, string_value='hello', boolean_value=None,
        )
        empty = SimpleNamespace(
            raw_input='', computed_type=None, computed_number=None,
            number_value=None, computed_string=None, string_value=None, boolean_value=None,
        )
        self.assertEqual(_excel_value(formula), '=A1*2')
        self.assertEqual(_excel_value(text), 'hello')
        self.assertIsNone(_excel_value(empty))
    
class ExcelPatchFormatTests(TestCase):
    def test_unknown_family_keeps_the_original_font(self):
        original = Font(name='Courier New', size=9)
        cell = _cell(original)
        _patch_font(cell, {'font_family': 'Comic Sans'})
        self.assertEqual(cell.font, original)

    def test_cleared_size_uses_the_openpyxl_default(self):
        cell = _cell(Font(name='Arial', size=20))
        _patch_font(cell, {'font_size': None})
        self.assertEqual(cell.font.size, 11)

    def test_invalid_text_color_raises(self):
        cell = _cell(Font(name='Arial'))
        before = copy(cell.font)
        with self.assertRaises(XlsxPatchExportError):
            _patch_font(cell, {'bold': True, 'text_color': '#12'})
        self.assertEqual(cell.font, before)

    def test_number_format_only_leaves_the_font_alone(self):
        original = Font(name='Courier New', italic=True)
        cell = _cell(original)
        _patch_font(cell, {'number_format': {'type': 'PERCENT'}})
        self.assertEqual(cell.font, original)

    def test_patch_number_format_falls_back_to_general(self):
        cell = _cell(Font())
        cell.number_format = '0.00%'
        _patch_number_format(cell, {'number_format': None})
        self.assertEqual(cell.number_format, 'General')

        cell.number_format = '0.00%'
        _patch_number_format(cell, {'number_format': {'type': 'DATE'}})
        self.assertEqual(cell.number_format, 'General')

        cell.number_format = '0.00%'
        _patch_number_format(cell, {'bold': True})
        self.assertEqual(cell.number_format, '0.00%')