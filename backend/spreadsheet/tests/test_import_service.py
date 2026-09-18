import csv
import os
import tempfile

from django.test import TestCase, override_settings

from core.models import Organization, Project, CustomUser
from core.services.file_parser import parse_file_to_json
from spreadsheet.models import Spreadsheet, Sheet, Cell
from spreadsheet.import_service import create_spreadsheet_from_upload
from spreadsheet.services import SpreadsheetService


class SpreadsheetImportServiceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Import Org', slug='import-org')
        self.user = CustomUser.objects.create_user(
            email='import@test.com',
            username='importuser',
            password='testpass123',
        )
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name='Import Project',
            organization=self.org,
            owner=self.user,
        )

    def _write_csv(self, rows, headers=('Campaign', 'Spend')):
        handle, path = tempfile.mkstemp(suffix='.csv')
        os.close(handle)
        with open(path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)
        return path

    def test_create_spreadsheet_from_csv_upload(self):
        path = self._write_csv([
            ('Alpha', '100'),
            ('Beta', '200'),
        ])
        try:
            result = create_spreadsheet_from_upload(
                project=self.project,
                parsed=parse_file_to_json(path, 'campaigns.csv', max_rows=None),
                original_filename='campaigns.csv',
            )
        finally:
            os.remove(path)

        self.assertEqual(result['name'], 'campaigns')
        spreadsheet = Spreadsheet.objects.get(id=result['spreadsheet_id'])
        self.assertEqual(spreadsheet.project_id, self.project.id)
        sheet = Sheet.objects.get(id=result['sheet_id'])
        self.assertEqual(sheet.name, 'Sheet1')
        cells = Cell.objects.filter(sheet=sheet, is_deleted=False)
        self.assertTrue(cells.filter(row__position=0, column__position=0, string_value='Campaign').exists())
        self.assertTrue(cells.filter(row__position=1, column__position=0, string_value='Alpha').exists())
        self.assertTrue(cells.filter(row__position=2, column__position=1, raw_input='200').exists())
        self.assertEqual(
            result['url'],
            f'/spreadsheets/{spreadsheet.id}?project_id={self.project.id}',
        )

    def test_dedupes_spreadsheet_name_when_exists(self):
        SpreadsheetService.create_spreadsheet(project=self.project, name='report')
        path = self._write_csv([('Only', '1')])
        try:
            result = create_spreadsheet_from_upload(
                project=self.project,
                parsed=parse_file_to_json(path, 'report.csv', max_rows=None),
                original_filename='report.csv',
            )
        finally:
            os.remove(path)

        self.assertEqual(result['name'], 'report (1)')

    def test_parse_file_caps_rows_at_setting_default(self):
        from django.test import override_settings

        path = self._write_csv([(f'Row{i}', str(i)) for i in range(250)])
        try:
            with override_settings(SPREADSHEET_AI_MAX_ROWS=100):
                parsed = parse_file_to_json(path, 'large.csv')
        finally:
            os.remove(path)

        self.assertEqual(len(parsed['sheets'][0]['rows']), 100)
        self.assertTrue(parsed['limits_hit']['rows'])

    def test_parse_file_explicit_max_rows(self):
        path = self._write_csv([(f'Row{i}', str(i)) for i in range(250)])
        try:
            parsed = parse_file_to_json(path, 'large.csv', max_rows=250)
        finally:
            os.remove(path)

        self.assertEqual(len(parsed['sheets'][0]['rows']), 250)
        self.assertFalse(parsed['limits_hit']['rows'])
