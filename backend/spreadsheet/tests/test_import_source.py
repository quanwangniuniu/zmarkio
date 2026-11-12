import io
import re
import tempfile
import zipfile
from decimal import Decimal
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import TestCase, override_settings
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.styles.colors import Color
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from spreadsheet.import_service import apply_excel_import_styles

from spreadsheet.models import (
    Cell,
    SheetColumn,
    SheetImportSource,
    SheetRow,
    SpreadsheetCellFormat,
    SpreadsheetHighlight,
)
from spreadsheet.tests.test_views import (
    create_test_organization,
    create_test_project,
    create_test_sheet,
    create_test_spreadsheet,
    create_test_user,
)

def _xlsx(build=None):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = 'Sheet1'
    if build is not None:
        build(worksheet)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _with_normal_size(content, size):
    """Rewrite the first <sz val="11"/> in styles.xml, as the browser test does."""
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as source, zipfile.ZipFile(output, 'w') as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == 'xl/styles.xml':
                data = re.sub(rb'<sz val="11(?:\.0)?"/>', f'<sz val="{size}"/>'.encode(), data, count=1)
            target.writestr(item, data)
    return output.getvalue()



@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class SheetImportSourceBaselineTests(TestCase):
    def setUp(self):
        self.owner = create_test_user('owner', 'owner@example.com')
        self.organization = create_test_organization()
        self.project = create_test_project(self.organization, owner=self.owner)
        self.spreadsheet = create_test_spreadsheet(self.project)
        self.sheet = create_test_sheet(self.spreadsheet)
        self.rows = [
            SheetRow.objects.create(sheet=self.sheet, position=i) for i in range(2)
        ]
        self.cols = [
            SheetColumn.objects.create(sheet=self.sheet, position=i, name=name)
            for i, name in enumerate(('A', 'B'))
        ]
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.url = (
            f'/api/spreadsheet/spreadsheets/{self.spreadsheet.slug}/'
            f'sheets/{self.sheet.id}/import-source/'
        )

    def _post_excel(self, content=None):
        uploaded = SimpleUploadedFile(
            'book.xlsx',
            _xlsx() if content is None else content,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        return self.client.post(
            self.url,
            {'origin': 'EXCEL', 'worksheet_name': 'Sheet1', 'file': uploaded},
            format='multipart',
        )

    def _post_google(self):
        return self.client.post(
            self.url,
            {
                'origin': 'GOOGLE',
                'worksheet_name': 'Budget',
                'google_spreadsheet_id': 'abc',
                'google_sheet_id': 0,
                'source_url': 'https://docs.google.com/spreadsheets/d/abc',
            },
            format='json',
        )

    def _finalize_import(self):
        return self.client.post(
            f'/api/spreadsheet/spreadsheets/{self.spreadsheet.slug}/'
            f'sheets/{self.sheet.id}/cells/import-finalize/',
            {},
            format='json',
        )

    
    def test_excel_post_stores_raw_input_not_computed(self):
        Cell.objects.create(
            sheet=self.sheet,
            row=self.rows[0],
            column=self.cols[0],
            raw_input='100',
            computed_number=Decimal('999'),
        )
        Cell.objects.create(
            sheet=self.sheet,
            row=self.rows[0],
            column=self.cols[1],
            raw_input='=A1*2',
            computed_number=Decimal('200'),
        )
        response = self._post_excel()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        baseline = SheetImportSource.objects.get(sheet=self.sheet).baseline
        self.assertEqual(baseline['version'], 1)
        self.assertEqual(baseline['row_ids'], [self.rows[0].id, self.rows[1].id])
        self.assertEqual(baseline['column_ids'], [self.cols[0].id, self.cols[1].id])
        self.assertEqual(baseline['cells']['0,0'], {'raw_input': '100'})
        self.assertEqual(baseline['cells']['0,1'], {'raw_input': '=A1*2'})
        self.assertNotIn('computed_number', str(baseline))

    def test_empty_raw_input_is_omitted(self):
        Cell.objects.create(
            sheet=self.sheet, row=self.rows[1], column=self.cols[1], raw_input=''
        )
        self._post_excel()
        baseline = SheetImportSource.objects.get(sheet=self.sheet).baseline
        self.assertNotIn('1,1', baseline['cells'])

    def test_formats_and_highlights_are_stored(self):
        SpreadsheetCellFormat.objects.create(
            sheet=self.sheet,
            row_index=0,
            column_index=0,
            bold=False,
            font_size=12,
            text_color='#112233',
            number_format={'type': 'CURRENCY', 'currency_code': 'USD', 'decimal_places': 2},
        )
        SpreadsheetHighlight.objects.create(
            spreadsheet=self.spreadsheet,
            sheet=self.sheet,
            scope='CELL',
            row_index=1,
            col_index=0,
            color='#F4B183',
        )
        SpreadsheetHighlight.objects.create(
            spreadsheet=self.spreadsheet,
            sheet=self.sheet,
            scope='ROW',
            row_index=0,
            col_index=0,
            color='#ABCDEF',
        )
        self._post_excel()
        baseline = SheetImportSource.objects.get(sheet=self.sheet).baseline
        self.assertEqual(baseline['formats']['0,0']['font_size'], 12)
        self.assertEqual(baseline['formats']['0,0']['bold'], False)
        self.assertEqual(baseline['highlights']['cells']['1,0'], '#F4B183')
        self.assertEqual(baseline['highlights']['rows']['0'], '#ABCDEF')

    def test_google_post_stores_baseline(self):
        Cell.objects.create(
            sheet=self.sheet, row=self.rows[0], column=self.cols[0], raw_input='250'
        )
        response = self._post_google()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        source = SheetImportSource.objects.get(sheet=self.sheet)
        self.assertEqual(source.origin, 'GOOGLE')
        self.assertEqual(source.baseline['cells']['0,0'], {'raw_input': '250'})

    def test_reimport_replaces_baseline(self):
        Cell.objects.create(
            sheet=self.sheet, row=self.rows[0], column=self.cols[0], raw_input='100'
        )
        self._post_excel()
        cell = Cell.objects.get(sheet=self.sheet, row=self.rows[0], column=self.cols[0])
        cell.raw_input = '250'
        cell.save(update_fields=['raw_input'])
        self._post_google()
        source = SheetImportSource.objects.get(sheet=self.sheet)
        self.assertEqual(SheetImportSource.objects.filter(sheet=self.sheet).count(), 1)
        self.assertEqual(source.origin, 'GOOGLE')
        self.assertEqual(source.baseline['cells']['0,0'], {'raw_input': '250'})

    def test_reimport_deletes_previous_file_only_after_commit(self):
        self._post_excel()
        source = SheetImportSource.objects.get(sheet=self.sheet)
        old_name = source.file.name
        storage = source.file.storage

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            response = self._post_excel()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(storage.exists(old_name))

        for callback in callbacks:
            callback()
        source.refresh_from_db()
        self.assertFalse(storage.exists(old_name))
        self.assertNotEqual(source.file.name, old_name)
        self.assertTrue(storage.exists(source.file.name))

    def test_failed_reimport_keeps_previous_file_and_reference(self):
        self._post_excel()
        source = SheetImportSource.objects.get(sheet=self.sheet)
        old_name = source.file.name

        with mock.patch.object(
            SheetImportSource.objects,
            'update_or_create',
            side_effect=DatabaseError('boom'),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                with self.assertRaises(DatabaseError):
                    self._post_excel()

        source.refresh_from_db()
        self.assertEqual(source.file.name, old_name)
        self.assertTrue(source.file.storage.exists(old_name))

    def test_csv_replacement_retires_previous_excel_source(self):
        self._post_excel()
        response = self._finalize_import()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            SheetImportSource.objects.filter(sheet=self.sheet, is_deleted=False).exists()
        )

    def test_excel_reimport_reactivates_source_with_new_baseline(self):
        self._post_excel()
        self._finalize_import()
        Cell.objects.create(
            sheet=self.sheet, row=self.rows[0], column=self.cols[0], raw_input='new'
        )
        response = self._post_excel()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        source = SheetImportSource.objects.get(sheet=self.sheet)
        self.assertFalse(source.is_deleted)
        self.assertEqual(source.baseline['cells']['0,0'], {'raw_input': 'new'})

    def test_google_reimport_reactivates_source(self):
        self._post_excel()
        self._finalize_import()
        response = self._post_google()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        source = SheetImportSource.objects.get(sheet=self.sheet)
        self.assertFalse(source.is_deleted)
        self.assertEqual(source.origin, 'GOOGLE')

    def _apply(self, content):
        apply_excel_import_styles(self.sheet, content, 'Sheet1')
        formats = {
            (fmt.row_index, fmt.column_index): fmt
            for fmt in SpreadsheetCellFormat.objects.filter(sheet=self.sheet)
        }
        fills = {
            (h.row_index, h.col_index): h.color
            for h in SpreadsheetHighlight.objects.filter(sheet=self.sheet, scope='CELL')
        }
        return formats, fills

    def test_excel_style_conversion_matches_the_browser_rules(self):
        """Cases from xlsx_style_import.test.ts, plus theme tints."""
        def build(ws):
            ws['A1'] = 'plain'
            ws['B1'] = 'explicit'
            ws['B1'].font = Font(size=9)
            for row, font in enumerate(
                [Font(bold=True), Font(italic=True), Font(strike=True), Font(name='Arial'), Font(name='Calibri')],
                start=2,
            ):
                ws.cell(row=row, column=1, value='styled').font = font
            text_colors = [
                Color('FFDC2626'), Color('FFFF0000'), Color('FF376092'), Color('FF000000'), Color('FFFFFFFF'),
                Color(theme=4, tint=0.3999755851924192), Color(theme=3, tint=-0.249977111117893),
            ]
            for row, color in enumerate(text_colors, start=1):
                ws.cell(row=row, column=3, value='colour').font = Font(color=color)
            fills = [
                ('solid', 'FFFEF08A'), ('solid', 'FFFDE047'), ('solid', 'FF376092'), ('solid', 'FFFF0000'),
                ('darkHorizontal', 'FFFEF08A'), ('solid', 'FF000000'), ('solid', 'FFFFFFFF'),
            ]
            for row, (pattern, argb) in enumerate(fills, start=1):
                ws.cell(row=row, column=4, value='fill').fill = PatternFill(fill_type=pattern, fgColor=argb)
            codes = [
                (0.5, '0.00%'), (0.5, '0%'), (1.5, '#,##0.00'), (1, '$#,##0.00'),
                (1, 'General'), (1, '[$£-809]#,##0.00'), (1, '[$$-C09]#,##0.00'),
            ]
            for row, (value, code) in enumerate(codes, start=1):
                ws.cell(row=row, column=5, value=value).number_format = code

        content = _xlsx(build)
        patched = _with_normal_size(content, 14)
        self.assertNotEqual(patched, content)
        formats, fills = self._apply(patched)

        self.assertEqual(formats[(0, 0)].font_size, 14)
        self.assertEqual(formats[(0, 1)].font_size, 9)
        self.assertEqual(
            [(formats[(r, 0)].bold, formats[(r, 0)].italic, formats[(r, 0)].strikethrough) for r in (1, 2, 3)],
            [(True, False, False), (False, True, False), (False, False, True)],
        )
        self.assertEqual(formats[(4, 0)].font_family, 'Arial, sans-serif')
        self.assertIsNone(formats[(5, 0)].font_family)
        self.assertEqual(
            [formats[(r, 2)].text_color for r in range(7)],
            ['#DC2626', '#FF0000', '#376092', None, None, '#95B3D7', '#17375E'],
        )
        self.assertEqual(fills, {
            (0, 3): '#FEF08A', (1, 3): '#FDE047', (2, 3): '#376092', (3, 3): '#FF0000',
        })
        self.assertEqual(
            [formats[(r, 4)].number_format for r in range(7)],
            [
                {'type': 'PERCENT', 'currency_code': None, 'decimal_places': 2},
                {'type': 'PERCENT', 'currency_code': None, 'decimal_places': 0},
                {'type': 'NUMBER', 'currency_code': None, 'decimal_places': 2},
                {'type': 'CURRENCY', 'currency_code': 'USD', 'decimal_places': 2},
                None,
                {'type': 'CURRENCY', 'currency_code': 'GBP', 'decimal_places': 2},
                None,
            ],
        )

    def test_excel_style_conversion_empty_and_merged_cells(self):
        def build(ws):
            ws['A1'].font = Font(bold=True)
            ws['A3'] = 'kept'
            ws.merge_cells('A5:B5')
            ws['A5'] = 'Hello'

        formats, _fills = self._apply(_xlsx(build))
        self.assertTrue(formats[(0, 0)].bold)
        self.assertIsNone(formats[(0, 0)].font_size)
        self.assertNotIn((1, 0), formats)
        self.assertIn((4, 0), formats)
        self.assertNotIn((4, 1), formats)


    def test_unreadable_excel_or_missing_worksheet_raises(self):
        with self.assertRaises(ValidationError):
            apply_excel_import_styles(self.sheet, b'PK\x03\x04fake', 'Sheet1')
        with self.assertRaises(ValidationError):
            apply_excel_import_styles(self.sheet, _xlsx(), 'Missing')


    def test_excel_post_writes_styles_before_the_snapshot(self):
        def build(ws):
            ws['A1'] = 'x'
            ws['A1'].font = Font(bold=True)

        response = self._post_excel(_xlsx(build))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            SpreadsheetCellFormat.objects.get(sheet=self.sheet, row_index=0, column_index=0).bold
        )
        baseline = SheetImportSource.objects.get(sheet=self.sheet).baseline
        self.assertTrue(baseline['formats']['0,0']['bold'])

    def test_unreadable_excel_post_returns_400_without_a_source(self):
        response = self._post_excel(b'PK\x03\x04fake')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(SheetImportSource.objects.filter(sheet=self.sheet).exists())
