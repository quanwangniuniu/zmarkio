import io
import tempfile
from contextlib import contextmanager
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, Side
from rest_framework.test import APIClient

from spreadsheet.import_service import build_import_baseline, diff_sheet
from spreadsheet.models import (
    Cell,
    CellValueType,
    SheetColumn,
    SheetImportSource,
    SheetRow,
    SpreadsheetCellFormat,
    SpreadsheetHighlight,
)
from spreadsheet.services import SheetService
from spreadsheet.tests.test_services import (
    create_test_organization,
    create_test_project,
    create_test_sheet,
    create_test_spreadsheet,
    create_test_user,
)
from spreadsheet.xlsx_export import (
    XlsxPatchExportError,
    build_patched_workbook,
    build_sheet_workbook,
)

def _workbook_bytes(build):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = 'Sheet1'
    build(workbook, worksheet)
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


@contextmanager
def _open_exported(content):
    workbook = load_workbook(io.BytesIO(content))
    try:
        yield workbook
    finally:
        workbook.close()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class TestXlsxPatchExport(TestCase):
    def setUp(self):
        self.user = create_test_user()
        self.organization = create_test_organization()
        self.project = create_test_project(self.organization, owner=self.user)
        self.spreadsheet = create_test_spreadsheet(self.project)
        self.sheet = create_test_sheet(self.spreadsheet)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = (
            f'/api/spreadsheet/spreadsheets/{self.spreadsheet.slug}/'
            f'sheets/{self.sheet.id}/export.xlsx'
        )

    def _cell(self, row_pos, col_pos, raw):
        row, _created = SheetRow.objects.get_or_create(sheet=self.sheet, position=row_pos)
        column, _created = SheetColumn.objects.get_or_create(
            sheet=self.sheet,
            position=col_pos,
            defaults={'name': chr(ord('A') + col_pos)},
        )
        return Cell.objects.create(
            sheet=self.sheet,
            row=row,
            column=column,
            raw_input=raw,
            string_value=raw,
            value_type=CellValueType.STRING,
        )

    def _save_excel_source(self, original_bytes, worksheet_name='Sheet1'):
        """Call only after the sheet matches the workbook and before any edit."""
        return SheetImportSource.objects.create(
            sheet=self.sheet,
            origin=SheetImportSource.Origin.EXCEL,
            worksheet_name=worksheet_name,
            original_filename='book.xlsx',
            baseline=build_import_baseline(self.sheet),
            file=ContentFile(original_bytes, name='book.xlsx'),
        )

    def _assert_rebuild(self):
        captured = {}
        real = build_sheet_workbook

        def wrapper(sheet):
            captured['content'] = real(sheet)
            return captured['content']

        with patch('spreadsheet.views.build_sheet_workbook', side_effect=wrapper):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, captured['content'])

    def test_no_source_uses_rebuild(self):
        self._cell(0, 0, 'plain')
        self._assert_rebuild()

    def test_google_source_uses_rebuild(self):
        self._cell(0, 0, 'plain')
        SheetImportSource.objects.create(
            sheet=self.sheet,
            origin=SheetImportSource.Origin.GOOGLE,
            worksheet_name='Budget',
            google_spreadsheet_id='abc',
            google_sheet_id=0,
        )
        self._assert_rebuild()

    def test_no_changes_return_original_bytes(self):
        self._cell(0, 0, 'same')
        original = _workbook_bytes(lambda workbook, worksheet: worksheet.__setitem__('A1', 'same'))
        self._save_excel_source(original)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, original)


    def test_bold_change_keeps_underline(self):
        self._cell(0, 0, 'styled')
        original = _workbook_bytes(
            lambda workbook, worksheet: (
                worksheet.__setitem__('A1', 'styled'),
                setattr(worksheet['A1'], 'font', Font(bold=False, underline='single')),
            )
        )
        self._save_excel_source(original)
        SpreadsheetCellFormat.objects.create(
            sheet=self.sheet, row_index=0, column_index=0, bold=True,
        )
        response = self.client.get(self.url)
        with _open_exported(response.content) as workbook:
            cell = workbook['Sheet1']['A1']
            self.assertTrue(cell.font.bold)
            self.assertEqual(cell.font.underline, 'single')

    def test_highlight_change_keeps_border_and_alignment(self):
        self._cell(0, 0, 'boxed')
        original = _workbook_bytes(
            lambda workbook, worksheet: (
                worksheet.__setitem__('A1', 'boxed'),
                setattr(worksheet['A1'], 'font', Font(underline='single')),
                setattr(worksheet['A1'], 'border', Border(left=Side(style='thin'))),
                setattr(worksheet['A1'], 'alignment', Alignment(horizontal='center')),
            )
        )
        self._save_excel_source(original)
        SpreadsheetHighlight.objects.create(
            spreadsheet=self.spreadsheet,
            sheet=self.sheet,
            scope='CELL',
            row_index=0,
            col_index=0,
            color='#BFDBFE',
        )
        response = self.client.get(self.url)
        with _open_exported(response.content) as workbook:
            cell = workbook['Sheet1']['A1']
            self.assertIn('BFDBFE', cell.fill.fgColor.rgb)
            self.assertEqual(cell.font.underline, 'single')
            self.assertEqual(cell.border.left.style, 'thin')
            self.assertEqual(cell.alignment.horizontal, 'center')

    def test_unchanged_custom_number_format_survives_bold(self):
        self._cell(0, 0, '10')
        original = _workbook_bytes(
            lambda workbook, worksheet: (
                worksheet.__setitem__('A1', 10),
                setattr(worksheet['A1'], 'number_format', '[$AUD]#,##0.00'),
            )
        )
        self._save_excel_source(original)
        SpreadsheetCellFormat.objects.create(
            sheet=self.sheet, row_index=0, column_index=0, bold=True,
        )
        response = self.client.get(self.url)
        with _open_exported(response.content) as workbook:
            cell = workbook['Sheet1']['A1']
            self.assertTrue(cell.font.bold)
            self.assertEqual(cell.number_format, '[$AUD]#,##0.00')

    def test_patch_failure_falls_back_to_rebuild(self):
        cell = self._cell(0, 0, 'old')
        original = _workbook_bytes(lambda workbook, worksheet: worksheet.__setitem__('A1', 'old'))
        self._save_excel_source(original)
        cell.raw_input = 'new'
        cell.string_value = 'new'
        cell.save(update_fields=['raw_input', 'string_value'])
        with patch(
            'spreadsheet.views.build_patched_workbook',
            side_effect=XlsxPatchExportError('patch failed'),
        ):
            self._assert_rebuild()

    def test_source_read_failure_falls_back_to_rebuild(self):
        self._cell(0, 0, 'same')
        original = _workbook_bytes(lambda workbook, worksheet: worksheet.__setitem__('A1', 'same'))
        self._save_excel_source(original)
        with patch(
            'django.db.models.fields.files.FieldFile.open',
            side_effect=OSError('unreadable'),
        ):
            self._assert_rebuild()

    def test_column_insert_since_import_rebuilds(self):
        self._cell(0, 0, 'left')
        self._cell(0, 1, 'right')
        original = _workbook_bytes(
            lambda workbook, worksheet: (
                worksheet.__setitem__('A1', 'left'),
                worksheet.__setitem__('B1', 'right'),
            )
        )
        self._save_excel_source(original)
        SheetService.insert_columns(self.sheet, position=1, count=1)
        with patch('spreadsheet.views.build_patched_workbook') as patcher:
            self._assert_rebuild()
        patcher.assert_not_called()

    def test_row_delete_since_import_rebuilds(self):
        self._cell(0, 0, 'drop')
        self._cell(1, 0, 'keep')
        original = _workbook_bytes(
            lambda workbook, worksheet: (
                worksheet.__setitem__('A1', 'drop'),
                worksheet.__setitem__('A2', 'keep'),
            )
        )
        self._save_excel_source(original)
        SheetService.delete_rows(self.sheet, position=0, count=1, created_by=self.user)
        with patch('spreadsheet.views.build_patched_workbook') as patcher:
            self._assert_rebuild()
        patcher.assert_not_called()

    def test_undone_column_insert_returns_original_bytes(self):
        from spreadsheet.models import SheetStructureOperation

        self._cell(0, 0, 'left')
        self._cell(0, 1, 'right')
        original = _workbook_bytes(
            lambda workbook, worksheet: (
                worksheet.__setitem__('A1', 'left'),
                worksheet.__setitem__('B1', 'right'),
                setattr(worksheet['B1'], 'font', Font(underline='single')),
            )
        )
        self._save_excel_source(original)
        inserted = SheetService.insert_columns(
            self.sheet, position=1, count=1, created_by=self.user,
        )
        operation = SheetStructureOperation.objects.get(id=inserted['operation_id'])
        SheetService.revert_structure_operation(self.sheet, operation)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, original)

    def _save_google_source(self):
        return SheetImportSource.objects.create(
            sheet=self.sheet,
            origin=SheetImportSource.Origin.GOOGLE,
            worksheet_name='Budget',
            google_spreadsheet_id='google-book',
            google_sheet_id=0,
            baseline=build_import_baseline(self.sheet),
        )

    def _connect_google(self):
        from google_docs_integration.models import GoogleDocsConnection

        connection = GoogleDocsConnection(user=self.user, is_active=True)
        connection.set_access_token('token-abc')
        connection.save()
        return connection

    def test_google_download_without_edits_returns_the_exported_workbook(self):
        self._cell(0, 0, 'same')
        self._save_google_source()
        self._connect_google()
        original = _workbook_bytes(
            lambda workbook, worksheet: (
                worksheet.__setitem__('A1', 'same'),
                setattr(worksheet['A1'], 'font', Font(underline='single')),
                workbook.create_sheet('Other'),
            )
        )
        with patch(
            'spreadsheet.views.export_google_spreadsheet_xlsx',
            return_value=(original, 'Sheet1'),
        ) as fetch:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, original)
        fetch.assert_called_once()

    def test_google_download_patches_a_value_and_keeps_underline(self):
        changed = self._cell(0, 0, 'old')
        self._save_google_source()
        self._connect_google()
        original = _workbook_bytes(
            lambda workbook, worksheet: (
                worksheet.__setitem__('A1', 'old'),
                setattr(worksheet['A1'], 'font', Font(underline='single')),
            )
        )
        changed.raw_input = 'new'
        changed.string_value = 'new'
        changed.save(update_fields=['raw_input', 'string_value'])
        with patch(
            'spreadsheet.views.export_google_spreadsheet_xlsx',
            return_value=(original, 'Sheet1'),
        ):
            response = self.client.get(self.url)
        with _open_exported(response.content) as workbook:
            self.assertEqual(workbook['Sheet1']['A1'].value, 'new')
            self.assertEqual(workbook['Sheet1']['A1'].font.underline, 'single')

    def test_google_highlight_keeps_border_and_alignment(self):
        self._cell(0, 0, 'boxed')
        self._save_google_source()
        self._connect_google()
        original = _workbook_bytes(
            lambda workbook, worksheet: (
                worksheet.__setitem__('A1', 'boxed'),
                setattr(worksheet['A1'], 'border', Border(left=Side(style='thin'))),
                setattr(worksheet['A1'], 'alignment', Alignment(horizontal='center')),
            )
        )
        SpreadsheetHighlight.objects.create(
            spreadsheet=self.spreadsheet,
            sheet=self.sheet,
            scope='CELL',
            row_index=0,
            col_index=0,
            color='#BFDBFE',
        )
        with patch(
            'spreadsheet.views.export_google_spreadsheet_xlsx',
            return_value=(original, 'Sheet1'),
        ):
            response = self.client.get(self.url)
        with _open_exported(response.content) as workbook:
            cell = workbook['Sheet1']['A1']
            self.assertIn('BFDBFE', cell.fill.fgColor.rgb)
            self.assertEqual(cell.border.left.style, 'thin')
            self.assertEqual(cell.alignment.horizontal, 'center')

    def test_google_column_insert_since_import_rebuilds(self):
        self._cell(0, 0, 'left')
        self._cell(0, 1, 'right')
        self._save_google_source()
        self._connect_google()
        SheetService.insert_columns(self.sheet, position=1, count=1, created_by=self.user)
        with patch('spreadsheet.views.export_google_spreadsheet_xlsx') as fetch:
            self._assert_rebuild()
        fetch.assert_not_called()

    def test_google_undo_returns_the_exported_workbook(self):
        from spreadsheet.models import SheetStructureOperation

        self._cell(0, 0, 'top')
        self._cell(1, 0, 'bottom')
        self._save_google_source()
        self._connect_google()
        original = _workbook_bytes(
            lambda workbook, worksheet: (
                worksheet.__setitem__('A1', 'top'),
                worksheet.__setitem__('A2', 'bottom'),
            )
        )
        deleted = SheetService.delete_rows(self.sheet, position=1, count=1, created_by=self.user)
        operation = SheetStructureOperation.objects.get(id=deleted['operation_id'])
        SheetService.revert_structure_operation(self.sheet, operation)
        with patch(
            'spreadsheet.views.export_google_spreadsheet_xlsx',
            return_value=(original, 'Sheet1'),
        ):
            response = self.client.get(self.url)
        self.assertEqual(response.content, original)

    def test_missing_google_worksheet_rebuilds(self):
        self._cell(0, 0, 'plain')
        self._save_google_source()
        self._connect_google()
        with patch(
            'spreadsheet.views.export_google_spreadsheet_xlsx',
            side_effect=ValueError('google worksheet was not found'),
        ):
            self._assert_rebuild()

    def test_workbook_bytes_use_the_live_worksheet_name(self):
        changed = self._cell(0, 0, 'old')
        source = self._save_google_source()
        original = _workbook_bytes(lambda workbook, worksheet: worksheet.__setitem__('A1', 'old'))
        changed.raw_input = 'new'
        changed.string_value = 'new'
        changed.save(update_fields=['raw_input', 'string_value'])
        content = build_patched_workbook(
            source,
            diff_sheet(source),
            workbook_bytes=original,
            worksheet_name='Sheet1',
        )
        with _open_exported(content) as workbook:
            self.assertEqual(workbook['Sheet1']['A1'].value, 'new')
        with self.assertRaises(XlsxPatchExportError):
            build_patched_workbook(
                source,
                diff_sheet(source),
                workbook_bytes=original,
                worksheet_name='Missing',
            )