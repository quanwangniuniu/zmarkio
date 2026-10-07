from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import Organization, Project
from spreadsheet.formula_engine import evaluate_formula
from spreadsheet.models import (
    Cell,
    CellValueType,
    ComputedCellType,
    Sheet,
    SheetColumn,
    SheetRow,
    Spreadsheet,
)
from spreadsheet.services import CellService, SheetService

User = get_user_model()


def create_test_user(username='testuser', email='test@example.com'):
    return User.objects.create_user(
        username=username,
        email=email,
        password='testpass123'
    )


def create_test_organization(name='Test Organization'):
    return Organization.objects.create(name=name)


def create_test_project(organization, name='Test Project', owner=None):
    return Project.objects.create(
        name=name,
        organization=organization,
        owner=owner
    )


class FormulaEngineTest(TestCase):
    def setUp(self):
        self.user = create_test_user()
        self.organization = create_test_organization()
        self.project = create_test_project(self.organization, owner=self.user)
        self.spreadsheet = Spreadsheet.objects.create(project=self.project, name='Test Spreadsheet')
        self.sheet = Sheet.objects.create(spreadsheet=self.spreadsheet, name='Sheet1', position=0)

        self.row1 = SheetRow.objects.create(sheet=self.sheet, position=0)
        self.row2 = SheetRow.objects.create(sheet=self.sheet, position=1)
        self.col_a = SheetColumn.objects.create(
            sheet=self.sheet,
            position=0,
            name=SheetService._generate_column_name(0)
        )
        self.col_b = SheetColumn.objects.create(
            sheet=self.sheet,
            position=1,
            name=SheetService._generate_column_name(1)
        )
        self.col_c = SheetColumn.objects.create(
            sheet=self.sheet,
            position=2,
            name=SheetService._generate_column_name(2)
        )
        self.col_d = SheetColumn.objects.create(
            sheet=self.sheet,
            position=3,
            name=SheetService._generate_column_name(3)
        )

    def test_formula_division(self):
        operations = [
            {'operation': 'set', 'row': 1, 'column': 1, 'raw_input': '10'},
            {'operation': 'set', 'row': 1, 'column': 2, 'raw_input': '2'},
            {'operation': 'set', 'row': 1, 'column': 3, 'raw_input': '=B2/C2'},
        ]

        CellService.batch_update_cells(self.sheet, operations, auto_expand=True)

        cell = Cell.objects.get(sheet=self.sheet, row=self.row2, column=self.col_d)
        self.assertEqual(cell.computed_type, ComputedCellType.NUMBER)
        self.assertEqual(cell.computed_number, Decimal('5'))

    def test_formula_multiplication_literal(self):
        operations = [
            {'operation': 'set', 'row': 0, 'column': 0, 'raw_input': '=2*3'},
        ]

        CellService.batch_update_cells(self.sheet, operations, auto_expand=True)

        cell = Cell.objects.get(sheet=self.sheet, row=self.row1, column=self.col_a)
        self.assertEqual(cell.computed_type, ComputedCellType.NUMBER)
        self.assertEqual(cell.computed_number, Decimal('6'))

    def test_formula_precision_quantized(self):
        operations = [
            {'operation': 'set', 'row': 0, 'column': 0, 'raw_input': '=0.1*0.2'},
        ]

        CellService.batch_update_cells(self.sheet, operations, auto_expand=True)

        cell = Cell.objects.get(sheet=self.sheet, row=self.row1, column=self.col_a)
        self.assertEqual(cell.computed_type, ComputedCellType.NUMBER)
        self.assertEqual(cell.computed_number, Decimal('0.02'))

    def test_formula_multiplication_reference(self):
        operations = [
            {'operation': 'set', 'row': 0, 'column': 0, 'raw_input': '5'},
            {'operation': 'set', 'row': 0, 'column': 1, 'raw_input': '=A1*2'},
        ]

        CellService.batch_update_cells(self.sheet, operations, auto_expand=True)

        cell = Cell.objects.get(sheet=self.sheet, row=self.row1, column=self.col_b)
        self.assertEqual(cell.computed_type, ComputedCellType.NUMBER)
        self.assertEqual(cell.computed_number, Decimal('10'))

    def test_formula_divide_by_zero(self):
        operations = [
            {'operation': 'set', 'row': 1, 'column': 1, 'raw_input': '10'},
            {'operation': 'set', 'row': 1, 'column': 2, 'raw_input': '0'},
            {'operation': 'set', 'row': 1, 'column': 3, 'raw_input': '=B2/C2'},
        ]

        CellService.batch_update_cells(self.sheet, operations, auto_expand=True)

        cell = Cell.objects.get(sheet=self.sheet, row=self.row2, column=self.col_d)
        self.assertEqual(cell.computed_type, ComputedCellType.ERROR)
        self.assertEqual(cell.error_code, '#DIV/0!')

    def test_formula_invalid_reference(self):
        operations = [
            {'operation': 'set', 'row': 1, 'column': 3, 'raw_input': '=ZZ999999'},
        ]

        CellService.batch_update_cells(self.sheet, operations, auto_expand=True)

        cell = Cell.objects.get(sheet=self.sheet, row=self.row2, column=self.col_d)
        self.assertEqual(cell.computed_type, ComputedCellType.EMPTY)
        self.assertEqual(cell.error_code, None)
        self.assertIsNone(cell.computed_number)

    def test_formula_invalid_operand_does_not_500(self):
        operations = [
            {'operation': 'set', 'row': 0, 'column': 0, 'raw_input': 'abc'},
            {'operation': 'set', 'row': 0, 'column': 1, 'raw_input': '=A1*2'},
        ]

        CellService.batch_update_cells(self.sheet, operations, auto_expand=True)

        cell = Cell.objects.get(sheet=self.sheet, row=self.row1, column=self.col_b)
        self.assertEqual(cell.computed_type, ComputedCellType.ERROR)
        self.assertEqual(cell.error_code, '#VALUE!')

    def test_comparison_and_if_basic(self):
        operations = [
            {'operation': 'set', 'row': 0, 'column': 0, 'raw_input': '=1=1'},
            {'operation': 'set', 'row': 1, 'column': 0, 'raw_input': '6'},
            {'operation': 'set', 'row': 1, 'column': 1, 'raw_input': '=A2*2>10'},
            {'operation': 'set', 'row': 2, 'column': 0, 'raw_input': '1'},
            {'operation': 'set', 'row': 2, 'column': 1, 'raw_input': '2'},
            {'operation': 'set', 'row': 2, 'column': 2, 'raw_input': '=A3<>B3'},
            {'operation': 'set', 'row': 3, 'column': 0, 'raw_input': '=1+2=3'},
        ]
        CellService.batch_update_cells(self.sheet, operations, auto_expand=True)

        cell_a1 = Cell.objects.get(sheet=self.sheet, row=self.row1, column=self.col_a)
        self.assertEqual(cell_a1.computed_type, ComputedCellType.BOOLEAN)
        self.assertEqual(cell_a1.computed_string, 'TRUE')

        cell_b2 = Cell.objects.get(sheet=self.sheet, row=self.row2, column=self.col_b)
        self.assertEqual(cell_b2.computed_type, ComputedCellType.BOOLEAN)
        self.assertEqual(cell_b2.computed_string, 'TRUE')

        cell_c3 = Cell.objects.get(sheet=self.sheet, row__position=2, column=self.col_c)
        self.assertEqual(cell_c3.computed_type, ComputedCellType.BOOLEAN)
        self.assertEqual(cell_c3.computed_string, 'TRUE')

        row4 = SheetRow.objects.get(sheet=self.sheet, position=3)
        cell_a4 = Cell.objects.get(sheet=self.sheet, row=row4, column=self.col_a)
        self.assertEqual(cell_a4.computed_type, ComputedCellType.BOOLEAN)
        self.assertEqual(cell_a4.computed_string, 'TRUE')

    def test_if_short_circuit_and_strings(self):
        operations = [
            {'operation': 'set', 'row': 0, 'column': 0, 'raw_input': 'abc'},
            {'operation': 'set', 'row': 0, 'column': 1, 'raw_input': '=A1/0'},
            {'operation': 'set', 'row': 0, 'column': 2, 'raw_input': '=IF(A1="abc",1,0)'},
            {'operation': 'set', 'row': 0, 'column': 3, 'raw_input': '=IF(1=0,B1,2)'},
            {'operation': 'set', 'row': 1, 'column': 0, 'raw_input': ''},
            {'operation': 'set', 'row': 1, 'column': 1, 'raw_input': '=IF(A2,1,2)'},
        ]
        CellService.batch_update_cells(self.sheet, operations, auto_expand=True)

        cell_c1 = Cell.objects.get(sheet=self.sheet, row=self.row1, column=self.col_c)
        self.assertEqual(cell_c1.computed_type, ComputedCellType.NUMBER)
        self.assertEqual(cell_c1.computed_number, Decimal('1'))

        cell_d1 = Cell.objects.get(sheet=self.sheet, row=self.row1, column=self.col_d)
        self.assertEqual(cell_d1.computed_type, ComputedCellType.NUMBER)
        self.assertEqual(cell_d1.computed_number, Decimal('2'))

        row2 = SheetRow.objects.get(sheet=self.sheet, position=1)
        cell_b2 = Cell.objects.get(sheet=self.sheet, row=row2, column=self.col_b)
        self.assertEqual(cell_b2.computed_type, ComputedCellType.NUMBER)
        self.assertEqual(cell_b2.computed_number, Decimal('2'))

    def test_string_comparison_invalid_operator(self):
        operations = [
            {'operation': 'set', 'row': 0, 'column': 0, 'raw_input': 'abc'},
            {'operation': 'set', 'row': 0, 'column': 1, 'raw_input': '=A1>"a"'},
        ]
        CellService.batch_update_cells(self.sheet, operations, auto_expand=True)

        cell_b1 = Cell.objects.get(sheet=self.sheet, row=self.row1, column=self.col_b)
        self.assertEqual(cell_b1.computed_type, ComputedCellType.ERROR)
        self.assertEqual(cell_b1.error_code, '#VALUE!')

    def test_long_decimal_comparison(self):
        long_decimal = '9.7654322457898765'
        operations = [
            {'operation': 'set', 'row': 0, 'column': 0, 'raw_input': long_decimal},
            {'operation': 'set', 'row': 0, 'column': 1, 'raw_input': f'=A1={long_decimal}'},
        ]
        CellService.batch_update_cells(self.sheet, operations, auto_expand=True)

        cell_b1 = Cell.objects.get(sheet=self.sheet, row=self.row1, column=self.col_b)
        self.assertEqual(cell_b1.computed_type, ComputedCellType.BOOLEAN)
        self.assertEqual(cell_b1.computed_string, 'TRUE')

    def test_and_or_not_truthiness_and_short_circuit(self):
        operations = [
            {'operation': 'set', 'row': 0, 'column': 0, 'raw_input': '0'},
            {'operation': 'set', 'row': 0, 'column': 1, 'raw_input': '=A1/0'},
            {'operation': 'set', 'row': 0, 'column': 2, 'raw_input': '=AND(1=1, 2=2)'},
            {'operation': 'set', 'row': 0, 'column': 3, 'raw_input': '=AND(1=1, 1=2)'},
            {'operation': 'set', 'row': 1, 'column': 0, 'raw_input': '=OR(1=2, 1=1)'},
            {'operation': 'set', 'row': 1, 'column': 1, 'raw_input': '=NOT(1=1)'},
            {'operation': 'set', 'row': 1, 'column': 2, 'raw_input': '=AND(1, "x")'},
            {'operation': 'set', 'row': 1, 'column': 3, 'raw_input': '=AND(1, "")'},
            {'operation': 'set', 'row': 2, 'column': 0, 'raw_input': '=OR(1=1, B1)'},
            {'operation': 'set', 'row': 2, 'column': 1, 'raw_input': '=AND(1=2, B1)'},
        ]
        CellService.batch_update_cells(self.sheet, operations, auto_expand=True)

        cell_c1 = Cell.objects.get(sheet=self.sheet, row=self.row1, column=self.col_c)
        self.assertEqual(cell_c1.computed_type, ComputedCellType.BOOLEAN)
        self.assertEqual(cell_c1.computed_string, 'TRUE')

        cell_d1 = Cell.objects.get(sheet=self.sheet, row=self.row1, column=self.col_d)
        self.assertEqual(cell_d1.computed_type, ComputedCellType.BOOLEAN)
        self.assertEqual(cell_d1.computed_string, 'FALSE')

        row2 = SheetRow.objects.get(sheet=self.sheet, position=1)
        cell_a2 = Cell.objects.get(sheet=self.sheet, row=row2, column=self.col_a)
        self.assertEqual(cell_a2.computed_type, ComputedCellType.BOOLEAN)
        self.assertEqual(cell_a2.computed_string, 'TRUE')

        cell_b2 = Cell.objects.get(sheet=self.sheet, row=row2, column=self.col_b)
        self.assertEqual(cell_b2.computed_type, ComputedCellType.BOOLEAN)
        self.assertEqual(cell_b2.computed_string, 'FALSE')

        cell_c2 = Cell.objects.get(sheet=self.sheet, row=row2, column=self.col_c)
        self.assertEqual(cell_c2.computed_type, ComputedCellType.BOOLEAN)
        self.assertEqual(cell_c2.computed_string, 'TRUE')

        cell_d2 = Cell.objects.get(sheet=self.sheet, row=row2, column=self.col_d)
        self.assertEqual(cell_d2.computed_type, ComputedCellType.BOOLEAN)
        self.assertEqual(cell_d2.computed_string, 'FALSE')

        row3 = SheetRow.objects.get(sheet=self.sheet, position=2)
        cell_a3 = Cell.objects.get(sheet=self.sheet, row=row3, column=self.col_a)
        self.assertEqual(cell_a3.computed_type, ComputedCellType.BOOLEAN)
        self.assertEqual(cell_a3.computed_string, 'TRUE')

        cell_b3 = Cell.objects.get(sheet=self.sheet, row=row3, column=self.col_b)
        self.assertEqual(cell_b3.computed_type, ComputedCellType.BOOLEAN)
        self.assertEqual(cell_b3.computed_string, 'FALSE')

    def test_vlookup_exact_match(self):
        operations = [
            {'operation': 'set', 'row': 0, 'column': 0, 'raw_input': 'Alice'},
            {'operation': 'set', 'row': 0, 'column': 1, 'raw_input': '10'},
            {'operation': 'set', 'row': 1, 'column': 0, 'raw_input': 'Bob'},
            {'operation': 'set', 'row': 1, 'column': 1, 'raw_input': '20'},
            {'operation': 'set', 'row': 0, 'column': 2, 'raw_input': '=VLOOKUP("Alice", A1:B2, 1, FALSE)'},
            {'operation': 'set', 'row': 0, 'column': 3, 'raw_input': '=VLOOKUP("Alice", A1:B2, 2, FALSE)'},
            {'operation': 'set', 'row': 0, 'column': 4, 'raw_input': '=VLOOKUP("Bob", A1:B2, 2)'},
            {'operation': 'set', 'row': 1, 'column': 2, 'raw_input': '=VLOOKUP("X", A1:B2, 2, FALSE)'},
            {'operation': 'set', 'row': 1, 'column': 3, 'raw_input': '=VLOOKUP("Alice", A1:B2, 0, FALSE)'},
            {'operation': 'set', 'row': 2, 'column': 2, 'raw_input': '=VLOOKUP("Bob", A1:B2, 3, FALSE)'},
            {'operation': 'set', 'row': 3, 'column': 2, 'raw_input': '=VLOOKUP("Bob", A1:B2, 2, TRUE)'},
            {'operation': 'set', 'row': 4, 'column': 2, 'raw_input': '=VLOOKUP(1, A1:B2, 2)'},
        ]
        CellService.batch_update_cells(self.sheet, operations, auto_expand=True)

        cell_c1 = Cell.objects.get(sheet=self.sheet, row=self.row1, column=self.col_c)
        self.assertEqual(cell_c1.computed_type, ComputedCellType.STRING)
        self.assertEqual(cell_c1.computed_string, 'Alice')

        cell_d1 = Cell.objects.get(sheet=self.sheet, row=self.row1, column=self.col_d)
        self.assertEqual(cell_d1.computed_type, ComputedCellType.NUMBER)
        self.assertEqual(cell_d1.computed_number, Decimal('10'))

        cell_e1 = Cell.objects.get(sheet=self.sheet, row=self.row1, column__position=4)
        self.assertEqual(cell_e1.computed_type, ComputedCellType.NUMBER)
        self.assertEqual(cell_e1.computed_number, Decimal('20'))

        row2 = SheetRow.objects.get(sheet=self.sheet, position=1)
        cell_c2 = Cell.objects.get(sheet=self.sheet, row=row2, column=self.col_c)
        self.assertEqual(cell_c2.computed_type, ComputedCellType.ERROR)
        self.assertEqual(cell_c2.error_code, '#N/A')

        cell_d2 = Cell.objects.get(sheet=self.sheet, row=row2, column=self.col_d)
        self.assertEqual(cell_d2.computed_type, ComputedCellType.ERROR)
        self.assertEqual(cell_d2.error_code, '#REF!')

        row3 = SheetRow.objects.get(sheet=self.sheet, position=2)
        cell_c3 = Cell.objects.get(sheet=self.sheet, row=row3, column=self.col_c)
        self.assertEqual(cell_c3.computed_type, ComputedCellType.ERROR)
        self.assertEqual(cell_c3.error_code, '#REF!')

        row4 = SheetRow.objects.get(sheet=self.sheet, position=3)
        cell_c4 = Cell.objects.get(sheet=self.sheet, row=row4, column=self.col_c)
        self.assertEqual(cell_c4.computed_type, ComputedCellType.ERROR)
        self.assertEqual(cell_c4.error_code, '#VALUE!')

        row5 = SheetRow.objects.get(sheet=self.sheet, position=4)
        cell_c5 = Cell.objects.get(sheet=self.sheet, row=row5, column=self.col_c)
        self.assertEqual(cell_c5.computed_type, ComputedCellType.ERROR)
        self.assertEqual(cell_c5.error_code, '#N/A')

    def test_divide_by_zero_does_not_stop_other_formulas(self):
        operations = [
            {'operation': 'set', 'row': 1, 'column': 1, 'raw_input': '10'},
            {'operation': 'set', 'row': 1, 'column': 2, 'raw_input': '0'},
            {'operation': 'set', 'row': 1, 'column': 3, 'raw_input': '=B2/C2'},
            {'operation': 'set', 'row': 1, 'column': 4, 'raw_input': '=B2+5'},
        ]

        CellService.batch_update_cells(self.sheet, operations, auto_expand=True)

        error_cell = Cell.objects.get(sheet=self.sheet, row=self.row2, column=self.col_d)
        self.assertEqual(error_cell.computed_type, ComputedCellType.ERROR)
        self.assertEqual(error_cell.error_code, '#DIV/0!')

        normal_cell = Cell.objects.get(sheet=self.sheet, row=self.row2, column__position=4)
        self.assertEqual(normal_cell.computed_type, ComputedCellType.NUMBER)
        self.assertEqual(normal_cell.computed_number, Decimal('15'))
        self.assertIsNone(normal_cell.error_code)

