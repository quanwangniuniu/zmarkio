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
from spreadsheet.services import SheetService

User = get_user_model()


def _make_udfs(*definitions):
    """Build udfs dict from (name, params, expression) tuples."""
    return {
        name.upper(): {"name": name.upper(), "params": params, "expression": expression}
        for name, params, expression in definitions
    }


class UDFResolverTest(TestCase):
    """Unit tests for the UDF resolver in formula_engine.evaluate_formula."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="pass"
        )
        self.org = Organization.objects.create(name="Test Org")
        self.project = Project.objects.create(
            name="Test Project", organization=self.org, owner=self.user
        )
        self.spreadsheet = Spreadsheet.objects.create(
            project=self.project, name="Test Sheet"
        )
        self.sheet = Sheet.objects.create(
            spreadsheet=self.spreadsheet, name="Sheet1", position=0
        )
        self.row1 = SheetRow.objects.create(sheet=self.sheet, position=0)
        self.row2 = SheetRow.objects.create(sheet=self.sheet, position=1)
        self.row3 = SheetRow.objects.create(sheet=self.sheet, position=2)
        self.col_a = SheetColumn.objects.create(
            sheet=self.sheet, position=0, name=SheetService._generate_column_name(0)
        )
        self.col_b = SheetColumn.objects.create(
            sheet=self.sheet, position=1, name=SheetService._generate_column_name(1)
        )
        self.col_c = SheetColumn.objects.create(
            sheet=self.sheet, position=2, name=SheetService._generate_column_name(2)
        )

    def _set_cell(self, row_position, col_position, value):
        """Helper to set a numeric cell value directly."""
        row = SheetRow.objects.get(sheet=self.sheet, position=row_position)
        col = SheetColumn.objects.get(sheet=self.sheet, position=col_position)
        Cell.objects.update_or_create(
            sheet=self.sheet, row=row, column=col,
            defaults={
                "number_value": Decimal(str(value)),
                "value_type": CellValueType.NUMBER,
                "computed_type": ComputedCellType.NUMBER,
                "computed_number": Decimal(str(value)),
            },
        )

    # ------------------------------------------------------------------
    # Basic scalar arguments
    # ------------------------------------------------------------------

    def test_udf_with_literal_numbers(self):
        """=MYROAS(100, 5) should return 20."""
        udfs = _make_udfs(("MYROAS", ["revenue", "cost"], "revenue / cost"))
        result = evaluate_formula("=MYROAS(100, 5)", self.sheet, udfs=udfs)
        self.assertEqual(result.computed_type, ComputedCellType.NUMBER)
        self.assertEqual(result.computed_number, Decimal("20"))

    def test_udf_with_cell_references(self):
        """=MYROAS(A1, B1) should resolve cell values."""
        self._set_cell(0, 0, 100)  # A1 = 100
        self._set_cell(0, 1, 4)    # B1 = 4
        udfs = _make_udfs(("MYROAS", ["revenue", "cost"], "revenue / cost"))
        result = evaluate_formula("=MYROAS(A1, B1)", self.sheet, udfs=udfs)
        self.assertEqual(result.computed_type, ComputedCellType.NUMBER)
        self.assertEqual(result.computed_number, Decimal("25"))

    def test_udf_addition(self):
        """=ADD(3, 4) should return 7."""
        udfs = _make_udfs(("ADD", ["a", "b"], "a + b"))
        result = evaluate_formula("=ADD(3, 4)", self.sheet, udfs=udfs)
        self.assertEqual(result.computed_number, Decimal("7"))

    def test_udf_single_param(self):
        """=DOUBLE(6) should return 12."""
        udfs = _make_udfs(("DOUBLE", ["x"], "x * 2"))
        result = evaluate_formula("=DOUBLE(6)", self.sheet, udfs=udfs)
        self.assertEqual(result.computed_number, Decimal("12"))

    # ------------------------------------------------------------------
    # Range arguments
    # ------------------------------------------------------------------

    def test_udf_with_range_argument(self):
        """=MYSUM(A1:A3) where expression=SUM(data) should sum the range."""
        self._set_cell(0, 0, 10)  # A1
        self._set_cell(1, 0, 20)  # A2
        self._set_cell(2, 0, 30)  # A3
        udfs = _make_udfs(("MYSUM", ["data"], "SUM(data)"))
        result = evaluate_formula("=MYSUM(A1:A3)", self.sheet, udfs=udfs)
        self.assertEqual(result.computed_type, ComputedCellType.NUMBER)
        self.assertEqual(result.computed_number, Decimal("60"))

    def test_udf_with_range_average(self):
        """=MYAVG(A1:A2) where expression=AVERAGE(data) should average the range."""
        self._set_cell(0, 0, 100)  # A1
        self._set_cell(1, 0, 20)   # A2
        udfs = _make_udfs(("MYAVG", ["data"], "AVERAGE(data)"))
        result = evaluate_formula("=MYAVG(A1:A2)", self.sheet, udfs=udfs)
        self.assertEqual(result.computed_type, ComputedCellType.NUMBER)
        self.assertEqual(result.computed_number, Decimal("60"))

    # ------------------------------------------------------------------
    # Nested UDF calls
    # ------------------------------------------------------------------

    def test_nested_udf_calls(self):
        """=DOUBLE(MYROAS(100, 5)) should return 40."""
        udfs = _make_udfs(
            ("MYROAS", ["revenue", "cost"], "revenue / cost"),
            ("DOUBLE", ["x"], "x * 2"),
        )
        result = evaluate_formula("=DOUBLE(MYROAS(100, 5))", self.sheet, udfs=udfs)
        self.assertEqual(result.computed_number, Decimal("40"))

    def test_udf_used_in_arithmetic_expression(self):
        """=MYROAS(100, 5) + 5 should return 25."""
        udfs = _make_udfs(("MYROAS", ["revenue", "cost"], "revenue / cost"))
        result = evaluate_formula("=MYROAS(100, 5) + 5", self.sheet, udfs=udfs)
        self.assertEqual(result.computed_number, Decimal("25"))

    # ------------------------------------------------------------------
    # Word boundary: param name must not be a substring of another
    # ------------------------------------------------------------------

    def test_param_substitution_respects_word_boundary(self):
        """Param 'rev' must not replace inside 'revenue'."""
        udfs = _make_udfs(("MYFN", ["rev", "revenue"], "revenue / rev"))
        result = evaluate_formula("=MYFN(2, 100)", self.sheet, udfs=udfs)
        self.assertEqual(result.computed_number, Decimal("50"))

    # ------------------------------------------------------------------
    # Error cases
    # ------------------------------------------------------------------

    def test_unknown_udf_returns_ref_error(self):
        """Calling an undefined UDF should return #REF!."""
        result = evaluate_formula("=NOTEXIST(1, 2)", self.sheet, udfs={})
        self.assertEqual(result.computed_type, ComputedCellType.ERROR)
        self.assertEqual(result.error_code, "#REF!")

    def test_wrong_argument_count_returns_value_error(self):
        """Passing wrong number of args should return #VALUE!."""
        udfs = _make_udfs(("MYROAS", ["revenue", "cost"], "revenue / cost"))
        result = evaluate_formula("=MYROAS(100)", self.sheet, udfs=udfs)
        self.assertEqual(result.computed_type, ComputedCellType.ERROR)
        self.assertEqual(result.error_code, "#VALUE!")

    def test_self_recursive_udf_returns_ref_error(self):
        """A UDF whose expression calls itself should return #REF!."""
        udfs = _make_udfs(("LOOP", ["x"], "LOOP(x)"))
        result = evaluate_formula("=LOOP(1)", self.sheet, udfs=udfs)
        self.assertEqual(result.computed_type, ComputedCellType.ERROR)
        self.assertEqual(result.error_code, "#REF!")

    def test_mutually_recursive_udfs_return_ref_error(self):
        """FOO → BAR → FOO indirect mutual recursion should return #REF!."""
        udfs = _make_udfs(
            ("FOO", ["x"], "BAR(x)"),
            ("BAR", ["x"], "FOO(x)"),
        )
        result = evaluate_formula("=FOO(1)", self.sheet, udfs=udfs)
        self.assertEqual(result.computed_type, ComputedCellType.ERROR)
        self.assertEqual(result.error_code, "#REF!")

    def test_three_way_mutual_recursion_returns_ref_error(self):
        """A → B → C → A three-way cycle should return #REF!."""
        udfs = _make_udfs(
            ("A", ["x"], "B(x)"),
            ("B", ["x"], "C(x)"),
            ("C", ["x"], "A(x)"),
        )
        result = evaluate_formula("=A(1)", self.sheet, udfs=udfs)
        self.assertEqual(result.computed_type, ComputedCellType.ERROR)
        self.assertEqual(result.error_code, "#REF!")

    def test_division_by_zero_in_udf(self):
        """UDF expression dividing by zero should return #DIV/0!."""
        udfs = _make_udfs(("DIVZ", ["x"], "x / 0"))
        result = evaluate_formula("=DIVZ(10)", self.sheet, udfs=udfs)
        self.assertEqual(result.computed_type, ComputedCellType.ERROR)
        self.assertEqual(result.error_code, "#DIV/0!")

    def test_no_udfs_passed_returns_ref_error(self):
        """evaluate_formula with udfs=None should not resolve UDF names."""
        result = evaluate_formula("=MYROAS(100, 5)", self.sheet, udfs=None)
        self.assertEqual(result.computed_type, ComputedCellType.ERROR)
        self.assertEqual(result.error_code, "#REF!")

    def test_recursive_check_does_not_block_similar_name(self):
        """Function named AVG must not be blocked by expression containing AVERAGE."""
        udfs = _make_udfs(("AVG", ["data"], "AVERAGE(data)"))
        self._set_cell(0, 0, 10)
        self._set_cell(1, 0, 20)
        result = evaluate_formula("=AVG(A1:A2)", self.sheet, udfs=udfs)
        self.assertEqual(result.computed_type, ComputedCellType.NUMBER)
        self.assertEqual(result.computed_number, Decimal("15"))
