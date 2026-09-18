from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from unittest.mock import patch

from django.contrib.auth import get_user_model
from core.models import Project, Organization, ProjectMember
from core.test_utils import grant_ai_consent
from spreadsheet.models import Spreadsheet, Sheet, SheetRow, SheetColumn, Cell
from spreadsheet.nl_pivot_service import generate_pivot_config

User = get_user_model()


def create_user(username='testuser', email='test@example.com'):
    return User.objects.create_user(username=username, email=email, password='testpass123')


def create_project(owner):
    organization = Organization.objects.create(name='Test Org')
    project = Project.objects.create(
        name='Test Project', organization=organization, owner=owner
    )
    ProjectMember.objects.create(
        user=owner, project=project, role='owner', is_active=True
    )
    return project


SHEET_SCHEMA = {
    'columns': [
        {'name': 'Region', 'has_data': True},
        {'name': 'Revenue', 'has_data': True},
    ],
    'row_count': 5,
}


class GeneratePivotConfigServiceTests(TestCase):
    """Unit tests for spreadsheet.nl_pivot_service.generate_pivot_config."""

    @patch('spreadsheet.nl_pivot_service.call_gemini_json')
    def test_valid_generation(self, mock_gemini):
        mock_gemini.return_value = {
            'config': {
                'rows_config': ['Region'],
                'columns_config': [],
                'values_config': [{'field': 'Revenue', 'aggregation': 'SUM', 'display': 'VALUE'}],
                'show_grand_total_row': True,
            }
        }
        config = generate_pivot_config('sum revenue by region', SHEET_SCHEMA)
        self.assertEqual(config['rows_config'], ['Region'])
        self.assertEqual(config['values_config'][0]['field'], 'Revenue')
        self.assertEqual(config['values_config'][0]['aggregation'], 'SUM')
        self.assertTrue(config['show_grand_total_row'])

    @patch('spreadsheet.nl_pivot_service.call_gemini_json')
    def test_unknown_field_raises(self, mock_gemini):
        mock_gemini.return_value = {
            'config': {
                'rows_config': ['Department'],
                'columns_config': [],
                'values_config': [{'field': 'Revenue', 'aggregation': 'SUM', 'display': 'VALUE'}],
                'show_grand_total_row': True,
            }
        }
        with self.assertRaises(ValueError):
            generate_pivot_config('pivot by department', SHEET_SCHEMA)

    @patch('spreadsheet.nl_pivot_service.call_gemini_json')
    def test_empty_values_config_raises(self, mock_gemini):
        mock_gemini.return_value = {
            'config': {
                'rows_config': ['Region'],
                'columns_config': [],
                'values_config': [],
                'show_grand_total_row': True,
            }
        }
        with self.assertRaises(ValueError):
            generate_pivot_config('group by region', SHEET_SCHEMA)

    @patch('spreadsheet.nl_pivot_service.call_gemini_json')
    def test_gemini_error_response_raises(self, mock_gemini):
        mock_gemini.return_value = {'error': 'Column "Department" does not exist in this sheet.'}
        with self.assertRaises(ValueError) as ctx:
            generate_pivot_config('pivot by department', SHEET_SCHEMA)
        self.assertIn('Department', str(ctx.exception))

    @patch('spreadsheet.nl_pivot_service.call_gemini_json')
    def test_invalid_aggregation_raises(self, mock_gemini):
        mock_gemini.return_value = {
            'config': {
                'rows_config': ['Region'],
                'columns_config': [],
                'values_config': [{'field': 'Revenue', 'aggregation': 'TOTAL', 'display': 'VALUE'}],
                'show_grand_total_row': True,
            }
        }
        with self.assertRaises(ValueError):
            generate_pivot_config('total revenue by region', SHEET_SCHEMA)


class GeneratePivotConfigViewTests(TestCase):
    """Integration tests for the /sheets/<id>/generate-pivot-config/ endpoint."""

    def setUp(self):
        self.user = create_user()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.project = create_project(self.user)
        self.spreadsheet = Spreadsheet.objects.create(project=self.project, name='Sheetbook')
        grant_ai_consent(self.user, self.spreadsheet)
        self.sheet = Sheet.objects.create(spreadsheet=self.spreadsheet, name='Sheet1', position=0)

        region_col = SheetColumn.objects.create(sheet=self.sheet, name='A', position=0)
        revenue_col = SheetColumn.objects.create(sheet=self.sheet, name='B', position=1)
        header_row = SheetRow.objects.create(sheet=self.sheet, position=0)
        data_row = SheetRow.objects.create(sheet=self.sheet, position=1)
        Cell.objects.create(sheet=self.sheet, row=header_row, column=region_col, raw_input='Region')
        Cell.objects.create(sheet=self.sheet, row=header_row, column=revenue_col, raw_input='Revenue')
        Cell.objects.create(sheet=self.sheet, row=data_row, column=region_col, raw_input='West')
        Cell.objects.create(sheet=self.sheet, row=data_row, column=revenue_col, raw_input='100')

    @patch('spreadsheet.nl_pivot_service.call_gemini_json')
    def test_generate_pivot_config_success(self, mock_gemini):
        mock_gemini.return_value = {
            'config': {
                'rows_config': ['Region'],
                'columns_config': [],
                'values_config': [{'field': 'Revenue', 'aggregation': 'SUM', 'display': 'VALUE'}],
                'show_grand_total_row': True,
            }
        }
        response = self.client.post(
            f'/api/spreadsheet/sheets/{self.sheet.id}/generate-pivot-config/',
            {'instruction': 'sum revenue by region'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['config']['rows_config'], ['Region'])

    def test_missing_instruction_returns_400(self):
        response = self.client.post(
            f'/api/spreadsheet/sheets/{self.sheet.id}/generate-pivot-config/',
            {'instruction': ''},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('spreadsheet.nl_pivot_service.call_gemini_json')
    def test_invalid_instruction_returns_422(self, mock_gemini):
        mock_gemini.return_value = {'error': 'Column "Department" does not exist in this sheet.'}
        response = self.client.post(
            f'/api/spreadsheet/sheets/{self.sheet.id}/generate-pivot-config/',
            {'instruction': 'pivot by department'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn('Department', response.data['error'])
