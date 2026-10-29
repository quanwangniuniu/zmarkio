import tempfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

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

    def _post_excel(self):
        uploaded = SimpleUploadedFile(
            'book.xlsx',
            b'PK\x03\x04fake',
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