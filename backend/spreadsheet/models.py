import uuid
from django.db import models
from core.slug_mixins import SluggedResourceModelMixin
from django.conf import settings
from django.db.models import Q
from django.core.exceptions import ValidationError
from core.models import TimeStampedModel


class Spreadsheet(SluggedResourceModelMixin, TimeStampedModel):
    project = models.ForeignKey(
        'core.Project',
        on_delete=models.CASCADE,
        related_name='spreadsheets',
        help_text="Project this spreadsheet belongs to"
    )
    name = models.CharField(
        max_length=200,
        help_text="Name of the spreadsheet"
    )

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['project', 'name'],
                condition=Q(is_deleted=False),
                name='unique_spreadsheet_name_per_project_active'
            ),
        ]
        indexes = [
            models.Index(fields=['project', 'is_deleted']),
        ]

    def __str__(self):
        return f"{self.name} (Project: {self.project.name})"


class SheetKind(models.TextChoices):
    NORMAL = 'normal', 'Normal'
    PIVOT = 'pivot', 'Pivot'


class Sheet(TimeStampedModel):
    spreadsheet = models.ForeignKey(
        Spreadsheet,
        on_delete=models.CASCADE,
        related_name='sheets',
        help_text="Spreadsheet this sheet belongs to"
    )
    name = models.CharField(
        max_length=200,
        help_text="Name of the sheet (tab name)"
    )
    position = models.IntegerField(
        help_text="Position/order of the sheet within the spreadsheet"
    )
    revision = models.PositiveBigIntegerField(
        default=0,
        help_text="Monotonic coordinate-structure revision for collaboration conflict checks"
    )
    kind = models.CharField(
        max_length=20,
        choices=SheetKind.choices,
        default=SheetKind.NORMAL,
        help_text="normal = standard sheet, pivot = pivot table sheet"
    )
    frozen_row_count = models.PositiveSmallIntegerField(
        default=0,
        help_text="Number of rows to freeze (0 = none, 1 = freeze first row, etc.)"
    )
    frozen_column_count = models.PositiveSmallIntegerField(
        default=0,
        help_text="Number of columns to freeze (0 = none)"
    )

    class Meta:
        ordering = ['position', 'created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['spreadsheet', 'name'],
                condition=Q(is_deleted=False),
                name='unique_sheet_name_per_spreadsheet_active'
            ),
            models.UniqueConstraint(
                fields=['spreadsheet', 'position'],
                condition=Q(is_deleted=False),
                name='unique_sheet_position_per_spreadsheet_active'
            ),
            models.CheckConstraint(
                check=Q(position__gte=0),
                name='sheet_position_non_negative'
            ),
        ]
        indexes = [
            models.Index(fields=['spreadsheet', 'is_deleted']),
            models.Index(fields=['spreadsheet', 'position']),
        ]

    def __str__(self):
        return f"{self.name} (in {self.spreadsheet.name})"


class PivotConfig(TimeStampedModel):
    """Persisted pivot table definition linked 1:1 to a pivot sheet."""

    pivot_sheet = models.OneToOneField(
        Sheet,
        on_delete=models.CASCADE,
        related_name='pivot_config',
        help_text="Pivot sheet that displays aggregated results"
    )
    source_sheet = models.ForeignKey(
        Sheet,
        on_delete=models.CASCADE,
        related_name='pivot_sources',
        help_text="Source sheet whose data is aggregated"
    )
    rows_config = models.JSONField(default=list, help_text="List of row field names")
    columns_config = models.JSONField(default=list, help_text="List of column configs (field or {field, sort})")
    values_config = models.JSONField(default=list, help_text="List of {field, aggregation, display}")
    filters_config = models.JSONField(default=dict, blank=True, help_text="Optional filters")
    show_grand_total_row = models.BooleanField(default=True)
    show_grand_total_column = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=['source_sheet'], name='pivotconfig_source_sheet_idx'),
        ]

    def __str__(self):
        return f"PivotConfig pivot_sheet={self.pivot_sheet_id} source_sheet={self.source_sheet_id}"


