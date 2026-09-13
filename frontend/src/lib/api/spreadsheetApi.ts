import api, { LONG_REQUEST_TIMEOUT_MS } from '../api';
import {
  SpreadsheetData,
  SpreadsheetListResponse,
  CreateSpreadsheetRequest,
  UpdateSpreadsheetRequest,
  SheetData,
  SheetListResponse,
  CreateSheetRequest,
  UpdateSheetRequest,
  PivotConfigDTO,
} from '@/types/spreadsheet';
import {
  isSheetRevisionConflictResponse,
  publishSheetRevisionConflict,
  setSheetRevision,
  withBaseRevision,
} from '@/lib/sheetRevisionStore';

/**
 * Timeout for long-running spreadsheet requests (import batch, large range read).
 * Default axios 10s is too short; uses the shared LONG_REQUEST_TIMEOUT_MS tier
 * (5 minutes, safety net — optimized batch writes should finish in <5s).
 */
const SPREADSHEET_LONG_REQUEST_TIMEOUT_MS = LONG_REQUEST_TIMEOUT_MS;

/**
 * Collab WS client id of this tab (set by useSheetSocket while a sheet room is
 * open). Attached as X-Sheet-Client-Id to every /api/spreadsheet/ request so
 * the backend can suppress this tab's own broadcast echo — structure-op
 * endpoints (insert/delete/sort/resize/revert/import-finalize) read it from
 * the header, so it must ride on all mutations without per-call plumbing.
 */
let sheetCollabClientId: string | null = null;

export function setSheetCollabClientId(clientId: string | null): void {
  sheetCollabClientId = clientId;
}

function captureSheetRevision<T>(sheetId: number | string, data: T): T {
  if (data && typeof data === 'object' && 'revision' in data) {
    setSheetRevision(sheetId, (data as { revision?: unknown }).revision);
  }
  return data;
}

