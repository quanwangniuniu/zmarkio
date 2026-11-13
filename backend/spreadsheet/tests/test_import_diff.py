from django.test import TestCase
from spreadsheet.services import SheetService
from spreadsheet.import_service import (
    _id_prefix_status,
    build_import_baseline,
    diff_cell_highlights,
    diff_cells,
    diff_formats,
    diff_sheet,
)
from spreadsheet.models import (
    Cell,
    SheetColumn,
    SheetRow,
    SheetStructureOperation,
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


def _source(baseline, sheet=None):
    class Source:
        pass
    source = Source()
    source.baseline = baseline
    source.sheet = sheet
    return source


class TestImportDiff(TestCase):
    def test_id_prefix(self):
        self.assertEqual(_id_prefix_status([501, 502, 503], [501, 502, 503]), 'same')
        self.assertEqual(_id_prefix_status([501, 502, 503], [501, 502, 503, 900]), 'append')
        self.assertEqual(_id_prefix_status([501, 502, 503], [900, 501, 502, 503]), 'unsafe')
        self.assertEqual(_id_prefix_status([501, 502, 503], [501, 900, 502, 503]), 'unsafe')
        self.assertEqual(_id_prefix_status([501, 502, 503], [501, 503]), 'unsafe')
        self.assertEqual(_id_prefix_status([501, 502, 503], [503, 502, 501]), 'unsafe')

    def test_cell_changes(self):
        changes, outside = diff_cells(
            {'0,0': {'raw_input': '100'}, '0,1': {'raw_input': '=A1*2'}},
            {'0,0': {'raw_input': '200'}, '1,0': {'raw_input': '9'}},
            2,
            2,
        )
        self.assertFalse(outside)
        self.assertEqual(changes['0,0'], {'raw_input': '200'})
        self.assertEqual(changes['0,1'], {'raw_input': None})
        self.assertEqual(changes['1,0'], {'raw_input': '9'})

        formula, _ = diff_cells(
            {'0,0': {'raw_input': '=A1*2'}},
            {'0,0': {'raw_input': '5'}},
            1,
            1,
        )
        self.assertEqual(formula['0,0'], {'raw_input': '5'})

        unchanged, _ = diff_cells(
            {'0,0': {'raw_input': '100'}},
            {'0,0': {'raw_input': '100'}},
            1,
            1,
        )
        self.assertEqual(unchanged, {})

        outside_changes, outside = diff_cells({}, {'3,0': {'raw_input': 'new'}}, 3, 2)
        self.assertTrue(outside)
        self.assertEqual(outside_changes, {})

    def test_format_returns_only_changed_fields(self):
        changes, outside = diff_formats(
            {'0,0': {'bold': False, 'font_size': 12, 'text_color': '#112233'}},
            {'0,0': {'bold': True, 'font_size': 12, 'text_color': None}},
            1,
            1,
        )
        self.assertFalse(outside)
        self.assertEqual(changes['0,0'], {'bold': True, 'text_color': None})

        same, _ = diff_formats({}, {'0,0': {'bold': False, 'font_size': None}}, 1, 1)
        self.assertEqual(same, {})

        number_change, _ = diff_formats(
            {'0,0': {'number_format': {'type': 'CURRENCY', 'currency_code': 'USD', 'decimal_places': 2}}},
            {'0,0': {'number_format': {'type': 'PERCENT', 'decimal_places': 0}}},
            1,
            1,
        )
        self.assertEqual(
            number_change['0,0']['number_format'],
            {'type': 'PERCENT', 'decimal_places': 0},
        )

    def test_cell_highlights(self):
        changes, outside = diff_cell_highlights(
            {'cells': {'0,0': '#111111', '0,1': '#222222'}},
            {'cells': {'0,0': '#111111', '0,1': '#333333', '1,0': '#444444'}},
            2,
            2,
        )
        self.assertFalse(outside)
        self.assertNotIn('0,0', changes)
        self.assertEqual(changes['0,1'], '#333333')
        self.assertEqual(changes['1,0'], '#444444')

        cleared, _ = diff_cell_highlights(
            {'cells': {'0,0': '#111111'}},
            {'cells': {}},
            1,
            1,
        )
        self.assertEqual(cleared['0,0'], None)

    def test_missing_and_bad_baseline(self):
        missing = diff_sheet(_source(None))
        self.assertEqual(missing['rebuild_reasons'], ['missing_baseline'])
        self.assertTrue(missing['requires_rebuild'])

        bad = diff_sheet(_source({'version': 2}))
        self.assertEqual(bad['rebuild_reasons'], ['unsupported_baseline_version'])

    def test_old_snapshot_values_compare_the_same_way(self):
        stored = {'0,0': {'text_color': ' #112233 ', 'bold': 0, 'font_size': True}}
        live = {'0,0': {'text_color': '#112233', 'bold': False, 'font_size': None}}
        changes, outside = diff_formats(stored, live, 1, 1)
        self.assertFalse(outside)
        self.assertEqual(changes, {})

    def test_rebuild_reason_is_logged(self):
        with self.assertLogs('spreadsheet.import_service', level='INFO') as logs:
            result = diff_sheet(_source(None))
        self.assertTrue(result['requires_rebuild'])
        self.assertIn('missing_baseline', logs.output[0])



class TestLiveSheetDiff(TestCase):
    def _sheet(self, label, row_count, column_count):
        owner = create_test_user(f'diff-{label}', f'diff-{label}@example.com')
        organization = create_test_organization()
        project = create_test_project(organization, owner=owner)
        spreadsheet = create_test_spreadsheet(project)
        sheet = create_test_sheet(spreadsheet)
        rows = [
            SheetRow.objects.create(sheet=sheet, position=index)
            for index in range(row_count)
        ]
        columns = [
            SheetColumn.objects.create(sheet=sheet, position=index, name=f'C{index}')
            for index in range(column_count)
        ]
        return sheet, rows, columns

    def _cell(self, sheet, row, column, raw):
        return Cell.objects.create(sheet=sheet, row=row, column=column, raw_input=raw)

    def test_live_sheet_no_edit_then_value_change(self):
        sheet, rows, columns = self._sheet('live', 1, 1)
        cell = self._cell(sheet, rows[0], columns[0], '100')
        SpreadsheetCellFormat.objects.create(
            sheet=sheet, row_index=0, column_index=0, bold=False, font_size=12,
        )

        baseline = build_import_baseline(sheet)
        unchanged = diff_sheet(_source(baseline, sheet))
        self.assertFalse(unchanged['requires_rebuild'])
        self.assertFalse(unchanged['has_changes'])
        self.assertEqual(unchanged['cells'], {})

        cell.raw_input = '200'
        cell.save(update_fields=['raw_input'])
        changed = diff_sheet(_source(baseline, sheet))
        self.assertEqual(changed['cells']['0,0'], {'raw_input': '200'})
        self.assertTrue(changed['has_changes'])
        self.assertFalse(changed['requires_rebuild'])

    def test_row_highlight_forces_rebuild(self):
        sheet, _rows, _columns = self._sheet('row-highlight', 1, 1)
        highlight = SpreadsheetHighlight.objects.create(
            spreadsheet=sheet.spreadsheet,
            sheet=sheet,
            scope='ROW',
            row_index=0,
            col_index=0,
            color='#111111',
        )
        baseline = build_import_baseline(sheet)
        highlight.color = '#222222'
        highlight.save(update_fields=['color'])
        result = diff_sheet(_source(baseline, sheet))
        self.assertTrue(result['requires_rebuild'])
        self.assertIn('row_highlight_changed', result['rebuild_reasons'])

    def test_appended_cell_forces_rebuild(self):
        sheet, rows, columns = self._sheet('appended', 1, 1)
        self._cell(sheet, rows[0], columns[0], '100')
        baseline = build_import_baseline(sheet)
        extra = SheetRow.objects.create(sheet=sheet, position=1)
        self._cell(sheet, extra, columns[0], 'new')
        result = diff_sheet(_source(baseline, sheet))
        self.assertTrue(result['requires_rebuild'])
        self.assertIn('appended_region_change', result['rebuild_reasons'])

    def test_resize_append_stays_patchable(self):
        sheet, rows, columns = self._sheet('resize-append', 1, 1)
        self._cell(sheet, rows[0], columns[0], 'old')
        baseline = build_import_baseline(sheet)
        SheetColumn.objects.create(sheet=sheet, position=1, name='C1')

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertFalse(result['has_changes'])
        self.assertEqual(result['cells'], {})

    def test_column_insert_rebuilds(self):
        sheet, rows, columns = self._sheet('col-insert', 1, 3)
        self._cell(sheet, rows[0], columns[1], 'hello')
        baseline = build_import_baseline(sheet)
        SheetService.insert_columns(sheet, position=1, count=1)

        result = diff_sheet(_source(baseline, sheet))
        self.assertTrue(result['requires_rebuild'])
        self.assertIn('column_structure_changed', result['rebuild_reasons'])
        self.assertEqual(result['cells'], {})

    def test_row_insert_rebuilds(self):
        sheet, rows, columns = self._sheet('row-insert', 3, 1)
        self._cell(sheet, rows[1], columns[0], 'hello')
        baseline = build_import_baseline(sheet)
        SheetService.insert_rows(sheet, position=1, count=1)

        result = diff_sheet(_source(baseline, sheet))
        self.assertTrue(result['requires_rebuild'])
        self.assertIn('row_structure_changed', result['rebuild_reasons'])

    def test_row_delete_rebuilds(self):
        sheet, rows, columns = self._sheet('row-delete', 3, 1)
        self._cell(sheet, rows[0], columns[0], 'drop')
        self._cell(sheet, rows[2], columns[0], 'keep')
        baseline = build_import_baseline(sheet)
        SheetService.delete_rows(sheet, position=0, count=1)

        result = diff_sheet(_source(baseline, sheet))
        self.assertTrue(result['requires_rebuild'])
        self.assertIn('row_structure_changed', result['rebuild_reasons'])

    def test_column_delete_rebuilds(self):
        sheet, rows, columns = self._sheet('col-delete', 1, 3)
        baseline = build_import_baseline(sheet)
        SheetService.delete_columns(sheet, position=2, count=1)

        result = diff_sheet(_source(baseline, sheet))
        self.assertTrue(result['requires_rebuild'])
        self.assertIn('column_structure_changed', result['rebuild_reasons'])

    def test_end_column_insert_with_a_value_rebuilds(self):
        sheet, rows, columns = self._sheet('end-insert', 1, 2)
        baseline = build_import_baseline(sheet)
        SheetService.insert_columns(sheet, position=2, count=1)
        inserted = SheetColumn.objects.get(sheet=sheet, position=2, is_deleted=False)
        self._cell(sheet, rows[0], inserted, 'tail')

        result = diff_sheet(_source(baseline, sheet))
        self.assertTrue(result['requires_rebuild'])
        self.assertIn('appended_region_change', result['rebuild_reasons'])

    def test_empty_end_column_insert_stays_patchable(self):
        sheet, rows, columns = self._sheet('empty-end-insert', 1, 2)
        self._cell(sheet, rows[0], columns[0], 'keep')
        baseline = build_import_baseline(sheet)
        SheetService.insert_columns(sheet, position=2, count=1)

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertFalse(result['has_changes'])

    def test_insert_then_delete_keeps_original_coordinates(self):
        sheet, rows, columns = self._sheet('cancel', 1, 1)
        cell = self._cell(sheet, rows[0], columns[0], 'hello')
        baseline = build_import_baseline(sheet)
        SheetService.insert_rows(sheet, position=0, count=1)
        SheetService.delete_rows(sheet, position=0, count=1)
        cell.raw_input = 'edited'
        cell.save(update_fields=['raw_input'])

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertEqual(result['cells'], {'0,0': {'raw_input': 'edited'}})

    def test_reverted_insert_is_patchable(self):
        sheet, rows, columns = self._sheet('revert', 1, 1)
        cell = self._cell(sheet, rows[0], columns[0], 'hello')
        baseline = build_import_baseline(sheet)
        created = SheetService.insert_rows(sheet, position=0, count=1)
        operation = SheetStructureOperation.objects.get(id=created['operation_id'])
        SheetService.revert_structure_operation(sheet, operation)
        cell.raw_input = 'edited'
        cell.save(update_fields=['raw_input'])

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertEqual(result['cells'], {'0,0': {'raw_input': 'edited'}})

    def test_reverted_delete_is_patchable(self):
        sheet, rows, columns = self._sheet('revert-delete', 3, 1)
        self._cell(sheet, rows[0], columns[0], 'a')
        self._cell(sheet, rows[1], columns[0], 'b')
        self._cell(sheet, rows[2], columns[0], 'c')
        baseline = build_import_baseline(sheet)
        deleted = SheetService.delete_rows(sheet, position=1, count=1)
        operation = SheetStructureOperation.objects.get(id=deleted['operation_id'])
        SheetService.revert_structure_operation(sheet, operation)

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertFalse(result['has_changes'])
        self.assertEqual(result['cells'], {})

    def test_redo_after_undo_rebuilds(self):
        sheet, rows, columns = self._sheet('redo', 1, 1)
        self._cell(sheet, rows[0], columns[0], 'hello')
        baseline = build_import_baseline(sheet)
        created = SheetService.insert_rows(sheet, position=0, count=1)
        operation = SheetStructureOperation.objects.get(id=created['operation_id'])
        SheetService.revert_structure_operation(sheet, operation)
        SheetService.insert_rows(sheet, position=0, count=1)

        result = diff_sheet(_source(baseline, sheet))
        self.assertTrue(result['requires_rebuild'])
        self.assertIn('row_structure_changed', result['rebuild_reasons'])

    def test_sort_still_rebuilds(self):
        sheet, rows, columns = self._sheet('sort', 2, 1)
        self._cell(sheet, rows[0], columns[0], 'b')
        self._cell(sheet, rows[1], columns[0], 'a')
        baseline = build_import_baseline(sheet)
        SheetService.sort_rows(sheet, column_position=0, direction='asc', has_header=False)

        result = diff_sheet(_source(baseline, sheet))
        self.assertTrue(result['requires_rebuild'])
        self.assertIn('row_structure_changed', result['rebuild_reasons'])
        self.assertEqual(result['cells'], {})

    def test_column_inserted_in_the_middle_after_delete_rebuilds(self):
        sheet, rows, columns = self._sheet('mid-extra', 1, 3)
        baseline = build_import_baseline(sheet)
        SheetService.delete_columns(sheet, position=1, count=1)
        extra = SheetColumn.objects.create(sheet=sheet, position=100, name='X')
        first = columns[0]
        first.position = 101
        first.save(update_fields=['position'])
        extra.position = 0
        extra.save(update_fields=['position'])
        first.position = 2
        first.save(update_fields=['position'])

        result = diff_sheet(_source(baseline, sheet))
        self.assertTrue(result['requires_rebuild'])
        self.assertIn('column_structure_changed', result['rebuild_reasons'])

    
    def test_default_format_row_is_not_stored(self):
        sheet, rows, columns = self._sheet('default-format', 1, 1)
        SpreadsheetCellFormat.objects.create(sheet=sheet, row_index=0, column_index=0)
        baseline = build_import_baseline(sheet)
        self.assertEqual(baseline['formats'], {})
