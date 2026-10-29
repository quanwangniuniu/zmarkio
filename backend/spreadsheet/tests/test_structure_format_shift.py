import uuid

from django.test import TestCase

from spreadsheet.models import (
    Cell,
    CellValueType,
    SheetColumn,
    SheetRow,
    SpreadsheetCellFormat,
    SpreadsheetHighlight,
    SpreadsheetHighlightScope,
    ComputedCellType
)
from spreadsheet.services import SheetService
from spreadsheet.tests.test_services import (
    create_test_organization,
    create_test_project,
    create_test_sheet,
    create_test_spreadsheet,
    create_test_user,
)


class TestStructureDecorationShift(TestCase):
    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.user = create_test_user(
            username=f"shift_{suffix}",
            email=f"shift_{suffix}@test.com",
        )
        organization = create_test_organization(name=f"Shift Org {suffix}")
        project = create_test_project(organization, owner=self.user)
        spreadsheet = create_test_spreadsheet(project)
        self.sheet = create_test_sheet(spreadsheet)
        self.spreadsheet = spreadsheet
        self.rows = [
            SheetRow.objects.create(sheet=self.sheet, position=i)
            for i in range(4)
        ]
        self.cols = [
            SheetColumn.objects.create(
                sheet=self.sheet,
                position=i,
                name=SheetService._generate_column_name(i),
            )
            for i in range(4)
        ]

    def _value(self, row_index, column_index, raw):
        return Cell.objects.create(
            sheet=self.sheet,
            row=self.rows[row_index],
            column=self.cols[column_index],
            value_type=CellValueType.STRING,
            raw_input=raw,
            string_value=raw,
        )

    def _format(self, row_index, column_index):
        return SpreadsheetCellFormat.objects.create(
            sheet=self.sheet,
            row_index=row_index,
            column_index=column_index,
            bold=True,
        )

    def _highlight(self, scope, row_index, col_index, color):
        return SpreadsheetHighlight.objects.create(
            spreadsheet=self.spreadsheet,
            sheet=self.sheet,
            scope=scope,
            row_index=row_index,
            col_index=col_index,
            color=color,
        )

    def _operation(self, result):
        from spreadsheet.models import SheetStructureOperation
        return SheetStructureOperation.objects.get(id=result['operation_id'])

    def _formula(self, row_index, column_index, raw):
        return Cell.objects.create(
            sheet=self.sheet,
            row=self.rows[row_index],
            column=self.cols[column_index],
            value_type=CellValueType.FORMULA,
            raw_input=raw,
            formula_value=raw,
        )

    def test_delete_referenced_row_undo_and_redo(self):
        from spreadsheet.services import CellService

        number = Cell.objects.create(
            sheet=self.sheet,
            row=self.rows[1],
            column=self.cols[0],
            value_type=CellValueType.NUMBER,
            raw_input='5',
            number_value=5,
        )
        formula = self._formula(0, 0, '=A2')
        CellService._recalculate_formula_cells([formula, number])

        deleted = SheetService.delete_rows(self.sheet, position=1, count=1, created_by=self.user)
        operation = self._operation(deleted)
        formula.refresh_from_db()
        self.assertEqual(formula.raw_input, '=#REF!')
        self.assertEqual(formula.formula_value, '=#REF!')
        self.assertEqual(formula.computed_type, ComputedCellType.ERROR)
        self.assertIn(str(formula.id), operation.formula_rewrites)

        SheetService.revert_structure_operation(self.sheet, operation)
        formula.refresh_from_db()
        self.assertEqual(formula.raw_input, '=A2')
        self.assertEqual(formula.formula_value, '=A2')
        self.assertEqual(formula.computed_number, 5)

        redone = SheetService.delete_rows(self.sheet, position=1, count=1, created_by=self.user)
        formula.refresh_from_db()
        self.assertEqual(formula.raw_input, '=#REF!')
        self.assertEqual(formula.formula_value, '=#REF!')
        self.assertIn(str(formula.id), self._operation(redone).formula_rewrites)

    def test_delete_referenced_column_undo_and_redo(self):
        formula = self._formula(0, 0, '=B1')
        deleted = SheetService.delete_columns(self.sheet, position=1, count=1, created_by=self.user)
        operation = self._operation(deleted)
        formula.refresh_from_db()
        self.assertEqual(formula.raw_input, '=#REF!')
        self.assertEqual(formula.formula_value, '=#REF!')

        SheetService.revert_structure_operation(self.sheet, operation)
        formula.refresh_from_db()
        self.assertEqual(formula.raw_input, '=B1')
        self.assertEqual(formula.formula_value, '=B1')

        SheetService.delete_columns(self.sheet, position=1, count=1, created_by=self.user)
        formula.refresh_from_db()
        self.assertEqual(formula.raw_input, '=#REF!')
        self.assertEqual(formula.formula_value, '=#REF!')

    def test_insert_row_and_column_undo_restores_shifted_formulas(self):
        row_formula = self._formula(0, 0, '=A3')
        inserted_row = self._operation(
            SheetService.insert_rows(self.sheet, position=1, count=1, created_by=self.user)
        )
        row_formula.refresh_from_db()
        self.assertEqual(row_formula.raw_input, '=A4')
        SheetService.revert_structure_operation(self.sheet, inserted_row)
        row_formula.refresh_from_db()
        self.assertEqual(row_formula.raw_input, '=A3')
        self.assertEqual(row_formula.formula_value, '=A3')

        column_formula = self._formula(0, 1, '=C1')
        inserted_column = self._operation(
            SheetService.insert_columns(self.sheet, position=1, count=1, created_by=self.user)
        )
        column_formula.refresh_from_db()
        self.assertEqual(column_formula.raw_input, '=D1')
        SheetService.revert_structure_operation(self.sheet, inserted_column)
        column_formula.refresh_from_db()
        self.assertEqual(column_formula.raw_input, '=C1')
        self.assertEqual(column_formula.formula_value, '=C1')

    def test_two_operations_undo_twice_then_redo_twice(self):
        formula = self._formula(0, 0, '=B2')
        first = self._operation(
            SheetService.insert_columns(self.sheet, position=1, count=1, created_by=self.user)
        )
        second = self._operation(
            SheetService.insert_rows(self.sheet, position=1, count=1, created_by=self.user)
        )
        formula.refresh_from_db()
        self.assertEqual(formula.raw_input, '=C3')

        SheetService.revert_structure_operation(self.sheet, second)
        formula.refresh_from_db()
        self.assertEqual(formula.raw_input, '=C2')
        SheetService.revert_structure_operation(self.sheet, first)
        formula.refresh_from_db()
        self.assertEqual(formula.raw_input, '=B2')

        SheetService.insert_columns(self.sheet, position=1, count=1, created_by=self.user)
        formula.refresh_from_db()
        self.assertEqual(formula.raw_input, '=C2')
        SheetService.insert_rows(self.sheet, position=1, count=1, created_by=self.user)
        formula.refresh_from_db()
        self.assertEqual(formula.raw_input, '=C3')
        self.assertEqual(formula.formula_value, '=C3')

    def test_undo_keeps_a_formula_edited_after_deletion(self):
        formula = self._formula(0, 0, '=C1')
        deleted = self._operation(
            SheetService.delete_columns(self.sheet, position=1, count=1, created_by=self.user)
        )
        formula.refresh_from_db()
        self.assertEqual(formula.raw_input, '=B1')
        formula.raw_input = '=B9'
        formula.formula_value = '=B9'
        formula.save(update_fields=['raw_input', 'formula_value'])

        SheetService.revert_structure_operation(self.sheet, deleted)
        formula.refresh_from_db()
        self.assertEqual(formula.raw_input, '=B9')
        self.assertEqual(formula.formula_value, '=B9')

    def test_undo_does_not_rewrite_a_formula_entered_after_an_empty_snapshot(self):
        deleted = self._operation(
            SheetService.delete_rows(self.sheet, position=1, count=1, created_by=self.user)
        )
        self.assertEqual(deleted.formula_rewrites, {})
        formula = self._formula(0, 0, '=A3')
        SheetService.revert_structure_operation(self.sheet, deleted)
        formula.refresh_from_db()
        self.assertEqual(formula.raw_input, '=A3')
        self.assertEqual(formula.formula_value, '=A3')

    def test_legacy_null_snapshot_cannot_restore_a_ref_error(self):
        formula = self._formula(0, 0, '=A2')
        deleted = self._operation(
            SheetService.delete_rows(self.sheet, position=1, count=1, created_by=self.user)
        )
        deleted.formula_rewrites = None
        deleted.save(update_fields=['formula_rewrites'])
        SheetService.revert_structure_operation(self.sheet, deleted)
        formula.refresh_from_db()
        self.assertEqual(formula.raw_input, '=#REF!')
        self.assertEqual(formula.formula_value, '=#REF!')

    def test_undo_restores_formatting_and_formula_together(self):
        self._format(1, 0)
        self._highlight(SpreadsheetHighlightScope.ROW, 1, 0, '#BFDBFE')
        formula = self._formula(0, 0, '=A2')
        deleted = self._operation(
            SheetService.delete_rows(self.sheet, position=1, count=1, created_by=self.user)
        )
        SheetService.revert_structure_operation(self.sheet, deleted)
        formula.refresh_from_db()
        self.assertEqual(formula.raw_input, '=A2')
        self.assertTrue(
            SpreadsheetCellFormat.objects.filter(
                sheet=self.sheet, row_index=1, column_index=0, bold=True,
            ).exists()
        )
        self.assertEqual(
            SpreadsheetHighlight.objects.get(
                sheet=self.sheet,
                scope=SpreadsheetHighlightScope.ROW,
                row_index=1,
                col_index=0,
            ).color,
            '#BFDBFE',
        )


    def test_insert_column_moves_value_format_and_highlights(self):
        value = self._value(1, 2, "hello")
        moved = self._format(1, 2)
        earlier = self._format(1, 0)
        cell_highlight = self._highlight(SpreadsheetHighlightScope.CELL, 1, 2, "#FDE047")
        column_highlight = self._highlight(SpreadsheetHighlightScope.COLUMN, 0, 2, "#BBF7D0")
        row_highlight = self._highlight(SpreadsheetHighlightScope.ROW, 1, 0, "#BFDBFE")

        SheetService.insert_columns(self.sheet, position=2, count=1, created_by=self.user)

        value.refresh_from_db()
        value.column.refresh_from_db()
        moved.refresh_from_db()
        earlier.refresh_from_db()
        cell_highlight.refresh_from_db()
        column_highlight.refresh_from_db()
        row_highlight.refresh_from_db()

        self.assertEqual(value.column_id, self.cols[2].id)
        self.assertEqual(value.column.position, 3)
        self.assertEqual((moved.row_index, moved.column_index), (1, 3))
        self.assertEqual((earlier.row_index, earlier.column_index), (1, 0))
        self.assertEqual((cell_highlight.row_index, cell_highlight.col_index), (1, 3))
        self.assertEqual((column_highlight.row_index, column_highlight.col_index), (0, 3))
        self.assertEqual((row_highlight.row_index, row_highlight.col_index), (1, 0))

    def test_delete_column_before_formatted_cell_shifts_left(self):
        value = self._value(1, 2, "hello")
        moved = self._format(1, 2)
        earlier = self._format(1, 0)
        cell_highlight = self._highlight(SpreadsheetHighlightScope.CELL, 1, 2, "#FDE047")

        SheetService.delete_columns(self.sheet, position=1, count=1, created_by=self.user)

        value.refresh_from_db()
        value.column.refresh_from_db()
        moved.refresh_from_db()
        earlier.refresh_from_db()
        cell_highlight.refresh_from_db()

        self.assertEqual(value.column_id, self.cols[2].id)
        self.assertEqual(value.column.position, 1)
        self.assertEqual((moved.row_index, moved.column_index), (1, 1))
        self.assertEqual(earlier.column_index, 0)
        self.assertEqual(cell_highlight.col_index, 1)

    def test_delete_formatted_column_removes_its_metadata(self):
        removed = self._format(1, 2)
        removed_highlight = self._highlight(SpreadsheetHighlightScope.CELL, 1, 2, "#FDE047")
        removed_column = self._highlight(SpreadsheetHighlightScope.COLUMN, 0, 2, "#BBF7D0")
        later = self._format(1, 3)
        earlier = self._format(1, 0)

        SheetService.delete_columns(self.sheet, position=2, count=1, created_by=self.user)

        self.assertFalse(SpreadsheetCellFormat.objects.filter(pk=removed.pk).exists())
        self.assertFalse(SpreadsheetHighlight.objects.filter(pk=removed_highlight.pk).exists())
        self.assertFalse(SpreadsheetHighlight.objects.filter(pk=removed_column.pk).exists())
        later.refresh_from_db()
        earlier.refresh_from_db()
        self.assertEqual(later.column_index, 2)
        self.assertEqual(earlier.column_index, 0)

    def test_insert_row_moves_value_format_and_highlights(self):
        value = self._value(2, 1, "hello")
        moved = self._format(2, 1)
        earlier = self._format(0, 1)
        cell_highlight = self._highlight(SpreadsheetHighlightScope.CELL, 2, 1, "#FDE047")
        row_highlight = self._highlight(SpreadsheetHighlightScope.ROW, 2, 0, "#BFDBFE")
        column_highlight = self._highlight(SpreadsheetHighlightScope.COLUMN, 0, 1, "#BBF7D0")

        SheetService.insert_rows(self.sheet, position=2, count=1, created_by=self.user)

        value.refresh_from_db()
        value.row.refresh_from_db()
        moved.refresh_from_db()
        earlier.refresh_from_db()
        cell_highlight.refresh_from_db()
        row_highlight.refresh_from_db()
        column_highlight.refresh_from_db()

        self.assertEqual(value.row_id, self.rows[2].id)
        self.assertEqual(value.row.position, 3)
        self.assertEqual((moved.row_index, moved.column_index), (3, 1))
        self.assertEqual(earlier.row_index, 0)
        self.assertEqual((cell_highlight.row_index, cell_highlight.col_index), (3, 1))
        self.assertEqual((row_highlight.row_index, row_highlight.col_index), (3, 0))
        self.assertEqual((column_highlight.row_index, column_highlight.col_index), (0, 1))

    def test_delete_row_before_formatted_cell_shifts_up(self):
        value = self._value(2, 1, "hello")
        moved = self._format(2, 1)
        earlier = self._format(0, 1)
        cell_highlight = self._highlight(SpreadsheetHighlightScope.CELL, 2, 1, "#FDE047")

        SheetService.delete_rows(self.sheet, position=1, count=1, created_by=self.user)

        value.refresh_from_db()
        value.row.refresh_from_db()
        moved.refresh_from_db()
        earlier.refresh_from_db()
        cell_highlight.refresh_from_db()

        self.assertEqual(value.row_id, self.rows[2].id)
        self.assertEqual(value.row.position, 1)
        self.assertEqual((moved.row_index, moved.column_index), (1, 1))
        self.assertEqual(earlier.row_index, 0)
        self.assertEqual(cell_highlight.row_index, 1)

    def test_delete_formatted_row_removes_its_metadata(self):
        removed = self._format(2, 1)
        removed_highlight = self._highlight(SpreadsheetHighlightScope.CELL, 2, 1, "#FDE047")
        removed_row = self._highlight(SpreadsheetHighlightScope.ROW, 2, 0, "#BFDBFE")
        later = self._format(3, 1)
        earlier = self._format(0, 1)

        SheetService.delete_rows(self.sheet, position=2, count=1, created_by=self.user)

        self.assertFalse(SpreadsheetCellFormat.objects.filter(pk=removed.pk).exists())
        self.assertFalse(SpreadsheetHighlight.objects.filter(pk=removed_highlight.pk).exists())
        self.assertFalse(SpreadsheetHighlight.objects.filter(pk=removed_row.pk).exists())
        later.refresh_from_db()
        earlier.refresh_from_db()
        self.assertEqual(later.row_index, 2)
        self.assertEqual(earlier.row_index, 0)

    def test_undo_multi_row_and_column_delete_restores_each_record(self):
        self._format(1, 0)
        self._format(2, 1)
        self._highlight(SpreadsheetHighlightScope.ROW, 1, 0, '#BFDBFE')
        self._highlight(SpreadsheetHighlightScope.ROW, 2, 0, '#FDE047')
        kept_row = self._format(3, 0)
        row_delete = self._operation(
            SheetService.delete_rows(self.sheet, position=1, count=2, created_by=self.user)
        )
        SheetService.revert_structure_operation(self.sheet, row_delete)
        kept_row.refresh_from_db()
        self.assertEqual(kept_row.row_index, 3)
        self.assertTrue(
            SpreadsheetCellFormat.objects.filter(
                sheet=self.sheet, row_index=1, column_index=0, bold=True,
            ).exists()
        )
        self.assertTrue(
            SpreadsheetCellFormat.objects.filter(
                sheet=self.sheet, row_index=2, column_index=1, bold=True,
            ).exists()
        )
        self.assertEqual(
            SpreadsheetHighlight.objects.get(
                sheet=self.sheet, scope=SpreadsheetHighlightScope.ROW, row_index=1, col_index=0,
            ).color,
            '#BFDBFE',
        )
        self.assertEqual(
            SpreadsheetHighlight.objects.get(
                sheet=self.sheet, scope=SpreadsheetHighlightScope.ROW, row_index=2, col_index=0,
            ).color,
            '#FDE047',
        )

        self._format(0, 1)
        self._highlight(SpreadsheetHighlightScope.COLUMN, 0, 1, '#BBF7D0')
        self._highlight(SpreadsheetHighlightScope.COLUMN, 0, 2, '#FDE047')
        kept_column = self._format(0, 3)
        column_delete = self._operation(
            SheetService.delete_columns(self.sheet, position=1, count=2, created_by=self.user)
        )
        SheetService.revert_structure_operation(self.sheet, column_delete)
        kept_column.refresh_from_db()
        self.assertEqual(kept_column.column_index, 3)
        self.assertTrue(
            SpreadsheetCellFormat.objects.filter(
                sheet=self.sheet, row_index=0, column_index=1, bold=True,
            ).exists()
        )
        self.assertEqual(
            SpreadsheetHighlight.objects.get(
                sheet=self.sheet, scope=SpreadsheetHighlightScope.COLUMN, row_index=0, col_index=1,
            ).color,
            '#BBF7D0',
        )
        self.assertEqual(
            SpreadsheetHighlight.objects.get(
                sheet=self.sheet, scope=SpreadsheetHighlightScope.COLUMN, row_index=0, col_index=2,
            ).color,
            '#FDE047',
        )