class SheetRow(TimeStampedModel):
    sheet = models.ForeignKey(
        Sheet,
        on_delete=models.CASCADE,
        related_name='rows',
        help_text="Sheet this row belongs to"
    )
    position = models.IntegerField(
        help_text="Position/row number within the sheet"
    )

    class Meta:
        ordering = ['position']
        constraints = [
            models.UniqueConstraint(
                fields=['sheet', 'position'],
                condition=Q(is_deleted=False),
                name='unique_row_position_per_sheet_active'
            ),
            models.CheckConstraint(
                check=Q(position__gte=0),
                name='sheetrow_position_non_negative'
            ),
        ]
        indexes = [
            models.Index(fields=['sheet', 'is_deleted']),
            models.Index(fields=['sheet', 'position']),
        ]

    def __str__(self):
        return f"Row {self.position} (in {self.sheet.name})"


class SheetColumn(TimeStampedModel): 
    sheet = models.ForeignKey(
        Sheet,
        on_delete=models.CASCADE,
        related_name='columns',
        help_text="Sheet this column belongs to"
    )
    name = models.CharField(
        max_length=200,
        help_text="Name of the column (e.g., 'A', 'B', 'C', should be automatically generated in serialization)"
    )
    position = models.IntegerField(
        help_text="Position/column number within the sheet"
    )

    class Meta:
        ordering = ['position']
        constraints = [
            models.UniqueConstraint(
                fields=['sheet', 'position'],
                condition=Q(is_deleted=False),
                name='unique_column_position_per_sheet_active'
            ),
            models.CheckConstraint(
                check=Q(position__gte=0),
                name='sheetcolumn_position_non_negative'
            ),
        ]
        indexes = [
            models.Index(fields=['sheet', 'is_deleted']),
            models.Index(fields=['sheet', 'position']),
        ]

    @property
    def index(self) -> int:
        """0-based column index; alias for `position`."""
        return self.position

    def __str__(self):
        return f"{self.name} (Column {self.position} in {self.sheet.name})"


class SheetStructureOperation(TimeStampedModel):
    """Log of structural sheet operations for simple revert support."""

    class OperationType(models.TextChoices):
        ROW_INSERT = 'ROW_INSERT', 'Row Insert'
        COL_INSERT = 'COL_INSERT', 'Column Insert'
        ROW_DELETE = 'ROW_DELETE', 'Row Delete'
        COL_DELETE = 'COL_DELETE', 'Column Delete'

    sheet = models.ForeignKey(
        Sheet,
        on_delete=models.CASCADE,
        related_name='structure_operations',
        help_text="Sheet this operation belongs to"
    )
    op_type = models.CharField(
        max_length=20,
        choices=OperationType.choices
    )
    anchor_position = models.IntegerField(
        help_text="Anchor position for the operation (insert/delete start index)"
    )
    count = models.IntegerField(
        help_text="Number of rows/columns inserted or deleted"
    )
    affected_ids = models.JSONField(
        default=list,
        help_text="List of affected row/column IDs"
    )
    affected_positions = models.JSONField(
        default=dict,
        help_text="Mapping of affected ID to original position"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sheet_structure_operations'
    )
    is_reverted = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['sheet', 'op_type']),
            models.Index(fields=['sheet', 'is_reverted']),
        ]

    def __str__(self):
        return f"{self.op_type} on {self.sheet_id} @ {self.anchor_position}"


class CellValueType(models.TextChoices):
    EMPTY = 'empty', 'Empty'
    STRING = 'string', 'String'
    NUMBER = 'number', 'Number'
    BOOLEAN = 'boolean', 'Boolean'
    FORMULA = 'formula', 'Formula'


class ComputedCellType(models.TextChoices):
    EMPTY = 'empty', 'Empty'
    NUMBER = 'number', 'Number'
    STRING = 'string', 'String'
    BOOLEAN = 'boolean', 'Boolean'
    ERROR = 'error', 'Error'


