"""
URL configuration for spreadsheet API
"""
from django.urls import path
from . import views

app_name = 'spreadsheet'

urlpatterns = [
    # Spreadsheet CRUD
    path('spreadsheets/', views.SpreadsheetListView.as_view(), name='spreadsheet-list'),
    path('spreadsheets/<str:id>/', views.SpreadsheetDetailView.as_view(), name='spreadsheet-detail'),
    
    # Sheet CRUD
    path('spreadsheets/<str:spreadsheet_id>/sheets/', views.SheetListView.as_view(), name='sheet-list'),
    path('spreadsheets/<str:spreadsheet_id>/sheets/<int:id>/', views.SheetDetailView.as_view(), name='sheet-detail'),
    path('spreadsheets/<str:spreadsheet_id>/sheets/<int:sheet_id>/resize/', views.SheetResizeView.as_view(), name='sheet-resize'),
    path('spreadsheets/<str:spreadsheet_id>/sheets/<int:sheet_id>/sort/', views.SheetSortView.as_view(), name='sheet-sort'),
    path('spreadsheets/<str:spreadsheet_id>/sheets/<int:sheet_id>/reorder-rows/', views.SheetReorderView.as_view(), name='sheet-reorder-rows'),

    # Rows and Columns (read-only)
    path('spreadsheets/<str:spreadsheet_id>/sheets/<int:sheet_id>/rows/', views.SheetRowListView.as_view(), name='sheet-row-list'),
    path('spreadsheets/<str:spreadsheet_id>/sheets/<int:sheet_id>/columns/', views.SheetColumnListView.as_view(), name='sheet-column-list'),
    path('spreadsheets/<str:spreadsheet_id>/sheets/<int:sheet_id>/rows/insert/', views.SheetRowInsertView.as_view(), name='sheet-row-insert'),
    path('spreadsheets/<str:spreadsheet_id>/sheets/<int:sheet_id>/columns/insert/', views.SheetColumnInsertView.as_view(), name='sheet-column-insert'),
    path('spreadsheets/<str:spreadsheet_id>/sheets/<int:sheet_id>/rows/delete/', views.SheetRowDeleteView.as_view(), name='sheet-row-delete'),
    path('spreadsheets/<str:spreadsheet_id>/sheets/<int:sheet_id>/columns/delete/', views.SheetColumnDeleteView.as_view(), name='sheet-column-delete'),
    path('spreadsheets/<str:spreadsheet_id>/sheets/<int:sheet_id>/operations/<int:operation_id>/revert/', views.SheetStructureOperationRevertView.as_view(), name='sheet-operation-revert'),

    # Sheet-id-only endpoints
    path('sheets/<int:sheet_id>/rows/insert/', views.SheetRowInsertByIdView.as_view(), name='sheet-row-insert-by-id'),
    path('sheets/<int:sheet_id>/columns/insert/', views.SheetColumnInsertByIdView.as_view(), name='sheet-column-insert-by-id'),
    path('sheets/<int:sheet_id>/rows/delete/', views.SheetRowDeleteByIdView.as_view(), name='sheet-row-delete-by-id'),
    path('sheets/<int:sheet_id>/columns/delete/', views.SheetColumnDeleteByIdView.as_view(), name='sheet-column-delete-by-id'),
    path('sheets/<int:sheet_id>/operations/<int:operation_id>/revert/', views.SheetOperationRevertByIdView.as_view(), name='sheet-operation-revert-by-id'),
    path('sheets/<int:sheet_id>/ws-ticket/', views.SheetWebSocketTicketView.as_view(), name='sheet-websocket-ticket'),
    
    # Cells
    path('spreadsheets/<str:spreadsheet_id>/sheets/<int:sheet_id>/cells/range/', views.CellRangeReadView.as_view(), name='cell-range-read'),
    path('spreadsheets/<str:spreadsheet_id>/sheets/<int:sheet_id>/cells/batch/', views.CellBatchUpdateView.as_view(), name='cell-batch-update'),
    path('spreadsheets/<str:spreadsheet_id>/sheets/<int:sheet_id>/cells/import-finalize/', views.ImportFinalizeView.as_view(), name='cell-import-finalize'),
    path('spreadsheets/<str:spreadsheet_id>/sheets/<int:sheet_id>/export.xlsx', views.SheetXlsxExportView.as_view(), name='sheet-xlsx-export'),
    path('spreadsheets/<str:spreadsheet_id>/sheets/<int:sheet_id>/pivot-config/', views.PivotConfigView.as_view(), name='pivot-config'),
    path('spreadsheets/<str:spreadsheet_id>/sheets/<int:sheet_id>/pivot-recompute/', views.PivotRecomputeView.as_view(), name='pivot-recompute'),
    path('spreadsheets/<str:spreadsheet_id>/sheets/<int:sheet_id>/highlights/', views.SpreadsheetHighlightListView.as_view(), name='sheet-highlight-list'),
    path('spreadsheets/<str:spreadsheet_id>/sheets/<int:sheet_id>/highlights/batch/', views.SpreadsheetHighlightBatchView.as_view(), name='sheet-highlight-batch'),
    path('spreadsheets/<str:spreadsheet_id>/sheets/<int:sheet_id>/cell-formats/', views.SpreadsheetCellFormatListView.as_view(), name='sheet-cell-format-list'),
    path('spreadsheets/<str:spreadsheet_id>/sheets/<int:sheet_id>/cell-formats/batch/', views.SpreadsheetCellFormatBatchView.as_view(), name='sheet-cell-format-batch'),

    # Workflow patterns
    path('patterns/', views.WorkflowPatternListCreateView.as_view(), name='pattern-list'),
    path('patterns/<uuid:id>/', views.WorkflowPatternDetailView.as_view(), name='pattern-detail'),
    path('patterns/<uuid:id>/apply/', views.WorkflowPatternApplyView.as_view(), name='pattern-apply'),
    path('pattern-jobs/<uuid:job_id>/', views.PatternJobStatusView.as_view(), name='pattern-job-status'),

    # Natural-language → PatternStep generation
    path('sheets/<int:sheet_id>/generate-pattern-steps/', views.GeneratePatternStepsView.as_view(), name='generate-pattern-steps'),

    # Natural-language → PivotConfig generation
    path('sheets/<int:sheet_id>/generate-pivot-config/', views.GeneratePivotConfigView.as_view(), name='generate-pivot-config'),
]
