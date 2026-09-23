"""
Serializers for spreadsheet models
Handles API serialization and deserialization following OpenAPI specification
"""
from rest_framework import serializers
from django.db import transaction
from django.core.exceptions import ValidationError as DjangoValidationError

from .models import (
    Spreadsheet,
    Sheet,
    SheetRow,
    SheetColumn,
    Cell,
    CellValueType,
    PivotConfig,
    WorkflowPattern,
    WorkflowPatternStep,
    PatternJob,
    SpreadsheetHighlight,
    SpreadsheetHighlightScope,
    SpreadsheetCellFormat,
    SheetKind,
    UserDefinedFunction,
)
from .services import SheetService


class SpreadsheetSerializer(serializers.ModelSerializer):
    """Serializer for Spreadsheet model (read operations)"""
    project = serializers.IntegerField(source='project.id', read_only=True)
    
    class Meta:
        model = Spreadsheet
        fields = ['slug', 
            'id', 'project', 'name', 'created_at', 'updated_at', 'is_deleted'
        ]
        read_only_fields = ['slug', 'id', 'created_at', 'updated_at', 'is_deleted']


class SpreadsheetCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating Spreadsheet instances"""
    
    class Meta:
        model = Spreadsheet
        fields = ['name']
    
    def validate_name(self, value):
        """Validate spreadsheet name"""
        if not value or not value.strip():
            raise serializers.ValidationError("Name cannot be empty")
        if len(value) > 200:
            raise serializers.ValidationError("Name cannot exceed 200 characters")
        return value.strip()


class SpreadsheetUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating Spreadsheet instances"""
    
    class Meta:
        model = Spreadsheet
        fields = ['name']
    
    def validate_name(self, value):
        """Validate spreadsheet name"""
        if not value or not value.strip():
            raise serializers.ValidationError("Name cannot be empty")
        if len(value) > 200:
            raise serializers.ValidationError("Name cannot exceed 200 characters")
        return value.strip()


class PivotConfigSerializer(serializers.ModelSerializer):
    """Read-only serializer for pivot config nested in sheet response."""
    source_sheet_id = serializers.IntegerField(source='source_sheet.id', read_only=True)

    class Meta:
        model = PivotConfig
        fields = [
            'id',
            'source_sheet_id',
            'rows_config',
            'columns_config',
            'values_config',
            'filters_config',
            'show_grand_total_row',
            'show_grand_total_column',
        ]
        read_only_fields = fields


class SheetSerializer(serializers.ModelSerializer):
    """Serializer for Sheet model (read operations)"""
    spreadsheet = serializers.IntegerField(source='spreadsheet.id', read_only=True)
    pivot_config = serializers.SerializerMethodField()

    class Meta:
        model = Sheet
        fields = [
            'id', 'spreadsheet', 'name', 'position', 'revision', 'kind',
            'frozen_row_count', 'frozen_column_count',
            'pivot_config',
            'created_at', 'updated_at', 'is_deleted'
        ]
        read_only_fields = ['id', 'spreadsheet', 'position', 'revision', 'kind', 'created_at', 'updated_at', 'is_deleted']

    def get_pivot_config(self, obj):
        try:
            return PivotConfigSerializer(obj.pivot_config).data
        except PivotConfig.DoesNotExist:
            return None


class PivotConfigCreateUpdateSerializer(serializers.Serializer):
    """Serializer for creating/updating pivot config via API."""
    source_sheet_id = serializers.IntegerField()
    # Keep shapes flexible; frontend uses arrays of strings/objects.
    rows_config = serializers.JSONField(default=list)
    columns_config = serializers.JSONField(default=list)
    values_config = serializers.JSONField(default=list)
    filters_config = serializers.JSONField(required=False, default=dict)
    show_grand_total_row = serializers.BooleanField(default=True)
    show_grand_total_column = serializers.BooleanField(default=True)


class SheetCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating Sheet instances - position is auto-assigned by server"""
    
    class Meta:
        model = Sheet
        fields = ['name']
    
    def validate(self, data):
        """Validate that position is not provided (it's server-assigned)"""
        if 'position' in self.initial_data:
            raise serializers.ValidationError({
                'position': 'position is read-only'
            })
        return data
    
    def validate_name(self, value):
        """Validate sheet name"""
        if not value or not value.strip():
            raise serializers.ValidationError("Name cannot be empty")
        if len(value) > 200:
            raise serializers.ValidationError("Name cannot exceed 200 characters")
        return value.strip()


class SheetUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating Sheet instances - position cannot be updated"""
    
    class Meta:
        model = Sheet
        fields = ['name', 'frozen_row_count', 'frozen_column_count']
    
    def validate(self, data):
        """Validate that position is not provided (it's read-only)"""
        if 'position' in self.initial_data:
            raise serializers.ValidationError({
                'position': 'position is read-only'
            })
        return data
    
    def validate_frozen_row_count(self, value):
        if value is not None and (value < 0 or value > 1000):
            raise serializers.ValidationError("frozen_row_count must be between 0 and 1000")
        return value
    
    def validate_frozen_column_count(self, value):
        if value is not None and (value < 0 or value > 100):
            raise serializers.ValidationError("frozen_column_count must be between 0 and 100")
        return value
    
    def validate_name(self, value):
        """Validate sheet name"""
        if value is not None:
            if not value.strip():
                raise serializers.ValidationError("Name cannot be empty")
            if len(value) > 200:
                raise serializers.ValidationError("Name cannot exceed 200 characters")
            return value.strip()
        return value


class SheetRowSerializer(serializers.ModelSerializer):
    """Serializer for SheetRow model (read-only)"""
    sheet = serializers.IntegerField(source='sheet.id', read_only=True)
    
    class Meta:
        model = SheetRow
        fields = [
            'id', 'sheet', 'position', 'created_at', 'updated_at', 'is_deleted'
        ]
        read_only_fields = ['id', 'sheet', 'position', 'created_at', 'updated_at', 'is_deleted']


class SheetColumnSerializer(serializers.ModelSerializer):
    """Serializer for SheetColumn model (read-only)"""
    sheet = serializers.IntegerField(source='sheet.id', read_only=True)
    name = serializers.SerializerMethodField()
    
    class Meta:
        model = SheetColumn
        fields = [
            'id', 'sheet', 'name', 'position', 'created_at', 'updated_at', 'is_deleted'
        ]
        read_only_fields = ['id', 'sheet', 'name', 'position', 'created_at', 'updated_at', 'is_deleted']

    def get_name(self, obj: SheetColumn) -> str:
        # Derive name from position to keep labels consistent after inserts
        return SheetService._generate_column_name(obj.position)


class CellSerializer(serializers.ModelSerializer):
    """Serializer for Cell model"""
    sheet = serializers.IntegerField(source='sheet.id', read_only=True)
    row = serializers.IntegerField(source='row.id', read_only=True)
    column = serializers.IntegerField(source='column.id', read_only=True)
    row_position = serializers.IntegerField(read_only=True)
    column_position = serializers.IntegerField(read_only=True)
    
    class Meta:
        model = Cell
        fields = [
            'id', 'sheet', 'row', 'column', 'row_position', 'column_position',
            'value_type', 'string_value', 'number_value', 'boolean_value', 'formula_value',
            'raw_input', 'computed_type', 'computed_number', 'computed_string', 'error_code',
            'created_at', 'updated_at', 'is_deleted'
        ]
        read_only_fields = [
            'id', 'sheet', 'row', 'column', 'row_position', 'column_position',
            'created_at', 'updated_at', 'is_deleted'
        ]


class SheetResizeSerializer(serializers.Serializer):
    """Serializer for sheet resize operation"""
    row_count = serializers.IntegerField(min_value=0)
    column_count = serializers.IntegerField(min_value=0)
    base_revision = serializers.IntegerField(min_value=0, required=False)
    
    def validate_row_count(self, value):
        """Validate row_count is non-negative"""
        if value < 0:
            raise serializers.ValidationError("row_count must be a non-negative integer")
        return value
    
    def validate_column_count(self, value):
        """Validate column_count is non-negative"""
        if value < 0:
            raise serializers.ValidationError("column_count must be a non-negative integer")
        return value


class SheetResizeResponseSerializer(serializers.Serializer):
    """Serializer for sheet resize response"""
    rows_created = serializers.IntegerField()
    columns_created = serializers.IntegerField()
    total_rows = serializers.IntegerField()
    total_columns = serializers.IntegerField()
    revision = serializers.IntegerField()


class CellRangeReadSerializer(serializers.Serializer):
    """Serializer for reading cell range"""
    start_row = serializers.IntegerField(min_value=0)
    end_row = serializers.IntegerField(min_value=0)
    start_column = serializers.IntegerField(min_value=0)
    end_column = serializers.IntegerField(min_value=0)
    include_sheet_dimensions = serializers.BooleanField(
        required=False,
        default=True,
        help_text='When false, skip full-sheet max row/column queries (faster for scroll/tile fetches).',
    )

    def validate(self, data):
        """Validate range parameters"""
        if data['start_row'] > data['end_row']:
            raise serializers.ValidationError({
                'start_row': 'start_row must be less than or equal to end_row'
            })
        if data['start_column'] > data['end_column']:
            raise serializers.ValidationError({
                'start_column': 'start_column must be less than or equal to end_column'
            })
        return data


class CellRangeResponseSerializer(serializers.Serializer):
    """Serializer for cell range response"""
    cells = CellSerializer(many=True)
    row_count = serializers.IntegerField()
    column_count = serializers.IntegerField()
    revision = serializers.IntegerField()


class SheetInsertSerializer(serializers.Serializer):
    """Serializer for row/column insert operations"""
    position = serializers.IntegerField(min_value=0)
    count = serializers.IntegerField(min_value=1, required=False, default=1)
    base_revision = serializers.IntegerField(min_value=0, required=False)


class SheetDeleteSerializer(serializers.Serializer):
    """Serializer for row/column delete operations"""
    position = serializers.IntegerField(min_value=0)
    count = serializers.IntegerField(min_value=1, required=False, default=1)
    base_revision = serializers.IntegerField(min_value=0, required=False)


class CellOperationSerializer(serializers.Serializer):
    """Serializer for a single cell operation"""
    operation = serializers.ChoiceField(choices=['set', 'clear'])
    row = serializers.IntegerField(min_value=0)
    column = serializers.IntegerField(min_value=0)
    raw_input = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    value_type = serializers.ChoiceField(
        choices=CellValueType.choices,
        required=False
    )
    string_value = serializers.CharField(required=False, allow_null=True)
    number_value = serializers.DecimalField(
        max_digits=1000,
        decimal_places=500,
        required=False,
        allow_null=True
    )
    boolean_value = serializers.BooleanField(required=False, allow_null=True)
    formula_value = serializers.CharField(required=False, allow_null=True)
    
    def validate(self, data):
        """Validate operation and required fields"""
        operation = data.get('operation')
        value_type = data.get('value_type')
        raw_input_provided = 'raw_input' in data
        
        if operation == 'set':
            if raw_input_provided:
                raw_input = data.get('raw_input')
                if raw_input is not None and not isinstance(raw_input, str):
                    raise serializers.ValidationError({
                        'raw_input': 'raw_input must be a string'
                    })
                return data

            if not value_type:
                raise serializers.ValidationError({
                    'value_type': 'value_type is required when operation is "set"'
                })
            
            # Validate value_type-specific fields
            if value_type == 'string':
                if 'string_value' not in data:
                    raise serializers.ValidationError({
                        'string_value': 'string_value is required when value_type is "string"'
                    })
            elif value_type == 'number':
                if 'number_value' not in data or data.get('number_value') is None:
                    raise serializers.ValidationError({
                        'number_value': 'number_value is required when value_type is "number"'
                    })
            elif value_type == 'boolean':
                if 'boolean_value' not in data or data.get('boolean_value') is None:
                    raise serializers.ValidationError({
                        'boolean_value': 'boolean_value is required when value_type is "boolean"'
                    })
            elif value_type == 'formula':
                if not data.get('formula_value'):
                    raise serializers.ValidationError({
                        'formula_value': 'formula_value is required when value_type is "formula"'
                    })
                if not data['formula_value'].startswith('='):
                    raise serializers.ValidationError({
                        'formula_value': 'formula_value must start with "="'
                    })
        
        return data


class CellBatchUpdateSerializer(serializers.Serializer):
    """Serializer for batch cell update"""
    operations = CellOperationSerializer(many=True, min_length=1, max_length=2000)
    auto_expand = serializers.BooleanField(default=True)
    # Correlation id for chunked imports (logging / finalize). Accepts UUIDs from the
    # browser (crypto.randomUUID) and short opaque strings in tests or custom clients.
    import_id = serializers.CharField(required=False, allow_null=True, allow_blank=True, max_length=64)
    chunk_index = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    import_mode = serializers.BooleanField(default=False)
    base_revision = serializers.IntegerField(min_value=0, required=False)
    # Realtime collab: WebSocket client id of the tab that made this edit, echoed
    # back in the cells_updated broadcast so the origin tab can drop its own echo.
    client_id = serializers.CharField(required=False, allow_null=True, allow_blank=True, max_length=64)

    def validate_operations(self, value):
        """Validate operations array"""
        if not value or len(value) == 0:
            raise serializers.ValidationError("Operations array must contain at least one operation")
        if len(value) > 2000:
            raise serializers.ValidationError("Operations array cannot exceed 2000 operations")
        return value


class CellBatchUpdateResponseSerializer(serializers.Serializer):
    """Serializer for batch update response"""
    updated = serializers.IntegerField()
    cleared = serializers.IntegerField()
    rows_expanded = serializers.IntegerField(default=0)
    columns_expanded = serializers.IntegerField(default=0)
    cells = CellSerializer(many=True, required=False)
    revision = serializers.IntegerField()


class SheetSortSerializer(serializers.Serializer):
    """Serializer for sheet sort request"""
    column_position = serializers.IntegerField(min_value=0)
    direction = serializers.ChoiceField(choices=['asc', 'desc'])
    has_header = serializers.BooleanField(default=True)
    base_revision = serializers.IntegerField(min_value=0, required=False)
    previous_sort_columns = serializers.ListField(
        required=False,
        default=list
    )

    def validate_previous_sort_columns(self, value):
        """
        Accept backward-compatible `number[]` and new history entries:
        [{column_position: number, direction: 'asc' | 'desc'}].
        """
        normalized = []
        for item in value:
            if isinstance(item, int):
                if item < 0:
                    raise serializers.ValidationError("Column positions must be >= 0")
                normalized.append(item)
                continue

            if not isinstance(item, dict):
                raise serializers.ValidationError(
                    "Each previous sort entry must be an integer column position or an object"
                )

            col = item.get('column_position')
            direction = item.get('direction')
            if not isinstance(col, int) or col < 0:
                raise serializers.ValidationError("column_position must be an integer >= 0")
            if direction not in ('asc', 'desc'):
                raise serializers.ValidationError("direction must be 'asc' or 'desc'")

            normalized.append({'column_position': col, 'direction': direction})

        return normalized


class SheetReorderSerializer(serializers.Serializer):
    """Serializer for row reorder (undo/redo)"""
    order = serializers.ListField(min_length=1)
    base_revision = serializers.IntegerField(min_value=0, required=False)

    def validate_order(self, value):
        for item in value:
            if not isinstance(item, dict) or 'row_id' not in item or 'position' not in item:
                raise serializers.ValidationError("Each item must have row_id and position")
        return value


class WorkflowPatternStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowPatternStep
        fields = ['id', 'seq', 'type', 'params', 'disabled', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class SpreadsheetHighlightSerializer(serializers.ModelSerializer):
    class Meta:
        model = SpreadsheetHighlight
        fields = ['id', 'scope', 'row_index', 'col_index', 'color', 'created_at', 'updated_at']


class SpreadsheetHighlightOpSerializer(serializers.Serializer):
    scope = serializers.ChoiceField(choices=SpreadsheetHighlightScope.choices)
    operation = serializers.ChoiceField(choices=['SET', 'CLEAR'])
    row = serializers.IntegerField(min_value=0, required=False)
    col = serializers.IntegerField(min_value=0, required=False)
    color = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):
        scope = data.get('scope')
        operation = data.get('operation')
        row = data.get('row')
        col = data.get('col')
        color = data.get('color')

        if scope == SpreadsheetHighlightScope.CELL:
            if row is None or col is None:
                raise serializers.ValidationError("row and col are required for CELL scope")
        elif scope == SpreadsheetHighlightScope.ROW:
            if row is None:
                raise serializers.ValidationError("row is required for ROW scope")
        elif scope == SpreadsheetHighlightScope.COLUMN:
            if col is None:
                raise serializers.ValidationError("col is required for COLUMN scope")

        if operation == 'SET' and (color is None or color == ''):
            raise serializers.ValidationError("color is required for SET operation")

        return data


class SpreadsheetHighlightBatchSerializer(serializers.Serializer):
    ops = SpreadsheetHighlightOpSerializer(many=True, min_length=1, max_length=2000)
    base_revision = serializers.IntegerField(min_value=0, required=False)


class SpreadsheetCellFormatSerializer(serializers.ModelSerializer):
    class Meta:
        model = SpreadsheetCellFormat
        fields = [
            'id', 'row_index', 'column_index', 'bold', 'italic', 'strikethrough', 'text_color',
            'font_family', 'font_size', 'number_format', 'created_at', 'updated_at',
        ]


class NumberFormatSerializer(serializers.Serializer):
    """Nested structure for number display format."""
    type = serializers.ChoiceField(
        choices=['GENERAL', 'NUMBER', 'CURRENCY', 'PERCENT'],
        required=False,
        default='GENERAL'
    )
    currency_code = serializers.CharField(required=False, allow_null=True, allow_blank=True, max_length=10)
    decimal_places = serializers.IntegerField(required=False, allow_null=True, min_value=0, max_value=10)


class SpreadsheetCellFormatOpSerializer(serializers.Serializer):
    row = serializers.IntegerField(min_value=0)
    column = serializers.IntegerField(min_value=0)
    bold = serializers.BooleanField(default=False)
    italic = serializers.BooleanField(default=False)
    strikethrough = serializers.BooleanField(default=False)
    text_color = serializers.CharField(required=False, allow_null=True, allow_blank=True, max_length=20)
    font_family = serializers.CharField(required=False, allow_null=True, allow_blank=True, max_length=100)
    font_size = serializers.IntegerField(required=False, allow_null=True, min_value=6, max_value=72)
    number_format = NumberFormatSerializer(required=False, allow_null=True)

    def validate_number_format(self, value):
        if value is None:
            return None
        result = dict(value)
        if result.get('type') == 'CURRENCY' and not result.get('currency_code'):
            result['currency_code'] = 'USD'
        return result


class SpreadsheetCellFormatBatchSerializer(serializers.Serializer):
    ops = SpreadsheetCellFormatOpSerializer(many=True, min_length=1, max_length=2000)
    base_revision = serializers.IntegerField(min_value=0, required=False)


class WorkflowPatternListSerializer(serializers.ModelSerializer):
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)

    class Meta:
        model = WorkflowPattern
        fields = [
            'id',
            'name',
            'description',
            'version',
            'origin_spreadsheet_id',
            'origin_sheet_id',
            'createdAt',
            'is_archived',
        ]


class WorkflowPatternDetailSerializer(serializers.ModelSerializer):
    steps = WorkflowPatternStepSerializer(many=True, read_only=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)

    class Meta:
        model = WorkflowPattern
        fields = [
            'id',
            'name',
            'description',
            'version',
            'origin_spreadsheet_id',
            'origin_sheet_id',
            'createdAt',
            'updatedAt',
            'is_archived',
            'steps',
        ]


class WorkflowPatternOriginSerializer(serializers.Serializer):
    spreadsheet_id = serializers.IntegerField(required=False, allow_null=True)
    sheet_id = serializers.IntegerField(required=False, allow_null=True)


class WorkflowPatternStepInputSerializer(serializers.Serializer):
    seq = serializers.IntegerField(min_value=1)
    type = serializers.CharField(max_length=50)
    params = serializers.JSONField()
    disabled = serializers.BooleanField(default=False)


class WorkflowPatternCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    origin = WorkflowPatternOriginSerializer(required=False)
    steps = WorkflowPatternStepInputSerializer(many=True)

    def validate_steps(self, value):
        if not value:
            raise serializers.ValidationError("At least one step is required")
        seqs = [step['seq'] for step in value]
        if len(seqs) != len(set(seqs)):
            raise serializers.ValidationError("Step seq values must be unique")
        return value

    def create(self, validated_data):
        owner = self.context['owner']
        origin = validated_data.pop('origin', {}) or {}
        steps = validated_data.pop('steps', [])
        with transaction.atomic():
            pattern = WorkflowPattern.objects.create(
                owner=owner,
                origin_spreadsheet_id=origin.get('spreadsheet_id'),
                origin_sheet_id=origin.get('sheet_id'),
                **validated_data
            )
            WorkflowPatternStep.objects.bulk_create(
                [WorkflowPatternStep(pattern=pattern, **step) for step in steps]
            )
        return pattern


class PatternApplySerializer(serializers.Serializer):
    spreadsheet_id = serializers.IntegerField()
    sheet_id = serializers.IntegerField()


class PatternJobStatusSerializer(serializers.ModelSerializer):
    current_step = serializers.IntegerField(source='step_cursor', allow_null=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    startedAt = serializers.DateTimeField(source='started_at', read_only=True)
    finishedAt = serializers.DateTimeField(source='finished_at', read_only=True)

    class Meta:
        model = PatternJob
        fields = [
            'id',
            'status',
            'progress',
            'current_step',
            'error_code',
            'error_message',
            'createdAt',
            'startedAt',
            'finishedAt',
        ]

class UserDefinedFunctionSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserDefinedFunction
        fields = ["id", "name", "params", "expression", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_name(self, value):
        import re
        if not re.fullmatch(r"[A-Za-z]+", value):
            raise serializers.ValidationError("Name must contain letters only (no digits or underscores).")
        BUILTIN = {"SUM", "AVERAGE", "COUNT", "MIN", "MAX", "IF", "AND", "OR", "NOT", "VLOOKUP", "ABS", "ROUND", "FLOOR", "CEILING"}
        if value.upper() in BUILTIN:
            raise serializers.ValidationError("Cannot override a built-in function.")
        return value.upper()

    def validate_params(self, value):
        if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
            raise serializers.ValidationError("params must be a list of strings.")
        return value