class Cell(TimeStampedModel):
    sheet = models.ForeignKey(
        Sheet,
        on_delete=models.CASCADE,
        related_name='cells',
        help_text="Sheet this cell belongs to"
    )
    row = models.ForeignKey(
        SheetRow,
        on_delete=models.CASCADE,
        related_name='cells',
        help_text="Row this cell belongs to"
    )
    column = models.ForeignKey(
        SheetColumn,
        on_delete=models.CASCADE,
        related_name='cells',
        help_text="Column this cell belongs to"
    )
    value_type = models.CharField(
        max_length=20,
        choices=CellValueType.choices,
        default=CellValueType.EMPTY,
        help_text="Type of value stored in this cell"
    )
    string_value = models.TextField(
        null=True,
        blank=True,
        help_text="String value. Text starting with '=' stored with ' prefix for round-trip editing."
    )
    number_value = models.DecimalField(
        max_digits=1000,
        decimal_places=500,
        null=True,
        blank=True,
        help_text="Numeric value"
    )
    boolean_value = models.BooleanField(
        null=True,
        blank=True,
        help_text="Boolean value"
    )
    formula_value = models.TextField(
        null=True,
        blank=True,
        help_text="Formula value (must start with '=')"
    )
    raw_input = models.TextField(
        null=True,
        blank=True,
        help_text="Original user input, including formulas starting with '='"
    )
    computed_type = models.CharField(
        max_length=20,
        choices=ComputedCellType.choices,
        default=ComputedCellType.EMPTY,
        help_text="Computed result type for formula or raw input"
    )
    computed_number = models.DecimalField(
        max_digits=1000,
        decimal_places=500,
        null=True,
        blank=True,
        help_text="Computed numeric result"
    )
    computed_string = models.TextField(
        null=True,
        blank=True,
        help_text="Computed string result"
    )
    error_code = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Formula error code (e.g. #DIV/0!, #REF!)"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['sheet', 'row', 'column'],
                condition=Q(is_deleted=False),
                name='unique_cell_position_per_sheet_active'
            ),
        ]
        indexes = [
            models.Index(fields=['sheet', 'is_deleted']),
            models.Index(fields=['sheet', 'row', 'column']),
        ]

    def clean(self):
        super().clean()
        if self.row and self.row.sheet_id != self.sheet_id:
            raise ValidationError({
                'row': 'Row must belong to the same sheet as the cell.'
            })
        if self.column and self.column.sheet_id != self.sheet_id:
            raise ValidationError({
                'column': 'Column must belong to the same sheet as the cell.'
            })

    @property
    def row_position(self):
        """Position of the row (for convenience in range queries)"""
        return self.row.position if self.row else None

    @property
    def column_position(self):
        """Position of the column (for convenience in range queries)"""
        return self.column.position if self.column else None

    def save(self, *args, **kwargs):
        validate = kwargs.pop('validate', True)
        if validate:
            self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"Cell at {self.column.name}{self.row.position} in {self.sheet.name}"


class CellDependency(TimeStampedModel):
    from_cell = models.ForeignKey(
        Cell,
        on_delete=models.CASCADE,
        related_name='formula_dependencies',
        help_text="Formula cell that depends on another cell"
    )
    to_cell = models.ForeignKey(
        Cell,
        on_delete=models.CASCADE,
        related_name='formula_dependents',
        help_text="Referenced cell that a formula depends on"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['from_cell', 'to_cell'],
                condition=Q(is_deleted=False),
                name='unique_cell_dependency_active'
            ),
        ]
        indexes = [
            models.Index(fields=['to_cell', 'is_deleted']),
        ]

    def __str__(self):
        return f"{self.from_cell} depends on {self.to_cell}"


class SpreadsheetHighlightScope(models.TextChoices):
    CELL = 'CELL', 'Cell'
    ROW = 'ROW', 'Row'
    COLUMN = 'COLUMN', 'Column'


class SpreadsheetHighlight(TimeStampedModel):
    spreadsheet = models.ForeignKey(
        Spreadsheet,
        on_delete=models.CASCADE,
        related_name='highlights'
    )
    sheet = models.ForeignKey(
        Sheet,
        on_delete=models.CASCADE,
        related_name='highlights'
    )
    scope = models.CharField(max_length=10, choices=SpreadsheetHighlightScope.choices)
    row_index = models.IntegerField(null=True, blank=True)
    col_index = models.IntegerField(null=True, blank=True)
    color = models.CharField(max_length=20)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['sheet', 'scope', 'row_index', 'col_index'],
                name='unique_sheet_highlight_scope_position'
            )
        ]
        indexes = [
            models.Index(fields=['sheet', 'scope']),
        ]

    def __str__(self):
        return f"{self.scope} highlight on sheet {self.sheet_id}"


