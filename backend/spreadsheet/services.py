"""
Business logic services for spreadsheet operations
Handles spreadsheet, sheet, row, column, and cell management
"""
from typing import Dict, List, Any, Optional, Tuple, Union
from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging
import re
from functools import cmp_to_key
from django.db import connection, transaction
from django.core.exceptions import ValidationError
from django.db.models import Q, Max, F
from django.utils import timezone

from .models import (
    Spreadsheet, Sheet, SheetRow, SheetColumn, Cell, CellValueType, ComputedCellType, CellDependency,
    SheetStructureOperation, WorkflowPattern, WorkflowPatternStep,
    SpreadsheetHighlight, SpreadsheetHighlightScope, UserDefinedFunction
)
from .formula_engine import evaluate_formula, extract_references, reference_to_indexes, FormulaError
from .formula_rewrite import rewrite_cells_for_operation
from .sparkline import compute_sparkline, is_sparkline
from rest_framework.exceptions import ValidationError as DRFValidationError
from .exceptions import SheetRevisionConflict
from core.models import Project, ProjectMember
from .access import accessible_sheets
from .tenant import current_tenant_schema, validate_tenant_schema

logger = logging.getLogger(__name__)


def user_has_sheet_access(user, sheet_id: int) -> bool:
    """Use the same project access policy for HTTP and WebSocket rooms."""
    return accessible_sheets(user).filter(id=sheet_id).exists()


def sheet_room_group_name(
    sheet_id: int,
    tenant_schema: str = "public",
) -> str:
    """Tenant-isolated channel-layer group name for a sheet room."""
    if isinstance(sheet_id, bool) or not isinstance(sheet_id, int) or sheet_id <= 0:
        raise ValueError("sheet_id must be a positive integer")
    schema_name = validate_tenant_schema(tenant_schema)
    if schema_name == "public":
        return f"sheet_{sheet_id}"
    schema_key = hashlib.sha256(schema_name.encode("utf-8")).hexdigest()[:20]
    return f"sheet_t_{schema_key}_{sheet_id}"


def _validate_broadcast_target(
    sheet_id: int,
    cells: Optional[List[Cell]] = None,
) -> int:
    """Reject malformed targets and cell payloads routed to the wrong sheet.

    Authorization belongs to the REST mutation boundary. Re-querying Sheet,
    User and ProjectMember here added several queries to every committed edit
    while providing no additional protection against an external caller.
    """
    sheet_room_group_name(sheet_id)
    if cells and any(cell.sheet_id != sheet_id for cell in cells):
        raise ValueError("All broadcast cells must belong to sheet_id")
    return sheet_id


def _lock_sheet_for_mutation(sheet: Sheet) -> Sheet:
    """Acquire the common per-sheet mutation lock inside an atomic block.

    Cell writes and coordinate-changing structure operations must all pass
    through this row lock. That makes their commit order deterministic and
    prevents a structure rewrite from interleaving with a coordinate write.
    """
    return (
        Sheet.objects.select_for_update()
        .select_related('spreadsheet')
        .get(pk=sheet.pk, is_deleted=False, spreadsheet__is_deleted=False)
    )


def _assert_sheet_revision(sheet: Sheet, base_revision: Optional[int]) -> None:
    if base_revision is None:
        return
    if base_revision != sheet.revision:
        raise SheetRevisionConflict(base_revision, sheet.revision)


def _advance_sheet_revision(sheet: Sheet) -> int:
    """Advance the revision while the caller holds the common Sheet row lock."""
    sheet.revision += 1
    sheet.save(update_fields=['revision'])
    return sheet.revision


def _cell_broadcast_payload(cell: Cell) -> Dict[str, Any]:
    """JSON-safe cell snapshot for the cells_updated broadcast.

    Mirrors the subset of CellSerializer fields the grid applies via
    applyCellsFromResponse. Decimals become strings (same as DRF output),
    so the channel layer JSON encoder never sees a Decimal.
    """
    return {
        'row_position': cell.row_position,
        'column_position': cell.column_position,
        'value_type': cell.value_type,
        'string_value': cell.string_value,
        'number_value': str(cell.number_value) if cell.number_value is not None else None,
        'boolean_value': cell.boolean_value,
        'formula_value': cell.formula_value,
        'raw_input': cell.raw_input,
        'computed_type': cell.computed_type,
        'computed_number': str(cell.computed_number) if cell.computed_number is not None else None,
        'computed_string': cell.computed_string,
        'error_code': cell.error_code,
        'is_deleted': cell.is_deleted,
        'updated_at': cell.updated_at.isoformat() if cell.updated_at else None,
    }


def broadcast_cells_updated(
    sheet_id: int,
    cells: List[Cell],
    origin_client_id: Optional[str] = None,
    origin_user_id: Optional[int] = None,
    revision: Optional[int] = None,
) -> None:
    """Queue a cells_updated broadcast to the sheet room for after commit.

    Registered via transaction.on_commit so subscribers never receive values
    that are not yet durable (or that a rollback would revert). Commit order
    therefore defines LWW order: the broadcast for the later commit is queued
    later, and per-cell payloads always carry the post-write DB state.

    Delivery failures never escape the on_commit callback. Invalid programmer
    input (for example a non-positive sheet id or cells from another sheet)
    still raises before commit.
    """
    _validate_broadcast_target(sheet_id, cells)
    if not cells:
        return
    room_group_name = sheet_room_group_name(
        sheet_id,
        tenant_schema=current_tenant_schema(),
    )
    payload_cells = [_cell_broadcast_payload(c) for c in cells]

    def _send():
        try:
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer

            channel_layer = get_channel_layer()
            if channel_layer is None:
                return
            async_to_sync(channel_layer.group_send)(
                room_group_name,
                {
                    'type': 'cells.updated',
                    'sheet_id': sheet_id,
                    'origin_client_id': origin_client_id,
                    'origin_user_id': origin_user_id,
                    'revision': revision,
                    'cells': payload_cells,
                },
            )
        except Exception:
            logger.exception('cells_updated broadcast failed for sheet_id=%s', sheet_id)

    transaction.on_commit(_send)


def broadcast_sheet_refresh(
    sheet_id: int,
    reason: str,
    origin_client_id: Optional[str] = None,
    origin_user_id: Optional[int] = None,
    revision: Optional[int] = None,
) -> None:
    """Queue a sheet_refresh_required broadcast to the sheet room (after commit).

    Coarse-grained companion to broadcast_cells_updated: structure operations
    (insert/delete rows or columns, sort, reorder, resize, revert) and import
    finalize shift cell positions, so per-cell payloads from before the shift
    would land in the wrong cells on stale peers. Instead of replaying each
    operation client-side, subscribers invalidate their caches and reload.

    Runs via transaction.on_commit: immediate under autocommit (the structure
    services commit internally before their view calls this), deferred to the
    commit point when a transaction is active. Delivery failures are logged
    and swallowed; malformed programmer input still raises.
    """

    _validate_broadcast_target(sheet_id)
    room_group_name = sheet_room_group_name(
        sheet_id,
        tenant_schema=current_tenant_schema(),
    )

    def _send():
        try:
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer

            channel_layer = get_channel_layer()
            if channel_layer is None:
                return
            async_to_sync(channel_layer.group_send)(
                room_group_name,
                {
                    'type': 'sheet.refresh_required',
                    'sheet_id': sheet_id,
                    'reason': reason,
                    'origin_client_id': origin_client_id,
                    'origin_user_id': origin_user_id,
                    'revision': revision,
                },
            )
        except Exception:
            logger.exception('sheet_refresh_required broadcast failed for sheet_id=%s', sheet_id)

    transaction.on_commit(_send)


def broadcast_spreadsheet_sheet_list_refresh(
    spreadsheet_id: int,
    reason: str,
    include_sheet_ids: Optional[List[int]] = None,
    origin_client_id: Optional[str] = None,
    origin_user_id: Optional[int] = None,
) -> None:
    """Notify every sheet room that the parent spreadsheet's tab list changed.

    Clients only subscribe to their active sheet room, so sheet create/update/
    delete events must fan out across the spreadsheet. Deleted sheet ids can be
    included explicitly so clients currently viewing a just-deleted sheet also
    receive the event and select a remaining sheet.
    """
    sheet_ids = set(
        Sheet.objects.filter(spreadsheet_id=spreadsheet_id, is_deleted=False)
        .values_list('id', flat=True)
    )
    sheet_ids.update(include_sheet_ids or [])
    for sheet_id in sorted(sheet_ids):
        broadcast_sheet_refresh(
            sheet_id=sheet_id,
            reason=reason,
            origin_client_id=origin_client_id,
            origin_user_id=origin_user_id,
        )


class CellBatchArgumentError(Exception):
    """Structured batch cell validation failure (maps to HTTP 400 in views)."""

    def __init__(self, details: List[Dict[str, Any]]):
        self.code = 'INVALID_ARGUMENT'
        self.details = details
        super().__init__(self.code)


class SpreadsheetService:
    """Service class for handling spreadsheet business logic"""
    
    @staticmethod
    @transaction.atomic
    def create_spreadsheet(project: Project, name: str) -> Spreadsheet:
        """
        Create a new spreadsheet
        
        Args:
            project: Project instance
            name: Spreadsheet name
            
        Returns:
            Created Spreadsheet instance
            
        Raises:
            ValidationError: If spreadsheet with same name already exists
        """
        # Check for duplicate name in the same project
        if Spreadsheet.objects.filter(
            project=project,
            name=name,
            is_deleted=False
        ).exists():
            raise ValidationError(
                f"Spreadsheet with name '{name}' already exists in this project"
            )
        
        spreadsheet = Spreadsheet.objects.create(
            project=project,
            name=name
        )
        
        return spreadsheet
    
    @staticmethod
    @transaction.atomic
    def update_spreadsheet(spreadsheet: Spreadsheet, name: str) -> Spreadsheet:
        """
        Update spreadsheet name
        
        Args:
            spreadsheet: Spreadsheet instance to update
            name: New name
            
        Returns:
            Updated Spreadsheet instance
            
        Raises:
            ValidationError: If spreadsheet with same name already exists
        """
        # Check for duplicate name (excluding current spreadsheet)
        if Spreadsheet.objects.filter(
            project=spreadsheet.project,
            name=name,
            is_deleted=False
        ).exclude(id=spreadsheet.id).exists():
            raise ValidationError(
                f"Spreadsheet with name '{name}' already exists in this project"
            )
        
        spreadsheet.name = name
        spreadsheet.save()
        
        return spreadsheet
    
    @staticmethod
    @transaction.atomic
    def delete_spreadsheet(spreadsheet: Spreadsheet) -> None:
        """
        Soft delete a spreadsheet
        
        Args:
            spreadsheet: Spreadsheet instance to delete
        """
        spreadsheet.is_deleted = True
        spreadsheet.save()