api.interceptors.request.use((config) => {
  if (sheetCollabClientId && config.url && config.url.startsWith('/api/spreadsheet/')) {
    (config.headers as Record<string, unknown>)['X-Sheet-Client-Id'] = sheetCollabClientId;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const data = error?.response?.data;
    const match = String(error?.config?.url || '').match(/\/sheets\/(\d+)\//);
    if (
      match &&
      isSheetRevisionConflictResponse(error?.response?.status, data)
    ) {
      publishSheetRevisionConflict(match[1], data.current_revision);
    }
    return Promise.reject(error);
  }
);

export const SpreadsheetAPI = {
  /**
   * Export a sheet to .xlsx. The backend (openpyxl) writes cell values plus a
   * native chart for every sparkline cell — something the frontend SheetJS
   * exporter cannot do (MED-295). Returns the file as a Blob to download.
   */
  exportSheetXlsx: async (
    spreadsheetId: number | string,
    sheetId: number | string,
  ): Promise<Blob> => {
    const response = await api.get(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/${sheetId}/export.xlsx`,
      { responseType: 'blob' },
    );
    return response.data as Blob;
  },

  createWebSocketTicket: async (
    sheetId: number | string,
    clientId: string
  ): Promise<{ ticket: string; expires_in: number }> => {
    const response = await api.post<{ ticket: string; expires_in: number }>(
      `/api/spreadsheet/sheets/${sheetId}/ws-ticket/`,
      { client_id: clientId },
      { headers: { 'X-Sheet-Client-Id': clientId } }
    );
    return response.data;
  },

  // List spreadsheets for a project
  listSpreadsheets: async (
    projectId: number | string,
    params?: {
      page?: number;
      page_size?: number;
      search?: string;
      order_by?: 'name' | 'created_at' | 'updated_at';
    }
  ): Promise<SpreadsheetListResponse> => {
    const queryParams: any = {
      project_id: projectId,
      ...params,
    };
    const response = await api.get<SpreadsheetListResponse>('/api/spreadsheet/spreadsheets/', {
      params: queryParams,
    });
    return response.data;
  },

  // Get a specific spreadsheet by ID
  getSpreadsheet: async (spreadsheetId: number | string): Promise<SpreadsheetData> => {
    const response = await api.get<SpreadsheetData>(`/api/spreadsheet/spreadsheets/${spreadsheetId}/`);
    return response.data;
  },

  // Create a new spreadsheet
  createSpreadsheet: async (
    projectId: number | string,
    data: CreateSpreadsheetRequest
  ): Promise<SpreadsheetData> => {
    const response = await api.post<SpreadsheetData>(
      `/api/spreadsheet/spreadsheets/?project_id=${projectId}`,
      data
    );
    return response.data;
  },

  // Update a spreadsheet
  updateSpreadsheet: async (
    spreadsheetId: number | string,
    data: UpdateSpreadsheetRequest
  ): Promise<SpreadsheetData> => {
    const response = await api.put<SpreadsheetData>(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/`,
      data
    );
    return response.data;
  },

  // Delete a spreadsheet (soft delete)
  deleteSpreadsheet: async (spreadsheetId: number | string): Promise<void> => {
    await api.delete(`/api/spreadsheet/spreadsheets/${spreadsheetId}/`);
  },

  // Sheet operations
  // List sheets for a spreadsheet
  listSheets: async (
    spreadsheetId: number | string,
    params?: {
      page?: number;
      page_size?: number;
      order_by?: 'name' | 'position' | 'created_at';
    }
  ): Promise<SheetListResponse> => {
    const queryParams: any = {
      ...params,
    };
    const response = await api.get<SheetListResponse>(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/`,
      { params: queryParams }
    );
    return response.data;
  },

  // Get a specific sheet by ID
  getSheet: async (spreadsheetId: number | string, sheetId: number | string): Promise<SheetData> => {
    const response = await api.get<SheetData>(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/${sheetId}/`
    );
    return captureSheetRevision(sheetId, response.data);
  },

  // Create a new sheet
  createSheet: async (
    spreadsheetId: number | string,
    data: CreateSheetRequest
  ): Promise<SheetData> => {
    const response = await api.post<SheetData>(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/`,
      data
    );
    return response.data;
  },

  // Update a sheet
  updateSheet: async (
    spreadsheetId: number | string,
    sheetId: number | string,
    data: UpdateSheetRequest
  ): Promise<SheetData> => {
    const response = await api.put<SheetData>(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/${sheetId}/`,
      data
    );
    return response.data;
  },

  // Sort rows by column (updates SheetRow.position only, no cell rewrite)
  sortSheet: async (
    spreadsheetId: number | string,
    sheetId: number | string,
    params: {
      column_position: number;
      direction: 'asc' | 'desc';
      has_header: boolean;
      previous_sort_columns?: Array<number | { column_position: number; direction: 'asc' | 'desc' }>;
    }
  ): Promise<{
    previous_order: Array<{ row_id: number | string; position: number }>;
    new_order: Array<{ row_id: number | string; position: number }>;
    revision: number;
  }> => {
    const response = await api.post<{
      previous_order: Array<{ row_id: number | string; position: number }>;
      new_order: Array<{ row_id: number | string; position: number }>;
      revision: number;
    }>(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/${sheetId}/sort/`,
      withBaseRevision(sheetId, {
        column_position: params.column_position,
        direction: params.direction,
        has_header: params.has_header,
        previous_sort_columns: params.previous_sort_columns ?? [],
      })
    );
    return captureSheetRevision(sheetId, response.data);
  },

  // Reorder rows by position (for undo/redo)
  reorderRows: async (
    spreadsheetId: number | string,
    sheetId: number | string,
    params: { order: Array<{ row_id: number | string; position: number }> }
  ): Promise<{ status: string; revision: number }> => {
    const response = await api.post<{ status: string; revision: number }>(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/${sheetId}/reorder-rows/`,
      withBaseRevision(sheetId, { order: params.order })
    );
    return captureSheetRevision(sheetId, response.data);
  },

  // Delete a sheet (soft delete) via project-scoped endpoint
  deleteSheet: async (projectId: number | string, spreadsheetId: number | string, sheetId: number | string): Promise<void> => {
    await api.delete(
      `/api/projects/${projectId}/spreadsheets/${spreadsheetId}/sheets/${sheetId}/`
    );
  },

  // Cell operations
  // Read cells in a range
  readCellRange: async (
    spreadsheetId: number | string,
    sheetId: number | string,
    startRow: number,
    endRow: number,
    startColumn: number,
    endColumn: number,
    options?: { includeSheetDimensions?: boolean }
  ): Promise<{
    cells: Array<{
      id: number | string;
      row_position: number;
      column_position: number;
      value_type: string;
      string_value?: string | null;
      number_value?: number | null;
      boolean_value?: boolean | null;
      formula_value?: string | null;
      raw_input?: string | null;
      computed_type?: string | null;
      computed_number?: number | string | null;
      computed_string?: string | null;
      error_code?: string | null;
      updated_at?: string | null;
    }>;
    row_count: number;
    column_count: number;
    /** Full sheet dimensions (use for grid size). When present, prefer over row_count/column_count which are the requested range size. */
    sheet_row_count?: number | null;
    sheet_column_count?: number | null;
    revision: number;
  }> => {
    const response = await api.post<{
      cells: Array<{
        id: number | string;
        row_position: number;
        column_position: number;
        value_type: string;
        string_value?: string | null;
        number_value?: number | null;
        boolean_value?: boolean | null;
        formula_value?: string | null;
        raw_input?: string | null;
        computed_type?: string | null;
        computed_number?: number | string | null;
        computed_string?: string | null;
        error_code?: string | null;
        updated_at?: string | null;
      }>;
      row_count: number;
      column_count: number;
      sheet_row_count?: number | null;
      sheet_column_count?: number | null;
      revision: number;
    }>(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/${sheetId}/cells/range/`,
      {
        start_row: startRow,
        end_row: endRow,
        start_column: startColumn,
        end_column: endColumn,
        ...(options?.includeSheetDimensions === false
          ? { include_sheet_dimensions: false }
          : {}),
      },
      { timeout: SPREADSHEET_LONG_REQUEST_TIMEOUT_MS }
    );
    return captureSheetRevision(sheetId, response.data);
  },

  // Batch update cells
  batchUpdateCells: async (
    spreadsheetId: number | string,
    sheetId: number | string,
    operations: Array<{
      operation: 'set' | 'clear';
      row: number;
      column: number;
      raw_input?: string | null;
      value_type?: string;
      string_value?: string | null;
      number_value?: number | null;
      boolean_value?: boolean | null;
      formula_value?: string | null;
    }>,
    autoExpand: boolean = true,
    options?: {
      importId?: string;
      chunkIndex?: number;
      importMode?: boolean;
      signal?: AbortSignal;
      /** Collab WS client id; echoed in the cells_updated broadcast so the origin tab can skip its own echo. */
      clientId?: string;
    }
  ): Promise<{
    updated: number;
    cleared: number;
    rows_expanded: number;
    columns_expanded: number;
    revision: number;
    cells?: Array<{
      id: number | string;
      row_position: number;
      column_position: number;
      value_type: string;
      string_value?: string | null;
      number_value?: number | null;
      boolean_value?: boolean | null;
      formula_value?: string | null;
      raw_input?: string | null;
      computed_type?: string | null;
      computed_number?: number | string | null;
      computed_string?: string | null;
      error_code?: string | null;
      updated_at?: string | null;
    }>;
  }> => {
    const body: Record<string, unknown> = withBaseRevision(sheetId, {
      operations,
      auto_expand: autoExpand,
    });
    if (options?.importId != null) body.import_id = options.importId;
    if (options?.chunkIndex != null) body.chunk_index = options.chunkIndex;
    if (options?.importMode === true) body.import_mode = true;
    if (options?.clientId) body.client_id = options.clientId;

    const config: {
      timeout: number;
      signal?: AbortSignal;
      headers?: Record<string, string>;
    } = {
      timeout: SPREADSHEET_LONG_REQUEST_TIMEOUT_MS,
    };
    if (options?.signal) config.signal = options.signal;
    if (options?.clientId) {
      config.headers = { 'X-Sheet-Client-Id': options.clientId };
    }

    const response = await api.post(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/${sheetId}/cells/batch/`,
      body,
      config
    );
    return captureSheetRevision(sheetId, response.data);
  },

  /** Finalize import: recompute formulas and update sheet meta. Call after all batch chunks complete. */
  finalizeImport: async (
    spreadsheetId: number | string,
    sheetId: number | string,
    importId: string
  ): Promise<{ status: string; revision: number }> => {
    const response = await api.post(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/${sheetId}/cells/import-finalize/`,
      { import_id: importId },
      { timeout: SPREADSHEET_LONG_REQUEST_TIMEOUT_MS }
    );
    return captureSheetRevision(sheetId, response.data);
  },

  // Highlights
  getHighlights: async (
    spreadsheetId: number | string,
    sheetId: number | string
  ): Promise<{
    highlights: Array<{
      id: number | string;
      scope: 'CELL' | 'ROW' | 'COLUMN';
      row_index: number | null;
      col_index: number | null;
      color: string;
      created_at: string;
      updated_at: string;
    }>;
    revision: number;
  }> => {
    const response = await api.get(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/${sheetId}/highlights/`
    );
    return captureSheetRevision(sheetId, response.data);
  },

  getCellFormats: async (
    spreadsheetId: number | string,
    sheetId: number | string
  ): Promise<{
    formats: Array<{
      id: number | string;
      row_index: number;
      column_index: number;
      bold: boolean;
      italic: boolean;
      strikethrough: boolean;
      text_color: string | null;
      font_family: string | null;
      font_size: number | null;
      number_format: {
        type?: 'GENERAL' | 'NUMBER' | 'CURRENCY' | 'PERCENT';
        currency_code?: string | null;
        decimal_places?: number | null;
      } | null;
      created_at: string;
      updated_at: string;
    }>;
    revision: number;
  }> => {
    const response = await api.get(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/${sheetId}/cell-formats/`
    );
    return captureSheetRevision(sheetId, response.data);
  },

  batchUpdateCellFormats: async (
    spreadsheetId: number | string,
    sheetId: number | string,
    ops: Array<{
      row: number;
      column: number;
      bold?: boolean;
      italic?: boolean;
      strikethrough?: boolean;
      text_color?: string | null;
      font_family?: string | null;
      font_size?: number | null;
      number_format?: {
        type?: 'GENERAL' | 'NUMBER' | 'CURRENCY' | 'PERCENT';
        currency_code?: string | null;
        decimal_places?: number | null;
      } | null;
    }>
  ): Promise<{ updated: number; revision: number }> => {
    const response = await api.post(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/${sheetId}/cell-formats/batch/`,
      withBaseRevision(sheetId, { ops })
    );
    return captureSheetRevision(sheetId, response.data);
  },

  batchUpdateHighlights: async (
    spreadsheetId: number | string,
    sheetId: number | string,
    ops: Array<{
      scope: 'CELL' | 'ROW' | 'COLUMN';
      row?: number;
      col?: number;
      color?: string;
      operation: 'SET' | 'CLEAR';
    }>
  ): Promise<{ updated: number; deleted: number; revision: number }> => {
    const response = await api.post(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/${sheetId}/highlights/batch/`,
      withBaseRevision(sheetId, { ops })
    );
    return captureSheetRevision(sheetId, response.data);
  },

  // Resize sheet (ensure minimum dimensions)
  resizeSheet: async (
    spreadsheetId: number | string,
    sheetId: number | string,
    rowCount: number,
    columnCount: number
  ): Promise<{
    rows_created: number;
    columns_created: number;
    total_rows: number;
    total_columns: number;
    revision: number;
  }> => {
    const response = await api.post(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/${sheetId}/resize/`,
      withBaseRevision(sheetId, {
        row_count: rowCount,
        column_count: columnCount,
      })
    );
    return captureSheetRevision(sheetId, response.data);
  },

  // Insert rows
  insertRows: async (
    spreadsheetId: number | string,
    sheetId: number | string,
    position: number,
    count: number = 1
  ): Promise<{
    rows_created: number;
    total_rows: number;
    operation_id: number | string;
    revision: number;
  }> => {
    const response = await api.post(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/${sheetId}/rows/insert/`,
      withBaseRevision(sheetId, {
        position,
        count,
      })
    );
    return captureSheetRevision(sheetId, response.data);
  },

  // Insert columns
  insertColumns: async (
    spreadsheetId: number | string,
    sheetId: number | string,
    position: number,
    count: number = 1
  ): Promise<{
    columns_created: number;
    total_columns: number;
    operation_id: number | string;
    revision: number;
  }> => {
    const response = await api.post(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/${sheetId}/columns/insert/`,
      withBaseRevision(sheetId, {
        position,
        count,
      })
    );
    return captureSheetRevision(sheetId, response.data);
  },

  // Delete rows
  deleteRows: async (
    spreadsheetId: number | string,
    sheetId: number | string,
    position: number,
    count: number = 1
  ): Promise<{
    rows_deleted: number;
    total_rows: number;
    operation_id: number | string;
    revision: number;
  }> => {
    const response = await api.post(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/${sheetId}/rows/delete/`,
      withBaseRevision(sheetId, {
        position,
        count,
      })
    );
    return captureSheetRevision(sheetId, response.data);
  },

  // Delete columns
  deleteColumns: async (
    spreadsheetId: number | string,
    sheetId: number | string,
    position: number,
    count: number = 1
  ): Promise<{
    columns_deleted: number;
    total_columns: number;
    operation_id: number | string;
    revision: number;
  }> => {
    const response = await api.post(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/${sheetId}/columns/delete/`,
      withBaseRevision(sheetId, {
        position,
        count,
      })
    );
    return captureSheetRevision(sheetId, response.data);
  },

  // Revert structure operation
  revertStructureOperation: async (
    spreadsheetId: number | string,
    sheetId: number | string,
    operationId: number | string
  ): Promise<{ operation_id: number | string; is_reverted: boolean; revision: number }> => {
    const response = await api.post(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/${sheetId}/operations/${operationId}/revert/`,
      withBaseRevision(sheetId, {})
    );
    return captureSheetRevision(sheetId, response.data);
  },

  // Upsert pivot configuration for a sheet and trigger recompute
  upsertPivotConfig: async (
    spreadsheetId: number | string,
    sheetId: number | string,
    payload: {
      sourceSheetId: number | string;
      rows: any[];
      columns: any[];
      values: any[];
      filters?: any;
      showGrandTotalRow?: boolean;
      showGrandTotalColumn?: boolean;
    }
  ): Promise<PivotConfigDTO> => {
    const body: Record<string, unknown> = {
      source_sheet_id: payload.sourceSheetId,
      rows_config: payload.rows,
      columns_config: payload.columns,
      values_config: payload.values,
    };
    if (payload.filters !== undefined) body.filters_config = payload.filters;
    if (payload.showGrandTotalRow !== undefined) body.show_grand_total_row = payload.showGrandTotalRow;
    if (payload.showGrandTotalColumn !== undefined) {
      body.show_grand_total_column = payload.showGrandTotalColumn;
    }

    const response = await api.post<PivotConfigDTO>(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/${sheetId}/pivot-config/`,
      body
    );
    return response.data;
  },
  // Trigger backend pivot recompute based on persisted config (fire-and-forget from UI).
  recomputePivot: async (
    spreadsheetId: number | string,
    sheetId: number | string
  ): Promise<{ status: string; detail?: string; revision?: number }> => {
    const response = await api.post<{ status: string; detail?: string }>(
      `/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/${sheetId}/pivot-recompute/`,
      {}
    );
    return captureSheetRevision(sheetId, response.data);
  },
};