class SpreadsheetCellFormat(TimeStampedModel):
    """Per-cell text formatting (bold, italic, strikethrough, text color, font family, font size)."""
    sheet = models.ForeignKey(
        Sheet,
        on_delete=models.CASCADE,
        related_name='cell_formats'
    )
    row_index = models.IntegerField(help_text="0-based row position")
    column_index = models.IntegerField(help_text="0-based column position")
    bold = models.BooleanField(default=False)
    italic = models.BooleanField(default=False)
    strikethrough = models.BooleanField(default=False)
    text_color = models.CharField(max_length=20, null=True, blank=True, help_text="Hex color e.g. #333333")
    font_family = models.CharField(max_length=100, null=True, blank=True, help_text="Font family e.g. Arial, Helvetica")
    font_size = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Font size in pixels")
    # Number format: { "type": "GENERAL"|"NUMBER"|"CURRENCY"|"PERCENT", "currency_code": "USD"|"EUR"|..., "decimal_places": 0..10 }
    number_format = models.JSONField(null=True, blank=True, help_text="Display format for numeric cells (type, currency_code, decimal_places)")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['sheet', 'row_index', 'column_index'],
                name='unique_sheet_cell_format_position'
            )
        ]
        indexes = [
            models.Index(fields=['sheet']),
        ]

    def __str__(self):
        return f"Cell format at ({self.row_index},{self.column_index}) on sheet {self.sheet_id}"


class WorkflowPattern(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='workflow_patterns'
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    version = models.IntegerField(default=1)
    origin_spreadsheet_id = models.IntegerField(null=True, blank=True)
    origin_sheet_id = models.IntegerField(null=True, blank=True)
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['owner', 'is_archived']),
        ]

    def __str__(self):
        return f"{self.name} (owner {self.owner_id})"


class WorkflowPatternStep(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pattern = models.ForeignKey(
        WorkflowPattern,
        on_delete=models.CASCADE,
        related_name='steps'
    )
    seq = models.PositiveIntegerField()
    type = models.CharField(max_length=50)
    params = models.JSONField(default=dict)
    disabled = models.BooleanField(default=False)

    class Meta:
        ordering = ['seq']
        constraints = [
            models.UniqueConstraint(
                fields=['pattern', 'seq'],
                name='unique_pattern_step_seq'
            ),
        ]
        indexes = [
            models.Index(fields=['pattern', 'seq']),
        ]

    def __str__(self):
        return f"{self.pattern_id} step {self.seq}"


class PatternJobStatus(models.TextChoices):
    QUEUED = 'queued', 'Queued'
    RUNNING = 'running', 'Running'
    SUCCEEDED = 'succeeded', 'Succeeded'
    FAILED = 'failed', 'Failed'


class PatternJob(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pattern = models.ForeignKey(
        WorkflowPattern,
        on_delete=models.CASCADE,
        related_name='jobs'
    )
    spreadsheet = models.ForeignKey(
        Spreadsheet,
        on_delete=models.CASCADE,
        related_name='pattern_jobs'
    )
    sheet = models.ForeignKey(
        Sheet,
        on_delete=models.CASCADE,
        related_name='pattern_jobs'
    )
    status = models.CharField(
        max_length=20,
        choices=PatternJobStatus.choices,
        default=PatternJobStatus.QUEUED
    )
    progress = models.PositiveSmallIntegerField(default=0)
    step_cursor = models.IntegerField(null=True, blank=True)
    error_code = models.CharField(max_length=50, null=True, blank=True)
    error_message = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pattern_jobs'
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_by', 'status']),
            models.Index(fields=['pattern', 'status']),
            models.Index(fields=['sheet', 'status']),
        ]

    def __str__(self):
        return f"PatternJob {self.id} ({self.status})"


class SpreadsheetAiConsent(TimeStampedModel):
    """One row == one user has consented, once, to sending *this* spreadsheet's
    contents to an external AI service.

    Replaces the old project-wide gate (``ProjectMember.ai_consent_at``): consent
    is now scoped to a single spreadsheet, so a member is prompted the first time
    each spreadsheet is analysed rather than once per project. ``created_at`` (from
    :class:`TimeStampedModel`) is the moment of consent.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='spreadsheet_ai_consents',
    )
    spreadsheet = models.ForeignKey(
        Spreadsheet,
        on_delete=models.CASCADE,
        related_name='ai_consents',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'spreadsheet'],
                name='unique_spreadsheet_ai_consent_per_user',
            ),
        ]
        indexes = [
            models.Index(
                fields=['spreadsheet', 'user'],
                name='sheet_ai_consent_lookup_idx',
            ),
        ]

    def __str__(self):
        return f"AI consent: user={self.user_id} spreadsheet={self.spreadsheet_id}"