class FormulaIdentifierResolverTest(TestCase):
    """`evaluate_formula`'s optional `identifier_resolver`, which lets callers
    outside the spreadsheet bind bare names to numbers (see
    `report.kpi_registry`). Sheet behaviour must be unchanged without one."""

    def setUp(self):
        self.user = create_test_user(username='resolveruser', email='resolver@example.com')
        self.organization = create_test_organization(name='Resolver Org')
        self.project = create_test_project(self.organization, name='Resolver Project', owner=self.user)
        self.spreadsheet = Spreadsheet.objects.create(project=self.project, name='Resolver Spreadsheet')
        self.sheet = Sheet.objects.create(spreadsheet=self.spreadsheet, name='Sheet1', position=0)
        self.values = {'revenue': Decimal('1000'), 'spend': Decimal('250'), 'net_profit': Decimal('750')}

    def _resolve(self, name):
        return self.values.get(name.lower())

    def test_bare_identifier_is_an_error_without_a_resolver(self):
        result = evaluate_formula('=revenue/spend', self.sheet)
        self.assertEqual(result.computed_type, ComputedCellType.ERROR)

    def test_underscore_identifier_is_an_error_without_a_resolver(self):
        """Used to fail while tokenizing; must still be an error, not a value."""
        result = evaluate_formula('=net_profit', self.sheet)
        self.assertEqual(result.computed_type, ComputedCellType.ERROR)

    def test_resolver_binds_bare_identifiers(self):
        result = evaluate_formula('=revenue/spend', self.sheet, identifier_resolver=self._resolve)
        self.assertEqual(result.computed_type, ComputedCellType.NUMBER)
        self.assertEqual(result.computed_number, Decimal('4'))

    def test_resolver_binds_underscore_identifiers(self):
        result = evaluate_formula('=net_profit/spend', self.sheet, identifier_resolver=self._resolve)
        self.assertEqual(result.computed_number, Decimal('3'))

    def test_unknown_identifier_reports_name_error(self):
        result = evaluate_formula('=revenue/unknown', self.sheet, identifier_resolver=self._resolve)
        self.assertEqual(result.computed_type, ComputedCellType.ERROR)
        self.assertEqual(result.error_code, '#NAME?')

    def test_functions_keep_priority_over_resolved_names(self):
        """A resolver must not shadow a built-in: MIN stays MIN."""
        resolver = lambda name: Decimal('99') if name.lower() == 'min' else self._resolve(name)
        result = evaluate_formula('=MIN(revenue, spend)', self.sheet, identifier_resolver=resolver)
        self.assertEqual(result.computed_number, Decimal('250'))

    def test_resolved_names_work_inside_function_arguments(self):
        result = evaluate_formula('=ROUND(revenue/spend, 1)', self.sheet, identifier_resolver=self._resolve)
        self.assertEqual(result.computed_number, Decimal('4.0'))

    def test_skipped_conditional_branch_is_not_resolved(self):
        result = evaluate_formula(
            '=IF(spend>0, revenue, missing_name)', self.sheet, identifier_resolver=self._resolve
        )
        self.assertEqual(result.computed_number, Decimal('1000'))

    def test_cell_references_still_resolve_alongside_named_values(self):
        row = SheetRow.objects.create(sheet=self.sheet, position=0)
        column = SheetColumn.objects.create(sheet=self.sheet, position=0, name='A')
        Cell.objects.create(
            sheet=self.sheet,
            row=row,
            column=column,
            raw_input='4',
            value_type=CellValueType.NUMBER,
            number_value=Decimal('4'),
            computed_type=ComputedCellType.NUMBER,
            computed_number=Decimal('4'),
        )
        result = evaluate_formula('=revenue/A1', self.sheet, identifier_resolver=self._resolve)
        self.assertEqual(result.computed_number, Decimal('250'))