class SheetService:
    """Service class for handling sheet business logic"""
    
    @staticmethod
    def _get_next_sheet_position(spreadsheet: Spreadsheet) -> int:
        """
        Get the next available position for a new sheet
        
        Args:
            spreadsheet: Spreadsheet instance
            
        Returns:
            Next position (0-indexed)
        """
        max_position = Sheet.objects.filter(
            spreadsheet=spreadsheet
        ).aggregate(Max('position'))['position__max']
        
        return (max_position + 1) if max_position is not None else 0
    
    @staticmethod
    @transaction.atomic
    def create_sheet(spreadsheet: Spreadsheet, name: str) -> Sheet:
        """
        Create a new sheet with auto-assigned position
        
        Position is automatically assigned based on creation order (max position + 1).
        Clients cannot provide or update position.
        Uses row-level locking to ensure concurrency-safe position assignment.
        
        Args:
            spreadsheet: Spreadsheet instance
            name: Sheet name
            
        Returns:
            Created Sheet instance
            
        Raises:
            ValidationError: If sheet with same name already exists
        """
        # Lock the spreadsheet row for concurrency-safe position assignment
        locked_spreadsheet = Spreadsheet.objects.select_for_update().get(id=spreadsheet.id)
        
        # Always auto-assign position based on creation order (with lock held)
        position = SheetService._get_next_sheet_position(locked_spreadsheet)
        
        # Check for duplicate name
        if Sheet.objects.filter(
            spreadsheet=locked_spreadsheet,
            name=name,
            is_deleted=False
        ).exists():
            raise ValidationError(
                f"Sheet with name '{name}' already exists in this spreadsheet"
            )
        
        # Check for duplicate position (should not happen with lock, but safety check)
        if Sheet.objects.filter(
            spreadsheet=locked_spreadsheet,
            position=position,
            is_deleted=False
        ).exists():
            raise ValidationError(
                f"Sheet with position {position} already exists in this spreadsheet"
            )
        
        sheet = Sheet.objects.create(
            spreadsheet=locked_spreadsheet,
            name=name,
            position=position
        )
        
        return sheet
    
    @staticmethod
    @transaction.atomic
    def update_sheet(
        sheet: Sheet,
        name: str,
        *,
        frozen_row_count: Optional[int] = None,
        frozen_column_count: Optional[int] = None,
    ) -> Sheet:
        """
        Update sheet (name, frozen_row_count, frozen_column_count).
        Position cannot be updated.
        
        Args:
            sheet: Sheet instance to update
            name: New name
            frozen_row_count: Optional new frozen row count
            frozen_column_count: Optional new frozen column count
            
        Returns:
            Updated Sheet instance
            
        Raises:
            ValidationError: If sheet with same name already exists
        """
        # Check for duplicate name (excluding current sheet)
        if Sheet.objects.filter(
            spreadsheet=sheet.spreadsheet,
            name=name,
            is_deleted=False
        ).exclude(id=sheet.id).exists():
            raise ValidationError(
                f"Sheet with name '{name}' already exists in this spreadsheet"
            )
        
        sheet.name = name
        if frozen_row_count is not None:
            sheet.frozen_row_count = max(0, min(1000, frozen_row_count))
        if frozen_column_count is not None:
            sheet.frozen_column_count = max(0, min(100, frozen_column_count))
        sheet.save()
        
        return sheet
    
    @staticmethod
    @transaction.atomic
    def delete_sheet(sheet: Sheet) -> None:
        """
        Soft delete a sheet
        
        Args:
            sheet: Sheet instance to delete
        """
        locked_sheet = (
            Sheet.objects.select_for_update()
            .get(pk=sheet.pk, spreadsheet__is_deleted=False)
        )
        if locked_sheet.is_deleted:
            sheet.is_deleted = True
            return
        locked_sheet.is_deleted = True
        locked_sheet.save()
        # Preserve the service's existing in-memory contract for callers/tests
        # holding the instance that was passed in.
        sheet.is_deleted = True
    
    @staticmethod
    def _generate_column_name(position: int) -> str:
        """
        Generate column name from position (A, B, C, ..., Z, AA, AB, ...)
        
        Args:
            position: Column position (0-indexed)
            
        Returns:
            Column name (e.g., 'A', 'B', 'AA')
        """
        result = ""
        position += 1  # Convert to 1-indexed for calculation
        while position > 0:
            position -= 1
            result = chr(ord('A') + (position % 26)) + result
            position //= 26
        return result
    
    @staticmethod
    @transaction.atomic
    def resize_sheet(
        sheet: Sheet,
        row_count: int,
        column_count: int,
        base_revision: Optional[int] = None,
    ) -> Dict[str, int]:
        """
        Ensure sheet has at least the specified number of rows and columns.
        Creates missing rows and columns as needed.

        Args:
            sheet: Sheet instance
            row_count: Target number of rows (0-indexed, so row_count=10 means rows 0-9)
            column_count: Target number of columns (0-indexed, so column_count=5 means columns 0-4)

        Returns:
            Dict with rows_created, columns_created, total_rows, total_columns
        """
        if row_count < 0 or column_count < 0:
            raise ValidationError("row_count and column_count must be non-negative integers")

        sheet = _lock_sheet_for_mutation(sheet)
        _assert_sheet_revision(sheet, base_revision)

        # Fetch row state in ONE query (was 3 separate queries + a max() call)
        row_qs = SheetRow.objects.filter(sheet=sheet).values_list('position', 'is_deleted')
        all_row_positions: set = set()
        active_row_positions: set = set()
        for pos, deleted in row_qs:
            all_row_positions.add(pos)
            if not deleted:
                active_row_positions.add(pos)

        # Fetch column state in ONE query (was 2 separate queries)
        col_qs = SheetColumn.objects.filter(sheet=sheet).values_list('position', 'is_deleted')
        active_col_positions: set = set()
        for pos, deleted in col_qs:
            if not deleted:
                active_col_positions.add(pos)

        # --- Rows ---
        # Set arithmetic replaces the O(N) Python loop over range(row_count).
        # Deleted positions are not reused; extras go beyond the current max.
        target_row_positions = set(range(row_count))
        fresh_row_positions = sorted(target_row_positions - all_row_positions)
        deleted_in_range = len(target_row_positions & (all_row_positions - active_row_positions))

        max_row_pos = max(all_row_positions) if all_row_positions else -1
        extra_row_start = max_row_pos + 1
        extra_row_positions = list(range(extra_row_start, extra_row_start + deleted_in_range))

        rows_to_create_positions = fresh_row_positions + extra_row_positions
        rows_created = len(rows_to_create_positions)
        if rows_to_create_positions:
            # ONE bulk INSERT replaces N individual INSERT statements (e.g. 3 000 rows → 6 batches)
            SheetRow.objects.bulk_create(
                [SheetRow(sheet=sheet, position=p, is_deleted=False) for p in rows_to_create_positions],
                batch_size=500,
                ignore_conflicts=True,
            )

        # --- Columns ---
        # Original logic: create at any position absent from active columns (including
        # previously-deleted positions, which have a partial unique index so they can be recreated).
        cols_to_create_positions = sorted(set(range(column_count)) - active_col_positions)
        columns_created = len(cols_to_create_positions)
        if cols_to_create_positions:
            SheetColumn.objects.bulk_create(
                [
                    SheetColumn(
                        sheet=sheet,
                        position=p,
                        name=SheetService._generate_column_name(p),
                        is_deleted=False,
                    )
                    for p in cols_to_create_positions
                ],
                batch_size=500,
                ignore_conflicts=True,
            )

        # Compute totals from already-fetched sets (avoids 2 extra COUNT queries)
        total_rows = len(active_row_positions) + rows_created
        total_columns = len(active_col_positions) + columns_created
        if rows_created or columns_created:
            _advance_sheet_revision(sheet)

        return {
            'rows_created': rows_created,
            'columns_created': columns_created,
            'total_rows': total_rows,
            'total_columns': total_columns,
            'revision': sheet.revision,
        }

    @staticmethod
    @transaction.atomic
    def insert_rows(
        sheet: Sheet,
        position: int,
        count: int = 1,
        created_by: Optional[Any] = None,
        base_revision: Optional[int] = None,
    ) -> Dict[str, int]:
        """
        Insert rows at a given position by shifting existing row positions.
        Does NOT touch Cell records; rows are inserted by position only.
        """
        if count < 1:
            raise ValidationError("count must be a positive integer")

        sheet = _lock_sheet_for_mutation(sheet)
        _assert_sheet_revision(sheet, base_revision)

        current_count = SheetRow.objects.filter(sheet=sheet, is_deleted=False).count()
        if position < 0 or position > current_count:
            raise ValidationError("position must be between 0 and current row count")

        shift_qs = SheetRow.objects.filter(
            sheet=sheet,
            is_deleted=False,
            position__gte=position
        )

        offset = 1_000_000
        if shift_qs.exists():
            # Phase 1: move shifted rows out of the way to avoid unique collisions
            shift_qs.update(position=F('position') + offset)

        # Create new rows at insert positions
        new_rows = [
            SheetRow(sheet=sheet, position=position + i)
            for i in range(count)
        ]
        created_rows = SheetRow.objects.bulk_create(new_rows)

        if shift_qs.exists():
            # Phase 2: move shifted rows back into their final positions
            SheetRow.objects.filter(
                sheet=sheet,
                is_deleted=False,
                position__gte=position + offset
            ).update(position=F('position') - offset + count)

        operation = SheetStructureOperation.objects.create(
            sheet=sheet,
            op_type=SheetStructureOperation.OperationType.ROW_INSERT,
            anchor_position=position,
            count=count,
            affected_ids=[row.id for row in created_rows],
            affected_positions={},
            created_by=created_by
        )
        rewritten_cells = rewrite_cells_for_operation(sheet.id, 'ROW_INSERT', position, count)
        for cell in rewritten_cells:
            CellService._update_dependencies(cell)
        CellService._recalculate_formula_cells(rewritten_cells)
        _advance_sheet_revision(sheet)

        result = {
            'rows_created': count,
            'total_rows': current_count + count,
            'operation_id': operation.id,
            'revision': sheet.revision,
        }
        # Recompute any pivot sheets that depend on this sheet as a source.
        try:
            from .pivot_service import recompute_pivots_for_source_sheet
            recompute_pivots_for_source_sheet(sheet)
        except Exception:
            logger.exception("Pivot recompute after insert_rows failed for sheet_id=%s", sheet.id)

        return result

    @staticmethod
    @transaction.atomic
    def insert_columns(
        sheet: Sheet,
        position: int,
        count: int = 1,
        created_by: Optional[Any] = None,
        base_revision: Optional[int] = None,
    ) -> Dict[str, int]:
        """
        Insert columns at a given position by shifting existing column positions.
        Does NOT touch Cell records; columns are inserted by position only.
        """
        if count < 1:
            raise ValidationError("count must be a positive integer")

        sheet = _lock_sheet_for_mutation(sheet)
        _assert_sheet_revision(sheet, base_revision)

        current_count = SheetColumn.objects.filter(sheet=sheet, is_deleted=False).count()
        if position < 0 or position > current_count:
            raise ValidationError("position must be between 0 and current column count")

        shift_qs = SheetColumn.objects.filter(
            sheet=sheet,
            is_deleted=False,
            position__gte=position
        )

        offset = 1_000_000
        if shift_qs.exists():
            # Phase 1: move shifted columns out of the way to avoid unique collisions
            shift_qs.update(position=F('position') + offset)

        new_columns = [
            SheetColumn(
                sheet=sheet,
                position=position + i,
                name=SheetService._generate_column_name(position + i)
            )
            for i in range(count)
        ]
        created_columns = SheetColumn.objects.bulk_create(new_columns)

        if shift_qs.exists():
            # Phase 2: move shifted columns back into their final positions
            SheetColumn.objects.filter(
                sheet=sheet,
                is_deleted=False,
                position__gte=position + offset
            ).update(position=F('position') - offset + count)

        operation = SheetStructureOperation.objects.create(
            sheet=sheet,
            op_type=SheetStructureOperation.OperationType.COL_INSERT,
            anchor_position=position,
            count=count,
            affected_ids=[column.id for column in created_columns],
            affected_positions={},
            created_by=created_by
        )
        rewritten_cells = rewrite_cells_for_operation(sheet.id, 'COL_INSERT', position, count)
        for cell in rewritten_cells:
            CellService._update_dependencies(cell)
        CellService._recalculate_formula_cells(rewritten_cells)
        _advance_sheet_revision(sheet)

        result = {
            'columns_created': count,
            'total_columns': current_count + count,
            'operation_id': operation.id,
            'revision': sheet.revision,
        }
        try:
            from .pivot_service import recompute_pivots_for_source_sheet
            recompute_pivots_for_source_sheet(sheet)
        except Exception:
            logger.exception("Pivot recompute after insert_columns failed for sheet_id=%s", sheet.id)

        return result

    @staticmethod
    @transaction.atomic
    def delete_rows(
        sheet: Sheet,
        position: int,
        count: int = 1,
        created_by: Optional[Any] = None,
        base_revision: Optional[int] = None,
    ) -> Dict[str, int]:
        """
        Delete rows at a given position by soft-deleting rows and shifting positions.
        Does NOT touch Cell records.
        """
        if count < 1:
            raise ValidationError("count must be a positive integer")

        sheet = _lock_sheet_for_mutation(sheet)
        _assert_sheet_revision(sheet, base_revision)

        active_qs = SheetRow.objects.filter(sheet=sheet, is_deleted=False)
        current_count = active_qs.count()
        if position < 0 or position + count > current_count:
            raise ValidationError("position range must be within current row count")

        target_rows = list(
            active_qs.filter(position__gte=position, position__lt=position + count)
            .order_by('position')
        )
        if len(target_rows) != count:
            raise ValidationError("row range is not contiguous or does not exist")

        affected_ids = [row.id for row in target_rows]
        affected_positions = {str(row.id): row.position for row in target_rows}

        # Soft delete target rows
        SheetRow.objects.filter(id__in=affected_ids).update(is_deleted=True)

        # Shift remaining rows down to fill the gap
        shift_qs = SheetRow.objects.filter(
            sheet=sheet,
            is_deleted=False,
            position__gte=position + count
        )
        offset = 1_000_000
        if shift_qs.exists():
            # Phase 1: move shifted rows out of the way
            shift_qs.update(position=F('position') + offset)
            # Phase 2: move to final positions
            SheetRow.objects.filter(
                sheet=sheet,
                is_deleted=False,
                position__gte=position + count + offset
            ).update(position=F('position') - offset - count)

        operation = SheetStructureOperation.objects.create(
            sheet=sheet,
            op_type=SheetStructureOperation.OperationType.ROW_DELETE,
            anchor_position=position,
            count=count,
            affected_ids=affected_ids,
            affected_positions=affected_positions,
            created_by=created_by
        )
        rewritten_cells = rewrite_cells_for_operation(sheet.id, 'ROW_DELETE', position, count)
        for cell in rewritten_cells:
            CellService._update_dependencies(cell)
        CellService._recalculate_formula_cells(rewritten_cells)
        _advance_sheet_revision(sheet)

        result = {
            'rows_deleted': count,
            'total_rows': current_count - count,
            'operation_id': operation.id,
            'revision': sheet.revision,
        }
        try:
            from .pivot_service import recompute_pivots_for_source_sheet
            recompute_pivots_for_source_sheet(sheet)
        except Exception:
            logger.exception("Pivot recompute after delete_rows failed for sheet_id=%s", sheet.id)

        return result

    @staticmethod
    @transaction.atomic
    def delete_columns(
        sheet: Sheet,
        position: int,
        count: int = 1,
        created_by: Optional[Any] = None,
        base_revision: Optional[int] = None,
    ) -> Dict[str, int]:
        """
        Delete columns at a given position by soft-deleting columns and shifting positions.
        Does NOT touch Cell records.
        """
        if count < 1:
            raise ValidationError("count must be a positive integer")

        sheet = _lock_sheet_for_mutation(sheet)
        _assert_sheet_revision(sheet, base_revision)

        active_qs = SheetColumn.objects.filter(sheet=sheet, is_deleted=False)
        current_count = active_qs.count()
        if position < 0 or position + count > current_count:
            raise ValidationError("position range must be within current column count")

        target_columns = list(
            active_qs.filter(position__gte=position, position__lt=position + count)
            .order_by('position')
        )
        if len(target_columns) != count:
            raise ValidationError("column range is not contiguous or does not exist")

        affected_ids = [column.id for column in target_columns]
        affected_positions = {str(column.id): column.position for column in target_columns}

        # Soft delete target columns
        SheetColumn.objects.filter(id__in=affected_ids).update(is_deleted=True)

        # Shift remaining columns left to fill the gap
        shift_qs = SheetColumn.objects.filter(
            sheet=sheet,
            is_deleted=False,
            position__gte=position + count
        )
        offset = 1_000_000
        if shift_qs.exists():
            # Phase 1: move shifted columns out of the way
            shift_qs.update(position=F('position') + offset)
            # Phase 2: move to final positions
            SheetColumn.objects.filter(
                sheet=sheet,
                is_deleted=False,
                position__gte=position + count + offset
            ).update(position=F('position') - offset - count)

        operation = SheetStructureOperation.objects.create(
            sheet=sheet,
            op_type=SheetStructureOperation.OperationType.COL_DELETE,
            anchor_position=position,
            count=count,
            affected_ids=affected_ids,
            affected_positions=affected_positions,
            created_by=created_by
        )
        rewritten_cells = rewrite_cells_for_operation(sheet.id, 'COL_DELETE', position, count)
        for cell in rewritten_cells:
            CellService._update_dependencies(cell)
        CellService._recalculate_formula_cells(rewritten_cells)
        _advance_sheet_revision(sheet)

        result = {
            'columns_deleted': count,
            'total_columns': current_count - count,
            'operation_id': operation.id,
            'revision': sheet.revision,
        }
        try:
            from .pivot_service import recompute_pivots_for_source_sheet
            recompute_pivots_for_source_sheet(sheet)
        except Exception:
            logger.exception("Pivot recompute after delete_columns failed for sheet_id=%s", sheet.id)

        return result

    @staticmethod
    def _cell_sort_tuple(cell) -> Tuple[int, Optional[float], str]:
        """Return normalized sortable payload: (type_order, num_val, str_val)."""
        empty = cell is None or (
            getattr(cell, 'computed_number', None) is None
            and not ((getattr(cell, 'computed_string', None) or '').strip())
            and not ((getattr(cell, 'raw_input', None) or '').strip())
        )
        if empty:
            return (2, None, '')
        num = getattr(cell, 'computed_number', None)
        try:
            n = float(num) if num is not None else None
        except (TypeError, InvalidOperation, ValueError):
            n = None
        s = (getattr(cell, 'computed_string', None) or '') or (str(num) if num is not None else '') or (getattr(cell, 'raw_input', None) or '')
        if n is not None and not (isinstance(n, float) and (n != n)):
            return (0, n, '')
        return (1, 0.0, s or '')

    @staticmethod
    def _compare_cells_for_sort(cell_a, cell_b, direction: str) -> int:
        """Compare two cells using spreadsheet ordering semantics and direction."""
        direction = (direction or 'asc').lower()
        if direction not in ('asc', 'desc'):
            direction = 'asc'

        type_a, num_a, str_a = SheetService._cell_sort_tuple(cell_a)
        type_b, num_b, str_b = SheetService._cell_sort_tuple(cell_b)

        if type_a != type_b:
            return -1 if type_a < type_b else 1

        if type_a == 0:
            if num_a == num_b:
                return 0
            if direction == 'asc':
                return -1 if (num_a or 0.0) < (num_b or 0.0) else 1
            return -1 if (num_a or 0.0) > (num_b or 0.0) else 1

        if type_a == 1:
            if str_a == str_b:
                return 0
            if direction == 'asc':
                return -1 if str_a < str_b else 1
            return -1 if str_a > str_b else 1

        return 0

    @staticmethod
    @transaction.atomic
    def sort_rows(
        sheet: Sheet,
        column_position: int,
        direction: str,
        has_header: bool = True,
        previous_sort_columns: Optional[List[Union[int, Dict[str, Any]]]] = None,
        base_revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Sort rows by updating SheetRow.position. Does NOT rewrite cells.
        Header row (position 0) is kept fixed when has_header=True.
        """
        direction = (direction or 'asc').lower()
        if direction not in ('asc', 'desc'):
            raise ValidationError("direction must be 'asc' or 'desc'")
        sheet = _lock_sheet_for_mutation(sheet)
        _assert_sheet_revision(sheet, base_revision)
        rows = list(
            SheetRow.objects.filter(sheet=sheet, is_deleted=False)
            .order_by('position')
            .select_related('sheet')
        )
        if not rows:
            return {'previous_order': [], 'new_order': [], 'revision': sheet.revision}

        if has_header:
            header_row = rows[0] if rows[0].position == 0 else None
            data_rows = [r for r in rows if r.position > 0]
        else:
            header_row = None
            data_rows = rows

        if not data_rows:
            return {
                'previous_order': [{'row_id': r.id, 'position': r.position} for r in rows],
                'new_order': [{'row_id': r.id, 'position': r.position} for r in rows],
                'revision': sheet.revision,
            }

        prev_cols = previous_sort_columns or []
        sort_history: List[Tuple[int, str]] = [(column_position, direction)]
        used_cols = {column_position}
        for entry in prev_cols:
            if isinstance(entry, dict):
                col = entry.get('column_position')
                tie_direction = (entry.get('direction') or 'asc').lower()
            else:
                col = entry
                tie_direction = direction

            if not isinstance(col, int) or col < 0 or col in used_cols:
                continue
            if tie_direction not in ('asc', 'desc'):
                tie_direction = 'asc'

            sort_history.append((col, tie_direction))
            used_cols.add(col)

        data_row_ids = [r.id for r in data_rows]
        sort_col_positions = [col for col, _ in sort_history]

        cells = list(
            Cell.objects.filter(
                sheet=sheet,
                row_id__in=data_row_ids,
                column__position__in=sort_col_positions,
                is_deleted=False
            ).select_related('row', 'column')
        )

        row_cells: Dict[int, Dict[int, Cell]] = {}
        for r_id in data_row_ids:
            row_cells[r_id] = {}
        for c in cells:
            if c.column and c.row_id in row_cells:
                row_cells[c.row_id][c.column.position] = c

        def compare_rows(row_a: SheetRow, row_b: SheetRow) -> int:
            cells_a = row_cells.get(row_a.id, {})
            cells_b = row_cells.get(row_b.id, {})
            for col, tie_direction in sort_history:
                cmp_result = SheetService._compare_cells_for_sort(
                    cells_a.get(col),
                    cells_b.get(col),
                    tie_direction,
                )
                if cmp_result != 0:
                    return cmp_result
            return 0

        # Python's sort is stable, so complete ties preserve existing row order.
        sorted_rows = sorted(data_rows, key=cmp_to_key(compare_rows))

        previous_order = [{'row_id': r.id, 'position': r.position} for r in rows]
        new_positions = []
        pos = 0
        if header_row:
            new_positions.append((header_row.id, 0))
            pos = 1
        for r in sorted_rows:
            new_positions.append((r.id, pos))
            pos += 1

        offset = 1_000_000
        for row_id, new_pos in new_positions:
            SheetRow.objects.filter(sheet=sheet, id=row_id, is_deleted=False).update(position=F('position') + offset)
        for row_id, new_pos in new_positions:
            SheetRow.objects.filter(sheet=sheet, id=row_id, is_deleted=False).update(position=new_pos)

        new_order = [{'row_id': rid, 'position': p} for rid, p in new_positions]
        _advance_sheet_revision(sheet)

        return {
            'previous_order': previous_order,
            'new_order': new_order,
            'revision': sheet.revision,
        }

    @staticmethod
    @transaction.atomic
    def reorder_rows(
        sheet: Sheet,
        order: List[Dict[str, int]],
        base_revision: Optional[int] = None,
    ) -> int:
        """
        Update SheetRow.position according to the given mapping. Used for undo/redo.
        order: [{"row_id": int, "position": int}, ...]
        """
        if not order:
            return sheet.revision
        sheet = _lock_sheet_for_mutation(sheet)
        _assert_sheet_revision(sheet, base_revision)
        offset = 1_000_000
        for item in order:
            rid = item.get('row_id')
            pos = item.get('position')
            if rid is not None and pos is not None:
                SheetRow.objects.filter(sheet=sheet, id=rid, is_deleted=False).update(position=F('position') + offset)
        for item in order:
            rid = item.get('row_id')
            pos = item.get('position')
            if rid is not None and pos is not None:
                SheetRow.objects.filter(sheet=sheet, id=rid, is_deleted=False).update(position=pos)
        return _advance_sheet_revision(sheet)

    @staticmethod
    @transaction.atomic
    def revert_structure_operation(
        sheet: Sheet,
        operation: SheetStructureOperation,
        base_revision: Optional[int] = None,
    ) -> Dict[str, int]:
        """
        Revert a previously logged structure operation.
        """
        sheet = _lock_sheet_for_mutation(sheet)
        _assert_sheet_revision(sheet, base_revision)
        operation = SheetStructureOperation.objects.select_for_update().get(
            pk=operation.pk
        )
        if operation.is_reverted:
            raise ValidationError("operation has already been reverted")
        if operation.sheet_id != sheet.id:
            raise ValidationError("operation does not belong to this sheet")

        offset = 1_000_000

        if operation.op_type == SheetStructureOperation.OperationType.ROW_INSERT:
            # Soft delete inserted rows and shift back
            SheetRow.objects.filter(id__in=operation.affected_ids).update(is_deleted=True)
            shift_qs = SheetRow.objects.filter(
                sheet=sheet,
                is_deleted=False,
                position__gte=operation.anchor_position + operation.count
            )
            if shift_qs.exists():
                shift_qs.update(position=F('position') + offset)
                SheetRow.objects.filter(
                    sheet=sheet,
                    is_deleted=False,
                    position__gte=operation.anchor_position + operation.count + offset
                ).update(position=F('position') - offset - operation.count)
            rewritten_cells = rewrite_cells_for_operation(sheet.id, 'ROW_DELETE', operation.anchor_position, operation.count)
            for cell in rewritten_cells:
                CellService._update_dependencies(cell)
            CellService._recalculate_formula_cells(rewritten_cells)

        elif operation.op_type == SheetStructureOperation.OperationType.COL_INSERT:
            SheetColumn.objects.filter(id__in=operation.affected_ids).update(is_deleted=True)
            shift_qs = SheetColumn.objects.filter(
                sheet=sheet,
                is_deleted=False,
                position__gte=operation.anchor_position + operation.count
            )
            if shift_qs.exists():
                shift_qs.update(position=F('position') + offset)
                SheetColumn.objects.filter(
                    sheet=sheet,
                    is_deleted=False,
                    position__gte=operation.anchor_position + operation.count + offset
                ).update(position=F('position') - offset - operation.count)
            rewritten_cells = rewrite_cells_for_operation(sheet.id, 'COL_DELETE', operation.anchor_position, operation.count)
            for cell in rewritten_cells:
                CellService._update_dependencies(cell)
            CellService._recalculate_formula_cells(rewritten_cells)

        elif operation.op_type == SheetStructureOperation.OperationType.ROW_DELETE:
            # Shift rows down to make space, then restore deleted rows to original positions
            shift_qs = SheetRow.objects.filter(
                sheet=sheet,
                is_deleted=False,
                position__gte=operation.anchor_position
            )
            if shift_qs.exists():
                shift_qs.update(position=F('position') + offset)
                SheetRow.objects.filter(
                    sheet=sheet,
                    is_deleted=False,
                    position__gte=operation.anchor_position + offset
                ).update(position=F('position') - offset + operation.count)

            for row_id, position in operation.affected_positions.items():
                SheetRow.objects.filter(id=int(row_id)).update(
                    is_deleted=False,
                    position=position
                )
            rewritten_cells = rewrite_cells_for_operation(sheet.id, 'ROW_INSERT', operation.anchor_position, operation.count)
            for cell in rewritten_cells:
                CellService._update_dependencies(cell)
            CellService._recalculate_formula_cells(rewritten_cells)

        elif operation.op_type == SheetStructureOperation.OperationType.COL_DELETE:
            shift_qs = SheetColumn.objects.filter(
                sheet=sheet,
                is_deleted=False,
                position__gte=operation.anchor_position
            )
            if shift_qs.exists():
                shift_qs.update(position=F('position') + offset)
                SheetColumn.objects.filter(
                    sheet=sheet,
                    is_deleted=False,
                    position__gte=operation.anchor_position + offset
                ).update(position=F('position') - offset + operation.count)

            for column_id, position in operation.affected_positions.items():
                SheetColumn.objects.filter(id=int(column_id)).update(
                    is_deleted=False,
                    position=position
                )
            rewritten_cells = rewrite_cells_for_operation(sheet.id, 'COL_INSERT', operation.anchor_position, operation.count)
            for cell in rewritten_cells:
                CellService._update_dependencies(cell)
            CellService._recalculate_formula_cells(rewritten_cells)

        else:
            raise ValidationError("unsupported operation type")

        operation.is_reverted = True
        operation.save(update_fields=['is_reverted'])
        _advance_sheet_revision(sheet)

        return {
            'operation_id': operation.id,
            'is_reverted': True,
            'revision': sheet.revision,
        }


class CellService:
    """Service class for handling cell business logic"""

    _NUMERIC_RE = re.compile(r'^[+-]?(\d+(\.\d*)?|\.\d+)$')

    @staticmethod
    def _normalize_decimal(value: Decimal) -> Decimal:
        return value

    @staticmethod
    def _log_position_duplicates(model, sheet: Sheet, position: int, label: str) -> None:
        matches = list(
            model.objects.filter(sheet=sheet, position=position).values('id', 'is_deleted')
        )
        if len(matches) > 1:
            logger.info(
                "Duplicate %s position detected sheet_id=%s position=%s matches=%s",
                label,
                sheet.id,
                position,
                matches
            )

    @staticmethod
    def _update_dependencies(cell: Cell) -> None:
        CellDependency.objects.filter(from_cell=cell).update(is_deleted=True)

        raw_input = cell.raw_input or ''
        if not raw_input.startswith('='):
            formula_value = cell.formula_value or ''
            if cell.value_type == CellValueType.FORMULA and formula_value.startswith('='):
                raw_input = formula_value
            else:
                return

        references = extract_references(raw_input)
        dependencies = []
        for ref in references:
            try:
                row_index, col_index = reference_to_indexes(ref)
            except FormulaError:
                continue

            row = CellService._get_or_create_row(cell.sheet, row_index)
            column = CellService._get_or_create_column(cell.sheet, col_index)
            to_cell, _ = Cell.objects.get_or_create(
                sheet=cell.sheet,
                row=row,
                column=column,
                defaults={
                    'is_deleted': False,
                    'value_type': CellValueType.EMPTY,
                    'raw_input': '',
                    'computed_type': ComputedCellType.EMPTY
                }
            )
            if to_cell.is_deleted:
                to_cell.is_deleted = False
                to_cell.save()

            dependencies.append(CellDependency(from_cell=cell, to_cell=to_cell))

        if dependencies:
            CellDependency.objects.bulk_create(dependencies, ignore_conflicts=True)

    @staticmethod
    def _collect_dependent_formula_cells(changed_cells: List[Cell]) -> List[Cell]:
        from collections import deque

        if not changed_cells:
            return []
        sheet_ids = {cell.sheet_id for cell in changed_cells}
        changed_ids = {cell.id for cell in changed_cells if cell.id is not None}
        if not changed_ids:
            return []

        if connection.vendor == 'postgresql':
            dependency_table = connection.ops.quote_name(
                CellDependency._meta.db_table
            )
            cell_table = connection.ops.quote_name(Cell._meta.db_table)
            sql = f"""
                WITH RECURSIVE affected(id) AS (
                    SELECT dependency.from_cell_id
                    FROM {dependency_table} dependency
                    JOIN {cell_table} dependent
                      ON dependent.id = dependency.from_cell_id
                    WHERE dependency.to_cell_id = ANY(%s)
                      AND dependency.is_deleted = FALSE
                      AND dependent.is_deleted = FALSE
                      AND dependent.sheet_id = ANY(%s)
                    UNION
                    SELECT dependency.from_cell_id
                    FROM {dependency_table} dependency
                    JOIN affected current
                      ON dependency.to_cell_id = current.id
                    JOIN {cell_table} dependent
                      ON dependent.id = dependency.from_cell_id
                    WHERE dependency.is_deleted = FALSE
                      AND dependent.is_deleted = FALSE
                      AND dependent.sheet_id = ANY(%s)
                )
                SELECT id FROM affected
            """
            with connection.cursor() as cursor:
                cursor.execute(
                    sql,
                    [list(changed_ids), list(sheet_ids), list(sheet_ids)],
                )
                affected_ids = {row[0] for row in cursor.fetchall()}
        else:
            # Portable fallback used by non-PostgreSQL unit environments.
            affected_ids = set()
            frontier = set(changed_ids)
            while frontier:
                next_ids = set(
                    CellDependency.objects.filter(
                        to_cell_id__in=frontier,
                        from_cell__sheet_id__in=sheet_ids,
                        from_cell__is_deleted=False,
                        is_deleted=False,
                    ).values_list('from_cell_id', flat=True)
                )
                next_ids -= affected_ids
                next_ids -= changed_ids
                affected_ids.update(next_ids)
                frontier = next_ids

        affected_ids -= changed_ids
        return list(
            Cell.objects.filter(
                id__in=affected_ids,
                is_deleted=False,
            ).select_related('sheet', 'row', 'column')
        )

    @staticmethod
    def _recalculate_formula_cells(changed_cells: List[Cell]) -> List[Cell]:
        affected_cells = CellService._collect_dependent_formula_cells(changed_cells)
        changed_formula_cells = [
            cell for cell in changed_cells
            if (cell.raw_input or '').startswith('=')
            or (cell.value_type == CellValueType.FORMULA and (cell.formula_value or '').startswith('='))
        ]
        candidate_ids = {
            cell.id
            for cell in affected_cells + changed_formula_cells
            if cell.id is not None
        }
        all_cells = {
            cell.id: cell
            for cell in Cell.objects.filter(
                id__in=candidate_ids,
                is_deleted=False,
            ).select_related('sheet', 'row', 'column')
        }

        if not all_cells:
            return []

        affected_ids = set(all_cells.keys())
        dependencies = CellDependency.objects.filter(
            from_cell_id__in=affected_ids,
            to_cell_id__in=affected_ids,
            is_deleted=False
        )

        in_degree = {cell_id: 0 for cell_id in affected_ids}
        adjacency = {cell_id: [] for cell_id in affected_ids}

        for dependency in dependencies:
            from_id = dependency.from_cell_id
            to_id = dependency.to_cell_id
            in_degree[from_id] += 1
            adjacency[to_id].append(from_id)

        from collections import deque
        queue = deque([cell_id for cell_id, degree in in_degree.items() if degree == 0])
        levels = []
        ordered_set = set()

        while queue:
            level_ids = list(queue)
            queue.clear()
            levels.append(level_ids)
            ordered_set.update(level_ids)
            for current_id in level_ids:
                for dependent_id in adjacency.get(current_id, []):
                    in_degree[dependent_id] -= 1
                    if in_degree[dependent_id] == 0:
                        queue.append(dependent_id)

        cycle_ids = affected_ids - ordered_set
        updated_cells = []

        udfs = {}
        if all_cells:
            project = next(iter(all_cells.values())).sheet.spreadsheet.project
            udf_qs = UserDefinedFunction.objects.filter(project=project)
            udfs = {
                udf.name.upper():{
                    "name": udf.name.upper(),
                    "params": udf.params,
                    "expression": udf.expression,
                }
                for udf in udf_qs
            }

        formula_contexts = {}
        for sheet_id in {cell.sheet_id for cell in all_cells.values()}:
            formula_contexts[sheet_id] = {
                'rows': set(
                    SheetRow.objects.filter(
                        sheet_id=sheet_id,
                        is_deleted=False,
                    ).values_list('position', flat=True)
                ),
                'columns': set(
                    SheetColumn.objects.filter(
                        sheet_id=sheet_id,
                        is_deleted=False,
                    ).values_list('position', flat=True)
                ),
                'cells': {},
            }
        for cell in all_cells.values():
            context = formula_contexts[cell.sheet_id]
            context['cells'][(cell.row.position, cell.column.position)] = cell
            cell.sheet._formula_active_row_positions = context['rows']
            cell.sheet._formula_active_column_positions = context['columns']
            cell.sheet._formula_cell_cache = context['cells']

        update_fields = [
            'computed_type',
            'computed_number',
            'computed_string',
            'error_code',
            'updated_at',
        ]
        for level_ids in levels:
            level_updates = []
            for cell_id in level_ids:
                cell = all_cells[cell_id]
                raw_input = cell.raw_input or ''
                formula_source = raw_input
                if not raw_input.startswith('='):
                    formula_value = cell.formula_value or ''
                    if cell.value_type == CellValueType.FORMULA and formula_value.startswith('='):
                        formula_source = formula_value
                    else:
                        continue

                # SPARKLINE cells are not scalar formulas: resolve the chart
                # spec + series ourselves and stash it as JSON in computed_string.
                if is_sparkline(formula_source):
                    try:
                        payload = compute_sparkline(formula_source, cell.sheet)
                        cell.computed_type = ComputedCellType.STRING
                        cell.computed_number = None
                        cell.computed_string = json.dumps(payload)
                        cell.error_code = None
                    except DRFValidationError:
                        cell.computed_type = ComputedCellType.ERROR
                        cell.computed_number = None
                        cell.computed_string = None
                        cell.error_code = '#ERROR!'
                    cell.updated_at = timezone.now()
                    level_updates.append(cell)
                    continue

                result = evaluate_formula(formula_source, cell.sheet, udfs)
                cell.computed_type = result.computed_type
                if result.computed_type == ComputedCellType.NUMBER and result.computed_number is not None:
                    cell.computed_number = Decimal(str(result.computed_number))
                else:
                    cell.computed_number = None
                cell.computed_string = result.computed_string
                cell.error_code = result.error_code
                cell.updated_at = timezone.now()
                level_updates.append(cell)
            if level_updates:
                Cell.objects.bulk_update(
                    level_updates,
                    update_fields,
                    batch_size=1000,
                )
                updated_cells.extend(level_updates)

        cycle_updates = []
        for cell_id in cycle_ids:
            cell = all_cells[cell_id]
            cell.computed_type = ComputedCellType.ERROR
            cell.computed_number = None
            cell.computed_string = None
            cell.error_code = "#CYCLE!"
            cell.updated_at = timezone.now()
            cycle_updates.append(cell)
        if cycle_updates:
            Cell.objects.bulk_update(
                cycle_updates,
                update_fields,
                batch_size=1000,
            )
            updated_cells.extend(cycle_updates)

        return updated_cells

    @staticmethod
    @transaction.atomic
    def recalculate_sheet_formulas(sheet: Sheet) -> None:
        """
        Recalculate all formula cells in a sheet. Used after import finalize when
        import_mode deferred formula computation.

        Also rebuilds dependency edges for each formula cell here, because
        batch_update_cells skips _update_dependencies under import_mode to avoid
        N+1 work per chunk.
        """
        sheet = _lock_sheet_for_mutation(sheet)
        formula_cells = list(
            Cell.objects.filter(
                sheet=sheet,
                is_deleted=False,
                row__is_deleted=False,
                column__is_deleted=False
            ).filter(
                Q(raw_input__startswith='=') | Q(value_type=CellValueType.FORMULA)
            ).select_related('sheet', 'row', 'column')
        )
        if formula_cells:
            for cell in formula_cells:
                CellService._update_dependencies(cell)
            CellService._recalculate_formula_cells(formula_cells)

    @staticmethod
    def _clear_cell(cell: Cell) -> None:
        cell.is_deleted = True
        cell.value_type = CellValueType.EMPTY
        cell.string_value = None
        cell.number_value = None
        cell.boolean_value = None
        cell.formula_value = None
        cell.raw_input = ''
        cell.computed_type = ComputedCellType.EMPTY
        cell.computed_number = None
        cell.computed_string = None
        cell.error_code = None
        cell.save()
        CellDependency.objects.filter(from_cell=cell).update(is_deleted=True)

    @staticmethod
    def _apply_raw_input(cell: Cell, raw_input: Optional[str]) -> None:
        raw_input_value = raw_input or ''
        raw_input_stripped = raw_input_value.strip()

        cell.raw_input = raw_input_value
        cell.error_code = None
        cell.computed_number = None
        cell.computed_string = None
        cell.computed_type = ComputedCellType.EMPTY

        if raw_input_stripped == '':
            cell.value_type = CellValueType.EMPTY
            cell.string_value = None
            cell.number_value = None
            cell.boolean_value = None
            cell.formula_value = None
            return

        if raw_input_value.startswith('='):
            cell.value_type = CellValueType.FORMULA
            cell.string_value = None
            cell.number_value = None
            cell.boolean_value = None
            cell.formula_value = raw_input_value
            cell.computed_type = ComputedCellType.EMPTY
            cell.computed_number = None
            cell.computed_string = None
            cell.error_code = None
            return

        if CellService._NUMERIC_RE.match(raw_input_stripped):
            try:
                number_value = Decimal(raw_input_stripped)
            except InvalidOperation:
                raise ValidationError('Invalid numeric value')
            cell.value_type = CellValueType.NUMBER
            cell.string_value = None
            cell.number_value = number_value
            cell.boolean_value = None
            cell.formula_value = None
            cell.computed_type = ComputedCellType.NUMBER
            cell.computed_number = number_value
            return

        cell.value_type = CellValueType.STRING
        cell.string_value = raw_input_value
        cell.number_value = None
        cell.boolean_value = None
        cell.formula_value = None
        cell.computed_type = ComputedCellType.STRING
        cell.computed_string = raw_input_value

    @staticmethod
    def _apply_value_type(cell: Cell, value_type: str, op: Dict[str, Any]) -> None:
        cell.error_code = None
        cell.computed_number = None
        cell.computed_string = None
        cell.computed_type = ComputedCellType.EMPTY

        if value_type == CellValueType.STRING:
            raw_input = op.get('string_value') or ''
            cell.raw_input = raw_input
            cell.value_type = CellValueType.STRING
            cell.string_value = raw_input
            cell.number_value = None
            cell.boolean_value = None
            cell.formula_value = None
            cell.computed_type = ComputedCellType.STRING
            cell.computed_string = raw_input
            return

        if value_type == CellValueType.NUMBER:
            number_value = op.get('number_value')
            if number_value is not None:
                try:
                    number_value = Decimal(str(number_value))
                except InvalidOperation:
                    raise ValidationError('Invalid numeric value')
            raw_input_override = op.get('raw_input')
            if isinstance(raw_input_override, str):
                cell.raw_input = raw_input_override
            else:
                cell.raw_input = '' if number_value is None else str(number_value)
            cell.value_type = CellValueType.NUMBER
            cell.string_value = None
            cell.number_value = number_value
            cell.boolean_value = None
            cell.formula_value = None
            if number_value is not None:
                cell.computed_type = ComputedCellType.NUMBER
                cell.computed_number = number_value
            return

        if value_type == CellValueType.BOOLEAN:
            boolean_value = op.get('boolean_value')
            cell.raw_input = '' if boolean_value is None else ('TRUE' if boolean_value else 'FALSE')
            cell.value_type = CellValueType.BOOLEAN
            cell.string_value = None
            cell.number_value = None
            cell.boolean_value = boolean_value
            cell.formula_value = None
            if boolean_value is not None:
                cell.computed_type = ComputedCellType.STRING
                cell.computed_string = 'TRUE' if boolean_value else 'FALSE'
            return

        if value_type == CellValueType.FORMULA:
            formula = op.get('formula_value', '')
            cell.raw_input = formula
            cell.value_type = CellValueType.FORMULA
            cell.string_value = None
            cell.number_value = None
            cell.boolean_value = None
            cell.formula_value = formula
            cell.computed_type = ComputedCellType.EMPTY
            cell.computed_number = None
            cell.computed_string = None
            cell.error_code = None
            return

        cell.raw_input = ''
        cell.value_type = CellValueType.EMPTY
        cell.string_value = None
        cell.number_value = None
        cell.boolean_value = None
        cell.formula_value = None
    
    @staticmethod
    def _get_or_create_row(sheet: Sheet, position: int) -> SheetRow:
        """
        Get existing row or create a new one
        
        Args:
            sheet: Sheet instance
            position: Row position
            
        Returns:
            SheetRow instance
        """
        CellService._log_position_duplicates(SheetRow, sheet, position, 'row')
        row = SheetRow.objects.filter(
            sheet=sheet,
            position=position,
            is_deleted=False
        ).first()
        if row:
            return row

        return SheetRow.objects.create(
            sheet=sheet,
            position=position,
            is_deleted=False
        )
    
    @staticmethod
    def _get_or_create_column(sheet: Sheet, position: int) -> SheetColumn:
        """
        Get existing column or create a new one
        
        Args:
            sheet: Sheet instance
            position: Column position
            
        Returns:
            SheetColumn instance
        """
        CellService._log_position_duplicates(SheetColumn, sheet, position, 'column')
        column = SheetColumn.objects.filter(
            sheet=sheet,
            position=position,
            is_deleted=False
        ).first()
        if column:
            return column

        return SheetColumn.objects.create(
            sheet=sheet,
            position=position,
            name=SheetService._generate_column_name(position),
            is_deleted=False
        )
    
    @staticmethod
    def read_cell_range(
        sheet: Sheet,
        start_row: int,
        end_row: int,
        start_column: int,
        end_column: int,
        include_sheet_dimensions: bool = True,
    ) -> Dict[str, Any]:
        """
        Read cells within a specified range.
        Returns a sparse array containing only cells that have values.
        
        Args:
            sheet: Sheet instance
            start_row: Starting row position (inclusive)
            end_row: Ending row position (inclusive)
            start_column: Starting column position (inclusive)
            end_column: Ending column position (inclusive)
            include_sheet_dimensions: When False, skip full-sheet Max(position) queries;
                sheet_row_count / sheet_column_count are None (faster for scroll/tile reads).

        Returns:
            Dict with cells array, row_count, and column_count
        """
        if start_row > end_row:
            raise ValidationError("start_row must be less than or equal to end_row")
        if start_column > end_column:
            raise ValidationError("start_column must be less than or equal to end_column")
        
        # Get rows and columns in range
        rows = SheetRow.objects.filter(
            sheet=sheet,
            position__gte=start_row,
            position__lte=end_row,
            is_deleted=False
        ).select_related('sheet')
        
        columns = SheetColumn.objects.filter(
            sheet=sheet,
            position__gte=start_column,
            position__lte=end_column,
            is_deleted=False
        ).select_related('sheet')
        
        # Get cells in range (only non-empty cells)
        # Use select_related to avoid N+1 queries when accessing sheet.id, row.id, column.id, row.position, column.position
        cells = Cell.objects.filter(
            sheet=sheet,
            row__position__gte=start_row,
            row__position__lte=end_row,
            column__position__gte=start_column,
            column__position__lte=end_column,
            row__is_deleted=False,
            column__is_deleted=False,
            is_deleted=False
        ).exclude(
            value_type=CellValueType.EMPTY
        ).select_related('sheet', 'row', 'column')

        if include_sheet_dimensions:
            # Full sheet dimensions (so the client can size the grid to the whole sheet, not just the requested range)
            row_agg = SheetRow.objects.filter(sheet=sheet, is_deleted=False).aggregate(Max('position'))
            col_agg = SheetColumn.objects.filter(sheet=sheet, is_deleted=False).aggregate(Max('position'))
            sheet_row_count = (row_agg['position__max'] + 1) if row_agg['position__max'] is not None else 0
            sheet_column_count = (col_agg['position__max'] + 1) if col_agg['position__max'] is not None else 0
        else:
            sheet_row_count = None
            sheet_column_count = None

        return {
            'cells': list(cells),
            'row_count': end_row - start_row + 1,
            'column_count': end_column - start_column + 1,
            'sheet_row_count': sheet_row_count,
            'sheet_column_count': sheet_column_count,
            'revision': sheet.revision,
        }
    
    @staticmethod
    @transaction.atomic
    def batch_update_cells(
        sheet: Sheet,
        operations: List[Dict[str, Any]],
        auto_expand: bool = True,
        import_mode: bool = False,
        origin_client_id: Optional[str] = None,
        origin_user_id: Optional[int] = None,
        base_revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Perform multiple cell operations (set or clear) in a single atomic transaction.
        
        This method is Google Sheets-style atomic: if ANY operation fails, the entire batch
        is rolled back (all-or-nothing).
        
        Operation semantics:
        - 'clear': Soft-deletes existing cells (sets is_deleted=True) and wipes all content fields.
                  If row/column/cell does not exist, this is a NO-OP (preserves sparse storage).
                  Does NOT trigger auto-expand of rows/columns.
        - 'set': Creates or updates a cell with the specified value.
                 If value_type is EMPTY, treats as clear operation (soft-delete + wipe, no-op if cell doesn't exist,
                 no auto-expand of rows/columns).
                 Auto-expand of rows/columns only applies when value_type is NOT EMPTY and auto_expand=True.
        
        Args:
            sheet: Sheet instance
            operations: List of operation dicts with keys:
                - operation: 'set' or 'clear'
                - row: Row position
                - column: Column position
                - value_type: Required for 'set' operation (EMPTY is treated as clear)
                - string_value, number_value, boolean_value, formula_value: Value-specific fields
            auto_expand: Whether to automatically create missing rows/columns (only for 'set' operations with non-EMPTY value_type)
            
        Returns:
            Dict with updated, cleared, rows_expanded, columns_expanded
            
        Raises:
            ValidationError: If any operation is invalid. The error detail contains:
                - code: "INVALID_ARGUMENT"
                - details: List of error dicts with index, row, column, field, message
        """
        # Coordinate writes share this mutex with insert/delete/sort/reorder.
        # Acquiring it before any row/column reads gives the whole operation a
        # single authoritative order relative to structural changes.
        sheet = _lock_sheet_for_mutation(sheet)
        _assert_sheet_revision(sheet, base_revision)

        # PHASE 1: VALIDATION (no DB writes)
        # Validate all operations and collect errors
        validation_errors = []
        rows_to_create = set()
        columns_to_create = set()

        # When auto_expand is disabled we must verify each op's (row, column) already exists.
        # This runs even when import_mode=True: real imports call resize_sheet first so every
        # target position exists and these checks are cheap no-ops; callers that skip resize
        # still get a clear 400 instead of silently skipping cell writes.
        # Two queries total (not per-op).
        if not auto_expand:
            existing_row_positions = set(
                SheetRow.objects.filter(sheet=sheet, is_deleted=False)
                .values_list('position', flat=True)
            )
            existing_col_positions = set(
                SheetColumn.objects.filter(sheet=sheet, is_deleted=False)
                .values_list('position', flat=True)
            )
        else:
            existing_row_positions = None
            existing_col_positions = None

        for index, op in enumerate(operations):
            # Validate required fields
            if 'operation' not in op:
                validation_errors.append({
                    'index': index,
                    'row': op.get('row'),
                    'column': op.get('column'),
                    'field': 'operation',
                    'message': 'operation is required'
                })
                continue
            
            operation = op['operation']
            if operation not in ['set', 'clear']:
                validation_errors.append({
                    'index': index,
                    'row': op.get('row'),
                    'column': op.get('column'),
                    'field': 'operation',
                    'message': f'operation must be "set" or "clear", got "{operation}"'
                })
                continue
            
            # Validate row and column positions
            if 'row' not in op:
                validation_errors.append({
                    'index': index,
                    'row': None,
                    'column': op.get('column'),
                    'field': 'row',
                    'message': 'row is required'
                })
                continue
            
            if 'column' not in op:
                validation_errors.append({
                    'index': index,
                    'row': op.get('row'),
                    'column': None,
                    'field': 'column',
                    'message': 'column is required'
                })
                continue
            
            row_pos = op['row']
            col_pos = op['column']
            
            if not isinstance(row_pos, int) or row_pos < 0:
                validation_errors.append({
                    'index': index,
                    'row': row_pos,
                    'column': col_pos,
                    'field': 'row',
                    'message': 'row must be a non-negative integer'
                })
                continue
            
            if not isinstance(col_pos, int) or col_pos < 0:
                validation_errors.append({
                    'index': index,
                    'row': row_pos,
                    'column': col_pos,
                    'field': 'column',
                    'message': 'column must be a non-negative integer'
                })
                continue
            
            # Validate 'set' operation requirements first
            # Auto-expand collection happens AFTER value_type validation
            if operation == 'set':
                raw_input = op.get('raw_input', None)
                raw_input_stripped = raw_input.strip() if isinstance(raw_input, str) else None
                if raw_input is not None:
                    if raw_input_stripped is None:
                        validation_errors.append({
                            'index': index,
                            'row': row_pos,
                            'column': col_pos,
                            'field': 'raw_input',
                            'message': 'raw_input must be a string'
                        })
                        continue

                    # Auto-expand collection only when raw_input is non-empty
                    if raw_input_stripped:
                        if not auto_expand and existing_row_positions is not None:
                            if row_pos not in existing_row_positions:
                                validation_errors.append({
                                    'index': index,
                                    'row': row_pos,
                                    'column': col_pos,
                                    'field': 'row',
                                    'message': f'Row {row_pos} does not exist and auto_expand is disabled'
                                })
                                continue

                            if col_pos not in existing_col_positions:
                                validation_errors.append({
                                    'index': index,
                                    'row': row_pos,
                                    'column': col_pos,
                                    'field': 'column',
                                    'message': f'Column {col_pos} does not exist and auto_expand is disabled'
                                })
                                continue
                        elif auto_expand:
                            rows_to_create.add(row_pos)
                            columns_to_create.add(col_pos)
                    if 'value_type' not in op:
                        continue

                if 'value_type' not in op:
                    validation_errors.append({
                        'index': index,
                        'row': row_pos,
                        'column': col_pos,
                        'field': 'value_type',
                        'message': 'value_type is required when operation is "set"'
                    })
                    continue
                
                value_type = op['value_type']
                if value_type not in [choice[0] for choice in CellValueType.choices]:
                    validation_errors.append({
                        'index': index,
                        'row': row_pos,
                        'column': col_pos,
                        'field': 'value_type',
                        'message': f'Invalid value_type: {value_type}'
                    })
                    continue
                
                # Validate value_type-specific fields
                if value_type == CellValueType.STRING:
                    if 'string_value' not in op:
                        validation_errors.append({
                            'index': index,
                            'row': row_pos,
                            'column': col_pos,
                            'field': 'string_value',
                            'message': 'string_value is required when value_type is "string"'
                        })
                
                elif value_type == CellValueType.NUMBER:
                    if 'number_value' not in op or op.get('number_value') is None:
                        validation_errors.append({
                            'index': index,
                            'row': row_pos,
                            'column': col_pos,
                            'field': 'number_value',
                            'message': 'number_value is required when value_type is "number"'
                        })
                
                elif value_type == CellValueType.BOOLEAN:
                    if 'boolean_value' not in op or op.get('boolean_value') is None:
                        validation_errors.append({
                            'index': index,
                            'row': row_pos,
                            'column': col_pos,
                            'field': 'boolean_value',
                            'message': 'boolean_value is required when value_type is "boolean"'
                        })
                
                elif value_type == CellValueType.FORMULA:
                    formula = op.get('formula_value', '')
                    if not formula:
                        validation_errors.append({
                            'index': index,
                            'row': row_pos,
                            'column': col_pos,
                            'field': 'formula_value',
                            'message': 'formula_value is required when value_type is "formula"'
                        })
                    elif not formula.startswith('='):
                        validation_errors.append({
                            'index': index,
                            'row': row_pos,
                            'column': col_pos,
                            'field': 'formula_value',
                            'message': 'formula_value must start with "="'
                        })
                
                # Auto-expand collection: only AFTER value_type is validated and confirmed != EMPTY
                # set+EMPTY must never trigger expansion (treated as clear)
                if value_type != CellValueType.EMPTY:
                    # Check if row/column exists (for non-auto-expand mode).
                    # Uses the set snapshot built above for O(1) lookup (no per-op SQL).
                    if not auto_expand and existing_row_positions is not None:
                        if row_pos not in existing_row_positions:
                            validation_errors.append({
                                'index': index,
                                'row': row_pos,
                                'column': col_pos,
                                'field': 'row',
                                'message': f'Row {row_pos} does not exist and auto_expand is disabled'
                            })
                            continue

                        if col_pos not in existing_col_positions:
                            validation_errors.append({
                                'index': index,
                                'row': row_pos,
                                'column': col_pos,
                                'field': 'column',
                                'message': f'Column {col_pos} does not exist and auto_expand is disabled'
                            })
                            continue
                    elif auto_expand:
                        # Track rows/columns to create (only for validated non-EMPTY 'set' operations)
                        rows_to_create.add(row_pos)
                        columns_to_create.add(col_pos)
        
        # If validation errors exist, raise before any DB writes (triggers rollback).
        # Do not use django.core.exceptions.ValidationError for structured payloads:
        # Django flattens nested dicts/lists into ErrorDetail/strings and breaks API clients.
        if validation_errors:
            raise CellBatchArgumentError(validation_errors)

        # PHASE 2: EXECUTION (bulk writes - no per-cell save/update_or_create)
        # Note: previously this section called _log_position_duplicates once per unique
        # row/col position for diagnostics, which issued hundreds of extra queries per
        # chunk during import. The low-traffic _get_or_create_row/_column paths still
        # log duplicates, so diagnostic coverage is preserved.
        row_positions = {op['row'] for op in operations}
        column_positions = {op['column'] for op in operations}

        # --- Bulk create missing rows (1 query fetch + 1 bulk_create) ---
        # Use ignore_conflicts=True so concurrent chunks racing to create the same
        # (sheet, position) row do not trigger IntegrityError against the partial
        # unique constraint `unique_row_position_per_sheet_active`. Re-SELECT afterwards
        # because ignore_conflicts=True does not backfill primary keys.
        existing_rows_list = list(
            SheetRow.objects.filter(
                sheet=sheet,
                position__in=row_positions,
                is_deleted=False
            ).select_related('sheet')
        )
        existing_rows = {r.position: r for r in existing_rows_list}
        missing_row_positions = [p for p in rows_to_create if p not in existing_rows]
        rows_expanded = 0
        if missing_row_positions:
            new_rows = [
                SheetRow(sheet=sheet, position=p, is_deleted=False)
                for p in missing_row_positions
            ]
            SheetRow.objects.bulk_create(new_rows, batch_size=500, ignore_conflicts=True)
            refreshed_rows = list(
                SheetRow.objects.filter(
                    sheet=sheet,
                    position__in=missing_row_positions,
                    is_deleted=False,
                ).select_related('sheet')
            )
            rows_expanded = len(refreshed_rows)
            for r in refreshed_rows:
                existing_rows[r.position] = r

        # --- Bulk create missing columns (1 query fetch + 1 bulk_create) ---
        existing_columns_list = list(
            SheetColumn.objects.filter(
                sheet=sheet,
                position__in=column_positions,
                is_deleted=False
            ).select_related('sheet')
        )
        existing_columns = {c.position: c for c in existing_columns_list}
        missing_col_positions = [p for p in columns_to_create if p not in existing_columns]
        columns_expanded = 0
        if missing_col_positions:
            new_cols = [
                SheetColumn(
                    sheet=sheet,
                    position=p,
                    name=SheetService._generate_column_name(p),
                    is_deleted=False
                )
                for p in missing_col_positions
            ]
            SheetColumn.objects.bulk_create(new_cols, batch_size=500, ignore_conflicts=True)
            refreshed_cols = list(
                SheetColumn.objects.filter(
                    sheet=sheet,
                    position__in=missing_col_positions,
                    is_deleted=False,
                ).select_related('sheet')
            )
            columns_expanded = len(refreshed_cols)
            for c in refreshed_cols:
                existing_columns[c.position] = c

        # --- Bulk fetch all existing cells for (row_pos, col_pos) in operations ---
        # Use row_id__in + column_id__in instead of a giant OR of Q(row__position=, column__position=)
        # to (1) keep the Q tree shallow (avoids RecursionError for large batches), and
        # (2) hit the (sheet, row, column) composite index directly without JOINs to SheetRow/SheetColumn.
        op_keys = {(op['row'], op['column']) for op in operations}
        row_ids = [r.id for r in existing_rows.values()]
        col_ids = [c.id for c in existing_columns.values()]
        cell_by_pos: Dict[Tuple[int, int], Cell] = {}
        if row_ids and col_ids:
            all_existing_cells = list(
                Cell.objects.filter(
                    sheet=sheet,
                    row_id__in=row_ids,
                    column_id__in=col_ids,
                ).only(
                    'id', 'sheet_id', 'row_id', 'column_id', 'is_deleted',
                    'value_type', 'string_value', 'number_value',
                    'boolean_value', 'formula_value', 'raw_input',
                    'computed_type', 'computed_number', 'computed_string', 'error_code',
                    'updated_at',
                )
            )
            row_pos_by_id = {r.id: p for p, r in existing_rows.items()}
            col_pos_by_id = {c.id: p for p, c in existing_columns.items()}
            for cell in all_existing_cells:
                rp = row_pos_by_id.get(cell.row_id)
                cp = col_pos_by_id.get(cell.column_id)
                if rp is None or cp is None:
                    continue
                if (rp, cp) in op_keys:
                    cell_by_pos[(rp, cp)] = cell

        # --- Split into to_clear, to_update, to_create (last op per cell wins) ---
        to_clear: List[Cell] = []
        pending_update: Dict[Tuple[int, int], Cell] = {}
        pending_create: Dict[Tuple[int, int], Cell] = {}
        updated_cells: Dict[int, Cell] = {}
        cleared_ids: List[int] = []

        CELL_CLEAR_FIELDS = [
            'is_deleted', 'value_type', 'string_value', 'number_value',
            'boolean_value', 'formula_value', 'raw_input', 'computed_type',
            'computed_number', 'computed_string', 'error_code', 'updated_at'
        ]
        CELL_SET_FIELDS = [
            'is_deleted', 'value_type', 'string_value', 'number_value',
            'boolean_value', 'formula_value', 'raw_input', 'computed_type',
            'computed_number', 'computed_string', 'error_code', 'updated_at'
        ]

        for op in operations:
            row_pos, col_pos = op['row'], op['column']
            operation = op['operation']
            row = existing_rows.get(row_pos)
            column = existing_columns.get(col_pos)
            k = (row_pos, col_pos)

            def do_clear():
                pending_update.pop(k, None)
                pending_create.pop(k, None)
                cell = cell_by_pos.get(k)
                if cell is not None and row and column:
                    if not cell.is_deleted:
                        cell.is_deleted = True
                        cell.value_type = CellValueType.EMPTY
                        cell.string_value = None
                        cell.number_value = None
                        cell.boolean_value = None
                        cell.formula_value = None
                        cell.raw_input = ''
                        cell.computed_type = ComputedCellType.EMPTY
                        cell.computed_number = None
                        cell.computed_string = None
                        cell.error_code = None
                        to_clear.append(cell)
                        cleared_ids.append(cell.id)
                        updated_cells[cell.id] = cell

            if operation == 'clear':
                if row is not None and column is not None:
                    do_clear()
                continue

            if operation == 'set':
                raw_input = op.get('raw_input', None)
                value_type = op.get('value_type')
                if raw_input is not None and value_type is None:
                    raw_input_stripped = (raw_input.strip() if isinstance(raw_input, str) else '') or ''
                    if raw_input_stripped == '':
                        if row and column:
                            do_clear()
                        continue
                    # Set from raw_input
                    cell = cell_by_pos.get(k)
                    if cell is not None:
                        pending_update.pop(k, None)
                        pending_create.pop(k, None)
                        if cell.is_deleted:
                            cell.is_deleted = False
                        CellService._apply_raw_input(cell, raw_input)
                        pending_update[k] = cell
                        updated_cells[cell.id] = cell
                    else:
                        if row is None or column is None:
                            continue
                        new_cell = Cell(sheet=sheet, row=row, column=column, is_deleted=False)
                        CellService._apply_raw_input(new_cell, raw_input)
                        pending_create[k] = new_cell
                    continue

                value_type = op['value_type']
                if value_type == CellValueType.EMPTY:
                    if row and column:
                        do_clear()
                    continue

                # Set from value_type
                if row is None or column is None:
                    continue
                cell = cell_by_pos.get(k)
                if cell is not None:
                    pending_update.pop(k, None)
                    pending_create.pop(k, None)
                    if cell.is_deleted:
                        cell.is_deleted = False
                    cell.value_type = value_type
                    CellService._apply_value_type(cell, value_type, op)
                    pending_update[k] = cell
                    updated_cells[cell.id] = cell
                else:
                    new_cell = Cell(sheet=sheet, row=row, column=column, is_deleted=False)
                    new_cell.value_type = value_type
                    CellService._apply_value_type(new_cell, value_type, op)
                    pending_create[k] = new_cell

        # --- Bulk execute: clear, update, create ---
        if to_clear:
            from django.utils import timezone
            for c in to_clear:
                c.updated_at = timezone.now()
            Cell.objects.bulk_update(to_clear, CELL_CLEAR_FIELDS, batch_size=1000)
            if cleared_ids:
                CellDependency.objects.filter(from_cell_id__in=cleared_ids).update(is_deleted=True)

        to_update = list(pending_update.values())
        to_create = list(pending_create.values())
        if to_update:
            from django.utils import timezone
            for c in to_update:
                c.updated_at = timezone.now()
            Cell.objects.bulk_update(to_update, CELL_SET_FIELDS, batch_size=1000)

        if to_create:
            created = Cell.objects.bulk_create(to_create, batch_size=1000)
            for c in created:
                updated_cells[c.id] = c

        # --- Preserve formula behavior: update dependencies for formula cells ---
        # In import_mode, defer dependency rebuilding to recalculate_sheet_formulas
        # (called from ImportFinalizeView). This avoids per-cell get_or_create churn
        # while ingesting large CSV/XLSX imports that typically have no formulas.
        if not import_mode:
            formula_cells = [
                c for c in (to_update + to_create)
                if (c.raw_input or '').startswith('=') or (
                    c.value_type == CellValueType.FORMULA and (c.formula_value or '').startswith('=')
                )
            ]
            for cell in formula_cells:
                CellService._update_dependencies(cell)

        updated = len(to_update) + len(to_create)
        cleared = len(to_clear)
        
        if not import_mode:
            recalculated_cells = CellService._recalculate_formula_cells(list(updated_cells.values()))
            for cell in recalculated_cells:
                updated_cells[cell.id] = cell

        # Auto-expansion changes the coordinate structure just like an explicit
        # resize. Advance the revision in the same transaction so peers reload
        # before applying coordinates based on the expanded grid.
        if rows_expanded or columns_expanded:
            _advance_sheet_revision(sheet)

        result = {
            'updated': updated,
            'cleared': cleared,
            'rows_expanded': rows_expanded,
            'columns_expanded': columns_expanded,
            'revision': sheet.revision,
        }
        if not import_mode:
            result['cells'] = list(updated_cells.values())
        else:
            result['cells'] = []

        # After successful batch update, recompute any pivot sheets depending on this sheet.
        # Skip during import_mode: per-chunk recompute would rerun the full pivot for every chunk
        # which is O(N) expensive; ImportFinalizeView runs a single recompute at the end instead.
        if not import_mode:
            try:
                from .pivot_service import recompute_pivots_for_source_sheet
                recompute_pivots_for_source_sheet(sheet)
            except Exception:
                logger.exception("Pivot recompute after batch_update_cells failed for sheet_id=%s", sheet.id)

        # Realtime collab: queue a cells_updated broadcast (delivered on commit).
        # import_mode skips it: imports write thousands of cells per chunk and
        # ImportFinalizeView triggers a full reload on completion anyway.
        if not import_mode:
            broadcast_cells_updated(
                sheet_id=sheet.id,
                cells=result['cells'],
                origin_client_id=origin_client_id,
                origin_user_id=origin_user_id,
                revision=sheet.revision,
            )

        return result


class WorkflowPatternService:
    """Service for applying workflow patterns to sheets."""

    @staticmethod
    def _column_label_to_index(label: str) -> int:
        result = 0
        for char in label:
            if not char.isalpha():
                raise ValueError(f"Invalid column label: {label}")
            result = result * 26 + (ord(char.upper()) - 64)
        return result - 1

    @staticmethod
    def _adjust_formula_references(formula: str, row_delta: int, col_delta: int) -> str:
        if not formula.startswith('=') or (row_delta == 0 and col_delta == 0):
            return formula

        def replace(match: re.Match) -> str:
            prefix, col_label, row_str = match.groups()
            try:
                row = int(row_str)
                if row <= 0:
                    return match.group(0)
                col_index = WorkflowPatternService._column_label_to_index(col_label)
                next_col = col_index + col_delta
                next_row = row + row_delta
                if next_col < 0 or next_row <= 0:
                    return match.group(0)
                next_label = SheetService._generate_column_name(next_col)
                return f"{prefix}{next_label}{next_row}"
            except Exception:
                return match.group(0)

        return re.sub(r'(^|[^A-Z0-9$])([A-Z]+)(\d+)', replace, formula)

    @staticmethod
    def _normalize_header(value: str) -> str:
        return re.sub(r'\s+', ' ', value.strip())

    @staticmethod
    def _header_key(value: str) -> str:
        return WorkflowPatternService._normalize_header(value).lower()

    @staticmethod
    def _resolve_header_column(sheet: Sheet, header_row: int, params: Dict[str, Any]) -> Optional[int]:
        from_header = params.get('from_header')
        locator = params.get('column_locator') or {}
        fallback_index = locator.get('fallback_index')

        columns = list(
            SheetColumn.objects.filter(sheet=sheet, is_deleted=False)
            .order_by('position')
            .values_list('position', flat=True)
        )
        if not columns:
            return None

        header_cells = {
            cell['column__position']: cell
            for cell in Cell.objects.filter(
                sheet=sheet,
                row__position=header_row,
                row__is_deleted=False,
                column__is_deleted=False,
                is_deleted=False
            ).values('column__position', 'raw_input', 'string_value', 'computed_string')
        }

        def get_header_text(col_pos: int) -> str:
            cell = header_cells.get(col_pos)
            if not cell:
                return ''
            return (
                cell.get('raw_input')
                or cell.get('string_value')
                or cell.get('computed_string')
                or ''
            )

        if from_header:
            target_key = WorkflowPatternService._header_key(str(from_header))
            for col_pos in columns:
                if WorkflowPatternService._header_key(get_header_text(col_pos)) == target_key:
                    return col_pos
            return None

        if fallback_index is None:
            return None
        fallback_pos = int(fallback_index) - 1
        if fallback_pos in columns and WorkflowPatternService._normalize_header(get_header_text(fallback_pos)) == '':
            return fallback_pos

        for offset in range(1, len(columns) + 1):
            for candidate in (fallback_pos - offset, fallback_pos + offset):
                if candidate in columns and WorkflowPatternService._normalize_header(get_header_text(candidate)) == '':
                    return candidate
        return None

    @staticmethod
    def _execute_one_step(
        sheet: Sheet,
        step_type: str,
        params: Dict[str, Any],
        created_by: Optional[Any] = None,
    ) -> None:
        """Execute a single pattern step (type, params). step_type is normalized to uppercase."""
        t = (step_type or '').strip().upper() if isinstance(step_type, str) else ''
        if t == 'GROUP':
            # Should be expanded by caller; no-op here to avoid recursion
            return
        if t == 'APPLY_FORMULA':
            target = params.get('target') or {}
            formula = params.get('formula')
            if not formula or not isinstance(formula, str):
                raise ValidationError("Missing formula for APPLY_FORMULA step")
            row = target.get('row')
            col = target.get('col')
            if row is None or col is None:
                raise ValidationError("Missing target cell for APPLY_FORMULA step")
            row_position = int(row) - 1
            col_position = int(col) - 1
            if row_position < 0 or col_position < 0:
                raise ValidationError("Invalid target cell for APPLY_FORMULA step")
            CellService.batch_update_cells(
                sheet=sheet,
                operations=[{
                    'operation': 'set',
                    'row': row_position,
                    'column': col_position,
                    'raw_input': formula,
                }],
                auto_expand=True
            )
        elif t == 'INSERT_ROW':
            index = params.get('index')
            position = params.get('position')
            if index is None or position not in ['above', 'below']:
                raise ValidationError("Invalid INSERT_ROW params")
            insert_position = int(index) - 1 if position == 'above' else int(index)
            if insert_position < 0:
                raise ValidationError("Invalid row index for INSERT_ROW step")
            SheetService.insert_rows(sheet=sheet, position=insert_position, count=1, created_by=created_by)
        elif t == 'INSERT_COLUMN':
            index = params.get('index')
            position = params.get('position')
            if index is None or position not in ['left', 'right']:
                raise ValidationError("Invalid INSERT_COLUMN params")
            insert_position = int(index) - 1 if position == 'left' else int(index)
            if insert_position < 0:
                raise ValidationError("Invalid column index for INSERT_COLUMN step")
            SheetService.insert_columns(sheet=sheet, position=insert_position, count=1, created_by=created_by)
        elif t == 'DELETE_COLUMN':
            index = params.get('index')
            if index is None:
                raise ValidationError("Invalid DELETE_COLUMN params")
            delete_position = int(index) - 1
            if delete_position < 0:
                raise ValidationError("Invalid column index for DELETE_COLUMN step")
            SheetService.delete_columns(sheet=sheet, position=delete_position, count=1, created_by=created_by)
        elif t == 'SET_COLUMN_NAME':
            header_row_index = params.get('header_row_index', 1)
            to_header = params.get('to_header')
            if to_header is None:
                raise ValidationError("Invalid SET_COLUMN_NAME params")
            header_row = int(header_row_index) - 1
            if header_row < 0:
                raise ValidationError("Invalid SET_COLUMN_NAME target")
            # Simplified semantics: always use the recorded column position (column_ref.index)
            # to decide which header cell to rename, instead of matching header text.
            column_ref = params.get('column_ref') or {}
            col_index_1based = column_ref.get('index')
            target_col = None
            if col_index_1based is not None:
                # column_ref.index is 1-based; convert to 0-based SheetColumn.position
                target_col_candidate = int(col_index_1based) - 1
                if target_col_candidate < 0:
                    raise ValidationError("Invalid column index for SET_COLUMN_NAME step")
                # Only apply if a column actually exists at this position
                if SheetColumn.objects.filter(sheet=sheet, position=target_col_candidate, is_deleted=False).exists():
                    target_col = target_col_candidate
            if target_col is not None:
                operation = {
                    'operation': 'clear' if str(to_header).strip() == '' else 'set',
                    'row': header_row,
                    'column': target_col,
                    'raw_input': str(to_header),
                }
                CellService.batch_update_cells(sheet=sheet, operations=[operation], auto_expand=True)
        elif t == 'APPLY_HIGHLIGHT':
            scope = (params.get('scope') or 'CELL').strip().upper()
            if scope not in ('CELL', 'ROW', 'COLUMN', 'RANGE'):
                scope = 'CELL'
            color = params.get('color') or '#FEF08A'
            target = params.get('target') or {}
            fallback = target.get('fallback') or {}
            header_row_index = int(params.get('header_row_index', 1)) - 1
            if header_row_index < 0:
                header_row_index = 0

            def _to_0based(val):
                if val is None:
                    return None
                v = int(val)
                return v - 1 if v >= 1 else 0

            spreadsheet = sheet.spreadsheet
            if scope == 'CELL':
                row_index = _to_0based(fallback.get('row_index'))
                col_index = _to_0based(fallback.get('col_index'))
                if row_index is None:
                    row_index = 0
                if col_index is None:
                    col_index = 0
                SpreadsheetHighlight.objects.update_or_create(
                    sheet=sheet,
                    scope=SpreadsheetHighlightScope.CELL,
                    row_index=row_index,
                    col_index=col_index,
                    defaults={'spreadsheet': spreadsheet, 'color': color},
                )
            elif scope == 'ROW':
                row_index = _to_0based(fallback.get('row_index'))
                if row_index is None:
                    raise ValidationError("APPLY_HIGHLIGHT ROW: missing row_index in target.fallback")
                SpreadsheetHighlight.objects.update_or_create(
                    sheet=sheet,
                    scope=SpreadsheetHighlightScope.ROW,
                    row_index=row_index,
                    col_index=0,
                    defaults={'spreadsheet': spreadsheet, 'color': color},
                )
            elif scope == 'COLUMN':
                col_index = _to_0based(fallback.get('col_index'))
                if col_index is None:
                    col_resolved = WorkflowPatternService._resolve_header_column(sheet, header_row_index, target)
                    if col_resolved is None:
                        raise ValidationError("APPLY_HIGHLIGHT COLUMN: could not resolve column from target")
                    col_index = col_resolved
                SpreadsheetHighlight.objects.update_or_create(
                    sheet=sheet,
                    scope=SpreadsheetHighlightScope.COLUMN,
                    row_index=0,
                    col_index=col_index,
                    defaults={'spreadsheet': spreadsheet, 'color': color},
                )
            else:
                start_row = _to_0based(fallback.get('start_row')) or 0
                start_col = _to_0based(fallback.get('start_col')) or 0
                end_row = _to_0based(fallback.get('end_row'))
                end_col = _to_0based(fallback.get('end_col'))
                if end_row is None:
                    end_row = start_row
                if end_col is None:
                    end_col = start_col
                for r in range(min(start_row, end_row), max(start_row, end_row) + 1):
                    for c in range(min(start_col, end_col), max(start_col, end_col) + 1):
                        SpreadsheetHighlight.objects.update_or_create(
                            sheet=sheet,
                            scope=SpreadsheetHighlightScope.CELL,
                            row_index=r,
                            col_index=c,
                            defaults={'spreadsheet': spreadsheet, 'color': color},
                        )
        elif t == 'FILL_SERIES':
            source = params.get('source') or {}
            fill_range = params.get('range') or {}
            source_row = source.get('row')
            source_col = source.get('col')
            start_row = fill_range.get('start_row')
            end_row = fill_range.get('end_row')
            start_col = fill_range.get('start_col')
            end_col = fill_range.get('end_col')
            if None in [source_row, source_col, start_row, end_row, start_col, end_col]:
                raise ValidationError("Invalid FILL_SERIES params")
            source_row = int(source_row) - 1
            source_col = int(source_col) - 1
            start_row = int(start_row) - 1
            end_row = int(end_row) - 1
            start_col = int(start_col) - 1
            end_col = int(end_col) - 1
            if source_row < 0 or source_col < 0:
                raise ValidationError("Invalid source cell for FILL_SERIES step")
            if min(start_row, end_row, start_col, end_col) < 0:
                raise ValidationError("Invalid range for FILL_SERIES step")
            row_start = min(start_row, end_row)
            row_end = max(start_row, end_row)
            col_start = min(start_col, end_col)
            col_end = max(start_col, end_col)
            source_cell = Cell.objects.filter(
                sheet=sheet,
                row__position=source_row,
                column__position=source_col,
                row__is_deleted=False,
                column__is_deleted=False,
                is_deleted=False
            ).select_related('row', 'column').first()
            source_raw_input = ''
            if source_cell is not None:
                source_raw_input = (
                    source_cell.raw_input or source_cell.formula_value
                    or source_cell.string_value or ''
                )
            operations = []
            for row in range(row_start, row_end + 1):
                for col in range(col_start, col_end + 1):
                    if row == source_row and col == source_col:
                        continue
                    row_delta = row - source_row
                    col_delta = col - source_col
                    next_raw_input = (
                        WorkflowPatternService._adjust_formula_references(source_raw_input, row_delta, col_delta)
                        if source_raw_input.startswith('=')
                        else source_raw_input
                    )
                    operations.append({
                        'operation': 'clear' if next_raw_input.strip() == '' else 'set',
                        'row': row,
                        'column': col,
                        'raw_input': next_raw_input,
                    })
            if operations:
                CellService.batch_update_cells(sheet=sheet, operations=operations, auto_expand=True)
        else:
            raise ValidationError(f"Unsupported step type {step_type}")

    @staticmethod
    def apply_pattern(
        pattern: WorkflowPattern,
        sheet: Sheet,
        created_by: Optional[Any] = None,
        progress_callback: Optional[Any] = None
    ) -> None:
        steps = list(
            WorkflowPatternStep.objects.filter(pattern=pattern, is_deleted=False).order_by('seq')
        )
        # Flatten GROUP steps into a single list (preserve order; no nested groups)
        def _normalize_type(t):
            return (t or '').strip().upper() if t else ''

        flat_steps = []
        for step in steps:
            step_type_normalized = _normalize_type(step.type)
            if step_type_normalized == 'GROUP':
                if step.disabled:
                    continue
                params = step.params or {}
                for sub in params.get('items') or []:
                    sub_type = sub.get('type') or 'APPLY_FORMULA'
                    if _normalize_type(sub_type) == 'GROUP':
                        # Nested group: expand recursively
                        sub_params = sub.get('params') or {}
                        for sub_sub in sub_params.get('items') or []:
                            flat_steps.append({
                                'type': sub_sub.get('type') or 'APPLY_FORMULA',
                                'params': sub_sub.get('params') or {},
                                'disabled': bool(sub_sub.get('disabled')),
                            })
                    else:
                        flat_steps.append({
                            'type': sub_type,
                            'params': sub.get('params') or {},
                            'disabled': bool(sub.get('disabled')),
                        })
            else:
                flat_steps.append({
                    'type': step.type,
                    'params': step.params or {},
                    'disabled': step.disabled,
                })
        total_steps = len(flat_steps)
        if total_steps == 0:
            if progress_callback:
                progress_callback(0, 0, 0)
            return

        completed = 0
        for idx, flat_step in enumerate(flat_steps):
            if progress_callback:
                progress_callback(idx + 1, completed, total_steps)

            if flat_step['disabled']:
                completed += 1
                if progress_callback:
                    progress_callback(idx + 1, completed, total_steps)
                continue

            step_type_raw = flat_step.get('type') or ''
            step_type = (step_type_raw if isinstance(step_type_raw, str) else str(step_type_raw)).strip().upper()
            params = flat_step['params'] or {}

            if step_type == 'GROUP':
                # Safety net: expand and run group items (should normally be flattened above)
                for sub in params.get('items') or []:
                    if sub.get('disabled'):
                        continue
                    sub_type = (sub.get('type') or 'APPLY_FORMULA')
                    if isinstance(sub_type, str) and sub_type.strip().upper() == 'GROUP':
                        sub_params = sub.get('params') or {}
                        for sub_sub in sub_params.get('items') or []:
                            if sub_sub.get('disabled'):
                                continue
                            WorkflowPatternService._execute_one_step(
                                sheet,
                                sub_sub.get('type') or 'APPLY_FORMULA',
                                sub_sub.get('params') or {},
                                created_by,
                            )
                    else:
                        WorkflowPatternService._execute_one_step(
                            sheet, sub.get('type') or 'APPLY_FORMULA', sub.get('params') or {}, created_by
                        )
            else:
                WorkflowPatternService._execute_one_step(sheet, step_type_raw, params, created_by)

            completed += 1
            if progress_callback:
                progress_callback(idx + 1, completed, total_steps)
