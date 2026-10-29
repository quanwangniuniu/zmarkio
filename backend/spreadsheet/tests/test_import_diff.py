from django.test import TestCase
from spreadsheet.import_baseline import build_import_baseline
from spreadsheet.services import SheetService
from spreadsheet.import_diff import (
    _id_prefix_status,
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
        self.assertEqual(missing['cells'], {})

        bad = diff_sheet(_source({'version': 2}))
        self.assertEqual(bad['rebuild_reasons'], ['unsupported_baseline_version'])

    def test_live_sheet_no_edit_then_value_change(self):
        owner = create_test_user('diff-owner', 'diff-owner@example.com')
        organization = create_test_organization()
        project = create_test_project(organization, owner=owner)
        spreadsheet = create_test_spreadsheet(project)
        sheet = create_test_sheet(spreadsheet)
        row = SheetRow.objects.create(sheet=sheet, position=0)
        column = SheetColumn.objects.create(sheet=sheet, position=0, name='A')
        Cell.objects.create(sheet=sheet, row=row, column=column, raw_input='100')
        SpreadsheetCellFormat.objects.create(
            sheet=sheet, row_index=0, column_index=0, bold=False, font_size=12,
        )

        baseline = build_import_baseline(sheet)
        unchanged = diff_sheet(_source(baseline, sheet))
        self.assertFalse(unchanged['requires_rebuild'])
        self.assertFalse(unchanged['has_changes'])
        self.assertEqual(unchanged['cells'], {})

        cell = Cell.objects.get(sheet=sheet)
        cell.raw_input = '200'
        cell.save(update_fields=['raw_input'])
        changed = diff_sheet(_source(baseline, sheet))
        self.assertEqual(changed['cells']['0,0'], {'raw_input': '200'})
        self.assertTrue(changed['has_changes'])
        self.assertFalse(changed['requires_rebuild'])

    def test_row_highlight_forces_rebuild(self):
        owner = create_test_user('diff-owner-2', 'diff-owner-2@example.com')
        organization = create_test_organization()
        project = create_test_project(organization, owner=owner)
        spreadsheet = create_test_spreadsheet(project)
        sheet = create_test_sheet(spreadsheet)
        SheetRow.objects.create(sheet=sheet, position=0)
        SheetColumn.objects.create(sheet=sheet, position=0, name='A')
        highlight = SpreadsheetHighlight.objects.create(
            spreadsheet=spreadsheet,
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
        self.assertTrue(result['structure_safe'])
        self.assertTrue(result['requires_rebuild'])
        self.assertIn('row_highlight_changed', result['rebuild_reasons'])
        self.assertEqual(result['cells'], {})
        self.assertEqual(result['highlights']['cells'], {})

    def test_appended_cell_forces_rebuild(self):
        owner = create_test_user('diff-owner-3', 'diff-owner-3@example.com')
        organization = create_test_organization()
        project = create_test_project(organization, owner=owner)
        spreadsheet = create_test_spreadsheet(project)
        sheet = create_test_sheet(spreadsheet)
        row = SheetRow.objects.create(sheet=sheet, position=0)
        column = SheetColumn.objects.create(sheet=sheet, position=0, name='A')
        Cell.objects.create(sheet=sheet, row=row, column=column, raw_input='100')
        baseline = build_import_baseline(sheet)
        extra = SheetRow.objects.create(sheet=sheet, position=1)
        Cell.objects.create(sheet=sheet, row=extra, column=column, raw_input='new')
        result = diff_sheet(_source(baseline, sheet))
        self.assertTrue(result['structure_safe'])
        self.assertTrue(result['requires_rebuild'])
        self.assertIn('appended_region_change', result['rebuild_reasons'])
        self.assertEqual(result['cells'], {})

class TestStructuralReplayDiff(TestCase):
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

    def test_column_insert_patches_final_coordinates(self):
        sheet, rows, columns = self._sheet('col-insert', 1, 3)
        cell = self._cell(sheet, rows[0], columns[1], 'hello')
        baseline = build_import_baseline(sheet)
        SheetService.insert_columns(sheet, position=1, count=1)
        cell.raw_input = 'goodbye'
        cell.save(update_fields=['raw_input'])
        SpreadsheetCellFormat.objects.create(
            sheet=sheet, row_index=0, column_index=2, bold=True,
        )

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertEqual(result['structural_operations'], [
            {'op_type': 'COL_INSERT', 'anchor_position': 1, 'count': 1},
        ])
        self.assertEqual(result['cells'], {'0,2': {'raw_input': 'goodbye'}})
        self.assertEqual(result['formats'], {'0,2': {'bold': True}})

    def test_row_delete_patches_final_coordinates(self):
        sheet, rows, columns = self._sheet('row-delete', 3, 1)
        self._cell(sheet, rows[0], columns[0], 'drop')
        kept = self._cell(sheet, rows[2], columns[0], 'keep')
        baseline = build_import_baseline(sheet)
        SheetService.delete_rows(sheet, position=0, count=1)
        kept.raw_input = 'edited'
        kept.save(update_fields=['raw_input'])

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertEqual(result['structural_operations'], [
            {'op_type': 'ROW_DELETE', 'anchor_position': 0, 'count': 1},
        ])
        self.assertEqual(result['cells'], {'1,0': {'raw_input': 'edited'}})

    def test_end_column_insert_is_replayed(self):
        sheet, rows, columns = self._sheet('end-insert', 1, 2)
        baseline = build_import_baseline(sheet)
        SheetService.insert_columns(sheet, position=2, count=1)
        inserted = SheetColumn.objects.get(sheet=sheet, position=2)
        self._cell(sheet, rows[0], inserted, 'tail')

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertEqual(result['structural_operations'], [
            {'op_type': 'COL_INSERT', 'anchor_position': 2, 'count': 1},
        ])
        self.assertEqual(result['cells'], {'0,2': {'raw_input': 'tail'}})

    def test_resize_append_stays_on_the_append_path(self):
        sheet, rows, columns = self._sheet('resize-append', 1, 1)
        self._cell(sheet, rows[0], columns[0], 'old')
        baseline = build_import_baseline(sheet)
        SheetColumn.objects.create(sheet=sheet, position=1, name='C1')

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertEqual(result['structural_operations'], [])
        self.assertEqual(result['cells'], {})

    def test_end_insert_plus_extra_column_rebuilds(self):
        sheet, rows, columns = self._sheet('mixed-append', 1, 1)
        baseline = build_import_baseline(sheet)
        SheetService.insert_columns(sheet, position=1, count=1)
        SheetColumn.objects.create(sheet=sheet, position=2, name='extra')

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertEqual(result['structural_operations'], [
            {'op_type': 'COL_INSERT', 'anchor_position': 1, 'count': 1},
        ])

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
        self.assertEqual(result['structural_operations'], [])
        self.assertEqual(result['cells'], {'0,0': {'raw_input': 'edited'}})

    def test_delete_then_new_insert_is_replayed(self):
        sheet, rows, columns = self._sheet('reinsert', 2, 1)
        self._cell(sheet, rows[0], columns[0], 'gone')
        kept = self._cell(sheet, rows[1], columns[0], 'kept')
        baseline = build_import_baseline(sheet)
        SheetService.delete_rows(sheet, position=0, count=1)
        SheetService.insert_rows(sheet, position=0, count=1)
        kept.raw_input = 'edited'
        kept.save(update_fields=['raw_input'])

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertEqual(result['structural_operations'], [
            {'op_type': 'ROW_DELETE', 'anchor_position': 0, 'count': 1},
            {'op_type': 'ROW_INSERT', 'anchor_position': 0, 'count': 1},
        ])
        self.assertEqual(result['cells'], {'1,0': {'raw_input': 'edited'}})

    def test_reverted_insert_is_ignored(self):
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
        self.assertEqual(result['structural_operations'], [])
        self.assertEqual(result['cells'], {'0,0': {'raw_input': 'edited'}})

    def test_missing_checkpoint_rebuilds(self):
        sheet, rows, columns = self._sheet('no-checkpoint', 1, 2)
        baseline = build_import_baseline(sheet)
        del baseline['structure_operation_checkpoint']
        SheetService.insert_columns(sheet, position=0, count=1)

        result = diff_sheet(_source(baseline, sheet))
        self.assertTrue(result['requires_rebuild'])
        self.assertIn('column_structure_changed', result['rebuild_reasons'])
        self.assertEqual(result['structural_operations'], [])
        self.assertEqual(result['cells'], {})

    def test_replay_mismatch_rebuilds(self):
        sheet, rows, columns = self._sheet('mismatch', 1, 3)
        baseline = build_import_baseline(sheet)
        SheetService.insert_columns(sheet, position=1, count=1)
        first = SheetColumn.objects.get(sheet=sheet, position=0)
        third = SheetColumn.objects.get(sheet=sheet, position=2)
        first.position = 1_000_000
        first.save(update_fields=['position'])
        third.position = 0
        third.save(update_fields=['position'])
        first.position = 2
        first.save(update_fields=['position'])

        result = diff_sheet(_source(baseline, sheet))
        self.assertTrue(result['requires_rebuild'])
        self.assertIn('column_structure_changed', result['rebuild_reasons'])
        self.assertEqual(result['structural_operations'], [])
        self.assertEqual(result['cells'], {})

    def test_sort_still_rebuilds(self):
        sheet, rows, columns = self._sheet('sort', 2, 1)
        self._cell(sheet, rows[0], columns[0], 'b')
        self._cell(sheet, rows[1], columns[0], 'a')
        baseline = build_import_baseline(sheet)
        SheetService.sort_rows(sheet, column_position=0, direction='asc', has_header=False)

        result = diff_sheet(_source(baseline, sheet))
        self.assertTrue(result['requires_rebuild'])
        self.assertIn('row_structure_changed', result['rebuild_reasons'])
        self.assertEqual(result['structural_operations'], [])
        self.assertEqual(result['cells'], {})
    
    def test_row_insert_and_column_delete_patch_final_coordinates(self):
        sheet, rows, columns = self._sheet('row-insert-col-delete', 2, 2)
        self._cell(sheet, rows[0], columns[0], 'drop')
        kept = self._cell(sheet, rows[1], columns[1], 'keep')
        baseline = build_import_baseline(sheet)
        SheetService.insert_rows(sheet, position=0, count=1)
        SheetService.delete_columns(sheet, position=0, count=1)
        kept.raw_input = 'edited'
        kept.save(update_fields=['raw_input'])

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertEqual(result['structural_operations'], [
            {'op_type': 'ROW_INSERT', 'anchor_position': 0, 'count': 1},
            {'op_type': 'COL_DELETE', 'anchor_position': 0, 'count': 1},
        ])
        self.assertEqual(result['cells'], {'2,0': {'raw_input': 'edited'}})

    def test_row_then_column_insert_applies_in_order(self):
        sheet, rows, columns = self._sheet('row-then-col', 2, 2)
        cell = self._cell(sheet, rows[1], columns[1], 'old')
        baseline = build_import_baseline(sheet)
        SheetService.insert_rows(sheet, position=0, count=1)
        SheetService.insert_columns(sheet, position=1, count=1)
        cell.raw_input = 'new'
        cell.save(update_fields=['raw_input'])

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertEqual(result['structural_operations'], [
            {'op_type': 'ROW_INSERT', 'anchor_position': 0, 'count': 1},
            {'op_type': 'COL_INSERT', 'anchor_position': 1, 'count': 1},
        ])
        self.assertEqual(result['cells'], {'2,2': {'raw_input': 'new'}})

    def test_shifted_format_and_highlight_are_not_edits(self):
        sheet, rows, columns = self._sheet('shift-style', 2, 2)
        self._cell(sheet, rows[1], columns[1], 'stay')
        SpreadsheetCellFormat.objects.create(
            sheet=sheet, row_index=1, column_index=1, bold=True,
        )
        SpreadsheetHighlight.objects.create(
            spreadsheet=sheet.spreadsheet,
            sheet=sheet,
            scope='CELL',
            row_index=1,
            col_index=1,
            color='#FFFF00',
        )
        SpreadsheetHighlight.objects.create(
            spreadsheet=sheet.spreadsheet,
            sheet=sheet,
            scope='ROW',
            row_index=1,
            col_index=0,
            color='#FFFF00',
        )
        SpreadsheetHighlight.objects.create(
            spreadsheet=sheet.spreadsheet,
            sheet=sheet,
            scope='COLUMN',
            row_index=0,
            col_index=1,
            color='#FFFF00',
        )
        baseline = build_import_baseline(sheet)
        SheetService.insert_columns(sheet, position=1, count=1)

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertFalse(result['has_changes'])
        self.assertEqual(result['structural_operations'], [
            {'op_type': 'COL_INSERT', 'anchor_position': 1, 'count': 1},
        ])
        self.assertEqual(result['cells'], {})
        self.assertEqual(result['formats'], {})
        self.assertEqual(result['highlights'], {'cells': {}})

    def test_cancelled_column_pair_leaves_the_row_insert(self):
        sheet, rows, columns = self._sheet('cancel-cols', 3, 2)
        cell = self._cell(sheet, rows[2], columns[0], 'old')
        baseline = build_import_baseline(sheet)
        SheetService.insert_columns(sheet, position=1, count=1)
        SheetService.delete_columns(sheet, position=1, count=1)
        SheetService.insert_rows(sheet, position=2, count=1)
        cell.raw_input = 'new'
        cell.save(update_fields=['raw_input'])

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertEqual(result['structural_operations'], [
            {'op_type': 'ROW_INSERT', 'anchor_position': 2, 'count': 1},
        ])
        self.assertEqual(result['cells'], {'3,0': {'raw_input': 'new'}})

    def test_cancelled_row_pair_leaves_the_column_insert(self):
        sheet, rows, columns = self._sheet('cancel-rows', 1, 2)
        cell = self._cell(sheet, rows[0], columns[1], 'old')
        baseline = build_import_baseline(sheet)
        SheetService.insert_rows(sheet, position=0, count=1)
        SheetService.delete_rows(sheet, position=0, count=1)
        SheetService.insert_columns(sheet, position=1, count=1)
        cell.raw_input = 'new'
        cell.save(update_fields=['raw_input'])

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertEqual(result['structural_operations'], [
            {'op_type': 'COL_INSERT', 'anchor_position': 1, 'count': 1},
        ])
        self.assertEqual(result['cells'], {'0,2': {'raw_input': 'new'}})

    def test_delete_then_insert_is_not_cancelled(self):
        sheet, rows, columns = self._sheet('delete-first', 2, 1)
        kept = self._cell(sheet, rows[1], columns[0], 'kept')
        baseline = build_import_baseline(sheet)
        SheetService.delete_rows(sheet, position=0, count=1)
        SheetService.insert_rows(sheet, position=0, count=1)
        kept.raw_input = 'edited'
        kept.save(update_fields=['raw_input'])

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertEqual(result['structural_operations'], [
            {'op_type': 'ROW_DELETE', 'anchor_position': 0, 'count': 1},
            {'op_type': 'ROW_INSERT', 'anchor_position': 0, 'count': 1},
        ])
        self.assertEqual(result['cells'], {'1,0': {'raw_input': 'edited'}})

    def test_column_pair_with_a_row_op_between_is_kept(self):
        sheet, rows, columns = self._sheet('not-adjacent', 1, 2)
        baseline = build_import_baseline(sheet)
        SheetService.insert_columns(sheet, position=1, count=1)
        SheetService.insert_rows(sheet, position=0, count=1)
        SheetService.delete_columns(sheet, position=1, count=1)

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertEqual(result['structural_operations'], [
            {'op_type': 'COL_INSERT', 'anchor_position': 1, 'count': 1},
            {'op_type': 'ROW_INSERT', 'anchor_position': 0, 'count': 1},
            {'op_type': 'COL_DELETE', 'anchor_position': 1, 'count': 1},
        ])

    def test_partial_column_delete_is_not_cancelled(self):
        sheet, rows, columns = self._sheet('partial-cancel', 1, 1)
        baseline = build_import_baseline(sheet)
        SheetService.insert_columns(sheet, position=1, count=2)
        SheetService.delete_columns(sheet, position=1, count=1)

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertEqual(result['structural_operations'], [
            {'op_type': 'COL_INSERT', 'anchor_position': 1, 'count': 2},
            {'op_type': 'COL_DELETE', 'anchor_position': 1, 'count': 1},
        ])
    
    def test_delete_column_with_trailing_column_stays_patchable(self):
        sheet, rows, columns = self._sheet('delete-pad-col', 1, 3)
        baseline = build_import_baseline(sheet)
        SheetService.delete_columns(sheet, position=1, count=1)
        SheetColumn.objects.create(sheet=sheet, position=2, name='pad')

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertEqual(result['structural_operations'], [
            {'op_type': 'COL_DELETE', 'anchor_position': 1, 'count': 1},
        ])
        self.assertEqual(result['cells'], {})

    def test_delete_last_column_with_trailing_column_stays_patchable(self):
        sheet, rows, columns = self._sheet('delete-last-col', 1, 3)
        baseline = build_import_baseline(sheet)
        SheetService.delete_columns(sheet, position=2, count=1)
        SheetColumn.objects.create(sheet=sheet, position=2, name='pad')

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertEqual(result['structural_operations'], [
            {'op_type': 'COL_DELETE', 'anchor_position': 2, 'count': 1},
        ])

    def test_delete_row_with_trailing_row_stays_patchable(self):
        sheet, rows, columns = self._sheet('delete-pad-row', 3, 1)
        baseline = build_import_baseline(sheet)
        SheetService.delete_rows(sheet, position=1, count=1)
        SheetRow.objects.create(sheet=sheet, position=2)

        result = diff_sheet(_source(baseline, sheet))
        self.assertFalse(result['requires_rebuild'])
        self.assertEqual(result['structural_operations'], [
            {'op_type': 'ROW_DELETE', 'anchor_position': 1, 'count': 1},
        ])

    def test_trailing_column_with_a_cell_still_rebuilds(self):
        sheet, rows, columns = self._sheet('pad-data', 1, 3)
        baseline = build_import_baseline(sheet)
        SheetService.delete_columns(sheet, position=1, count=1)
        extra = SheetColumn.objects.create(sheet=sheet, position=2, name='pad')
        self._cell(sheet, rows[0], extra, 'typed')

        result = diff_sheet(_source(baseline, sheet))
        self.assertTrue(result['requires_rebuild'])
        self.assertIn('appended_region_change', result['rebuild_reasons'])
        self.assertEqual(result['structural_operations'], [])
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
        self.assertEqual(result['structural_operations'], [])
    
    def test_undo_removes_the_formula_patch_and_redo_keeps_structure(self):
        sheet, rows, columns = self._sheet('formula-undo', 3, 1)
        formula = self._cell(sheet, rows[0], columns[0], '=A2')
        baseline = build_import_baseline(sheet)

        deleted = SheetService.delete_rows(sheet, position=1, count=1)
        operation = SheetStructureOperation.objects.get(id=deleted['operation_id'])
        formula.refresh_from_db()
        self.assertEqual(formula.raw_input, '=#REF!')

        SheetService.revert_structure_operation(sheet, operation)
        formula.refresh_from_db()
        self.assertEqual(formula.raw_input, '=A2')
        undone = diff_sheet(_source(baseline, sheet))
        self.assertFalse(undone['requires_rebuild'])
        self.assertEqual(undone['structural_operations'], [])
        self.assertEqual(undone['cells'], {})

        SheetService.delete_rows(sheet, position=1, count=1)
        redone = diff_sheet(_source(baseline, sheet))
        self.assertFalse(redone['requires_rebuild'])
        self.assertEqual(redone['structural_operations'], [
            {'op_type': 'ROW_DELETE', 'anchor_position': 1, 'count': 1},
        ])
        self.assertEqual(redone['cells'], {'0,0': {'raw_input': '=#REF!'}})