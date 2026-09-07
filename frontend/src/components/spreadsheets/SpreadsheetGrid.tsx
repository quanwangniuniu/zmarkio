'use client';

import { useState, useEffect, useRef, useCallback, useMemo, forwardRef, useImperativeHandle } from 'react';
import { createPortal } from 'react-dom';
import { Undo2, Redo2, Bold, Italic, Strikethrough, Palette, ChevronLeft, ChevronRight, ChevronDown, Snowflake, Check, Table2, Upload, Download, FileSpreadsheet, Loader2 } from 'lucide-react';
import { SpreadsheetAPI } from '@/lib/api/spreadsheetApi';
import { googleDocsApi } from '@/lib/api/googleDocsApi';
import toast from 'react-hot-toast';
import Modal from '@/components/ui/Modal';
import {
  parseCSVFile,
  parseXLSXFile,
  buildCellOperations,
  chunkOperations,
  exportMatrixToCSV,
  CellOperation,
  XLSXParseResult,
} from '@/components/spreadsheets/spreadsheetImportExport';
import SparklineCell from '@/components/spreadsheets/SparklineCell';
import {
  isSparklineRawInput,
  parseSparklinePayload,
  type SparklinePayload,
} from '@/components/spreadsheets/sparklineData';
import { adjustFormulaReferences, colLabelToIndex } from '@/lib/spreadsheet/formulaFill';
import { ApplyHighlightParams } from '@/types/patterns';
import BrandSelect from '@/components/ui/BrandSelect';
import type { SheetPresenceUser } from '@/lib/sheetSocketStore';

export type SpreadsheetSelectionChange = {
  row: number;
  col: number;
  startRow: number;
  endRow: number;
  startCol: number;
  endCol: number;
} | null;

interface SpreadsheetGridProps {
  spreadsheetId: number | string;
  sheetId: number;
  loading?: boolean;
  spreadsheetName?: string;
  sheetName?: string;
  /** Number of rows to freeze (0 = none, 1 = freeze first row). Sheet-level property. */
  frozenRowCount?: number;
  /** Called when freeze header is toggled; parent should update sheet state and pass new frozenRowCount. */
  onFreezeHeaderChange?: (frozenRowCount: number) => void;
  /** Fired when local active cell / selection changes (for collab presence). */
  onSelectionChange?: (selection: SpreadsheetSelectionChange) => void;
  /** Collab WS client id of this tab; sent with cell saves so the server can suppress our own broadcast echo. */
  collabClientId?: string;
  /** Remote collaborators whose active cells/selections are rendered over the grid. */
  remotePresenceUsers?: SheetPresenceUser[];
  onFormulaCommit?: (data: { row: number; col: number; formula: string }) => void;
  onInsertRowCommit?: (payload: { index: number; position: 'above' | 'below' }) => void;
  onInsertColumnCommit?: (payload: { index: number; position: 'left' | 'right' }) => void;
  onDeleteColumnCommit?: (payload: { index: number }) => void;
  onFillCommit?: (payload: {
    source: { row: number; col: number };
    range: { start_row: number; end_row: number; start_col: number; end_col: number };
  }) => void;
  onHeaderRenameCommit?: (payload: {
    rowIndex: number;
    colIndex: number;
    newValue: string;
    oldValue: string;
  }) => void;
  onHighlightCommit?: (payload: ApplyHighlightParams) => void;
  highlightCell?: { row: number; col: number } | null;
  highlightLocations?: { row: number; col: number }[] | null;
  /** Called when hydration status changes (importing -> hydrating -> ready). Parent can disable Apply Pattern until ready. */
  onHydrationStatusChange?: (status: 'idle' | 'importing' | 'hydrating' | 'ready') => void;
  /** Called when user clicks the Pivot Table button. Receives cell data for pivot builder. */
  onOpenPivotBuilder?: (data: {
    cells: Map<string, { rawInput: string; computedString?: string | null }>;
    rowCount: number;
    colCount: number;
  }) => void;
}

export interface SpreadsheetGridHandle {
  applyFormula: (row: number, col: number, value: string) => Promise<void>;
  insertRow: (position: number, count?: number) => Promise<void>;
  insertColumn: (position: number, count?: number) => Promise<void>;
  deleteColumn: (position: number, count?: number) => Promise<void>;
  refresh: () => void;
  applyHighlightOperation: (payload: ApplyHighlightParams) => void;
  /** Apply committed remote cell changes from a cells_updated broadcast (same shape as the batch REST response). */
  applyRemoteCells: (
    cells: Array<{
      row_position: number;
      column_position: number;
      raw_input?: string | null;
      string_value?: string | null;
      number_value?: number | string | null;
      boolean_value?: boolean | null;
      formula_value?: string | null;
      computed_type?: string | null;
      computed_number?: number | string | null;
      computed_string?: string | null;
      error_code?: string | null;
      updated_at?: string | null;
    }>
  ) => void;
  navigateToCell: (row: number, col: number) => void;
}

type CellKey = string; // Format: `${row}:${col}` (0-based indices)

interface CellData {
  rawInput: string;
  computedType?: string | null;
  computedNumber?: number | string | null;
  computedString?: string | null;
  errorCode?: string | null;
  updatedAt?: string | null;
  isLoaded: boolean; // Track if cell was loaded from backend
}

interface PendingOperation {
  row: number;
  column: number;
  operation: 'set' | 'clear';
  raw_input?: string | null;
  value_type?: 'string' | 'number' | 'formula';
  number_value?: number | null;
  string_value?: string | null;
}

interface ActiveCell {
  row: number;
  col: number;
}

interface SelectionRange {
  startRow: number;
  endRow: number;
  startCol: number;
  endCol: number;
}

type HighlightOp = {
  scope: 'CELL' | 'ROW' | 'COLUMN';
  row?: number;
  col?: number;
  color?: string;
  operation: 'SET' | 'CLEAR';
};

interface CellChange {
  row: number;
  col: number;
  prevValue: string;
  nextValue: string;
}

interface HistoryEntry {
  changes: CellChange[];
}

interface ColorHistoryEntry {
  ops: Array<{
    scope: 'CELL' | 'ROW' | 'COLUMN';
    row?: number;
    col?: number;
    prevColor: string | undefined;
  }>;
}

interface ColorRedoEntry {
  ops: Array<{
    scope: 'CELL' | 'ROW' | 'COLUMN';
    row?: number;
    col?: number;
    prevColor: string | undefined;
    nextColor: string | undefined;
  }>;
}

interface StructureRedoEntry {
  type: 'row_insert' | 'col_insert' | 'row_delete' | 'col_delete';
  count: number;
  position: number;
}

type StructureOp = {
  id: number | string;
  type: 'row_insert' | 'col_insert' | 'row_delete' | 'col_delete';
  count: number;
  position: number;
};

type NumberFormatType = 'GENERAL' | 'NUMBER' | 'CURRENCY' | 'PERCENT';

interface NumberFormat {
  type: NumberFormatType;
  currencyCode?: string | null;
  decimalPlaces?: number | null;
}

interface CellFormat {
  bold: boolean;
  italic: boolean;
  strikethrough: boolean;
  textColor: string | null;
  fontFamily: string | null;
  fontSize: number | null;
  numberFormat: NumberFormat | null;
}

interface FormatStyleOp {
  row: number;
  col: number;
  prev: CellFormat;
  next: CellFormat;
}

interface FormatStyleEntry {
  ops: FormatStyleOp[];
}

interface SortHistoryEntry {
  previous_order: Array<{ row_id: number | string; position: number }>;
  new_order: Array<{ row_id: number | string; position: number }>;
}

interface SortColumnHistoryEntry {
  column_position: number;
  direction: 'asc' | 'desc';
}

interface ParsedColumnFilter {
  operator: '>=' | '>' | '<=' | '<' | '=';
  rawValue: string;
  numericValue: number | null;
}

type UndoEntry =
  | { type: 'cell'; entry: HistoryEntry }
  | { type: 'color'; entry: ColorHistoryEntry }
  | { type: 'format'; entry: FormatStyleEntry }
  | { type: 'structure'; op: StructureOp }
  | { type: 'sort'; entry: SortHistoryEntry };

type RedoEntry =
  | { type: 'cell'; entry: HistoryEntry }
  | { type: 'color'; entry: ColorRedoEntry }
  | { type: 'format'; entry: FormatStyleEntry }
  | { type: 'structure'; entry: StructureRedoEntry }
  | { type: 'sort'; entry: SortHistoryEntry };

interface ResizeState {
  type: 'col' | 'row';
  index: number;
  startPosition: number;
  startSize: number;
  pointerId: number;
}

interface SizeIndex {
  indices: number[];
  prefix: number[];
  totalDelta: number;
}

const DEFAULT_ROWS = 1000;
const DEFAULT_COLUMNS = 26; // A-Z
const ROW_HEIGHT = 24; // pixels
const COLUMN_WIDTH = 120; // pixels
const ROW_MIN_HEIGHT = 20; // pixels
const COLUMN_MIN_WIDTH = 40; // pixels
const ROW_NUMBER_WIDTH = 50; // pixels
const HEADER_HEIGHT = 24; // pixels
const RESIZE_HANDLE_SIZE = 6; // pixels
const CELL_PADDING_X = 4; // pixels
const CELL_PADDING_Y = 2; // pixels
const CELL_FONT_SIZE = 12; // pixels (matches text-sm)
const OVERSCAN_ROWS = 20; // Render extra rows above/below viewport
const OVERSCAN_COLUMNS = 6; // Render extra columns left/right of viewport
const AUTO_GROW_ROWS = 50; // Batch add rows when expanding (deprecated - only used for import)
const AUTO_GROW_COLUMNS = 50; // Batch add columns when expanding (deprecated - only used for import)
// Keep the commit-to-broadcast path inside the <300 ms acceptance budget.
// Synchronous multi-cell operations still batch into one React update before
// this timer starts, while direct edits reach peers with ample network/DB headroom.
const DEBOUNCE_MS = 50;
const RESIZE_DEBOUNCE_MS = 500; // Debounce delay for resize API calls
const MAX_ROWS = 100000; // Hard cap for grid size
const MAX_COLUMNS = 702; // ZZZ (26 * 27) - hard cap for grid size
// Fixed tile size for range loading: the visible viewport is covered by one or more
// tiles; each tile is cached and fetched independently so scrolling only requests
// newly entered tiles (not a shifting bounding box over the same region).
const TILE_ROWS = 50;
const TILE_COLUMNS = 20;
/** Max parallel range/tile read requests to avoid swarming the network / browser connection pool */
const RANGE_TILE_READ_CONCURRENCY = 3;
const ADD_ROWS_TRIGGER_DISTANCE = 100; // Show "Add rows" UI when within this many pixels of bottom
const PREFETCH_ROWS_PER_CHUNK = 100; // Rows per request during post-import hydration
const PREFETCH_CONCURRENCY = 2; // Max concurrent readCellRange requests during hydration
const IMPORT_BATCH_CONCURRENCY = 6; // Max concurrent batch uploads during import. After A2 chunks no longer create rows/cols (auto_expand=false), so Row/Column lock contention is gone. 6 is safe since each chunk writes disjoint cells.
const DEFAULT_NUMBER_FORMAT: NumberFormat = { type: 'GENERAL' };

const DEFAULT_CELL_FORMAT: CellFormat = {
  bold: false,
  italic: false,
  strikethrough: false,
  textColor: null,
  fontFamily: null,
  fontSize: null,
  numberFormat: null,
};

const parseColumnFilterExpression = (input: string): ParsedColumnFilter | null => {
  const trimmed = input.trim();
  if (!trimmed) return null;
  const match = trimmed.match(/^(>=|<=|>|<|=)?\s*(.*?)\s*$/);
  if (!match) return null;
  const operator = (match[1] ?? '=') as ParsedColumnFilter['operator'];
  const rawValue = (match[2] ?? '').trim();
  if (!rawValue) return null;
  const numericCandidate = Number(rawValue);
  return {
    operator,
    rawValue,
    numericValue: Number.isFinite(numericCandidate) ? numericCandidate : null,
  };
};

const HIGHLIGHT_COLORS = [
  { id: 'yellow', label: 'Yellow', value: '#FEF08A' },
  { id: 'green', label: 'Green', value: '#BBF7D0' },
  { id: 'blue', label: 'Blue', value: '#BFDBFE' },
  { id: 'pink', label: 'Pink', value: '#FBCFE8' },
  { id: 'gray', label: 'Gray', value: '#E5E7EB' },
];
const CLEAR_HIGHLIGHT = 'clear';

const FONT_FAMILIES = [
  { id: 'inherit', label: 'Default', value: '' },
  { id: 'arial', label: 'Arial', value: 'Arial, sans-serif' },
  { id: 'helvetica', label: 'Helvetica', value: 'Helvetica, Arial, sans-serif' },
  { id: 'georgia', label: 'Georgia', value: 'Georgia, serif' },
  { id: 'times', label: 'Times New Roman', value: '"Times New Roman", Times, serif' },
  { id: 'monospace', label: 'Monospace', value: 'monospace' },
];

const FONT_SIZES = [9, 10, 11, 12, 14, 16, 18, 20, 24, 28];

const CURRENCIES = [
  { code: 'USD', symbol: '$', label: 'USD ($)' },
  { code: 'EUR', symbol: '€', label: 'EUR (€)' },
  { code: 'GBP', symbol: '£', label: 'GBP (£)' },
  { code: 'JPY', symbol: '¥', label: 'JPY (¥)' },
];

const TEXT_COLORS = [
  { id: 'black', label: 'Black', value: '#111827' },
  { id: 'gray', label: 'Gray', value: '#6B7280' },
  { id: 'red', label: 'Red', value: '#DC2626' },
  { id: 'orange', label: 'Orange', value: '#EA580C' },
  { id: 'yellow', label: 'Yellow', value: '#CA8A04' },
  { id: 'green', label: 'Green', value: '#16A34A' },
  { id: 'blue', label: 'Blue', value: '#2563EB' },
  { id: 'purple', label: 'Purple', value: '#7C3AED' },
];

/**
 * Convert 0-based column index to Excel-style label (A, B, ..., Z, AA, AB, ...)
 * @param index 0-based column index
 * @returns Column label (A-Z, AA-ZZ, AAA-ZZZ, etc.)
 */
const columnIndexToLabel = (index: number): string => {
  if (index < 0) return '';
  if (index < 26) {
    return String.fromCharCode(65 + index); // A-Z
  }
  
  // For AA and beyond
  let result = '';
  let remaining = index;
  
  while (remaining >= 0) {
    result = String.fromCharCode(65 + (remaining % 26)) + result;
    remaining = Math.floor(remaining / 26) - 1;
  }
  
  return result;
};

const getCellKey = (row: number, col: number): CellKey => {
  return `${row}:${col}`;
};

const parseCellKey = (key: CellKey): { row: number; col: number } => {
  const [row, col] = key.split(':').map(Number);
  return { row, col };
};

const buildSizeIndex = (sizes: Record<number, number>, defaultSize: number): SizeIndex => {
  const entries = Object.entries(sizes)
    .map(([key, value]) => ({ index: Number(key), delta: value - defaultSize }))
    .filter((entry) => Number.isFinite(entry.index) && entry.delta !== 0)
    .sort((a, b) => a.index - b.index);

  const indices: number[] = [];
  const prefix: number[] = [];
  let totalDelta = 0;

  for (const entry of entries) {
    totalDelta += entry.delta;
    indices.push(entry.index);
    prefix.push(totalDelta);
  }

  return { indices, prefix, totalDelta };
};

const getDeltaBefore = (index: number, sizeIndex: SizeIndex): number => {
  const { indices, prefix } = sizeIndex;
  let low = 0;
  let high = indices.length - 1;
  let position = -1;

  while (low <= high) {
    const mid = Math.floor((low + high) / 2);
    if (indices[mid] < index) {
      position = mid;
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }

  return position >= 0 ? prefix[position] : 0;
};

const findIndexAtOffset = (
  offset: number,
  count: number,
  getOffset: (index: number) => number,
  getSize: (index: number) => number
): number => {
  if (count <= 0) return 0;
  const clampedOffset = Math.max(0, offset);
  let low = 0;
  let high = count - 1;

  while (low <= high) {
    const mid = Math.floor((low + high) / 2);
    const start = getOffset(mid);
    const end = start + getSize(mid);

    if (clampedOffset < start) {
      high = mid - 1;
    } else if (clampedOffset >= end) {
      low = mid + 1;
    } else {
      return mid;
    }
  }

  return Math.max(0, Math.min(count - 1, low));
};

type NormalizedRange = {
  startRow: number;
  endRow: number;
  startColumn: number;
  endColumn: number;
};

const makeRangeKey = (range: NormalizedRange): string =>
  `${range.startRow}-${range.endRow}-${range.startColumn}-${range.endColumn}`;

/**
 * Split a row/column range into non-overlapping fixed tiles (TILE_ROWS × TILE_COLUMNS).
 * Used so scroll hits per-tile cache: moving the viewport fetches only newly entered tiles.
 */
const enumerateTilesInRange = (
  startRow: number,
  endRow: number,
  startColumn: number,
  endColumn: number,
  rowLimit: number,
  colLimit: number
): NormalizedRange[] => {
  const maxRow = Math.max(0, Math.min(rowLimit - 1, MAX_ROWS - 1));
  const maxCol = Math.max(0, Math.min(colLimit - 1, MAX_COLUMNS - 1));
  const sr = Math.max(0, Math.min(startRow, maxRow));
  const er = Math.max(sr, Math.min(endRow, maxRow));
  const sc = Math.max(0, Math.min(startColumn, maxCol));
  const ec = Math.max(sc, Math.min(endColumn, maxCol));
  if (sr > er || sc > ec) return [];

  const firstRowTile = Math.floor(sr / TILE_ROWS);
  const lastRowTile = Math.floor(er / TILE_ROWS);
  const firstColTile = Math.floor(sc / TILE_COLUMNS);
  const lastColTile = Math.floor(ec / TILE_COLUMNS);
  const out: NormalizedRange[] = [];
  for (let r = firstRowTile; r <= lastRowTile; r += 1) {
    for (let c = firstColTile; c <= lastColTile; c += 1) {
      const startR = r * TILE_ROWS;
      const endR = Math.min(maxRow, (r + 1) * TILE_ROWS - 1);
      const startC = c * TILE_COLUMNS;
      const endC = Math.min(maxCol, (c + 1) * TILE_COLUMNS - 1);
      out.push({ startRow: startR, endRow: endR, startColumn: startC, endColumn: endC });
    }
  }
  return out;
};

async function runTileReadTasks(tasks: Array<() => Promise<void>>): Promise<void> {
  if (tasks.length === 0) return;
  const n = Math.min(RANGE_TILE_READ_CONCURRENCY, tasks.length);
  let index = 0;
  const worker = async () => {
    while (index < tasks.length) {
      const i = index;
      index += 1;
      await tasks[i]();
    }
  };
  await Promise.all(Array.from({ length: n }, () => worker()));
}

// Cache cells per sheetId to maintain isolation
const cellCache = new Map<number, Map<CellKey, CellData>>();
const loadedRangesCache = new Map<number, Set<string>>(); // Set of per-tile range keys (makeRangeKey)
// Track in-flight range requests per sheet so multiple callers share the same promise (keyed by tile)
const inFlightRangeRequests = new Map<number, Map<string, Promise<void>>>();
// Cache dimensions per sheetId
const dimensionsCache = new Map<number, { rowCount: number; colCount: number }>();

/**
 * Parse a TSV (tab-separated values) string into a 2D array of strings.
 *
 * - Rows are separated by `\n` or `\r\n`
 * - Columns are separated by `\t`
 * - Trailing empty rows are discarded (to match Excel / Sheets behavior)
 */
const parseTSV = (text: string): string[][] => {
  if (!text) return [];

  // Normalize newlines and split into rows
  const rawRows = text.replace(/\r\n/g, '\n').split('\n');

  // Drop trailing empty rows
  while (rawRows.length > 0 && rawRows[rawRows.length - 1] === '') {
    rawRows.pop();
  }

  if (rawRows.length === 0) return [];

  return rawRows.map((row) => row.split('\t'));
};

type RemoteCellPresence = {
  selectionOwner: SheetPresenceUser | null;
  cursors: SheetPresenceUser[];
};

function resolveRemoteCellPresence(
  users: SheetPresenceUser[],
  row: number,
  col: number
): RemoteCellPresence {
  let selectionOwner: SheetPresenceUser | null = null;
  const cursors: SheetPresenceUser[] = [];

  for (const user of users) {
    const cursor = user.cursor;
    if (!cursor?.isActive) continue;

    const activeRow = cursor.row;
    const activeCol = cursor.col;
    if (activeRow != null && activeCol != null && activeRow === row && activeCol === col) {
      cursors.push(user);
    }

    const startRow = cursor.startRow ?? activeRow;
    const endRow = cursor.endRow ?? activeRow;
    const startCol = cursor.startCol ?? activeCol;
    const endCol = cursor.endCol ?? activeCol;
    if (startRow == null || endRow == null || startCol == null || endCol == null) continue;

    const minRow = Math.min(startRow, endRow);
    const maxRow = Math.max(startRow, endRow);
    const minCol = Math.min(startCol, endCol);
    const maxCol = Math.max(startCol, endCol);
    if (row >= minRow && row <= maxRow && col >= minCol && col <= maxCol) {
      selectionOwner = user;
    }
  }

  return { selectionOwner, cursors };
}

const SpreadsheetGrid = forwardRef<SpreadsheetGridHandle, SpreadsheetGridProps>(({
  spreadsheetId,
  sheetId,
  loading = false,
  spreadsheetName,
  sheetName,
  onFormulaCommit,
  onInsertRowCommit,
  onInsertColumnCommit,
  onDeleteColumnCommit,
  onFillCommit,
  onHeaderRenameCommit,
  onHighlightCommit,
  highlightCell,
  highlightLocations,
  onHydrationStatusChange,
  frozenRowCount = 0,
  onFreezeHeaderChange,
  onOpenPivotBuilder,
  onSelectionChange,
  collabClientId,
  remotePresenceUsers = [],
}: SpreadsheetGridProps, ref) => {
  const isGridLoading = loading || sheetId <= 0;
  const [rowCount, setRowCount] = useState(DEFAULT_ROWS);
  const [colCount, setColCount] = useState(DEFAULT_COLUMNS);
  const [colWidths, setColWidths] = useState<Record<number, number>>({});
  const [rowHeights, setRowHeights] = useState<Record<number, number>>({});
  const [cells, setCells] = useState<Map<CellKey, CellData>>(new Map());
  const [cellHighlightsBySheet, setCellHighlightsBySheet] = useState<Record<number, Map<CellKey, string>>>({});
  const [rowHighlightsBySheet, setRowHighlightsBySheet] = useState<Record<number, Record<number, string>>>({});
  const [colHighlightsBySheet, setColHighlightsBySheet] = useState<Record<number, Record<number, string>>>({});
  const [cellFormatsBySheet, setCellFormatsBySheet] = useState<Record<number, Map<CellKey, CellFormat>>>({});
  const [highlightMenuOpen, setHighlightMenuOpen] = useState(false);
  const [selectedHighlight, setSelectedHighlight] = useState(HIGHLIGHT_COLORS[0].value);
  const [textColorMenuOpen, setTextColorMenuOpen] = useState(false);
  const [currencyMenuOpen, setCurrencyMenuOpen] = useState(false);
  const [selectedTextColor, setSelectedTextColor] = useState<string | null>(null);
  const [selectedFontFamily, setSelectedFontFamily] = useState<string | null>(null);
  const [selectedFontSize, setSelectedFontSize] = useState<number | null>(null);
  const [selectedNumberFormat, setSelectedNumberFormat] = useState<NumberFormat | null>(null);
  const [activeCell, setActiveCell] = useState<ActiveCell | null>(null);
  const [anchorCell, setAnchorCell] = useState<ActiveCell | null>(null); // Selection start point
  const [focusCell, setFocusCell] = useState<ActiveCell | null>(null); // Selection end point
  const [isSelecting, setIsSelecting] = useState(false); // Track if mouse is down for selection
  const [editingCell, setEditingCell] = useState<CellKey | null>(null);
  const [editValue, setEditValue] = useState<string>('');
  const [mode, setMode] = useState<'navigation' | 'edit'>('navigation');
  const [navigationLocked, setNavigationLocked] = useState(false);
  const [pendingOps, setPendingOps] = useState<Map<CellKey, PendingOperation>>(new Map());
  const [hydrationStatus, setHydrationStatus] = useState<'idle' | 'importing' | 'hydrating' | 'ready'>('ready');

  useEffect(() => {
    setCellHighlightsBySheet((prev) => (prev[sheetId] ? prev : { ...prev, [sheetId]: new Map() }));
    setRowHighlightsBySheet((prev) => (prev[sheetId] ? prev : { ...prev, [sheetId]: {} }));
    setColHighlightsBySheet((prev) => (prev[sheetId] ? prev : { ...prev, [sheetId]: {} }));
    setCellFormatsBySheet((prev) => (prev[sheetId] ? prev : { ...prev, [sheetId]: new Map() }));
  }, [sheetId]);

  useEffect(() => {
    onHydrationStatusChange?.(hydrationStatus);
  }, [hydrationStatus, onHydrationStatusChange]);

  useEffect(() => {
    setHydrationStatus('ready');
  }, [sheetId]);

  const cellHighlights = cellHighlightsBySheet[sheetId] ?? new Map();
  const rowHighlights = rowHighlightsBySheet[sheetId] ?? {};
  const colHighlights = colHighlightsBySheet[sheetId] ?? {};
  const cellFormats = cellFormatsBySheet[sheetId] ?? new Map();

  const highlightOpsRef = useRef<HighlightOp[]>([]);
  const highlightFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const importAbortControllerRef = useRef<AbortController | null>(null);
  const cellFormatsBySheetRef = useRef<Record<number, Map<CellKey, CellFormat>>>({});
  cellFormatsBySheetRef.current = cellFormatsBySheet;

  const enqueueHighlightOps = useCallback(
    (ops: HighlightOp[]) => {
      if (ops.length === 0) return;
      highlightOpsRef.current.push(...ops);
      if (highlightFlushTimerRef.current) return;
      highlightFlushTimerRef.current = setTimeout(async () => {
        const batch = highlightOpsRef.current.splice(0, highlightOpsRef.current.length);
        highlightFlushTimerRef.current = null;
        try {
          await SpreadsheetAPI.batchUpdateHighlights(spreadsheetId, sheetId, batch);
        } catch (error) {
          console.error('Failed to save highlights:', error);
          toast.error('Failed to save highlights');
        }
      }, 400);
    },
    [spreadsheetId, sheetId]
  );

  useEffect(() => {
    return () => {
      if (highlightFlushTimerRef.current) {
        clearTimeout(highlightFlushTimerRef.current);
        highlightFlushTimerRef.current = null;
      }
      if (highlightOpsRef.current.length > 0) {
        const batch = highlightOpsRef.current.splice(0, highlightOpsRef.current.length);
        // Best-effort flush of any pending highlight ops on unmount
        SpreadsheetAPI.batchUpdateHighlights(spreadsheetId, sheetId, batch).catch((error) => {
          console.error('Failed to save highlights on unmount:', error);
        });
      }
    };
  }, [spreadsheetId, sheetId]);
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [undoStack, setUndoStack] = useState<UndoEntry[]>([]);
  const [redoStack, setRedoStack] = useState<RedoEntry[]>([]);
  const [visibleRange, setVisibleRange] = useState({
    startRow: 0,
    endRow: Math.min(30, DEFAULT_ROWS - 1),
    startCol: 0,
    endCol: Math.min(10, DEFAULT_COLUMNS - 1),
  });
  const [isImporting, setIsImporting] = useState(false);
  /** Tracks first-viewport loading so visible cells can render inline placeholders without blocking the sheet shell. */
  const [cellCanvasLoading, setCellCanvasLoading] = useState(true);
  const [importProgress, setImportProgress] = useState<{ current: number; total: number } | null>(null);
  const [xlsxImport, setXlsxImport] = useState<XLSXParseResult | null>(null);
  const [selectedXlsxSheet, setSelectedXlsxSheet] = useState<string>('');
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const [exportMenuAnchor, setExportMenuAnchor] = useState<{ top: number; left: number; width: number } | null>(null);
  const [sheetsImportModalOpen, setSheetsImportModalOpen] = useState(false);
  const [sheetsImportUrl, setSheetsImportUrl] = useState('');
  const [sheetsImportLoading, setSheetsImportLoading] = useState(false);
  const [headerMenu, setHeaderMenu] = useState<{
    type: 'row' | 'col';
    index: number;
    x: number;
    y: number;
  } | null>(null);
  const [sortMenu, setSortMenu] = useState<{ colIndex: number; x: number; y: number } | null>(null);
  const sortHistoryRef = useRef<SortColumnHistoryEntry[]>([]); // Most recent sort first, with direction per column
  const [columnFilters, setColumnFilters] = useState<Record<number, string>>({});
  const [columnFilterOrder, setColumnFilterOrder] = useState<number[]>([]);
  const [isReverting, setIsReverting] = useState(false);
  const [isResizing, setIsResizing] = useState(false);
  const [formulaBarValue, setFormulaBarValue] = useState<string>('');
  const [isFormulaBarEditing, setIsFormulaBarEditing] = useState(false);
  const [formulaBarTarget, setFormulaBarTarget] = useState<CellKey | null>(null);
  const [isFilling, setIsFilling] = useState(false);
  const [fillPreview, setFillPreview] = useState<{ direction: 'horizontal' | 'vertical' | null; count: number } | null>(
    null
  );
  const [isFillSubmitting, setIsFillSubmitting] = useState(false);
  const [showAddRowsUI, setShowAddRowsUI] = useState(false);
  const [addRowsInputValue, setAddRowsInputValue] = useState('1000');

  const inputRef = useRef<HTMLInputElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const debounceTimerRef = useRef<NodeJS.Timeout | null>(null);
  const resizeDebounceTimerRef = useRef<NodeJS.Timeout | null>(null);
  const scrollRafIdRef = useRef<number | null>(null);
  // Invalidates range responses that started before a canonical refresh or
  // sheet switch. Clearing the in-flight map alone cannot cancel HTTP reads;
  // without this guard a stale response can repopulate pre-structure cells.
  const cellCacheGenerationRef = useRef(0);
  const pendingCanonicalRefreshRef = useRef(false);
  const canonicalRefreshInFlightRef = useRef(0);
  const pendingOpsRef = useRef(pendingOps);
  const isSavingRef = useRef(isSaving);
  pendingOpsRef.current = pendingOps;
  isSavingRef.current = isSaving;
  const pendingSelectionRef = useRef<{ position: 'start' | 'end' | number } | null>(null);
  const resizeStateRef = useRef<ResizeState | null>(null);
  const fillStateRef = useRef<{
    startRow: number;
    startCol: number;
    startX: number;
    startY: number;
    direction: 'horizontal' | 'vertical' | null;
    pointerId: number;
  } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const exportMenuRef = useRef<HTMLDivElement>(null);
  const exportTriggerRef = useRef<HTMLButtonElement>(null);
  const highlightMenuRef = useRef<HTMLDivElement>(null);
  const highlightTriggerRef = useRef<HTMLButtonElement>(null);
  const textColorMenuRef = useRef<HTMLDivElement>(null);

  // Initialize dimensions and cells cache for this sheetId
  useEffect(() => {
    pendingCanonicalRefreshRef.current = false;
    cellCacheGenerationRef.current += 1;
    if (!cellCache.has(sheetId)) {
      cellCache.set(sheetId, new Map());
    }
    if (!loadedRangesCache.has(sheetId)) {
      loadedRangesCache.set(sheetId, new Set());
    }
    
    // Load cached dimensions or use defaults (finite grid: 1000x26)
    const cachedDimensions = dimensionsCache.get(sheetId);
    if (cachedDimensions) {
      setRowCount(cachedDimensions.rowCount);
      setColCount(cachedDimensions.colCount);
    } else {
      // New sheet: use finite grid defaults
      setRowCount(DEFAULT_ROWS);
      setColCount(DEFAULT_COLUMNS);
      dimensionsCache.set(sheetId, { rowCount: DEFAULT_ROWS, colCount: DEFAULT_COLUMNS });
    }
    
    // Load cached cells for this sheet
    const cachedCells = cellCache.get(sheetId) || new Map();
    setCells(new Map(cachedCells));
    
    // Reset selection when switching sheets
    setActiveCell(null);
    setAnchorCell(null);
    setFocusCell(null);
    setIsSelecting(false);
    setColWidths({});
    setRowHeights({});
    setIsResizing(false);
    resizeStateRef.current = null;
    setUndoStack([]);
    setRedoStack([]);
    setMode('navigation');
    setNavigationLocked(false);
    sortHistoryRef.current = [];
    setColumnFilters({});
    setColumnFilterOrder([]);
  }, [sheetId]);

  /**
   * Push a new entry to the undo history stack.
   * Each entry groups one logical user action (edit, paste, batch delete).
   */
  const pushHistoryEntry = useCallback((entry: HistoryEntry) => {
    if (!entry.changes.length) return;
    setUndoStack((prev) => [...prev, { type: 'cell', entry }]);
    setRedoStack([]);
  }, []);

  const applyCellValueLocal = useCallback(
    (row: number, col: number, value: string) => {
      const key = getCellKey(row, col);
      const cellData: CellData = {
        rawInput: value,
        computedType: null,
        computedNumber: null,
        computedString: null,
        errorCode: null,
        isLoaded: true,
      };

      setCells((prev) => {
        const next = new Map(prev);
        next.set(key, cellData);

        const cachedCells = cellCache.get(sheetId) || new Map();
        cachedCells.set(key, cellData);
        cellCache.set(sheetId, cachedCells);

        return next;
      });
    },
    [sheetId]
  );

  /**
   * Batched variant of applyCellValueLocal. Used by large imports where calling
   * the per-cell helper N times would clone the cells Map N times (O(n^2)).
   * Applies all entries with a single setCells + single cellCache write.
   */
  const applyCellValuesLocalBatch = useCallback(
    (entries: Array<{ row: number; col: number; value: string }>) => {
      if (!entries.length) return;
      setCells((prev) => {
        const next = new Map(prev);
        const cachedCells = cellCache.get(sheetId) || new Map();
        for (const entry of entries) {
          const key = getCellKey(entry.row, entry.col);
          const cellData: CellData = {
            rawInput: entry.value,
            computedType: null,
            computedNumber: null,
            computedString: null,
            errorCode: null,
            isLoaded: true,
          };
          next.set(key, cellData);
          cachedCells.set(key, cellData);
        }
        cellCache.set(sheetId, cachedCells);
        return next;
      });
    },
    [sheetId]
  );

  // True when a cell editor is mounted and active.
  // In this mode, we must NOT handle grid-level keyboard shortcuts so that
  // native text editing (typing, Backspace/Delete, Ctrl/Cmd+Z, etc.) works.
  const isEditing = mode === 'edit';

  const rowSizeIndex = useMemo(() => buildSizeIndex(rowHeights, ROW_HEIGHT), [rowHeights]);
  const colSizeIndex = useMemo(() => buildSizeIndex(colWidths, COLUMN_WIDTH), [colWidths]);

  const getRowHeight = useCallback((row: number) => rowHeights[row] ?? ROW_HEIGHT, [rowHeights]);
  const getColumnWidth = useCallback((col: number) => colWidths[col] ?? COLUMN_WIDTH, [colWidths]);

  const getRowOffset = useCallback(
    (rowIndex: number) => {
      const clamped = Math.max(0, Math.min(rowIndex, rowCount));
      return clamped * ROW_HEIGHT + getDeltaBefore(clamped, rowSizeIndex);
    },
    [rowCount, rowSizeIndex]
  );

  const getColumnOffset = useCallback(
    (colIndex: number) => {
      const clamped = Math.max(0, Math.min(colIndex, colCount));
      return clamped * COLUMN_WIDTH + getDeltaBefore(clamped, colSizeIndex);
    },
    [colCount, colSizeIndex]
  );

  const getRowIndexAtOffset = useCallback(
    (offset: number) => findIndexAtOffset(offset, rowCount, getRowOffset, getRowHeight),
    [rowCount, getRowOffset, getRowHeight]
  );

  const getColumnIndexAtOffset = useCallback(
    (offset: number) => findIndexAtOffset(offset, colCount, getColumnOffset, getColumnWidth),
    [colCount, getColumnOffset, getColumnWidth]
  );

  const totalRowHeight = useMemo(
    () => rowCount * ROW_HEIGHT + rowSizeIndex.totalDelta,
    [rowCount, rowSizeIndex]
  );

  const totalColumnWidth = useMemo(
    () => colCount * COLUMN_WIDTH + colSizeIndex.totalDelta,
    [colCount, colSizeIndex]
  );

  /**
   * State transitions:
   * - Navigation Mode: grid navigation/selection keys only
   * - Edit Mode: text input is active
   * - navigationLocked: when true, arrow keys move the caret within the input
   *   instead of navigating between cells
   */
  const enterEditMode = useCallback(
    (cell: ActiveCell, initialValue: string, locked: boolean, caret: 'start' | 'end' | number) => {
      const key = getCellKey(cell.row, cell.col);
      setActiveCell(cell);
      setEditingCell(key);
      setEditValue(initialValue);
      setMode('edit');
      setNavigationLocked(locked);
      pendingSelectionRef.current = { position: caret };
    },
    []
  );

  /**
   * Compute selection range from anchor and focus cells
   * 
   * Selection Model:
   * - anchorCell: The starting point of the selection (where user started selecting)
   * - focusCell: The current end point of the selection (where user is now)
   * - The actual selection rectangle is computed as the bounding box of these two points
   * 
   * This allows selection to work in any direction (up, down, left, right from anchor)
   * and enables future features like copy/paste operations on the selected range.
   * 
   * Returns null if no valid selection exists
   */
  const computeSelectionRange = useCallback((): SelectionRange | null => {
    if (!anchorCell || !focusCell) {
      return null;
    }
    
    return {
      startRow: Math.min(anchorCell.row, focusCell.row),
      endRow: Math.max(anchorCell.row, focusCell.row),
      startCol: Math.min(anchorCell.col, focusCell.col),
      endCol: Math.max(anchorCell.col, focusCell.col),
    };
  }, [anchorCell, focusCell]);

  /**
   * Check if a cell is within the selection range
   */
  const isCellInSelection = useCallback(
    (row: number, col: number): boolean => {
      const range = computeSelectionRange();
      if (!range) return false;
      
      return (
        row >= range.startRow &&
        row <= range.endRow &&
        col >= range.startCol &&
        col <= range.endCol
      );
    },
    [computeSelectionRange]
  );

  /**
   * Get an "effective" selection range:
   * - If a multi-cell selection exists, use that range.
   * - Otherwise, fall back to the active cell as a 1x1 range.
   *
   * This is used by copy/paste so they work even when only a single
   * active cell is selected.
   */
  const getEffectiveSelectionRange = useCallback((): SelectionRange | null => {
    const range = computeSelectionRange();
    if (range) {
      return range;
    }

    if (activeCell) {
      return {
        startRow: activeCell.row,
        endRow: activeCell.row,
        startCol: activeCell.col,
        endCol: activeCell.col,
      };
    }

    return null;
  }, [computeSelectionRange, activeCell]);

  useEffect(() => {
    if (!onSelectionChange) return;
    if (!activeCell) {
      onSelectionChange(null);
      return;
    }
    const range = getEffectiveSelectionRange();
    onSelectionChange({
      row: activeCell.row,
      col: activeCell.col,
      startRow: range?.startRow ?? activeCell.row,
      endRow: range?.endRow ?? activeCell.row,
      startCol: range?.startCol ?? activeCell.col,
      endCol: range?.endCol ?? activeCell.col,
    });
  }, [activeCell, anchorCell, focusCell, onSelectionChange, getEffectiveSelectionRange]);

  const isSingleCellSelection = useMemo(() => {
    if (!activeCell || !anchorCell || !focusCell) return false;
    return (
      activeCell.row === anchorCell.row &&
      activeCell.col === anchorCell.col &&
      anchorCell.row === focusCell.row &&
      anchorCell.col === focusCell.col
    );
  }, [activeCell, anchorCell, focusCell]);

  const isCellInFillPreview = useCallback(
    (row: number, col: number): boolean => {
      if (!isFilling || !fillPreview || !activeCell) return false;
      if (!fillPreview.direction || fillPreview.count === 0) return false;
      if (fillPreview.direction === 'vertical') {
        const start = activeCell.row + Math.min(0, fillPreview.count);
        const end = activeCell.row + Math.max(0, fillPreview.count);
        return col === activeCell.col && row >= start && row <= end && row !== activeCell.row;
      }
      const start = activeCell.col + Math.min(0, fillPreview.count);
      const end = activeCell.col + Math.max(0, fillPreview.count);
      return row === activeCell.row && col >= start && col <= end && col !== activeCell.col;
    },
    [activeCell, fillPreview, isFilling]
  );

  /**
   * Resize grid dimensions (for import or manual expansion)
   * @param targetRows Target row count
   * @param targetCols Target column count
   * @param persistToBackend Whether to persist to backend (default: true)
   * @returns true if resize succeeded, false if clamped to max
   */
  /**
   * Synchronously update the local grid dimensions (rowCount / colCount) and the
   * module-level dimensionsCache. Returns the effective (clamped) dimensions and
   * whether clamping occurred, so callers can decide what to persist.
   *
   * No backend call is made here. Callers that also want to persist should pair this
   * with `persistResizeToBackend`, or use the composite `resizeGrid` wrapper below.
   */
  const updateGridDimensionsLocal = useCallback(
    (
      targetRows: number,
      targetCols: number,
    ): { clampedRows: number; clampedCols: number; wasClamped: boolean } => {
      const clampedRows = Math.min(MAX_ROWS, Math.max(0, targetRows));
      const clampedCols = Math.min(MAX_COLUMNS, Math.max(0, targetCols));
      const wasClamped = clampedRows !== targetRows || clampedCols !== targetCols;

      setRowCount(clampedRows);
      setColCount(clampedCols);
      dimensionsCache.set(sheetId, { rowCount: clampedRows, colCount: clampedCols });

      return { clampedRows, clampedCols, wasClamped };
    },
    [sheetId]
  );

  /**
   * Persist the given sheet dimensions to the backend.
   * - `immediate=false` (default): debounce the POST so rapid edits coalesce.
   * - `immediate=true`: skip debounce, await the POST, return true on success and
   *   false if the call errored. Used by the import path.
   */
  const persistResizeToBackend = useCallback(
    async (clampedRows: number, clampedCols: number, immediate: boolean = false): Promise<boolean> => {
      if (immediate) {
        if (resizeDebounceTimerRef.current) {
          clearTimeout(resizeDebounceTimerRef.current);
          resizeDebounceTimerRef.current = null;
        }
        try {
          await SpreadsheetAPI.resizeSheet(spreadsheetId, sheetId, clampedRows, clampedCols);
          return true;
        } catch (error: any) {
          console.error('Failed to persist sheet dimensions:', error);
          return false;
        }
      }

      if (resizeDebounceTimerRef.current) {
        clearTimeout(resizeDebounceTimerRef.current);
      }
      resizeDebounceTimerRef.current = setTimeout(async () => {
        try {
          await SpreadsheetAPI.resizeSheet(spreadsheetId, sheetId, clampedRows, clampedCols);
        } catch (error: any) {
          console.error('Failed to persist sheet dimensions:', error);
          // Non-blocking error - dimensions are still updated locally
        }
      }, RESIZE_DEBOUNCE_MS);
      return true;
    },
    [sheetId, spreadsheetId]
  );

  /**
   * Composite helper that keeps the original single-call API: update local state
   * first, then kick off the backend persist. Used by existing (non-import) call
   * sites so they do not need to change.
   *
   * Returns false only when:
   *   - the requested dimensions were clamped by MAX_ROWS / MAX_COLUMNS, OR
   *   - `immediate=true` and the backend call failed.
   */
  const resizeGrid = useCallback(
    async (
      targetRows: number,
      targetCols: number,
      persistToBackend: boolean = true,
      immediate: boolean = false
    ): Promise<boolean> => {
      const { clampedRows, clampedCols, wasClamped } = updateGridDimensionsLocal(targetRows, targetCols);

      if (!persistToBackend) {
        return !wasClamped;
      }

      const persisted = await persistResizeToBackend(clampedRows, clampedCols, immediate);
      if (immediate && !persisted) {
        return false;
      }
      return !wasClamped;
    },
    [updateGridDimensionsLocal, persistResizeToBackend]
  );

  /**
   * Expand dimensions if needed (ONLY for import - deprecated for normal edits)
   * @deprecated Use resizeGrid for manual expansion. This is kept only for import compatibility.
   */
  const ensureDimensions = useCallback(
    (minRow: number, minCol: number, allowAutoExpand: boolean = false) => {
      if (!allowAutoExpand) {
        // In finite grid mode, ensureDimensions does nothing unless explicitly allowed (for import)
        return;
      }

      let newRowCount = rowCount;
      let newColCount = colCount;
      let needsUpdate = false;

      if (minRow >= rowCount && rowCount < MAX_ROWS) {
        newRowCount = Math.min(MAX_ROWS, Math.max(rowCount + AUTO_GROW_ROWS, minRow + 1));
        needsUpdate = true;
      }

      if (minCol >= colCount && colCount < MAX_COLUMNS) {
        newColCount = Math.min(MAX_COLUMNS, Math.max(colCount + AUTO_GROW_COLUMNS, minCol + 1));
        needsUpdate = true;
      }

      if (needsUpdate) {
        resizeGrid(newRowCount, newColCount, true);
      }
    },
    [rowCount, colCount, resizeGrid]
  );

  // Safe default when container is missing or has zero size (prevents grid from vanishing)
  const safeDefaultRange = useMemo(
    () => ({
      startRow: 0,
      endRow: Math.min(30, Math.max(0, rowCount - 1)),
      startColumn: 0,
      endColumn: Math.min(10, Math.max(0, colCount - 1)),
    }),
    [rowCount, colCount]
  );

  // Compute visible range from scroll position; never return empty or invalid range
  const computeVisibleRange = useCallback((): {
    startRow: number;
    endRow: number;
    startColumn: number;
    endColumn: number;
  } => {
    const safeRows = Math.max(1, rowCount);
    const safeCols = Math.max(1, colCount);
    const maxRow = safeRows - 1;
    const maxCol = safeCols - 1;

    if (!gridRef.current) {
      return {
        startRow: 0,
        endRow: Math.min(30, maxRow),
        startColumn: 0,
        endColumn: Math.min(10, maxCol),
      };
    }

    const container = gridRef.current;
    const containerHeight = container.clientHeight;
    const containerWidth = container.clientWidth;

    if (containerHeight <= 0 || containerWidth <= 0) {
      return safeDefaultRange;
    }

    const scrollTop = container.scrollTop;
    const scrollLeft = container.scrollLeft;

    const maxScrollTop = Math.max(0, totalRowHeight - containerHeight + HEADER_HEIGHT);
    const clampedScrollTop = Math.min(scrollTop, maxScrollTop);
    const adjustedScrollTop = Math.max(0, clampedScrollTop - HEADER_HEIGHT);

    let startRow = Math.max(0, Math.min(maxRow, getRowIndexAtOffset(adjustedScrollTop) - OVERSCAN_ROWS));
    let endRow = Math.min(maxRow, Math.max(startRow, getRowIndexAtOffset(Math.min(adjustedScrollTop + containerHeight, totalRowHeight)) + OVERSCAN_ROWS));
    endRow = Math.max(startRow, endRow);

    const dataViewportWidth = Math.max(0, containerWidth - ROW_NUMBER_WIDTH);
    let startColumn = Math.max(0, getColumnIndexAtOffset(scrollLeft) - OVERSCAN_COLUMNS);
    let endColumn = Math.min(maxCol, getColumnIndexAtOffset(scrollLeft + dataViewportWidth) + OVERSCAN_COLUMNS);
    endColumn = Math.max(startColumn, endColumn);

    if (!Number.isFinite(startRow) || !Number.isFinite(endRow) || !Number.isFinite(startColumn) || !Number.isFinite(endColumn)) {
      return safeDefaultRange;
    }

    return {
      startRow,
      endRow: Math.min(maxRow, Math.max(startRow, endRow)),
      startColumn,
      endColumn: Math.min(maxCol, Math.max(startColumn, endColumn)),
    };
  }, [rowCount, colCount, getRowIndexAtOffset, getColumnIndexAtOffset, totalRowHeight, safeDefaultRange]);

  // Mark one fixed tile (aligned bounds) as loaded.
  const markTileLoaded = useCallback((tile: NormalizedRange) => {
    const rangeKey = makeRangeKey(tile);
    const loadedRanges = loadedRangesCache.get(sheetId);
    if (loadedRanges) {
      loadedRanges.add(rangeKey);
    }
  }, [sheetId]);

  // Load cells from backend for a range
  const loadCellRange = useCallback(
    async (
      startRow: number,
      endRow: number,
      startColumn: number,
      endColumn: number,
      force: boolean = false,
      loadOptions?: { includeSheetDimensions?: boolean }
    ) => {
      const requestGeneration = cellCacheGenerationRef.current;
      const wantSheetDimensions = loadOptions?.includeSheetDimensions !== false;

      let inFlightForSheet = inFlightRangeRequests.get(sheetId);
      if (!inFlightForSheet) {
        inFlightForSheet = new Map<string, Promise<void>>();
        inFlightRangeRequests.set(sheetId, inFlightForSheet);
      }

      const loadedRanges = loadedRangesCache.get(sheetId);
      const rLimit = Math.max(1, rowCount);
      const cLimit = Math.max(1, colCount);
      const tiles = enumerateTilesInRange(startRow, endRow, startColumn, endColumn, rLimit, cLimit);
      if (tiles.length === 0) return;

      if (force) {
        for (const t of tiles) {
          const k = makeRangeKey(t);
          loadedRanges?.delete(k);
        }
      }

      const toAwait: Promise<void>[] = [];
      const newTileTasks: Array<() => Promise<void>> = [];
      let willSendSheetDimensions = wantSheetDimensions;

      for (const tile of tiles) {
        const rangeKey = makeRangeKey(tile);
        const cacheHit = !force && !!loadedRanges && loadedRanges.has(rangeKey);
        if (cacheHit) {
          continue;
        }

        const inFlight = inFlightForSheet.get(rangeKey);
        if (inFlight) {
          toAwait.push(inFlight);
          continue;
        }

        const includeThisDimension = willSendSheetDimensions;
        if (includeThisDimension) {
          willSendSheetDimensions = false;
        }

        // Register a placeholder immediately so concurrent loadCellRange calls dedupe
        // on the same tile, while the HTTP request only starts when a worker runs
        // (RANGE_TILE_READ_CONCURRENCY limits in-flight reads).
        let completeTileRead!: () => void;
        const p = new Promise<void>((resolve) => {
          completeTileRead = resolve;
        });
        inFlightForSheet.set(rangeKey, p);

        newTileTasks.push(async () => {
          try {
            const response = await SpreadsheetAPI.readCellRange(
              spreadsheetId,
              sheetId,
              tile.startRow,
              tile.endRow,
              tile.startColumn,
              tile.endColumn,
              { includeSheetDimensions: includeThisDimension }
            );

            if (requestGeneration !== cellCacheGenerationRef.current) {
              return;
            }

            if (includeThisDimension) {
              const res = response as typeof response & {
                sheet_row_count?: number | null;
                sheet_column_count?: number | null;
              };
              const sheetRows = res.sheet_row_count != null ? res.sheet_row_count : null;
              const sheetCols = res.sheet_column_count != null ? res.sheet_column_count : null;
              if (sheetRows != null && sheetCols != null) {
                const backendRowCount = Math.min(MAX_ROWS, Math.max(DEFAULT_ROWS, sheetRows));
                const backendColCount = Math.min(MAX_COLUMNS, Math.max(DEFAULT_COLUMNS, sheetCols));
                if (backendRowCount !== rowCount || backendColCount !== colCount) {
                  setRowCount(backendRowCount);
                  setColCount(backendColCount);
                  dimensionsCache.set(sheetId, { rowCount: backendRowCount, colCount: backendColCount });
                }
                if (backendRowCount > sheetRows || backendColCount > sheetCols) {
                  void resizeGrid(backendRowCount, backendColCount, true);
                }
              }
            }

            setCells((prev) => {
              const next = new Map(prev);
              const cachedCells = cellCache.get(sheetId) || new Map();

              response.cells.forEach((cell) => {
                const key = getCellKey(cell.row_position, cell.column_position);
                const existing = next.get(key);
                const incomingUpdatedAt = cell.updated_at
                  ? Date.parse(cell.updated_at)
                  : Number.NaN;
                const existingUpdatedAt = existing?.updatedAt
                  ? Date.parse(existing.updatedAt)
                  : Number.NaN;
                if (
                  Number.isFinite(incomingUpdatedAt) &&
                  Number.isFinite(existingUpdatedAt) &&
                  incomingUpdatedAt < existingUpdatedAt
                ) {
                  return;
                }
                const fallbackRawInput =
                  cell.raw_input ??
                  cell.formula_value ??
                  cell.string_value ??
                  (cell.number_value != null ? String(cell.number_value) : '') ??
                  (cell.boolean_value != null ? (cell.boolean_value ? 'TRUE' : 'FALSE') : '');
                const cellData: CellData = {
                  rawInput: fallbackRawInput,
                  computedType: cell.computed_type ?? null,
                  computedNumber: cell.computed_number ?? null,
                  computedString: cell.computed_string ?? null,
                  errorCode: cell.error_code ?? null,
                  updatedAt: cell.updated_at ?? null,
                  isLoaded: true,
                };
                next.set(key, cellData);
                cachedCells.set(key, cellData);
              });

              cellCache.set(sheetId, cachedCells);
              return next;
            });

            markTileLoaded(tile);
          } catch (error: any) {
            console.error('Failed to load cell range:', error);
          } finally {
            const currentForSheet = inFlightRangeRequests.get(sheetId);
            if (currentForSheet) {
              currentForSheet.delete(rangeKey);
            }
            completeTileRead();
          }
        });
      }

      if (newTileTasks.length > 0) {
        await runTileReadTasks(newTileTasks);
      }
      if (toAwait.length > 0) {
        await Promise.all(toAwait);
      }
    },
    [spreadsheetId, sheetId, markTileLoaded, resizeGrid, rowCount, colCount]
  );

  const applyCellsFromResponse = useCallback(
    (cellsResponse?: Array<{
      row_position: number;
      column_position: number;
      raw_input?: string | null;
      string_value?: string | null;
      number_value?: number | string | null;
      boolean_value?: boolean | null;
      formula_value?: string | null;
      computed_type?: string | null;
      computed_number?: number | string | null;
      computed_string?: string | null;
      error_code?: string | null;
      updated_at?: string | null;
    }>, options?: { source?: 'local' | 'remote' }) => {
      if (!cellsResponse || cellsResponse.length === 0) return;
      setCells((prev) => {
        const next = new Map(prev);
        const cachedCells = cellCache.get(sheetId) || new Map();

        cellsResponse.forEach((cell) => {
          const key = getCellKey(cell.row_position, cell.column_position);
          // A peer broadcast must not overwrite a newer local optimistic edit
          // that has not reached the authoritative HTTP write path yet.
          if (options?.source === 'remote' && pendingOpsRef.current.has(key)) {
            return;
          }
          const existing = next.get(key);
          const incomingUpdatedAt = cell.updated_at
            ? Date.parse(cell.updated_at)
            : Number.NaN;
          const existingUpdatedAt = existing?.updatedAt
            ? Date.parse(existing.updatedAt)
            : Number.NaN;
          if (
            options?.source === 'remote' &&
            Number.isFinite(incomingUpdatedAt) &&
            Number.isFinite(existingUpdatedAt) &&
            incomingUpdatedAt < existingUpdatedAt
          ) {
            return;
          }
          const fallbackRawInput =
            cell.raw_input ??
            cell.formula_value ??
            cell.string_value ??
            (cell.number_value != null ? String(cell.number_value) : '') ??
            (cell.boolean_value != null ? (cell.boolean_value ? 'TRUE' : 'FALSE') : '');
          const cellData: CellData = {
            rawInput: fallbackRawInput,
            computedType: cell.computed_type ?? null,
            computedNumber: cell.computed_number ?? null,
            computedString: cell.computed_string ?? null,
            errorCode: cell.error_code ?? null,
            updatedAt: cell.updated_at ?? existing?.updatedAt ?? null,
            isLoaded: true,
          };
          next.set(key, cellData);
          cachedCells.set(key, cellData);
        });

        cellCache.set(sheetId, cachedCells);
        return next;
      });
    },
    [sheetId]
  );

  const resetSheetCaches = useCallback((options?: { preserveCells?: boolean }) => {
    cellCacheGenerationRef.current += 1;
    if (!options?.preserveCells) {
      cellCache.set(sheetId, new Map());
    }
    loadedRangesCache.set(sheetId, new Set());
    const inFlightForSheet = inFlightRangeRequests.get(sheetId);
    if (inFlightForSheet) {
      inFlightForSheet.clear();
      inFlightRangeRequests.delete(sheetId);
    }
    if (!options?.preserveCells) {
      setCells(new Map());
    }
  }, [sheetId]);

  useEffect(() => {
    if (isGridLoading) return;
    let cancelled = false;
    const loadHighlights = async () => {
      try {
        const response = await SpreadsheetAPI.getHighlights(spreadsheetId, sheetId);
        if (cancelled) return;
        const cells = new Map<CellKey, string>();
        const rows: Record<number, string> = {};
        const cols: Record<number, string> = {};
        response.highlights.forEach((highlight) => {
          if (highlight.scope === 'CELL' && highlight.row_index != null && highlight.col_index != null) {
            cells.set(getCellKey(highlight.row_index, highlight.col_index), highlight.color);
          } else if (highlight.scope === 'ROW' && highlight.row_index != null) {
            rows[highlight.row_index] = highlight.color;
          } else if (highlight.scope === 'COLUMN' && highlight.col_index != null) {
            cols[highlight.col_index] = highlight.color;
          }
        });
        setCellHighlightsBySheet((prev) => ({ ...prev, [sheetId]: cells }));
        setRowHighlightsBySheet((prev) => ({ ...prev, [sheetId]: rows }));
        setColHighlightsBySheet((prev) => ({ ...prev, [sheetId]: cols }));
      } catch (error) {
        console.error('Failed to load highlights:', error);
      }
    };
    loadHighlights();
    return () => {
      cancelled = true;
    };
  }, [isGridLoading, spreadsheetId, sheetId]);

  useEffect(() => {
    if (isGridLoading) return;
    let cancelled = false;
    const loadCellFormats = async () => {
      try {
        const response = await SpreadsheetAPI.getCellFormats(spreadsheetId, sheetId);
        if (cancelled) return;
        const map = new Map<CellKey, CellFormat>();
        response.formats.forEach((f) => {
          const key = getCellKey(f.row_index, f.column_index);
          const nf = f.number_format;
          map.set(key, {
            bold: f.bold,
            italic: f.italic,
            strikethrough: f.strikethrough,
            textColor: f.text_color ?? null,
            fontFamily: f.font_family ?? null,
            fontSize: f.font_size ?? null,
            numberFormat:
              nf && nf.type
                ? {
                    type: nf.type as NumberFormatType,
                    currencyCode: nf.currency_code ?? null,
                    decimalPlaces: nf.decimal_places ?? null,
                  }
                : null,
          });
        });
        setCellFormatsBySheet((prev) => ({ ...prev, [sheetId]: map }));
      } catch (error) {
        console.error('Failed to load cell formats:', error);
      }
    };
    loadCellFormats();
    return () => {
      cancelled = true;
    };
  }, [isGridLoading, spreadsheetId, sheetId]);

  const refreshSheet = useCallback(() => {
    pendingCanonicalRefreshRef.current = true;

    const queuedOps = pendingOpsRef.current;
    const hadActiveEditor = editingCell !== null;
    if (hadActiveEditor) {
      // An editor opened before a remote structure change still owns the old
      // coordinate. Remove it before replacing the canonical cell map; outer
      // pointer/keyboard guards cannot stop the input's own Enter/blur commit.
      setEditingCell(null);
      setEditValue('');
      setMode('navigation');
      setNavigationLocked(false);
    }
    if (queuedOps.size > 0) {
      const emptyQueue = new Map<CellKey, PendingOperation>();
      pendingOpsRef.current = emptyQueue;
      setPendingOps(emptyQueue);
    }
    if (queuedOps.size > 0 || hadActiveEditor) {
      toast.error(
        'The sheet structure changed. Unsaved edits were discarded; please enter them again.',
        { duration: 5000 }
      );
    }

    // If a write is already in flight, let it finish before fetching canonical
    // coordinates. The shared backend sheet lock decides the final order.
    if (isGridLoading || isSavingRef.current) {
      pendingCanonicalRefreshRef.current = true;
      return;
    }
    pendingCanonicalRefreshRef.current = false;
    canonicalRefreshInFlightRef.current += 1;
    setCellCanvasLoading(true);
    // A canonical refresh follows coordinate-changing operations. Replace the
    // cell map instead of merging the response, otherwise cells at their old
    // coordinates remain visible alongside their shifted canonical values.
    resetSheetCaches();
    const range = computeVisibleRange();
    setVisibleRange({
      startRow: range.startRow,
      endRow: range.endRow,
      startCol: range.startColumn,
      endCol: range.endColumn,
    });
    const maybePromise = loadCellRange(
      range.startRow,
      range.endRow,
      range.startColumn,
      range.endColumn,
      true
    );
    const finishLoading = () => {
      canonicalRefreshInFlightRef.current = Math.max(
        0,
        canonicalRefreshInFlightRef.current - 1
      );
      if (canonicalRefreshInFlightRef.current === 0) {
        setCellCanvasLoading(false);
      }
    };
    if (maybePromise && typeof (maybePromise as Promise<void>).then === 'function') {
      void (maybePromise as Promise<void>).finally(finishLoading);
    } else {
      finishLoading();
    }

    // Also reload highlights and cell formats from the backend so that
    // decoration changes made by background jobs (e.g. pattern apply)
    // are immediately reflected in the UI without a full page reload.
    SpreadsheetAPI.getHighlights(spreadsheetId, sheetId)
      .then((response) => {
        const cells = new Map<CellKey, string>();
        const rows: Record<number, string> = {};
        const cols: Record<number, string> = {};
        response.highlights.forEach((highlight) => {
          if (highlight.scope === 'CELL' && highlight.row_index != null && highlight.col_index != null) {
            cells.set(getCellKey(highlight.row_index, highlight.col_index), highlight.color);
          } else if (highlight.scope === 'ROW' && highlight.row_index != null) {
            rows[highlight.row_index] = highlight.color;
          } else if (highlight.scope === 'COLUMN' && highlight.col_index != null) {
            cols[highlight.col_index] = highlight.color;
          }
        });
        setCellHighlightsBySheet((prev) => ({ ...prev, [sheetId]: cells }));
        setRowHighlightsBySheet((prev) => ({ ...prev, [sheetId]: rows }));
        setColHighlightsBySheet((prev) => ({ ...prev, [sheetId]: cols }));
      })
      .catch((error) => {
        console.error('Failed to load highlights on refresh:', error);
      });

    SpreadsheetAPI.getCellFormats(spreadsheetId, sheetId)
      .then((response) => {
        const map = new Map<CellKey, CellFormat>();
        response.formats.forEach((f) => {
          const key = getCellKey(f.row_index, f.column_index);
          const nf = f.number_format;
          map.set(key, {
            bold: f.bold,
            italic: f.italic,
            strikethrough: f.strikethrough,
            textColor: f.text_color ?? null,
            fontFamily: f.font_family ?? null,
            fontSize: f.font_size ?? null,
            numberFormat:
              nf && nf.type
                ? {
                    type: nf.type as NumberFormatType,
                    currencyCode: nf.currency_code ?? null,
                    decimalPlaces: nf.decimal_places ?? null,
                  }
                : null,
          });
        });
        setCellFormatsBySheet((prev) => ({ ...prev, [sheetId]: map }));
      })
      .catch((error) => {
        console.error('Failed to load cell formats on refresh:', error);
      });
  }, [
    isGridLoading,
    resetSheetCaches,
    computeVisibleRange,
    loadCellRange,
    spreadsheetId,
    sheetId,
    editingCell,
  ]);

  useEffect(() => {
    if (!isGridLoading && !isSaving && pendingCanonicalRefreshRef.current) {
      refreshSheet();
    }
  }, [isGridLoading, isSaving, refreshSheet]);

  const handleAddRows = useCallback(async () => {
    const rowsToAdd = parseInt(addRowsInputValue, 10);
    if (isNaN(rowsToAdd) || rowsToAdd <= 0) {
      toast.error('Please enter a valid number of rows');
      return;
    }

    const newRowCount = rowCount + rowsToAdd;
    if (newRowCount > MAX_ROWS) {
      toast.error(`Cannot add ${rowsToAdd} rows. Maximum is ${MAX_ROWS} rows total.`);
      return;
    }

    try {
      await resizeGrid(newRowCount, colCount, true);
      toast.success(`Added ${rowsToAdd} rows`);
      setShowAddRowsUI(false);
      setAddRowsInputValue('1000');
    } catch (error: any) {
      console.error('Failed to add rows:', error);
      toast.error('Failed to add rows');
    }
  }, [addRowsInputValue, rowCount, colCount, resizeGrid]);

  const handleInsertRow = useCallback(
    async (position: number, count: number = 1) => {
      if (rowCount + count > MAX_ROWS) {
        toast.error('Row limit reached');
        return;
      }

      try {
        const response = await SpreadsheetAPI.insertRows(spreadsheetId, sheetId, position, count);
        const nextRowCount = rowCount + count;
        setRowCount(nextRowCount);
        dimensionsCache.set(sheetId, { rowCount: nextRowCount, colCount });
        setUndoStack((prev) => [...prev, { type: 'structure', op: { id: response.operation_id, type: 'row_insert', count, position } }]);
        setRedoStack([]);
        resetSheetCaches();
        await loadCellRange(
          visibleRange.startRow,
          visibleRange.endRow,
          visibleRange.startCol,
          visibleRange.endCol,
          true
        );
      } catch (error: any) {
        console.error('Failed to insert row:', error);
        toast.error('Failed to insert row');
      }
    },
    [rowCount, colCount, spreadsheetId, sheetId, resetSheetCaches, loadCellRange, visibleRange]
  );

  const handleInsertColumn = useCallback(
    async (position: number, count: number = 1) => {
      if (colCount + count > MAX_COLUMNS) {
        toast.error('Column limit reached');
        return;
      }

      try {
        // Ensure backend has at least `position` columns (insert at position needs columns 0..position-1 to exist)
        const minColsNeeded = position;
        const targetCols = Math.max(colCount, minColsNeeded);
        await SpreadsheetAPI.resizeSheet(spreadsheetId, sheetId, rowCount, targetCols);
        if (targetCols > colCount) {
          setColCount(targetCols);
          dimensionsCache.set(sheetId, { rowCount, colCount: targetCols });
        }
        const response = await SpreadsheetAPI.insertColumns(spreadsheetId, sheetId, position, count);
        const nextColCount = colCount + count;
        setColCount(nextColCount);
        dimensionsCache.set(sheetId, { rowCount, colCount: nextColCount });
        setUndoStack((prev) => [...prev, { type: 'structure', op: { id: response.operation_id, type: 'col_insert', count, position } }]);
        setRedoStack([]);
        resetSheetCaches();
        await loadCellRange(
          visibleRange.startRow,
          visibleRange.endRow,
          visibleRange.startCol,
          visibleRange.endCol,
          true
        );
      } catch (error: any) {
        console.error('Failed to insert column:', error);
        toast.error('Failed to insert column');
      }
    },
    [rowCount, colCount, spreadsheetId, sheetId, resetSheetCaches, loadCellRange, visibleRange]
  );

  const openHeaderMenu = useCallback(
    (type: 'row' | 'col', index: number, clientX: number, clientY: number) => {
      setHeaderMenu({ type, index, x: clientX, y: clientY });
    },
    []
  );

  const handleDeleteRow = useCallback(
    async (position: number, count: number = 1) => {
      if (rowCount - count < 0) {
        toast.error('Row limit reached');
        return;
      }

      try {
        const response = await SpreadsheetAPI.deleteRows(spreadsheetId, sheetId, position, count);
        const nextRowCount = Math.max(0, rowCount - count);
        setRowCount(nextRowCount);
        dimensionsCache.set(sheetId, { rowCount: nextRowCount, colCount });
        setUndoStack((prev) => [...prev, { type: 'structure', op: { id: response.operation_id, type: 'row_delete', count, position } }]);
        setRedoStack([]);
        resetSheetCaches();
        await loadCellRange(
          Math.max(0, visibleRange.startRow - count),
          Math.max(0, visibleRange.endRow - count),
          visibleRange.startCol,
          visibleRange.endCol,
          true
        );
        toast.success(count === 1 ? 'Deleted row.' : 'Deleted.');
      } catch (error: any) {
        console.error('Failed to delete row:', error);
        const msg =
          error?.response?.data?.error ||
          error?.response?.data?.detail ||
          error?.message ||
          'Delete failed.';
        toast.error(msg);
      }
    },
    [rowCount, colCount, spreadsheetId, sheetId, resetSheetCaches, loadCellRange, visibleRange]
  );

  const handleDeleteColumn = useCallback(
    async (position: number, count: number = 1) => {
      if (colCount - count < 0) {
        toast.error('Column limit reached');
        return;
      }

      try {
        const response = await SpreadsheetAPI.deleteColumns(spreadsheetId, sheetId, position, count);
        const nextColCount = Math.max(0, colCount - count);
        setColCount(nextColCount);
        dimensionsCache.set(sheetId, { rowCount, colCount: nextColCount });
        setUndoStack((prev) => [...prev, { type: 'structure', op: { id: response.operation_id, type: 'col_delete', count, position } }]);
        setRedoStack([]);
        resetSheetCaches();
        await loadCellRange(
          visibleRange.startRow,
          visibleRange.endRow,
          Math.max(0, visibleRange.startCol - count),
          Math.max(0, visibleRange.endCol - count),
          true
        );
        toast.success(count === 1 ? 'Deleted column.' : 'Deleted.');
      } catch (error: any) {
        console.error('Failed to delete column:', error);
        const msg =
          error?.response?.data?.error ||
          error?.response?.data?.detail ||
          error?.message ||
          'Delete failed.';
        toast.error(msg);
      }
    },
    [rowCount, colCount, spreadsheetId, sheetId, resetSheetCaches, loadCellRange, visibleRange]
  );

  const selectRow = useCallback(
    (row: number) => {
      const endCol = Math.max(0, colCount - 1);
      const start = { row, col: 0 };
      setActiveCell(start);
      setAnchorCell(start);
      setFocusCell({ row, col: endCol });
      setEditingCell(null);
      setMode('navigation');
      setNavigationLocked(false);
    },
    [colCount]
  );

  const selectColumn = useCallback(
    (col: number) => {
      const endRow = Math.max(0, rowCount - 1);
      const start = { row: 0, col };
      setActiveCell(start);
      setAnchorCell(start);
      setFocusCell({ row: endRow, col });
      setEditingCell(null);
      setMode('navigation');
      setNavigationLocked(false);
    },
    [rowCount]
  );

  const handleRowHeaderClick = useCallback(
    (row: number) => {
      selectRow(row);
    },
    [selectRow]
  );

  const handleColumnHeaderClick = useCallback(
    (col: number) => {
      selectColumn(col);
    },
    [selectColumn]
  );

  const openSortMenu = useCallback((colIndex: number, e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    setSortMenu({ colIndex, x: e.clientX, y: e.clientY });
  }, []);

  const [isSorting, setIsSorting] = useState(false);
  const getActiveSortDirection = useCallback((colIndex: number): 'asc' | 'desc' | null => {
    const entry = sortHistoryRef.current.find((item) => item.column_position === colIndex);
    return entry?.direction ?? null;
  }, []);
  const hasColumnFilter = useCallback(
    (colIndex: number): boolean => Boolean((columnFilters[colIndex] ?? '').trim()),
    [columnFilters]
  );
  const getActiveFilterExpression = useCallback(
    (colIndex: number): string => columnFilters[colIndex] ?? '',
    [columnFilters]
  );
  const handleColumnFilterChange = useCallback((colIndex: number, value: string) => {
    setColumnFilters((prev) => {
      const next = { ...prev };
      if (!value.trim()) delete next[colIndex];
      else next[colIndex] = value;
      return next;
    });
    setColumnFilterOrder((prev) => {
      const withoutCurrent = prev.filter((col) => col !== colIndex);
      if (!value.trim()) return withoutCurrent;
      return [...withoutCurrent, colIndex];
    });
  }, []);

  const handleColumnSort = useCallback(
    async (sortCol: number, direction: 'asc' | 'desc') => {
      if (isSorting || rowCount <= 0 || colCount <= 0) return;
      setSortMenu(null);
      setIsSorting(true);
      try {
        const previousSortColumns = sortHistoryRef.current
          .filter((entry) => entry.column_position !== sortCol)
          .slice(0, 19);
        const result = await SpreadsheetAPI.sortSheet(spreadsheetId, sheetId, {
          column_position: sortCol,
          direction,
          has_header: true,
          previous_sort_columns: previousSortColumns,
        });
        sortHistoryRef.current = [{ column_position: sortCol, direction }, ...previousSortColumns];

        resetSheetCaches();
        await loadCellRange(visibleRange.startRow, visibleRange.endRow, visibleRange.startCol, visibleRange.endCol, true);

        setUndoStack((u) => [...u, { type: 'sort', entry: { previous_order: result.previous_order, new_order: result.new_order } }]);
        setRedoStack([]);
        toast.success(`Sorted by column ${columnIndexToLabel(sortCol)} ${direction === 'asc' ? 'A→Z' : 'Z→A'}`);
      } catch (error: any) {
        console.error('Sort failed:', error);
        toast.error(error?.response?.data?.detail ?? error?.message ?? 'Sort failed');
      } finally {
        setIsSorting(false);
      }
    },
    [
      isSorting,
      rowCount,
      colCount,
      spreadsheetId,
      sheetId,
      resetSheetCaches,
      loadCellRange,
      visibleRange,
    ]
  );

  const doesCellMatchFilter = useCallback(
    (row: number, col: number, filter: ParsedColumnFilter): boolean => {
      const key = getCellKey(row, col);
      const cellData = cells.get(key);
      const rawInput = (cellData?.rawInput ?? '').trim();
      const displayText =
        (cellData?.computedString != null ? String(cellData.computedString) : null) ??
        (cellData?.computedNumber != null ? String(cellData.computedNumber) : null) ??
        rawInput;
      const normalizedCellText = displayText.trim();

      const rawNumeric = Number(rawInput);
      const computedNumeric = Number(cellData?.computedNumber);
      const numericValue = Number.isFinite(computedNumeric)
        ? computedNumeric
        : Number.isFinite(rawNumeric)
        ? rawNumeric
        : null;

      if (filter.operator === '=') {
        if (filter.numericValue != null && numericValue != null) {
          return numericValue === filter.numericValue;
        }
        return normalizedCellText.toLowerCase() === filter.rawValue.toLowerCase();
      }

      if (numericValue == null || filter.numericValue == null) return false;
      if (filter.operator === '>=') return numericValue >= filter.numericValue;
      if (filter.operator === '>') return numericValue > filter.numericValue;
      if (filter.operator === '<=') return numericValue <= filter.numericValue;
      return numericValue < filter.numericValue;
    },
    [cells]
  );

  const performUndoStructure = useCallback(
    async (op: StructureOp) => {
      if (isReverting) return;
      setIsReverting(true);
      try {
        await SpreadsheetAPI.revertStructureOperation(spreadsheetId, sheetId, op.id);
        if (op.type === 'row_insert') {
          const nextRowCount = Math.max(0, rowCount - op.count);
          setRowCount(nextRowCount);
          dimensionsCache.set(sheetId, { rowCount: nextRowCount, colCount });
        } else if (op.type === 'row_delete') {
          const nextRowCount = rowCount + op.count;
          setRowCount(nextRowCount);
          dimensionsCache.set(sheetId, { rowCount: nextRowCount, colCount });
        } else if (op.type === 'col_insert') {
          const nextColCount = Math.max(0, colCount - op.count);
          setColCount(nextColCount);
          dimensionsCache.set(sheetId, { rowCount, colCount: nextColCount });
        } else if (op.type === 'col_delete') {
          const nextColCount = colCount + op.count;
          setColCount(nextColCount);
          dimensionsCache.set(sheetId, { rowCount, colCount: nextColCount });
        }
        resetSheetCaches();
        await loadCellRange(
          visibleRange.startRow,
          visibleRange.endRow,
          visibleRange.startCol,
          visibleRange.endCol,
          true
        );
      } catch (error: any) {
        console.error('Failed to revert operation:', error);
        toast.error('Failed to undo');
        throw error;
      } finally {
        setIsReverting(false);
      }
    },
    [isReverting, spreadsheetId, sheetId, rowCount, colCount, resetSheetCaches, loadCellRange, visibleRange]
  );

  const applyUndoColor = useCallback((entry: ColorHistoryEntry) => {
    entry.ops.forEach((op) => {
        if (op.scope === 'ROW' && op.row != null) {
          setRowHighlightsBySheet((p) => {
            const next = { ...(p[sheetId] ?? {}) };
            if (op.prevColor != null) next[op.row!] = op.prevColor;
            else delete next[op.row!];
            return { ...p, [sheetId]: next };
          });
          enqueueHighlightOps([
            {
              scope: 'ROW',
              row: op.row,
              color: op.prevColor,
              operation: op.prevColor != null ? 'SET' : 'CLEAR',
            },
          ]);
        } else if (op.scope === 'COLUMN' && op.col != null) {
          setColHighlightsBySheet((p) => {
            const next = { ...(p[sheetId] ?? {}) };
            if (op.prevColor != null) next[op.col!] = op.prevColor;
            else delete next[op.col!];
            return { ...p, [sheetId]: next };
          });
          enqueueHighlightOps([
            {
              scope: 'COLUMN',
              col: op.col,
              color: op.prevColor,
              operation: op.prevColor != null ? 'SET' : 'CLEAR',
            },
          ]);
        } else if (op.scope === 'CELL' && op.row != null && op.col != null) {
          setCellHighlightsBySheet((p) => {
            const next = new Map(p[sheetId] ?? new Map());
            const key = getCellKey(op.row!, op.col!);
            if (op.prevColor != null) next.set(key, op.prevColor);
            else next.delete(key);
            return { ...p, [sheetId]: next };
          });
          enqueueHighlightOps([
            {
              scope: 'CELL',
              row: op.row,
              col: op.col,
              color: op.prevColor,
              operation: op.prevColor != null ? 'SET' : 'CLEAR',
            },
          ]);
        }
      });
  }, [sheetId, enqueueHighlightOps]);

  const canUndo = undoStack.length > 0;

  const getCellRawInput = useCallback(
    (row: number, col: number): string => {
      const key = getCellKey(row, col);
      const cellData = cells.get(key);
      return cellData?.rawInput ?? '';
    },
    [cells]
  );

  const applyUndoSort = useCallback(
    async (entry: SortHistoryEntry) => {
      await SpreadsheetAPI.reorderRows(spreadsheetId, sheetId, { order: entry.previous_order });
      resetSheetCaches();
      await loadCellRange(visibleRange.startRow, visibleRange.endRow, visibleRange.startCol, visibleRange.endCol, true);
    },
    [spreadsheetId, sheetId, resetSheetCaches, loadCellRange, visibleRange]
  );

  const applyRedoSort = useCallback(
    async (entry: SortHistoryEntry) => {
      await SpreadsheetAPI.reorderRows(spreadsheetId, sheetId, { order: entry.new_order });
      resetSheetCaches();
      await loadCellRange(visibleRange.startRow, visibleRange.endRow, visibleRange.startCol, visibleRange.endCol, true);
    },
    [spreadsheetId, sheetId, resetSheetCaches, loadCellRange, visibleRange]
  );

  const recordFormulaCommit = useCallback(
    (row: number, col: number, value: string) => {
      const trimmedValue = value.trim();
      if (!trimmedValue.startsWith('=')) return;
      // Single source of truth for formula recording; formula bar routes here too.
      onFormulaCommit?.({ row, col, formula: trimmedValue });
    },
    [onFormulaCommit]
  );

  const submitFormulaBarValue = useCallback(
    async (targetKey: CellKey | null, value: string) => {
      if (!targetKey) return;
      const { row, col } = parseCellKey(targetKey);
      const prevValue = getCellRawInput(row, col);
      setIsSaving(true);
      setSaveError(null);
      try {
        const normalized = normalizeCommittedValue(value);
        const operation: PendingOperation = {
          operation: normalized.rawInput === '' ? 'clear' : 'set',
          row,
          column: col,
          ...(normalized.rawInput !== '' && { raw_input: normalized.rawInput }),
        };
        if (normalized.rawInput !== '') {
          if (normalized.valueType === 'number') {
            operation.value_type = 'number';
            operation.number_value = normalized.numberValue ?? null;
            operation.string_value = null;
          } else if (normalized.valueType === 'string') {
            operation.value_type = 'string';
            operation.string_value = normalized.stringValue ?? '';
            operation.number_value = null;
          }
        }

        const response = await SpreadsheetAPI.batchUpdateCells(
          spreadsheetId,
          sheetId,
          [operation],
          true,
          collabClientId ? { clientId: collabClientId } : undefined
        );
        applyCellsFromResponse(response.cells);
        setPendingOps((prev) => {
          const next = new Map(prev);
          next.delete(targetKey);
          return next;
        });
        if (!response.cells || response.cells.length === 0) {
          await loadCellRange(row, row, col, col, true);
        }
        recordFormulaCommit(row, col, value);
        if (onHeaderRenameCommit && row === 0 && prevValue !== value) {
          onHeaderRenameCommit({
            rowIndex: row,
            colIndex: col,
            newValue: value,
            oldValue: prevValue,
          });
        }
      } catch (error: any) {
        console.error('Failed to save formula bar edit:', error);
        const errorMessage =
          error?.response?.data?.error ||
          error?.response?.data?.detail ||
          error?.message ||
          'Failed to save formula';
        setSaveError(errorMessage);
        toast.error(errorMessage, { duration: 3000 });
      } finally {
        setIsSaving(false);
      }
    },
    [
      applyCellsFromResponse,
      loadCellRange,
      sheetId,
      spreadsheetId,
      recordFormulaCommit,
      getCellRawInput,
      onHeaderRenameCommit,
      collabClientId,
    ]
  );

  // Load initial visible range on mount and when sheetId changes
  useEffect(() => {
    setCellCanvasLoading(true);
    if (isGridLoading) return;
    let cancelled = false;

    // Reset scroll position to top when switching sheets (only on sheetId change)
    if (gridRef.current) {
      gridRef.current.scrollTop = 0;
      gridRef.current.scrollLeft = 0;
    }
    
    // Small delay to ensure DOM is ready and table height is calculated
    const timer = setTimeout(() => {
      if (cancelled) return;
      const finishLoading = () => {
        if (!cancelled) setCellCanvasLoading(false);
      };
      if (!gridRef.current) {
        finishLoading();
        return;
      }
      // Ensure scroll position is still at top (prevent any auto-scroll)
      if (gridRef.current.scrollTop !== 0) {
        gridRef.current.scrollTop = 0;
      }
      const range = computeVisibleRange();
      setVisibleRange({
        startRow: range.startRow,
        endRow: range.endRow,
        startCol: range.startColumn,
        endCol: range.endColumn,
      });
      const maybePromise = loadCellRange(range.startRow, range.endRow, range.startColumn, range.endColumn);
      if (maybePromise && typeof (maybePromise as Promise<void>).then === 'function') {
        void (maybePromise as Promise<void>).finally(finishLoading);
      } else {
        finishLoading();
      }
    }, 100);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [isGridLoading, sheetId, spreadsheetId, computeVisibleRange, loadCellRange]);

  // Handle scroll to load more cells (no auto-expand). We schedule work in
  // requestAnimationFrame so we compute the viewport and trigger range loads
  // at most once per frame even if the browser fires many scroll events.
  const handleScroll = useCallback(() => {
    if (isGridLoading) return;
    if (!gridRef.current) return;

    if (scrollRafIdRef.current != null) {
      cancelAnimationFrame(scrollRafIdRef.current);
    }

    scrollRafIdRef.current = requestAnimationFrame(() => {
      scrollRafIdRef.current = null;

      const range = computeVisibleRange();
      loadCellRange(range.startRow, range.endRow, range.startColumn, range.endColumn, false, {
        includeSheetDimensions: false,
      });
      setVisibleRange({
        startRow: range.startRow,
        endRow: range.endRow,
        startCol: range.startColumn,
        endCol: range.endColumn,
      });

      // Show "Add rows" UI when near bottom of grid
      if (gridRef.current) {
        const container = gridRef.current;
        const scrollTop = container.scrollTop;
        const scrollHeight = container.scrollHeight;
        const clientHeight = container.clientHeight;
        const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
        const isNearBottom = distanceFromBottom < ADD_ROWS_TRIGGER_DISTANCE;
        const isAtMaxRows = rowCount >= MAX_ROWS;

        // Show UI if near bottom and not at max
        if (isNearBottom && !isAtMaxRows && range.endRow >= rowCount - 10) {
          setShowAddRowsUI(true);
        } else {
          setShowAddRowsUI(false);
        }
      }
    });
  }, [isGridLoading, computeVisibleRange, loadCellRange, rowCount]);

  // Recompute visible range if dimensions change (e.g., after expansion)
  // But preserve scroll position - don't reset it
  useEffect(() => {
    if (!gridRef.current) return;
    // Only update visibleRange based on current scroll position, don't change scroll
    const range = computeVisibleRange();
    setVisibleRange({
      startRow: range.startRow,
      endRow: range.endRow,
      startCol: range.startColumn,
      endCol: range.endColumn,
    });
  }, [rowCount, colCount, computeVisibleRange]);

  // ResizeObserver: recompute visible range when container is resized (e.g. panel toggle)
  useEffect(() => {
    const el = gridRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry || !gridRef.current) return;
      const { width, height } = entry.contentRect;
      if (width <= 0 || height <= 0) return;
      const range = computeVisibleRange();
      setVisibleRange({
        startRow: range.startRow,
        endRow: range.endRow,
        startCol: range.startColumn,
        endCol: range.endColumn,
      });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [computeVisibleRange]);

  // Debounced batch save
  const flushPendingOps = useCallback(async () => {
    if (
      pendingOps.size === 0 ||
      isSaving ||
      pendingCanonicalRefreshRef.current ||
      canonicalRefreshInFlightRef.current > 0
    ) return;

    setIsSaving(true);
    setSaveError(null);

    // Merge operations by cell (last write wins)
    const submittedOps = new Map(pendingOps);
    const operations: PendingOperation[] = Array.from(submittedOps.values());
    let minRow = Number.POSITIVE_INFINITY;
    let maxRow = Number.NEGATIVE_INFINITY;
    let minCol = Number.POSITIVE_INFINITY;
    let maxCol = Number.NEGATIVE_INFINITY;

    operations.forEach((op) => {
      minRow = Math.min(minRow, op.row);
      maxRow = Math.max(maxRow, op.row);
      minCol = Math.min(minCol, op.column);
      maxCol = Math.max(maxCol, op.column);
    });

    try {
      const response = await SpreadsheetAPI.batchUpdateCells(
        spreadsheetId,
        sheetId,
        operations,
        true,
        collabClientId ? { clientId: collabClientId } : undefined
      );
      // Only remove the exact operations included in this request. Edits made
      // while the request was in flight have newer operation objects and must
      // remain queued for the next LWW batch.
      setPendingOps((current) => {
        const next = new Map(current);
        submittedOps.forEach((submitted, key) => {
          if (next.get(key) === submitted) next.delete(key);
        });
        return next;
      });
      applyCellsFromResponse(response.cells);
      if (Number.isFinite(minRow)) {
        await loadCellRange(minRow, maxRow, minCol, maxCol, true);
      }
    } catch (error: any) {
      console.error('Failed to save cells:', error);
      const errorMessage =
        error?.response?.data?.error ||
        error?.response?.data?.detail ||
        error?.message ||
        'Failed to save cells';
      setSaveError(errorMessage);
      toast.error(errorMessage, { duration: 3000 });
      // Keep queue on failure so user can retry
    } finally {
      setIsSaving(false);
    }
  }, [pendingOps, spreadsheetId, sheetId, isSaving, loadCellRange, applyCellsFromResponse, collabClientId]);

  // Debounce flush
  useEffect(() => {
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }

    if (pendingOps.size > 0) {
      debounceTimerRef.current = setTimeout(() => {
        flushPendingOps();
      }, DEBOUNCE_MS);
    }

    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
    };
  }, [pendingOps, flushPendingOps]);

  const getCellNumericValue = useCallback(
    (row: number, col: number): number | null => {
      const key = getCellKey(row, col);
      const cellData = cells.get(key);
      if (!cellData) return 0;
      const rawInput = cellData.rawInput ?? '';
      if (rawInput.trim() === '') return 0;
      if (cellData.computedType === 'number' && cellData.computedNumber != null) {
        return Number(cellData.computedNumber);
      }
      if (!rawInput.startsWith('=')) {
        const parsed = Number(rawInput);
        return Number.isFinite(parsed) ? parsed : 0;
      }
      return 0;
    },
    [cells]
  );

  const getHighlightColor = useCallback(
    (row: number, col: number) => {
      const key = getCellKey(row, col);
      return cellHighlights.get(key) ?? rowHighlights[row] ?? colHighlights[col] ?? null;
    },
    [cellHighlights, rowHighlights, colHighlights]
  );

  const getCellFormat = useCallback(
    (row: number, col: number): CellFormat => {
      const key = getCellKey(row, col);
      return cellFormats.get(key) ?? DEFAULT_CELL_FORMAT;
    },
    [cellFormats]
  );

  const normalizeHeader = useCallback((value: string) => value.trim().replace(/\s+/g, ' '), []);

  const resolveHeaderColumnByName = useCallback(
    (headerValue: string | null | undefined) => {
      if (!headerValue) return null;
      const target = normalizeHeader(headerValue).toLowerCase();
      if (!target) return null;
      for (let col = 0; col < colCount; col += 1) {
        const headerText = normalizeHeader(getCellRawInput(0, col)).toLowerCase();
        if (headerText && headerText === target) {
          return col;
        }
      }
      return null;
    },
    [colCount, getCellRawInput, normalizeHeader]
  );

  const buildHighlightPayload = useCallback(
    (color: string, scope: ApplyHighlightParams['scope'], range: SelectionRange) => {
      const headerRowIndex = 1;
      const startColHeader = normalizeHeader(getCellRawInput(0, range.startCol));
      const endColHeader = normalizeHeader(getCellRawInput(0, range.endCol));
      const isHeaderRow = range.startRow === 0 && range.endRow === 0;

      if (scope === 'COLUMN') {
        return {
          color,
          scope,
          header_row_index: headerRowIndex,
          target: {
            by_header: startColHeader || null,
            fallback: { col_index: range.startCol + 1 },
          },
        } as ApplyHighlightParams;
      }

      if (scope === 'ROW') {
        return {
          color,
          scope,
          header_row_index: headerRowIndex,
          target: {
            fallback: { row_index: range.startRow + 1 },
          },
        } as ApplyHighlightParams;
      }

      if (scope === 'CELL') {
        return {
          color,
          scope,
          header_row_index: headerRowIndex,
          target: {
            by_header: isHeaderRow ? startColHeader || null : undefined,
            fallback: { row_index: range.startRow + 1, col_index: range.startCol + 1 },
          },
        } as ApplyHighlightParams;
      }

      return {
        color,
        scope: 'RANGE',
        header_row_index: headerRowIndex,
        target: {
          by_headers: {
            start: isHeaderRow ? startColHeader || null : undefined,
            end: isHeaderRow ? endColHeader || null : undefined,
          },
          fallback: {
            start_row: range.startRow + 1,
            end_row: range.endRow + 1,
            start_col: range.startCol + 1,
            end_col: range.endCol + 1,
          },
        },
      } as ApplyHighlightParams;
    },
    [getCellRawInput, normalizeHeader]
  );

  const evaluateFormulaLocally = useCallback(
    (formula: string): number | null => {
      if (!formula.startsWith('=')) return null;
      const expr = formula.slice(1);
      const tokens: Array<{ type: 'number' | 'op' | 'ref' | 'lparen' | 'rparen'; value: string }> = [];
      let idx = 0;
      while (idx < expr.length) {
        const char = expr[idx];
        if (char === ' ' || char === '\t') {
          idx += 1;
          continue;
        }
        if ('+-*/()'.includes(char)) {
          tokens.push({
            type: char === '(' ? 'lparen' : char === ')' ? 'rparen' : 'op',
            value: char,
          });
          idx += 1;
          continue;
        }
        if ((char >= '0' && char <= '9') || char === '.') {
          let start = idx;
          idx += 1;
          while (idx < expr.length && /[0-9.]/.test(expr[idx])) idx += 1;
          tokens.push({ type: 'number', value: expr.slice(start, idx) });
          continue;
        }
        if (/[A-Za-z]/.test(char)) {
          let start = idx;
          idx += 1;
          while (idx < expr.length && /[A-Za-z]/.test(expr[idx])) idx += 1;
          const colLabel = expr.slice(start, idx).toUpperCase();
          let rowStart = idx;
          while (idx < expr.length && /[0-9]/.test(expr[idx])) idx += 1;
          const rowLabel = expr.slice(rowStart, idx);
          if (rowLabel) {
            tokens.push({ type: 'ref', value: `${colLabel}${rowLabel}` });
            continue;
          }
          return null;
        }
        return null;
      }

      let cursor = 0;
      const peek = () => tokens[cursor];
      const consume = () => tokens[cursor++];

      const parseExpression = (): number | null => {
        let value = parseTerm();
        while (value !== null && peek() && peek().type === 'op' && '+-'.includes(peek().value)) {
          const op = consume().value;
          const right = parseTerm();
          if (right === null) return null;
          value = op === '+' ? value + right : value - right;
        }
        return value;
      };

      const parseTerm = (): number | null => {
        let value = parseFactor();
        while (value !== null && peek() && peek().type === 'op' && '*/'.includes(peek().value)) {
          const op = consume().value;
          const right = parseFactor();
          if (right === null) return null;
          if (op === '*') {
            value = value * right;
          } else {
            if (right === 0) return null;
            value = value / right;
          }
        }
        return value;
      };

      const parseFactor = (): number | null => {
        const token = peek();
        if (!token) return null;
        if (token.type === 'op' && '+-'.includes(token.value)) {
          const op = consume().value;
          const next = parseFactor();
          if (next === null) return null;
          return op === '+' ? next : -next;
        }
        if (token.type === 'number') {
          consume();
          const parsed = Number(token.value);
          return Number.isFinite(parsed) ? parsed : null;
        }
        if (token.type === 'ref') {
          consume();
          const match = token.value.match(/^([A-Z]+)(\d+)$/);
          if (!match) return null;
          const colIndex = colLabelToIndex(match[1]);
          const rowIndex = Number(match[2]) - 1;
          if (rowIndex < 0 || colIndex < 0) return null;
          return getCellNumericValue(rowIndex, colIndex);
        }
        if (token.type === 'lparen') {
          consume();
          const inner = parseExpression();
          if (!peek() || peek().type !== 'rparen') return null;
          consume();
          return inner;
        }
        return null;
      };

      const result = parseExpression();
      if (result === null || peek()) return null;
      return Number.isFinite(result) ? result : null;
    },
    [getCellNumericValue]
  );

  const formatComputedNumber = useCallback((value: number | string): string => {
    const rawValue = String(value);
    if (rawValue.includes('e') || rawValue.includes('E')) {
      return rawValue;
    }
    const parts = rawValue.split('.');
    if (parts.length === 1) {
      return rawValue;
    }
    const [integerPart, fractionalPart] = parts;
    const limitedFraction = fractionalPart.slice(0, 10);
    const limited = `${integerPart}.${limitedFraction}`;
    return limited.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
  }, []);

  const formatNumericForDisplay = useCallback(
    (value: number, numberFormat: NumberFormat | null): string => {
      const nf = numberFormat ?? DEFAULT_NUMBER_FORMAT;
      const dp = nf.decimalPlaces ?? 2;
      const roundForDisplay = (v: number, places: number, trimZeros: boolean): string => {
        if (places <= 0) return String(Math.round(v));
        const mult = 10 ** places;
        const rounded = Math.round(v * mult) / mult;
        const s = rounded.toFixed(places);
        return trimZeros ? s.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '') : s;
      };
      if (nf.type === 'PERCENT') {
        return `${roundForDisplay(value * 100, dp, false)}%`;
      }
      if (nf.type === 'CURRENCY') {
        const sym = CURRENCIES.find((c) => c.code === (nf.currencyCode || 'USD'))?.symbol ?? '$';
        return `${sym}${roundForDisplay(value, dp, false)}`;
      }
      if (nf.type === 'NUMBER') {
        return roundForDisplay(value, dp, true);
      }
      return formatComputedNumber(value);
    },
    [formatComputedNumber]
  );

  const getCellDisplayValue = useCallback(
    (row: number, col: number): string => {
      const key = getCellKey(row, col);
      const cellData = cells.get(key);
      if (!cellData) return '';
      const rawInput = cellData.rawInput || '';
      // Sparkline cells render as a chart (SparklineCell wins in the render path),
      // so this value is only used for copy/paste and CSV export. Return the
      // formula itself — never the backend JSON payload, and not a blank.
      if (isSparklineRawInput(rawInput)) return rawInput.trim();
      const numberFormat = getCellFormat(row, col).numberFormat;
      const formatNum = (v: number | string) =>
        formatNumericForDisplay(Number(v), numberFormat);
      if (!rawInput.startsWith('=')) {
        const trimmed = rawInput.trim();
        if (/^[+-]?(\d+(\.\d*)?|\.\d+)$/.test(trimmed)) {
          return formatNum(trimmed);
        }
        return rawInput;
      }
      if (cellData.errorCode) {
        if (cellData.errorCode === '#VALUE!') {
          const localValue = evaluateFormulaLocally(rawInput);
          if (localValue != null) return formatNum(localValue);
        }
        return cellData.errorCode;
      }
      if (cellData.computedType === 'number' && cellData.computedNumber != null) {
        return formatNum(cellData.computedNumber);
      }
      if (cellData.computedType === 'boolean') {
        if (cellData.computedString != null) return cellData.computedString;
        return '';
      }
      if (cellData.computedType === 'string' && cellData.computedString != null) {
        return cellData.computedString;
      }
      if (cellData.computedType == null || cellData.computedNumber == null) {
        const localValue = evaluateFormulaLocally(rawInput);
        if (localValue != null) return formatNum(localValue);
      }
      if (cellData.computedType === 'empty') return '';
      return '';
    },
    [cells, evaluateFormulaLocally, formatNumericForDisplay, getCellFormat]
  );

  const getCellSparkline = useCallback(
    (row: number, col: number): SparklinePayload | null => {
      const cellData = cells.get(getCellKey(row, col));
      if (!cellData || !isSparklineRawInput(cellData.rawInput)) return null;
      return parseSparklinePayload(cellData.computedString);
    },
    [cells],
  );

  const getFormulaBarDisplayValue = useCallback((): string => {
    if (!activeCell) return '';
    const rawInput = getCellRawInput(activeCell.row, activeCell.col);
    if (rawInput) return rawInput;
    return getCellDisplayValue(activeCell.row, activeCell.col);
  }, [activeCell, getCellDisplayValue, getCellRawInput]);

  useEffect(() => {
    if (isFormulaBarEditing) return;
    if (!activeCell) {
      setFormulaBarValue('');
      setFormulaBarTarget(null);
      return;
    }
    const key = getCellKey(activeCell.row, activeCell.col);
    setFormulaBarTarget(key);
    setFormulaBarValue(getFormulaBarDisplayValue());
  }, [activeCell, getFormulaBarDisplayValue, isFormulaBarEditing]);

  const getUsedRangeFromCells = useCallback((): SelectionRange | null => {
    let minRow = Number.POSITIVE_INFINITY;
    let maxRow = Number.NEGATIVE_INFINITY;
    let minCol = Number.POSITIVE_INFINITY;
    let maxCol = Number.NEGATIVE_INFINITY;

    cells.forEach((cell, key) => {
      if (!cell.rawInput) return;
      const { row, col } = parseCellKey(key);
      minRow = Math.min(minRow, row);
      maxRow = Math.max(maxRow, row);
      minCol = Math.min(minCol, col);
      maxCol = Math.max(maxCol, col);
    });

    if (!Number.isFinite(minRow)) {
      return null;
    }

    return {
      startRow: minRow,
      endRow: maxRow,
      startCol: minCol,
      endCol: maxCol,
    };
  }, [cells]);

  const buildMatrixFromRange = useCallback(
    (range: SelectionRange): string[][] => {
      const matrix: string[][] = [];
      for (let r = range.startRow; r <= range.endRow; r += 1) {
        const row: string[] = [];
        for (let c = range.startCol; c <= range.endCol; c += 1) {
          row.push(getCellDisplayValue(r, c));
        }
        matrix.push(row);
      }
      return matrix;
    },
    [getCellDisplayValue]
  );

  const normalizeCommittedValue = useCallback((input: string) => {
    const rawInput = input.trim();
    if (rawInput.startsWith('=')) {
      return {
        valueType: 'formula' as const,
        rawInput,
      };
    }

    const cleaned = rawInput.replace(/,/g, '');
    const currencySymbols = ['$', '¥', '€', '£'];
    let sign = '';
    let working = cleaned;

    if (working.startsWith('-')) {
      sign = '-';
      working = working.slice(1);
    }

    if (currencySymbols.includes(working[0])) {
      working = working.slice(1);
      if (working.startsWith('-')) {
        sign = '-';
        working = working.slice(1);
      }
    }

    const normalized = `${sign}${working}`;
    if (/^-?\d+(\.\d+)?$/.test(normalized)) {
      return {
        valueType: 'number' as const,
        rawInput,
        numberValue: Number.parseFloat(normalized),
      };
    }

    return {
      valueType: 'string' as const,
      rawInput,
      stringValue: rawInput,
    };
  }, []);

  // Set cell value (optimistic update + enqueue for save)
  const setCellValue = useCallback(
    (row: number, col: number, value: string) => {
      const key = getCellKey(row, col);
      const normalized = normalizeCommittedValue(value);
      const trimmedValue = normalized.rawInput;

      // Update local state immediately (optimistic UI)
      setCells((prev) => {
        const next = new Map(prev);
        const cellData: CellData = {
          rawInput: trimmedValue,
          computedType: null,
          computedNumber: null,
          computedString: null,
          errorCode: null,
          isLoaded: true,
        };
        next.set(key, cellData);

        // Also update cache
        const cachedCells = cellCache.get(sheetId) || new Map();
        cachedCells.set(key, cellData);
        cellCache.set(sheetId, cachedCells);

        return next;
      });

      // Enqueue operation for saving
      setPendingOps((prev) => {
        const next = new Map(prev);
        const operation: PendingOperation = {
          row, // 0-based
          column: col, // 0-based
          operation: trimmedValue === '' ? 'clear' : 'set',
          ...(trimmedValue !== '' && { raw_input: trimmedValue }),
        };
        if (trimmedValue !== '') {
          if (normalized.valueType === 'number') {
            operation.value_type = 'number';
            operation.number_value = normalized.numberValue ?? null;
            operation.string_value = null;
          } else if (normalized.valueType === 'string') {
            operation.value_type = 'string';
            operation.string_value = normalized.stringValue ?? '';
            operation.number_value = null;
          }
        }
        next.set(key, operation); // Last write wins
        return next;
      });
    },
    [sheetId, normalizeCommittedValue]
  );

  const applyUndoCell = useCallback((entry: HistoryEntry) => {
    entry.changes.forEach((change) => {
      setCellValue(change.row, change.col, change.prevValue);
    });
  }, [setCellValue]);

  const applyUndoFormat = useCallback(
    async (entry: FormatStyleEntry) => {
      entry.ops.forEach((op) => {
        setCellFormatsBySheet((prev) => {
          const current = prev[sheetId] ?? new Map();
          const next = new Map(current);
          next.set(getCellKey(op.row, op.col), op.prev);
          return { ...prev, [sheetId]: next };
        });
      });
      const apiOps = entry.ops.map((op) => ({
        row: op.row,
        column: op.col,
        bold: op.prev.bold,
        italic: op.prev.italic,
        strikethrough: op.prev.strikethrough,
        text_color: op.prev.textColor,
        font_family: op.prev.fontFamily,
        font_size: op.prev.fontSize,
        number_format: op.prev.numberFormat
          ? { type: op.prev.numberFormat.type, currency_code: op.prev.numberFormat.currencyCode ?? undefined, decimal_places: op.prev.numberFormat.decimalPlaces ?? undefined }
          : null,
      }));
      try {
        await SpreadsheetAPI.batchUpdateCellFormats(spreadsheetId, sheetId, apiOps);
      } catch (e) {
        console.error('Failed to persist format undo:', e);
      }
    },
    [sheetId, spreadsheetId]
  );

  const applyRedoFormat = useCallback(
    async (entry: FormatStyleEntry) => {
      entry.ops.forEach((op) => {
        setCellFormatsBySheet((prev) => {
          const current = prev[sheetId] ?? new Map();
          const next = new Map(current);
          next.set(getCellKey(op.row, op.col), op.next);
          return { ...prev, [sheetId]: next };
        });
      });
      const apiOps = entry.ops.map((op) => ({
        row: op.row,
        column: op.col,
        bold: op.next.bold,
        italic: op.next.italic,
        strikethrough: op.next.strikethrough,
        text_color: op.next.textColor,
        font_family: op.next.fontFamily,
        font_size: op.next.fontSize,
        number_format: op.next.numberFormat
          ? { type: op.next.numberFormat.type, currency_code: op.next.numberFormat.currencyCode ?? undefined, decimal_places: op.next.numberFormat.decimalPlaces ?? undefined }
          : null,
      }));
      try {
        await SpreadsheetAPI.batchUpdateCellFormats(spreadsheetId, sheetId, apiOps);
      } catch (e) {
        console.error('Failed to persist format redo:', e);
      }
    },
    [sheetId, spreadsheetId]
  );

  const handleUnifiedUndo = useCallback(async () => {
    if (undoStack.length === 0) return;
    const last = undoStack[undoStack.length - 1];
    setUndoStack((prev) => prev.slice(0, -1));
    if (last.type === 'cell') {
      setRedoStack((prev) => [...prev, last]);
      applyUndoCell(last.entry);
      toast.success('Undo complete');
    } else if (last.type === 'color') {
      const rowH = rowHighlightsBySheet[sheetId] ?? {};
      const colH = colHighlightsBySheet[sheetId] ?? {};
      const cellH = cellHighlightsBySheet[sheetId] ?? new Map<string, string>();
      const redoOps: ColorRedoEntry['ops'] = last.entry.ops.map((op) => {
        let nextColor: string | undefined;
        if (op.scope === 'ROW' && op.row != null) nextColor = rowH[op.row];
        else if (op.scope === 'COLUMN' && op.col != null) nextColor = colH[op.col];
        else if (op.scope === 'CELL' && op.row != null && op.col != null) nextColor = cellH.get(getCellKey(op.row, op.col));
        return { ...op, nextColor };
      });
      setRedoStack((prev) => [...prev, { type: 'color', entry: { ops: redoOps } }]);
      applyUndoColor(last.entry);
      toast.success('Undo complete');
    } else if (last.type === 'format') {
      setRedoStack((prev) => [...prev, last]);
      await applyUndoFormat(last.entry);
      toast.success('Undo complete');
    } else if (last.type === 'structure') {
      setRedoStack((prev) => [...prev, { type: 'structure', entry: { type: last.op.type, count: last.op.count, position: last.op.position } }]);
      await performUndoStructure(last.op);
      toast.success('Undo complete');
    } else if (last.type === 'sort') {
      setRedoStack((prev) => [...prev, last]);
      await applyUndoSort(last.entry);
      toast.success('Undo complete');
    }
  }, [
    undoStack,
    rowHighlightsBySheet,
    colHighlightsBySheet,
    cellHighlightsBySheet,
    sheetId,
    applyUndoCell,
    applyUndoColor,
    applyUndoFormat,
    performUndoStructure,
    applyUndoSort,
  ]);

  const applyRedoCell = useCallback((entry: HistoryEntry) => {
    entry.changes.forEach((change) => {
      setCellValue(change.row, change.col, change.nextValue);
    });
  }, [setCellValue]);

  const applyRedoColor = useCallback((entry: ColorRedoEntry) => {
    entry.ops.forEach((op) => {
        if (op.scope === 'ROW' && op.row != null) {
          const r = op.row;
          setRowHighlightsBySheet((p) => {
            const next = { ...(p[sheetId] ?? {}) };
            if (op.nextColor != null) next[r] = op.nextColor;
            else delete next[r];
            return { ...p, [sheetId]: next };
          });
          enqueueHighlightOps([
            { scope: 'ROW', row: op.row, color: op.nextColor ?? undefined, operation: op.nextColor != null ? 'SET' : 'CLEAR' },
          ]);
        } else if (op.scope === 'COLUMN' && op.col != null) {
          const c = op.col;
          setColHighlightsBySheet((p) => {
            const next = { ...(p[sheetId] ?? {}) };
            if (op.nextColor != null) next[c] = op.nextColor;
            else delete next[c];
            return { ...p, [sheetId]: next };
          });
          enqueueHighlightOps([
            { scope: 'COLUMN', col: op.col, color: op.nextColor ?? undefined, operation: op.nextColor != null ? 'SET' : 'CLEAR' },
          ]);
        } else if (op.scope === 'CELL' && op.row != null && op.col != null) {
          setCellHighlightsBySheet((p) => {
            const next = new Map(p[sheetId] ?? new Map());
            const key = getCellKey(op.row!, op.col!);
            if (op.nextColor != null) next.set(key, op.nextColor);
            else next.delete(key);
            return { ...p, [sheetId]: next };
          });
          enqueueHighlightOps([
            { scope: 'CELL', row: op.row, col: op.col, color: op.nextColor ?? undefined, operation: op.nextColor != null ? 'SET' : 'CLEAR' },
          ]);
        }
      });
  }, [sheetId, enqueueHighlightOps]);

  const applyFormatToSelection = useCallback(
    async (patch: Partial<CellFormat>) => {
      const range = getEffectiveSelectionRange();
      if (!range) return;
      // Read current formats from ref (always up-to-date) to avoid relying on setState updater timing
      const current = cellFormatsBySheetRef.current[sheetId] ?? new Map();
      const next = new Map(current);
      const ops: FormatStyleOp[] = [];
      const apiOps: Array<{
        row: number;
        column: number;
        bold?: boolean;
        italic?: boolean;
        strikethrough?: boolean;
        text_color?: string | null;
        font_family?: string | null;
        font_size?: number | null;
        number_format?: { type: NumberFormatType; currency_code?: string | null; decimal_places?: number | null } | null;
      }> = [];
      for (let r = range.startRow; r <= range.endRow; r += 1) {
        for (let c = range.startCol; c <= range.endCol; c += 1) {
          const key = getCellKey(r, c);
          const prevFormat = current.get(key) ?? DEFAULT_CELL_FORMAT;
          const nextFormat: CellFormat = {
            bold: patch.bold !== undefined ? patch.bold : prevFormat.bold,
            italic: patch.italic !== undefined ? patch.italic : prevFormat.italic,
            strikethrough: patch.strikethrough !== undefined ? patch.strikethrough : prevFormat.strikethrough,
            textColor: patch.textColor !== undefined ? patch.textColor : prevFormat.textColor,
            fontFamily: patch.fontFamily !== undefined ? patch.fontFamily : prevFormat.fontFamily,
            fontSize: patch.fontSize !== undefined ? patch.fontSize : prevFormat.fontSize,
            numberFormat: patch.numberFormat !== undefined ? patch.numberFormat : prevFormat.numberFormat,
          };
          const changed =
            prevFormat.bold !== nextFormat.bold ||
            prevFormat.italic !== nextFormat.italic ||
            prevFormat.strikethrough !== nextFormat.strikethrough ||
            prevFormat.textColor !== nextFormat.textColor ||
            prevFormat.fontFamily !== nextFormat.fontFamily ||
            prevFormat.fontSize !== nextFormat.fontSize ||
            JSON.stringify(prevFormat.numberFormat) !== JSON.stringify(nextFormat.numberFormat);
          if (changed) {
            ops.push({ row: r, col: c, prev: prevFormat, next: nextFormat });
            next.set(key, nextFormat);
            apiOps.push({
              row: r,
              column: c,
              bold: nextFormat.bold,
              italic: nextFormat.italic,
              strikethrough: nextFormat.strikethrough,
              text_color: nextFormat.textColor,
              font_family: nextFormat.fontFamily,
              font_size: nextFormat.fontSize,
              number_format: nextFormat.numberFormat
                ? {
                    type: nextFormat.numberFormat.type,
                    currency_code: nextFormat.numberFormat.currencyCode ?? null,
                    decimal_places: nextFormat.numberFormat.decimalPlaces ?? null,
                  }
                : null,
            });
          }
        }
      }
      if (ops.length > 0) {
        setCellFormatsBySheet((prev) => ({ ...prev, [sheetId]: next }));
        setUndoStack((u) => [...u, { type: 'format', entry: { ops } }]);
        setRedoStack([]);
        try {
          await SpreadsheetAPI.batchUpdateCellFormats(spreadsheetId, sheetId, apiOps);
          console.debug('Format applied', { count: apiOps.length });
        } catch (error: any) {
          console.error('Failed to update cell formats:', error);
          toast.error('Failed to apply format');
          setUndoStack((u) => u.slice(0, -1));
          setCellFormatsBySheet((prev) => {
            const cur = prev[sheetId] ?? new Map();
            const revert = new Map(cur);
            ops.forEach((op) => revert.set(getCellKey(op.row, op.col), op.prev));
            return { ...prev, [sheetId]: revert };
          });
        }
      }
    },
    [sheetId, spreadsheetId, getEffectiveSelectionRange]
  );

  const handleUnifiedRedo = useCallback(async () => {
    if (redoStack.length === 0) return;
    const last = redoStack[redoStack.length - 1];
    setRedoStack((prev) => prev.slice(0, -1));
    if (last.type === 'cell') {
      setUndoStack((prev) => [...prev, last]);
      applyRedoCell(last.entry);
      toast.success('Redo complete');
    } else if (last.type === 'color') {
      setUndoStack((prev) => [...prev, { type: 'color', entry: { ops: last.entry.ops.map((o) => ({ scope: o.scope, row: o.row, col: o.col, prevColor: o.nextColor })) } }]);
      applyRedoColor(last.entry);
      toast.success('Redo complete');
    } else if (last.type === 'format') {
      setUndoStack((prev) => [...prev, last]);
      await applyRedoFormat(last.entry);
      toast.success('Redo complete');
    } else if (last.type === 'structure') {
      const entry = last.entry;
      try {
        if (entry.type === 'row_insert') {
          await handleInsertRow(entry.position, entry.count);
        } else if (entry.type === 'col_insert') {
          await handleInsertColumn(entry.position, entry.count);
        } else if (entry.type === 'row_delete') {
          await handleDeleteRow(entry.position, entry.count);
        } else if (entry.type === 'col_delete') {
          await handleDeleteColumn(entry.position, entry.count);
        }
        toast.success('Redo complete');
      } catch (error: any) {
        console.error('Failed to redo structure:', error);
        toast.error('Failed to redo');
        setRedoStack((prev) => [...prev, last]);
      }
    } else if (last.type === 'sort') {
      setUndoStack((prev) => [...prev, last]);
      try {
        await applyRedoSort(last.entry);
        toast.success('Redo complete');
      } catch (error: any) {
        console.error('Failed to redo sort:', error);
        toast.error('Failed to redo');
        setUndoStack((prev) => prev.slice(0, -1));
        setRedoStack((prev) => [...prev, last]);
      }
    }
  }, [redoStack, handleInsertRow, handleInsertColumn, handleDeleteRow, handleDeleteColumn, applyRedoCell, applyRedoColor, applyRedoFormat, applyRedoSort]);

  const canRedo = redoStack.length > 0;

  // Navigate to a cell
  const navigateToCell = useCallback(
    (row: number, col: number, clearSelection: boolean = true) => {
      // Clamp to valid range (finite grid - no auto-expand)
      const clampedRow = Math.max(0, Math.min(row, rowCount - 1));
      const clampedCol = Math.max(0, Math.min(col, colCount - 1));
      
      const newCell = { row: clampedRow, col: clampedCol };
      setActiveCell(newCell);
      setEditingCell(null);
      
      // Clear selection if requested (default behavior for single cell navigation)
      if (clearSelection) {
        setAnchorCell(null);
        setFocusCell(null);
      }
      
      // Scroll cell into view if needed
      if (gridRef.current) {
        const container = gridRef.current;
        const dataViewportHeight = Math.max(0, container.clientHeight - HEADER_HEIGHT);
        const dataViewportWidth = Math.max(0, container.clientWidth - ROW_NUMBER_WIDTH);
        const dataScrollTop = Math.max(0, container.scrollTop - HEADER_HEIGHT);
        const dataScrollLeft = container.scrollLeft;

        const cellTop = getRowOffset(clampedRow);
        const cellBottom = cellTop + getRowHeight(clampedRow);
        const cellLeft = getColumnOffset(clampedCol);
        const cellRight = cellLeft + getColumnWidth(clampedCol);

        if (cellTop < dataScrollTop) {
          container.scrollTop = cellTop + HEADER_HEIGHT;
        } else if (cellBottom > dataScrollTop + dataViewportHeight) {
          container.scrollTop = cellBottom - dataViewportHeight + HEADER_HEIGHT;
        }

        if (cellLeft < dataScrollLeft) {
          container.scrollLeft = cellLeft;
        } else if (cellRight > dataScrollLeft + dataViewportWidth) {
          container.scrollLeft = cellRight - dataViewportWidth;
        }
      }
    },
    [rowCount, colCount, getRowOffset, getRowHeight, getColumnOffset, getColumnWidth]
  );

  const handleFreezeHeader = useCallback(async () => {
    const next = frozenRowCount === 0 ? 1 : 0;
    try {
      await SpreadsheetAPI.updateSheet(spreadsheetId, sheetId, { frozen_row_count: next });
      onFreezeHeaderChange?.(next);
      toast.success(next > 0 ? 'Header frozen' : 'Header unfrozen');
    } catch (err: unknown) {
      console.error('Failed to update freeze header:', err);
      toast.error('Failed to update freeze header');
    }
  }, [spreadsheetId, sheetId, frozenRowCount, onFreezeHeaderChange]);

  // Handle keyboard navigation (Navigation Mode only)
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (isImporting) {
        return;
      }

      // When editing a cell, let the browser and the input handle ALL keys.
      // This preserves native text editing behavior (typing, Backspace/Delete,
      // Ctrl/Cmd+Z, Ctrl/Cmd+C/V, arrow keys within the text, etc).
      if (isEditing) {
        return;
      }

      if (!activeCell) {
        // If no active cell, start at (0, 0)
        if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Enter', 'Tab'].includes(e.key)) {
          e.preventDefault();
          navigateToCell(0, 0);
        }
      }

      const targetCell = activeCell ?? { row: 0, col: 0 };

      // Typing entry -> Edit Mode (navigationLocked=false)
      const isPrintable =
        e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey;
      if (isPrintable || e.key === ' ' || e.key === 'Tab') {
        e.preventDefault();
        const charToInsert = e.key === 'Tab' ? '\t' : e.key;
        enterEditMode(targetCell, charToInsert, false, 'end');
        return;
      }

      // Enter -> Edit Mode (navigationLocked=true)
      if (e.key === 'Enter') {
        e.preventDefault();
        const value = getCellRawInput(targetCell.row, targetCell.col);
        enterEditMode(targetCell, value, true, 'end');
        return;
      }

      const { row, col } = targetCell;
      let newRow = row;
      let newCol = col;
      const isShiftPressed = e.shiftKey;

      // Global undo (Ctrl/Cmd+Z) when not editing - delegates to unified undo
      if ((e.key === 'z' || e.key === 'Z') && (e.metaKey || e.ctrlKey) && !e.shiftKey) {
        e.preventDefault();
        handleUnifiedUndo();
        return;
      }
      // Global redo (Ctrl/Cmd+Shift+Z) when not editing
      if ((e.key === 'z' || e.key === 'Z') && (e.metaKey || e.ctrlKey) && e.shiftKey) {
        e.preventDefault();
        handleUnifiedRedo();
        return;
      }

      // Freeze header (Ctrl+Shift+F)
      if ((e.key === 'f' || e.key === 'F') && (e.metaKey || e.ctrlKey) && e.shiftKey) {
        e.preventDefault();
        void handleFreezeHeader();
        return;
      }

      switch (e.key) {
        case 'Backspace':
        case 'Delete': {
          // Batch clear selected cells (or the active cell if no range)
          e.preventDefault();
          const rangeToClear = getEffectiveSelectionRange();
          if (!rangeToClear) {
            return;
          }

          const changes: CellChange[] = [];
          for (let r = rangeToClear.startRow; r <= rangeToClear.endRow; r++) {
            for (let c = rangeToClear.startCol; c <= rangeToClear.endCol; c++) {
              const prevValue = getCellRawInput(r, c);
              const nextValue = '';
              if (prevValue === nextValue) continue;

              changes.push({
                row: r,
                col: c,
                prevValue,
                nextValue,
              });

              setCellValue(r, c, nextValue);
            }
          }

          if (changes.length) {
            pushHistoryEntry({ changes });
          }
          break;
        }
        case 'ArrowUp':
          e.preventDefault();
          newRow = Math.max(0, row - 1);
          if (isShiftPressed) {
            // Extend selection
            if (!anchorCell) {
              // Start selection from current active cell
              setAnchorCell({ row, col });
            }
            // Clamp to grid bounds
            const clampedNewRow = Math.max(0, Math.min(newRow, rowCount - 1));
            setFocusCell({ row: clampedNewRow, col });
            setActiveCell({ row: clampedNewRow, col });
          } else {
            // Clear selection and move active cell
            navigateToCell(newRow, col, true);
          }
          break;
        case 'ArrowDown':
          e.preventDefault();
          newRow = Math.min(rowCount - 1, row + 1);
          if (isShiftPressed) {
            // Extend selection
            if (!anchorCell) {
              setAnchorCell({ row, col });
            }
            setFocusCell({ row: newRow, col });
            setActiveCell({ row: newRow, col });
          } else {
            navigateToCell(newRow, col, true);
          }
          break;
        case 'ArrowLeft':
          e.preventDefault();
          newCol = Math.max(0, col - 1);
          if (isShiftPressed) {
            // Extend selection
            if (!anchorCell) {
              setAnchorCell({ row, col });
            }
            setFocusCell({ row, col: newCol });
            setActiveCell({ row, col: newCol });
          } else {
            navigateToCell(row, newCol, true);
          }
          break;
        case 'ArrowRight':
          e.preventDefault();
          newCol = Math.min(colCount - 1, col + 1);
          if (isShiftPressed) {
            // Extend selection
            if (!anchorCell) {
              setAnchorCell({ row, col });
            }
            setFocusCell({ row, col: newCol });
            setActiveCell({ row, col: newCol });
          } else {
            navigateToCell(row, newCol, true);
          }
          break;
        case 'Tab':
          e.preventDefault();
          if (col < colCount - 1) {
            newCol = col + 1;
          } else {
            // Wrap to next row
            newRow = Math.min(rowCount - 1, row + 1);
            newCol = 0;
          }
          navigateToCell(newRow, newCol);
          break;
        case 'Escape':
          e.preventDefault();
          setEditingCell(null);
          setEditValue('');
          break;
      }
    },
    [activeCell, isEditing, rowCount, colCount, navigateToCell, getCellRawInput, getEffectiveSelectionRange, setCellValue, enterEditMode, pushHistoryEntry, handleUnifiedUndo, handleUnifiedRedo, handleFreezeHeader]
  );

  // Track if mouse moved during selection (to distinguish click vs drag)
  const mouseDownRef = useRef<{ row: number; col: number; time: number } | null>(null);

  const startResize = useCallback(
    (e: React.PointerEvent<HTMLDivElement>, type: ResizeState['type'], index: number) => {
      if (editingCell || isImporting) return;
      e.preventDefault();
      e.stopPropagation();

      const startSize = type === 'col' ? getColumnWidth(index) : getRowHeight(index);
      resizeStateRef.current = {
        type,
        index,
        startPosition: type === 'col' ? e.clientX : e.clientY,
        startSize,
        pointerId: e.pointerId,
      };
      setIsResizing(true);
      setIsSelecting(false);
      e.currentTarget.setPointerCapture(e.pointerId);
    },
    [editingCell, isImporting, getColumnWidth, getRowHeight]
  );

  const handleResizePointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const state = resizeStateRef.current;
    if (!state || state.pointerId !== e.pointerId) return;
    e.preventDefault();

    if (state.type === 'col') {
      const delta = e.clientX - state.startPosition;
      const nextWidth = Math.max(COLUMN_MIN_WIDTH, state.startSize + delta);
      setColWidths((prev) => {
        if (prev[state.index] === nextWidth) return prev;
        return { ...prev, [state.index]: nextWidth };
      });
    } else {
      const delta = e.clientY - state.startPosition;
      const nextHeight = Math.max(ROW_MIN_HEIGHT, state.startSize + delta);
      setRowHeights((prev) => {
        if (prev[state.index] === nextHeight) return prev;
        return { ...prev, [state.index]: nextHeight };
      });
    }
  }, []);

  const handleResizePointerUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const state = resizeStateRef.current;
    if (!state || state.pointerId !== e.pointerId) return;
    resizeStateRef.current = null;
    setIsResizing(false);
    e.currentTarget.releasePointerCapture(e.pointerId);
  }, []);

  const getCellIndexFromPointer = useCallback(
    (clientX: number, clientY: number): { row: number; col: number } | null => {
      if (!gridRef.current) return null;
      const rect = gridRef.current.getBoundingClientRect();
      const offsetX = clientX - rect.left + gridRef.current.scrollLeft - ROW_NUMBER_WIDTH;
      const offsetY = clientY - rect.top + gridRef.current.scrollTop - HEADER_HEIGHT;
      if (offsetX < 0 || offsetY < 0) return null;
      const col = getColumnIndexAtOffset(offsetX);
      const row = getRowIndexAtOffset(offsetY);
      return { row, col };
    },
    [getColumnIndexAtOffset, getRowIndexAtOffset]
  );

  const handleFillPointerMove = useCallback(
    (e: PointerEvent) => {
      if (!fillStateRef.current || !isFilling) return;
      const state = fillStateRef.current;
      const dx = e.clientX - state.startX;
      const dy = e.clientY - state.startY;
      const nextDirection = Math.abs(dx) >= Math.abs(dy) ? 'horizontal' : 'vertical';
      const targetCell = getCellIndexFromPointer(e.clientX, e.clientY);
      if (!targetCell) {
        setFillPreview({ direction: nextDirection, count: 0 });
        return;
      }
      let count = 0;
      if (nextDirection === 'horizontal') {
        count = targetCell.col - state.startCol;
      } else {
        count = targetCell.row - state.startRow;
      }
      fillStateRef.current = { ...state, direction: nextDirection };
      setFillPreview({ direction: nextDirection, count });
    },
    [getCellIndexFromPointer, isFilling]
  );

  const handleFillPointerUp = useCallback(async () => {
    if (!fillStateRef.current) return;
    const state = fillStateRef.current;
    fillStateRef.current = null;
    setIsFilling(false);

    if (!fillPreview || !fillPreview.direction || fillPreview.count === 0) {
      setFillPreview(null);
      return;
    }
    if (isFillSubmitting) return;

    const sourceRow = state.startRow;
    const sourceCol = state.startCol;
    const sourceRawInput = getCellRawInput(sourceRow, sourceCol);
    const operations: Array<{
      operation: 'set' | 'clear';
      row: number;
      column: number;
      raw_input: string;
    }> = [];
    let minRow: number | null = null;
    let maxRow: number | null = null;
    let minCol: number | null = null;
    let maxCol: number | null = null;
    const step = fillPreview.count > 0 ? 1 : -1;
    for (let i = step; Math.abs(i) <= Math.abs(fillPreview.count); i += step) {
      const targetRow = fillPreview.direction === 'vertical' ? sourceRow + i : sourceRow;
      const targetCol = fillPreview.direction === 'horizontal' ? sourceCol + i : sourceCol;
      if (targetRow < 0 || targetCol < 0) {
        continue;
      }
      const rowDelta = targetRow - sourceRow;
      const colDelta = targetCol - sourceCol;
      const nextRawInput = sourceRawInput.startsWith('=')
        ? adjustFormulaReferences(sourceRawInput, rowDelta, colDelta)
        : sourceRawInput;
      operations.push({
        operation: nextRawInput.trim() === '' ? 'clear' : 'set',
        row: targetRow,
        column: targetCol,
        raw_input: nextRawInput,
      });
      minRow = minRow == null ? targetRow : Math.min(minRow, targetRow);
      maxRow = maxRow == null ? targetRow : Math.max(maxRow, targetRow);
      minCol = minCol == null ? targetCol : Math.min(minCol, targetCol);
      maxCol = maxCol == null ? targetCol : Math.max(maxCol, targetCol);
    }

    if (!operations.length) {
      setFillPreview(null);
      return;
    }

    setIsFillSubmitting(true);
    try {
      const response = await SpreadsheetAPI.batchUpdateCells(
        spreadsheetId,
        sheetId,
        operations,
        true,
        collabClientId ? { clientId: collabClientId } : undefined
      );
      applyCellsFromResponse(response.cells);
      if (onFillCommit && minRow != null && maxRow != null && minCol != null && maxCol != null) {
        onFillCommit({
          source: { row: sourceRow + 1, col: sourceCol + 1 },
          range: {
            start_row: minRow + 1,
            end_row: maxRow + 1,
            start_col: minCol + 1,
            end_col: maxCol + 1,
          },
        });
      }
    } catch (error: any) {
      console.error('Failed to fill cells:', error);
      const errorMessage =
        error?.response?.data?.error ||
        error?.response?.data?.detail ||
        error?.message ||
        'Failed to fill cells';
      toast.error(errorMessage, { duration: 3000 });
    } finally {
      setFillPreview(null);
      setIsFillSubmitting(false);
    }
  }, [applyCellsFromResponse, fillPreview, getCellRawInput, isFillSubmitting, onFillCommit, sheetId, spreadsheetId, collabClientId]);

  useEffect(() => {
    if (!isFilling) return;
    const onMove = (event: PointerEvent) => handleFillPointerMove(event);
    const onUp = () => handleFillPointerUp();
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp, { once: true });
    return () => {
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
    };
  }, [handleFillPointerMove, handleFillPointerUp, isFilling]);

  // Handle cell mouse down - start selection
  const handleCellMouseDown = useCallback(
    (e: React.MouseEvent, row: number, col: number) => {
      // Don't interfere with editing
      if (editingCell || isImporting || isResizing || resizeStateRef.current) return;

      e.preventDefault();

       // Ensure the grid container has focus so copy/paste events fire here
      if (gridRef.current) {
        gridRef.current.focus({ preventScroll: true });
      }

      const cell = { row, col };
      
      // Track mouse down position and time
      mouseDownRef.current = { row, col, time: Date.now() };
      
      // Set anchor and focus to clicked cell
      setAnchorCell(cell);
      setFocusCell(cell);
      setActiveCell(cell);
      setIsSelecting(true);
      // Note: No auto-expand - grid is finite
    },
    [editingCell, isImporting, isResizing]
  );

  const handleFillHandlePointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>, row: number, col: number) => {
      if (isFillSubmitting || isImporting || editingCell) return;
      e.stopPropagation();
      e.preventDefault();
      setIsFilling(true);
      setFillPreview({ direction: null, count: 0 });
      fillStateRef.current = {
        startRow: row,
        startCol: col,
        startX: e.clientX,
        startY: e.clientY,
        direction: null,
        pointerId: e.pointerId,
      };
      e.currentTarget.setPointerCapture(e.pointerId);
    },
    [editingCell, isFillSubmitting, isImporting]
  );

  // Handle mouse move while selecting
  const handleMouseMove = useCallback(
    (e: MouseEvent) => {
      if (!isSelecting || !gridRef.current || isResizing || resizeStateRef.current) return;
      
      const container = gridRef.current;
      const rect = container.getBoundingClientRect();
      let scrollTop = container.scrollTop;
      let scrollLeft = container.scrollLeft;
      
      // Account for header height / row number width
      const headerHeight = HEADER_HEIGHT;
      const rowNumberColumnWidth = ROW_NUMBER_WIDTH;
      
      // Handle auto-scrolling when mouse is near edges
      const scrollThreshold = 50; // pixels from edge to trigger scroll
      const scrollSpeed = 10; // pixels to scroll per frame
      
      const mouseY = e.clientY - rect.top;
      const mouseX = e.clientX - rect.left;
      
      // Vertical scrolling
      if (mouseY < scrollThreshold && scrollTop > 0) {
        scrollTop = Math.max(0, scrollTop - scrollSpeed);
        container.scrollTop = scrollTop;
      } else if (mouseY > rect.height - scrollThreshold && scrollTop < container.scrollHeight - container.clientHeight) {
        scrollTop = Math.min(container.scrollHeight - container.clientHeight, scrollTop + scrollSpeed);
        container.scrollTop = scrollTop;
      }
      
      // Horizontal scrolling
      if (mouseX < scrollThreshold + rowNumberColumnWidth && scrollLeft > 0) {
        scrollLeft = Math.max(0, scrollLeft - scrollSpeed);
        container.scrollLeft = scrollLeft;
      } else if (mouseX > rect.width - scrollThreshold && scrollLeft < container.scrollWidth - container.clientWidth) {
        scrollLeft = Math.min(container.scrollWidth - container.clientWidth, scrollLeft + scrollSpeed);
        container.scrollLeft = scrollLeft;
      }
      
      // Recalculate after potential scrolling
      scrollTop = container.scrollTop;
      scrollLeft = container.scrollLeft;
      
      // Calculate mouse position relative to grid
      const mouseYRelative = mouseY + scrollTop - headerHeight;
      const mouseXRelative = mouseX + scrollLeft - rowNumberColumnWidth;
      
      // Calculate which cell the mouse is over
      const row = getRowIndexAtOffset(mouseYRelative);
      const col = getColumnIndexAtOffset(mouseXRelative);
      
      // Clamp to valid range
      const clampedRow = Math.min(row, rowCount - 1);
      const clampedCol = Math.min(col, colCount - 1);
      
      // Update focus cell
      const newFocusCell = { row: clampedRow, col: clampedCol };
      setFocusCell(newFocusCell);
      setActiveCell(newFocusCell);
      
      // Ensure dimensions
      // Note: No auto-expand - grid is finite
    },
    [isSelecting, rowCount, colCount, isResizing, getRowIndexAtOffset, getColumnIndexAtOffset]
  );

  // Handle mouse up - finalize selection
  const handleMouseUp = useCallback(() => {
    if (isSelecting) {
      setIsSelecting(false);
      
      // If mouse didn't move (or moved very little), treat as single click
      // This ensures 1x1 selection for single clicks
      if (mouseDownRef.current && anchorCell && focusCell) {
        const moved = 
          anchorCell.row !== focusCell.row || 
          anchorCell.col !== focusCell.col;
        
        // If no movement, ensure we have a 1x1 selection
        if (!moved) {
          setAnchorCell(anchorCell);
          setFocusCell(anchorCell);
        }
      }
      
      mouseDownRef.current = null;
    }
  }, [isSelecting, anchorCell, focusCell]);

  const handleSelectAll = useCallback(() => {
    if (isImporting) return;
    const lastRow = Math.max(0, rowCount - 1);
    const lastCol = Math.max(0, colCount - 1);

    if (gridRef.current) {
      gridRef.current.focus({ preventScroll: true });
    }

    setIsSelecting(false);
    setEditingCell(null);
    setEditValue('');
    setMode('navigation');
    setNavigationLocked(false);

    const start = { row: 0, col: 0 };
    setActiveCell(start);
    setAnchorCell(start);
    setFocusCell({ row: lastRow, col: lastCol });
  }, [isImporting, rowCount, colCount]);

  // Attach/detach mouse event listeners
  useEffect(() => {
    if (isSelecting) {
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
      
      return () => {
        document.removeEventListener('mousemove', handleMouseMove);
        document.removeEventListener('mouseup', handleMouseUp);
      };
    }
  }, [isSelecting, handleMouseMove, handleMouseUp]);

  // Note: Single click handling is now done via mouseDown/mouseUp
  // This ensures proper selection behavior for both clicks and drags

  // Handle cell double click
  const handleCellDoubleClick = useCallback(
    (row: number, col: number) => {
      if (isImporting) return;
      const value = getCellRawInput(row, col);
      // Match Enter behavior: edit mode with navigation locked.
      enterEditMode({ row, col }, value, true, 'end');
    },
    [enterEditMode, getCellRawInput, isImporting]
  );

  // Focus input when entering edit mode
  useEffect(() => {
    if (editingCell && inputRef.current) {
      inputRef.current.focus({ preventScroll: true });
      const selection = pendingSelectionRef.current;
      if (selection) {
        const length = inputRef.current.value.length;
        const position =
          selection.position === 'end'
            ? length
            : selection.position === 'start'
              ? 0
              : Math.max(0, Math.min(length, selection.position));
        inputRef.current.setSelectionRange(position, position);
        pendingSelectionRef.current = null;
      } else {
        inputRef.current.select();
      }
    }
  }, [editingCell]);

  // Handle commit cell edit
  const handleCommitEdit = useCallback(() => {
    if (!editingCell) return;
    if (
      pendingCanonicalRefreshRef.current ||
      canonicalRefreshInFlightRef.current > 0
    ) {
      // The editor's row/column belongs to the pre-refresh structure. Never
      // enqueue it after the socket has advanced to the canonical revision.
      setEditingCell(null);
      setEditValue('');
      setMode('navigation');
      setNavigationLocked(false);
      return;
    }

    const { row, col } = parseCellKey(editingCell);
    const prevValue = getCellRawInput(row, col);
    const nextValue = editValue;

    // Record this edit as a single undoable action
    if (prevValue !== nextValue) {
      pushHistoryEntry({
        changes: [
          {
            row,
            col,
            prevValue,
            nextValue,
          },
        ],
      });
    }

    setCellValue(row, col, nextValue);
    recordFormulaCommit(row, col, nextValue);
    if (onHeaderRenameCommit && row === 0 && prevValue !== nextValue) {
      onHeaderRenameCommit({
        rowIndex: row,
        colIndex: col,
        newValue: nextValue,
        oldValue: prevValue,
      });
    }
    setEditingCell(null);
    setEditValue('');
    setMode('navigation');
    setNavigationLocked(false);
  }, [
    editingCell,
    editValue,
    setCellValue,
    getCellRawInput,
    pushHistoryEntry,
    recordFormulaCommit,
    onHeaderRenameCommit,
  ]);

  // Handle cancel edit
  const handleCancelEdit = useCallback(() => {
    setEditingCell(null);
    setEditValue('');
    setMode('navigation');
    setNavigationLocked(false);
  }, []);

  // Handle input blur
  const handleInputBlur = useCallback(() => {
    handleCommitEdit();
  }, [handleCommitEdit]);

  // Handle input keydown
  const handleInputKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        handleCommitEdit();

        if (!navigationLocked && activeCell) {
          // Non-locked: keep existing behavior (move down)
          const nextRow = Math.min(rowCount - 1, activeCell.row + 1);
          navigateToCell(nextRow, activeCell.col);
        }
        // Ensure grid regains focus after leaving edit mode
        requestAnimationFrame(() => {
          gridRef.current?.focus({ preventScroll: true });
        });
        return;
      }

      if (e.key === 'Escape') {
        e.preventDefault();
        handleCancelEdit();
        requestAnimationFrame(() => {
          gridRef.current?.focus({ preventScroll: true });
        });
        return;
      }

      if (navigationLocked) {
        // Locked Edit Mode: arrows move caret, not cell selection
        if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
          e.preventDefault();
          const input = e.currentTarget;
          const length = input.value.length;
          const position = e.key === 'ArrowUp' ? 0 : length;
          input.setSelectionRange(position, position);
        }
        return;
      }

      // Non-locked Edit Mode: arrow keys exit edit and move selection
      if (
        e.key === 'ArrowUp' ||
        e.key === 'ArrowDown' ||
        e.key === 'ArrowLeft' ||
        e.key === 'ArrowRight'
      ) {
        e.preventDefault();
        handleCommitEdit();

        if (!activeCell) return;
        let nextRow = activeCell.row;
        let nextCol = activeCell.col;

        if (e.key === 'ArrowUp') nextRow = Math.max(0, activeCell.row - 1);
        if (e.key === 'ArrowDown') nextRow = Math.min(rowCount - 1, activeCell.row + 1);
        if (e.key === 'ArrowLeft') nextCol = Math.max(0, activeCell.col - 1);
        if (e.key === 'ArrowRight') nextCol = Math.min(colCount - 1, activeCell.col + 1);

        navigateToCell(nextRow, nextCol);
        requestAnimationFrame(() => {
          gridRef.current?.focus({ preventScroll: true });
        });
      }
    },
    [
      handleCommitEdit,
      handleCancelEdit,
      activeCell,
      rowCount,
      colCount,
      navigateToCell,
      navigationLocked,
    ]
  );

  const handleFormulaBarChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (!isFormulaBarEditing) {
        setIsFormulaBarEditing(true);
      }
      if (!formulaBarTarget && activeCell) {
        setFormulaBarTarget(getCellKey(activeCell.row, activeCell.col));
      }
      setFormulaBarValue(e.target.value);
    },
    [activeCell, formulaBarTarget, isFormulaBarEditing]
  );

  const handleFormulaBarCommit = useCallback(async () => {
    const targetKey =
      formulaBarTarget ?? (activeCell ? getCellKey(activeCell.row, activeCell.col) : null);
    await submitFormulaBarValue(targetKey, formulaBarValue);
    setIsFormulaBarEditing(false);
    if (targetKey) {
      setFormulaBarTarget(targetKey);
    }
  }, [activeCell, formulaBarTarget, formulaBarValue, submitFormulaBarValue]);

  const handleFormulaBarKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        handleFormulaBarCommit();
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setIsFormulaBarEditing(false);
        setFormulaBarValue(getFormulaBarDisplayValue());
        if (activeCell) {
          setFormulaBarTarget(getCellKey(activeCell.row, activeCell.col));
        }
      }
    },
    [activeCell, getFormulaBarDisplayValue, handleFormulaBarCommit]
  );

  /**
   * Handle batch copy via Ctrl/Cmd + C.
   *
   * We use the selection range if present; otherwise we fall back to
   * the single active cell. The copied content is written to the
   * clipboard as text/plain using TSV (tab-separated values):
   * - Columns are separated by '\t'
   * - Rows are separated by '\n'
   */
  const handleCopy = useCallback(
    (e: React.ClipboardEvent<HTMLDivElement>) => {
      // If a cell editor is active, let the browser handle copy normally
      // so users can copy text inside the input.
      if (isEditing) {
        return;
      }

      const range = getEffectiveSelectionRange();
      if (!range) {
        return;
      }

      e.preventDefault();

      // Debug logging to verify copy handler is firing and what is selected
      console.log('[SpreadsheetGrid] onCopy fired', {
        activeElement: document.activeElement,
        range,
        activeCell,
      });

      const rows: string[] = [];
      for (let r = range.startRow; r <= range.endRow; r++) {
        const rowValues: string[] = [];
        for (let c = range.startCol; c <= range.endCol; c++) {
          const value = getCellDisplayValue(r, c);
          rowValues.push(value ?? '');
        }
        rows.push(rowValues.join('\t'));
      }

      const tsv = rows.join('\n');

      // Log the TSV content we are attempting to write
      console.log('[SpreadsheetGrid] onCopy TSV', tsv);

      try {
        e.clipboardData.setData('text/plain', tsv);
      } catch (err) {
        // Silently ignore clipboard write errors
        console.error('Failed to write to clipboard:', err);
      }
    },
    [isEditing, getEffectiveSelectionRange, getCellDisplayValue, activeCell]
  );

  /**
   * Handle batch paste via Ctrl/Cmd + V from Excel/Google Sheets.
   *
   * - Read `text/plain` from the clipboard.
   * - Parse as TSV into a 2D array.
   * - Determine paste start (selection start or active cell).
   * - Optimistically update local cell store and enqueue operations
   *   for the debounced batch saver.
   */
  const handlePaste = useCallback(
    (e: React.ClipboardEvent<HTMLDivElement>) => {
      // If a cell editor is active, let the browser handle paste normally
      // so users can paste text inside the input.
      if (isEditing) {
        return;
      }

      const text = e.clipboardData.getData('text/plain');
      if (!text) {
        return;
      }

      const matrix = parseTSV(text);
      if (matrix.length === 0) {
        return;
      }

      const range = computeSelectionRange();
      const startRow =
        range?.startRow ?? activeCell?.row ?? 0;
      const startCol =
        range?.startCol ?? activeCell?.col ?? 0;

      e.preventDefault();

      // Debug logging to verify paste handler is firing and clipboard content
      console.log('[SpreadsheetGrid] onPaste fired', {
        activeElement: document.activeElement,
        range,
        activeCell,
        text,
      });

      // Compute maximum target row/column to ensure grid expansion
      let maxTargetRow = startRow;
      let maxTargetCol = startCol;
      const changes: CellChange[] = [];
      for (let r = 0; r < matrix.length; r++) {
        const row = matrix[r];
        for (let c = 0; c < row.length; c++) {
          const targetRow = startRow + r;
          const targetCol = startCol + c;
          if (targetRow > maxTargetRow) maxTargetRow = targetRow;
          if (targetCol > maxTargetCol) maxTargetCol = targetCol;

          const prevValue = getCellRawInput(targetRow, targetCol);
          const nextValue = row[c] ?? '';
          if (prevValue !== nextValue) {
            changes.push({
              row: targetRow,
              col: targetCol,
              prevValue,
              nextValue,
            });
          }
        }
      }

      // Check bounds - paste will be clipped if exceeds grid size
      const exceedsRows = maxTargetRow >= rowCount;
      const exceedsCols = maxTargetCol >= colCount;
      
      if (exceedsRows || exceedsCols) {
        if (exceedsRows) {
          toast.error(`Paste exceeds grid size. Only ${rowCount} rows available. Add more rows to paste full data.`);
        }
        if (exceedsCols) {
          toast.error(`Paste exceeds grid size. Only ${colCount} columns available. Add more columns to paste full data.`);
        }
      }

      // Apply all values via the existing setCellValue helper, which:
      // - Updates local UI optimistically
      // - Enqueues a PendingOperation for the debounced batch saver
      // - Clips to grid bounds automatically
      for (let r = 0; r < matrix.length; r++) {
        const row = matrix[r];
        const targetRow = startRow + r;
        if (targetRow >= rowCount) break; // Stop if exceeds rows
        
        for (let c = 0; c < row.length; c++) {
          const targetCol = startCol + c;
          if (targetCol >= colCount) break; // Stop if exceeds cols

          const value = row[c] ?? '';

          // Skip cells that would exceed hard maximum dimensions
          if (targetRow >= MAX_ROWS || targetCol >= MAX_COLUMNS) {
            continue;
          }

          setCellValue(targetRow, targetCol, value);
        }
      }

      // Group this paste as a single undoable action
      if (changes.length) {
        pushHistoryEntry({ changes });
      }
    },
    [isEditing, computeSelectionRange, activeCell, setCellValue, getCellRawInput, pushHistoryEntry]
  );

  // Cleanup timers on unmount
  useEffect(() => {
    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
      if (resizeDebounceTimerRef.current) {
        clearTimeout(resizeDebounceTimerRef.current);
      }
       if (scrollRafIdRef.current != null) {
         cancelAnimationFrame(scrollRafIdRef.current);
       }
    };
  }, []);

  const buildUsedRangeMatrix = useCallback((): string[][] => {
    const range = getUsedRangeFromCells();

    // If no non-empty cells, export a 1x1 empty sheet.
    if (!range) {
      return [['']];
    }

    // NOTE: For MVP we export the used range based on currently loaded cells.
    // If a cell contains formulas, we export the stored string value if present.
    return buildMatrixFromRange(range);
  }, [getUsedRangeFromCells, buildMatrixFromRange]);

  const getExportFileBaseName = useCallback(() => {
    const baseSpreadsheet = spreadsheetName?.trim() || `spreadsheet-${spreadsheetId}`;
    const baseSheet = sheetName?.trim() || `sheet-${sheetId}`;
    return `${baseSpreadsheet}-${baseSheet}`;
  }, [spreadsheetName, sheetName, spreadsheetId, sheetId]);

  const handleExportCSV = useCallback(() => {
    const matrix = buildUsedRangeMatrix();
    const csv = exportMatrixToCSV(matrix);
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${getExportFileBaseName()}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }, [buildUsedRangeMatrix, getExportFileBaseName]);

  const handleExportXLSX = useCallback(async () => {
    // Delegate to the backend so the .xlsx carries native charts for sparkline
    // cells (the frontend SheetJS path is value-only). See MED-295.
    const blob = await SpreadsheetAPI.exportSheetXlsx(spreadsheetId, sheetId);
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${getExportFileBaseName()}.xlsx`;
    link.click();
    URL.revokeObjectURL(url);
  }, [spreadsheetId, sheetId, getExportFileBaseName]);

  const handleImportClick = useCallback(() => {
    if (isImporting) return;
    fileInputRef.current?.click();
  }, [isImporting]);

  /**
   * Parse error details from API error and determine if it's a network/timeout issue
   */
  const parseImportError = useCallback((error: any): {
    isNetworkError: boolean;
    statusCode?: number;
    message: string;
    fullError: any;
  } => {
    const fullError = error;
    const statusCode = error?.response?.status;
    const errorData = error?.response?.data;
    
    // Check for abort/timeout/network errors
    const isAbortError = error?.name === 'AbortError' || error?.code === 'ECONNABORTED' || error?.message?.includes('aborted');
    const isTimeout = error?.code === 'ECONNABORTED' || error?.message?.includes('timeout');
    const isNetworkError = !error?.response || error?.message?.includes('Network Error') || error?.message?.includes('Failed to fetch');
    const isGatewayError = statusCode === 502 || statusCode === 503 || statusCode === 504;
    
    const isNetworkIssue = isAbortError || isTimeout || isNetworkError || isGatewayError;
    
    // Extract error message from response
    let message = '';
    if (errorData) {
      message = errorData.error || errorData.detail || errorData.message || errorData.error_message || '';
    }
    if (!message && error?.message) {
      message = error.message;
    }
    if (!message) {
      message = 'Unknown error';
    }
    
    return {
      isNetworkError: isNetworkIssue,
      statusCode,
      message,
      fullError,
    };
  }, []);

  /**
   * Reconcile import by checking if data was actually saved despite the error
   */
  const reconcileImport = useCallback(async (expectedMaxRow: number, expectedMaxCol: number): Promise<boolean> => {
    try {
      // Refetch the imported range to check if data exists
      const response = await SpreadsheetAPI.readCellRange(
        spreadsheetId,
        sheetId,
        0,
        Math.min(expectedMaxRow, visibleRange.endRow),
        0,
        Math.min(expectedMaxCol, visibleRange.endCol)
      );
      
      // Check if we got any cells back (indicating import may have succeeded)
      if (response.cells && response.cells.length > 0) {
        applyCellsFromResponse(response.cells);
        return true;
      }
      return false;
    } catch (reconcileError) {
      console.error('Reconciliation check failed:', reconcileError);
      return false;
    }
  }, [spreadsheetId, sheetId, visibleRange, applyCellsFromResponse]);

  /**
   * Run tasks with a concurrency limit (at most N in flight at once).
   */
  const runWithConcurrency = useCallback(
    async (tasks: Array<() => Promise<void>>, concurrency: number): Promise<void[]> => {
      const results: void[] = new Array(tasks.length);
      let index = 0;
      const worker = async (): Promise<void> => {
        while (index < tasks.length) {
          const i = index++;
          results[i] = await tasks[i]();
        }
      };
      const workers = Array.from(
        { length: Math.min(concurrency, tasks.length) },
        () => worker()
      );
      await Promise.all(workers);
      return results;
    },
    []
  );

  /**
   * After import completes: fetch sheet meta (dimensions) and prefetch all cells in used range
   * so the grid is fully hydrated without relying on scroll. Enables correct row/col counts and
   * pattern apply without "loading more" on scroll.
   */
  const runPostImportHydration = useCallback(
    async (usedMaxRow: number, usedMaxCol: number) => {
      setHydrationStatus('hydrating');
      try {
        // 1) Fetch sheet meta (rowCount, colCount) via a small range read; backend returns sheet_row_count / sheet_column_count
        await loadCellRange(0, Math.min(0, usedMaxRow), 0, Math.min(0, usedMaxCol), true, {
          includeSheetDimensions: true,
        });

        // 2) Prefetch all cells in used range in deterministic chunks (e.g. 100 rows per request), concurrency 2
        const chunkTasks: Array<() => Promise<void>> = [];
        for (
          let rowStart = 0;
          rowStart <= usedMaxRow;
          rowStart += PREFETCH_ROWS_PER_CHUNK
        ) {
          const endRow = Math.min(rowStart + PREFETCH_ROWS_PER_CHUNK - 1, usedMaxRow);
          const sr = rowStart;
          const er = endRow;
          const sc = 0;
          const ec = usedMaxCol;
          chunkTasks.push(() =>
            loadCellRange(sr, er, sc, ec, true, { includeSheetDimensions: false })
          );
        }
        await runWithConcurrency(chunkTasks, PREFETCH_CONCURRENCY);
      } catch (err) {
        console.error('[Hydration] Prefetch failed:', err);
      } finally {
        setHydrationStatus('ready');
      }
    },
    [loadCellRange, runWithConcurrency]
  );

  const runImportMatrix = useCallback(
    async (matrix: string[][]) => {
      if (!matrix.length) {
        toast.error('Import file is empty');
        return;
      }

      setHydrationStatus('importing');

      const startRow = 0;
      const startCol = 0;

      const { operations, maxRow, maxCol } = buildCellOperations(matrix, startRow, startCol);
      const normalizedOperations = operations.map((op) => {
        const normalized = normalizeCommittedValue(op.raw_input || '');
        // Only annotate as number when the value is a finite JS number.
        // Infinity / NaN would be serialized as null by JSON.stringify, which
        // makes the backend reject the chunk with 400 (number_value required).
        if (normalized.valueType !== 'number' || !Number.isFinite(normalized.numberValue ?? NaN)) {
          return op;
        }
        return {
          ...op,
          raw_input: normalized.rawInput,
          value_type: 'number' as const,
          number_value: normalized.numberValue ?? null,
          string_value: null,
        };
      });
      if (!operations.length) {
        toast.error('No non-empty cells found to import');
        return;
      }

      // Calculate required dimensions (1-based to 0-based conversion: maxRow/maxCol are already 0-based from buildCellOperations)
      const requiredRows = maxRow + 1; // +1 because maxRow is 0-based index
      const requiredCols = maxCol + 1; // +1 because maxCol is 0-based index

      // Check if import exceeds max limits
      if (requiredRows > MAX_ROWS) {
        toast.error(`Import requires ${requiredRows} rows, but maximum is ${MAX_ROWS}. Please split the file.`);
        return;
      }
      if (requiredCols > MAX_COLUMNS) {
        toast.error(`Import requires ${requiredCols} columns, but maximum is ${MAX_COLUMNS}. Please split the file.`);
        return;
      }

      // Step 1: update the grid's local dimensions synchronously so optimistic apply
      // below can render into the newly expanded area without waiting on the network.
      const targetRowCount = Math.max(rowCount, requiredRows);
      const targetColCount = Math.max(colCount, requiredCols);
      const { clampedRows, clampedCols, wasClamped } = updateGridDimensionsLocal(
        targetRowCount,
        targetColCount,
      );
      if (wasClamped) {
        toast.error('Failed to resize grid for import');
        return;
      }

      // Step 2: record the reverse history entry (prev -> next) so the user can undo,
      // AND so we have the prevValue list available if we need to roll back the
      // optimistic apply when the backend resize call fails.
      //
      // For large imports (>5 000 ops) skip the per-cell getCellRawInput scan: it would
      // call getCellRawInput ~30 000 times on the main thread and build a 30 000-element
      // array just to enable undo. Undo is not a realistic action after a bulk import and
      // the memory overhead outweighs the benefit.
      const HISTORY_OP_THRESHOLD = 5000;
      const changes: CellChange[] = [];
      if (operations.length <= HISTORY_OP_THRESHOLD) {
        operations.forEach((op) => {
          const prevValue = getCellRawInput(op.row, op.column);
          const nextValue = op.raw_input || '';
          if (prevValue === nextValue) return;
          changes.push({
            row: op.row,
            col: op.column,
            prevValue,
            nextValue,
          });
        });
      }

      if (changes.length) {
        pushHistoryEntry({ changes });
      }

      // Step 3: optimistic UI apply. One batched setCells, so the user sees the
      // imported data IMMEDIATELY (before the backend resize network call).
      applyCellValuesLocalBatch(
        operations.map((op) => ({ row: op.row, col: op.column, value: op.raw_input || '' }))
      );

      // Step 4: start the backend resize (don't await yet). Chunks need the server-side
      // SheetRow / SheetColumn rows to exist before they can be safely written with
      // auto_expand=false, so we must await this before the first chunk fires - but
      // NOT before the optimistic display.
      const resizePromise = persistResizeToBackend(clampedRows, clampedCols, /* immediate */ true);

      // Step 5: also prepare chunks locally while the network call is in flight; this
      // work is free (pure in-memory) and lets chunks fire as soon as resize lands.
      // chunk size 2000: row_id__in + column_id__in avoids the old Q() OR recursion limit,
      // so larger chunks are safe. 2000 halves round-trips vs the previous 1000.
      const chunks = chunkOperations<CellOperation>(normalizedOperations, 2000);
      setImportProgress({ current: 0, total: chunks.length });

      const importId =
        typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
          ? crypto.randomUUID()
          : `import_${Date.now()}_${Math.random().toString(16).slice(2)}`;
      const abortController = new AbortController();
      importAbortControllerRef.current = abortController;
      const signal = abortController.signal;

      // Step 6: now actually wait for the backend to finish building rows/columns.
      const resizePersisted = await resizePromise;
      if (!resizePersisted) {
        // Roll back the optimistic apply so the user does not see "fake" data that
        // was never written to the backend.
        // - For smaller imports we have precise pre-import values in `changes`.
        // - For larger imports `changes` may be intentionally empty, so fall back to
        //   clearing the optimistically populated cells touched by this import.
        const rollbackUpdates =
          changes.length > 0
            ? changes.map((c) => ({ row: c.row, col: c.col, value: c.prevValue }))
            : Array.from(
                operations.reduce((acc, op) => {
                  acc.set(`${op.row}:${op.column}`, { row: op.row, col: op.column, value: '' });
                  return acc;
                }, new Map<string, { row: number; col: number; value: string }>()),
              ).map(([, cell]) => cell);

        if (rollbackUpdates.length > 0) {
          applyCellValuesLocalBatch(rollbackUpdates);
        }
        importAbortControllerRef.current = null;
        toast.error('Failed to resize grid for import');
        return;
      }

      // Error reporting strategy:
      // - `firstError` owns the single reported failure. Concurrent in-flight requests
      //   that are later aborted should stay silent so the user sees one toast / one log.
      // - On the first failure of a chunk, retry that chunk serially once before giving up
      //   (covers transient network blips and DB lock contention). Only on a second failure
      //   do we abort siblings and surface the error.
      let firstError: any = null;
      let lastChunkIndex = -1;

      const isCancelError = (err: any): boolean => {
        const name = err?.name;
        const code = err?.code;
        return (
          name === 'CanceledError' ||
          name === 'AbortError' ||
          name === 'Cancel' ||
          code === 'ERR_CANCELED'
        );
      };

      try {
        const chunkTasks = chunks.map((chunk, i) => async () => {
          if (signal.aborted) return;
          // auto_expand=false: rows/cols were already created by the immediate
          // resize above, so the server can skip existence checks and bulk_create.
          const runOnce = () =>
            SpreadsheetAPI.batchUpdateCells(spreadsheetId, sheetId, chunk, false, {
              importId,
              chunkIndex: i,
              importMode: true,
              signal,
            });

          let succeeded = false;
          try {
            await runOnce();
            succeeded = true;
          } catch (err: any) {
            if (signal.aborted && isCancelError(err)) return;
            if (firstError) return;
            try {
              await new Promise((resolve) => setTimeout(resolve, 500));
              if (signal.aborted) return;
              await runOnce();
              succeeded = true;
            } catch (retryErr: any) {
              if (signal.aborted && isCancelError(retryErr)) return;
              if (!firstError) {
                firstError = retryErr;
                lastChunkIndex = i;
                importAbortControllerRef.current?.abort();
              }
              return;
            }
          }
          if (succeeded) {
            setImportProgress((prev) =>
              prev ? { current: prev.current + 1, total: prev.total } : prev
            );
          }
        });
        await runWithConcurrency(chunkTasks, IMPORT_BATCH_CONCURRENCY);
        if (firstError) throw firstError;

        // All chunks complete: finalize (recalc formulas) on the server.
        await SpreadsheetAPI.finalizeImport(spreadsheetId, sheetId, importId);

        // Hydration strategy:
        // - Pure data imports (no '=' prefixed ops): the optimistic local apply already
        //   holds every raw_input we just pushed. A full re-fetch would be ~100 range
        //   requests returning identical data. Skip it.
        // - Imports containing formulas: the server may have computed values we don't
        //   have locally. Fetch ONCE over the bounding box of formula ops rather than
        //   slicing the whole region into many 100-row requests.
        const formulaOps = operations.filter((op) => (op.raw_input || '').startsWith('='));
        if (formulaOps.length === 0) {
          setHydrationStatus('ready');
        } else {
          let fMinRow = Infinity;
          let fMaxRow = -Infinity;
          let fMinCol = Infinity;
          let fMaxCol = -Infinity;
          for (const op of formulaOps) {
            if (op.row < fMinRow) fMinRow = op.row;
            if (op.row > fMaxRow) fMaxRow = op.row;
            if (op.column < fMinCol) fMinCol = op.column;
            if (op.column > fMaxCol) fMaxCol = op.column;
          }
          setHydrationStatus('hydrating');
          try {
            await loadCellRange(fMinRow, fMaxRow, fMinCol, fMaxCol, true);
          } catch (err) {
            console.error('[Hydration] Formula bbox fetch failed:', err);
          } finally {
            setHydrationStatus('ready');
          }
        }
        importAbortControllerRef.current = null;
      } catch (error: any) {
        importAbortControllerRef.current = null;
        setHydrationStatus('ready');
        if (error?.name === 'AbortError') {
          // User cancelled import - don't show error toast
          throw error;
        }
        const errorInfo = parseImportError(error);

        // Log full error details for debugging
        console.error('[Import] Error details:', {
          error: errorInfo.fullError,
          statusCode: errorInfo.statusCode,
          message: errorInfo.message,
          chunkIndex: lastChunkIndex,
          totalChunks: chunks.length,
          responseData: error?.response?.data,
        });

        // Handle network/timeout errors with reconciliation
        if (errorInfo.isNetworkError) {
          console.log('[Import] Network/timeout error detected. Checking if import succeeded...');
          
          // Wait a moment for backend to finish processing
          await new Promise(resolve => setTimeout(resolve, 2000));
          
          // Reconcile: check if import actually succeeded
          const reconciled = await reconcileImport(maxRow, maxCol);
          
          if (reconciled) {
            console.log('[Import] Reconciliation successful - import completed on backend');
            // Refresh the visible range to show imported data
            await loadCellRange(
              visibleRange.startRow,
              Math.min(maxRow, visibleRange.endRow),
              visibleRange.startCol,
              Math.min(maxCol, visibleRange.endCol),
              true
            );
            // Return successfully - caller will show success toast
            return;
          } else {
            // Import didn't succeed, show network error
            console.log('[Import] Reconciliation failed - import did not complete');
            const statusText = errorInfo.statusCode ? ` (HTTP ${errorInfo.statusCode})` : '';
            toast.error(`Import failed due to network error${statusText}. Please try again.`);
            throw error;
          }
        }
        
        // Handle validation/file errors with detailed message
        const statusText = errorInfo.statusCode ? ` (HTTP ${errorInfo.statusCode})` : '';
        const userMessage = errorInfo.message || 'Unknown error';
        toast.error(`Import failed${statusText}: ${userMessage}`);
        throw error;
      }
    },
    [
      applyCellValuesLocalBatch,
      getCellRawInput,
      pushHistoryEntry,
      spreadsheetId,
      sheetId,
      normalizeCommittedValue,
      parseImportError,
      reconcileImport,
      loadCellRange,
      runWithConcurrency,
      visibleRange,
      updateGridDimensionsLocal,
      persistResizeToBackend,
      rowCount,
      colCount,
    ]
  );

  const handleFileChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;

      const isCSV = file.type === 'text/csv' || file.name.toLowerCase().endsWith('.csv');
      const isXLSX = file.name.toLowerCase().endsWith('.xlsx');

      if (!isCSV) {
        if (!isXLSX) {
          toast.error('Please upload a CSV or XLSX file');
          e.target.value = '';
          return;
        }
      }

      try {
        setIsImporting(true);
        setImportProgress({ current: 0, total: 0 });

        if (isCSV) {
          const matrix = await parseCSVFile(file);
          await runImportMatrix(matrix);
          toast.success('Import complete');
          return;
        }

        const parsed = await parseXLSXFile(file);
        if (!parsed.sheetNames.length) {
          toast.error('No worksheets found');
          return;
        }

        if (parsed.sheetNames.length === 1) {
          const matrix = parsed.sheets[parsed.sheetNames[0]] || [];
          await runImportMatrix(matrix);
          toast.success('Import complete');
          return;
        }

        setXlsxImport(parsed);
        setSelectedXlsxSheet(parsed.sheetNames[0]);
      } catch (error: any) {
        if (error?.name === 'AbortError') return;
        // If error has response, or is timeout/network (no response), runImportMatrix already showed a toast
        const isTimeoutOrNetwork = !error?.response && (error?.code === 'ECONNABORTED' || /timeout|network error|failed to fetch/i.test(error?.message || ''));
        if (error?.response || isTimeoutOrNetwork) {
          console.error('[Import] API/timeout error (already handled):', error);
        } else {
          // File parsing error
          console.error('[Import] File parsing error:', error);
          const errorInfo = parseImportError(error);
          console.error('[Import] Parsing error details:', {
            error: errorInfo.fullError,
            message: errorInfo.message,
          });
          toast.error(`Import failed: ${errorInfo.message || 'Invalid file format'}`);
        }
      } finally {
        setIsImporting(false);
        setImportProgress(null);
        e.target.value = '';
      }
    },
    [runImportMatrix, parseImportError]
  );

  const handleConfirmXlsxImport = useCallback(async () => {
    if (!xlsxImport || !selectedXlsxSheet) {
      return;
    }

    const matrix = xlsxImport.sheets[selectedXlsxSheet] || [];
    try {
      setIsImporting(true);
      setImportProgress({ current: 0, total: 0 });
      await runImportMatrix(matrix);
      toast.success('Import complete');
      setXlsxImport(null);
      setSelectedXlsxSheet('');
    } catch (error: any) {
      if (error?.name === 'AbortError') {
        return; // User cancelled - no toast
      }
      // If error has response, or is timeout/network, runImportMatrix already showed a toast
      const isTimeoutOrNetwork = !error?.response && (error?.code === 'ECONNABORTED' || /timeout|network error|failed to fetch/i.test(error?.message || ''));
      if (error?.response || isTimeoutOrNetwork) {
        console.error('[Import] API/timeout error (already handled):', error);
      } else {
        console.error('[Import] Unexpected error:', error);
        const errorInfo = parseImportError(error);
        toast.error(`Import failed: ${errorInfo.message || 'Unknown error'}`);
      }
    } finally {
      setIsImporting(false);
      setImportProgress(null);
    }
  }, [xlsxImport, selectedXlsxSheet, runImportMatrix, parseImportError]);

  const handleCancelXlsxImport = useCallback(() => {
    importAbortControllerRef.current?.abort();
    setXlsxImport(null);
    setSelectedXlsxSheet('');
  }, []);

  const handleImportGoogleSheets = useCallback(async () => {
    const url = sheetsImportUrl.trim();
    if (!url) return;
    setSheetsImportLoading(true);
    try {
      const { matrix } = await googleDocsApi.importFromGoogleSheets(url);
      setSheetsImportModalOpen(false);
      setSheetsImportUrl('');
      setIsImporting(true);
      setImportProgress({ current: 0, total: 0 });
      await runImportMatrix(matrix);
      toast.success('Google Sheets import complete');
    } catch (err: any) {
      const msg = err?.response?.data?.error || 'Google Sheets import failed. Please try again.';
      toast.error(msg);
    } finally {
      setSheetsImportLoading(false);
      setIsImporting(false);
      setImportProgress(null);
    }
  }, [sheetsImportUrl, runImportMatrix]);

  const handleExportGoogleSheets = useCallback(async () => {
    const matrix = buildUsedRangeMatrix();
    const title = getExportFileBaseName();
    const toastId = toast.loading('Exporting to Google Sheets...');
    try {
      const result = await googleDocsApi.exportToGoogleSheets(title, matrix);
      toast.dismiss(toastId);
      toast.success(
        (t) => (
          <span>
            Exported!{' '}
            <a
              href={result.url}
              target="_blank"
              rel="noopener noreferrer"
              className="underline text-[#3CCED7]"
              onClick={() => toast.dismiss(t.id)}
            >
              Open in Google Sheets
            </a>
          </span>
        ),
        { duration: 8000 }
      );
    } catch (err: any) {
      toast.dismiss(toastId);
      const msg = err?.response?.data?.error || 'Google Sheets export failed. Please try again.';
      toast.error(msg);
    }
  }, [buildUsedRangeMatrix, getExportFileBaseName]);

  useEffect(() => {
    if (!exportMenuOpen) return;

    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      if (target.closest('[data-export-menu]') || target.closest('[data-export-menu-trigger]')) {
        return;
      }
      setExportMenuOpen(false);
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setExportMenuOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [exportMenuOpen]);

  useEffect(() => {
    if (!highlightMenuOpen) return;
    const handleClick = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      if (target.closest('[data-highlight-menu]') || target.closest('[data-highlight-menu-trigger]')) {
        return;
      }
      setHighlightMenuOpen(false);
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [highlightMenuOpen]);

  useEffect(() => {
    if (!textColorMenuOpen) return;
    const handleClick = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      if (target.closest('[data-text-color-menu]') || target.closest('[data-text-color-trigger]')) {
        return;
      }
      setTextColorMenuOpen(false);
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [textColorMenuOpen]);

  useEffect(() => {
    if (!currencyMenuOpen) return;
    const handleClick = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      if (target.closest('[data-currency-menu]') || target.closest('[data-format-currency-trigger]')) {
        return;
      }
      setCurrencyMenuOpen(false);
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [currencyMenuOpen]);

  useEffect(() => {
    if (!headerMenu) return;

    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      if (target.closest('[data-header-context-menu]')) {
        return;
      }
      setHeaderMenu(null);
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setHeaderMenu(null);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [headerMenu]);

  useEffect(() => {
    if (!sortMenu) return;
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      if (target.closest('[data-sort-menu]') || target.closest('[data-col-sort-trigger]')) return;
      setSortMenu(null);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSortMenu(null);
    };
    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [sortMenu]);

  const selectedCellKey = activeCell ? getCellKey(activeCell.row, activeCell.col) : null;
  const editingCellCoords = editingCell ? parseCellKey(editingCell) : null;
  const selectionRange = useMemo(() => computeSelectionRange(), [computeSelectionRange]);
  const effectiveSelectionRange = useMemo(
    () => getEffectiveSelectionRange(),
    [getEffectiveSelectionRange]
  );
  const hasSelection = Boolean(effectiveSelectionRange);

  const formatStateForSelection = useMemo(() => {
    if (!effectiveSelectionRange) return null;
    const r = effectiveSelectionRange;
    let boldCount = 0;
    let italicCount = 0;
    let strikethroughCount = 0;
    let total = 0;
    for (let row = r.startRow; row <= r.endRow; row += 1) {
      for (let col = r.startCol; col <= r.endCol; col += 1) {
        const f = cellFormats.get(getCellKey(row, col)) ?? DEFAULT_CELL_FORMAT;
        if (f.bold) boldCount += 1;
        if (f.italic) italicCount += 1;
        if (f.strikethrough) strikethroughCount += 1;
        total += 1;
      }
    }
    return {
      bold: total > 0 && boldCount === total,
      italic: total > 0 && italicCount === total,
      strikethrough: total > 0 && strikethroughCount === total,
    };
  }, [effectiveSelectionRange, cellFormats]);

  // Sync selected format fields from active cell when selection changes
  useEffect(() => {
    if (!activeCell) return;
    const fmt = cellFormats.get(getCellKey(activeCell.row, activeCell.col)) ?? DEFAULT_CELL_FORMAT;
    setSelectedTextColor(fmt.textColor ?? null);
    setSelectedFontFamily(fmt.fontFamily ?? null);
    setSelectedFontSize(fmt.fontSize ?? null);
    setSelectedNumberFormat(fmt.numberFormat ?? null);
  }, [activeCell?.row, activeCell?.col, cellFormats]);

  const applyHighlightToSelection = useCallback(
    (color: string | null, recordColor: string) => {
      if (!effectiveSelectionRange) return;
      const rowH = rowHighlightsBySheet[sheetId] ?? {};
      const colH = colHighlightsBySheet[sheetId] ?? {};
      const cellH = cellHighlightsBySheet[sheetId] ?? new Map<string, string>();

      const isFullRowSelection =
        selectionRange != null &&
        selectionRange.startCol === 0 &&
        selectionRange.endCol === Math.max(0, colCount - 1);
      const isFullColSelection =
        selectionRange != null &&
        selectionRange.startRow === 0 &&
        selectionRange.endRow === Math.max(0, rowCount - 1);

      setRedoStack([]);
      if (isFullRowSelection) {
        const colorUndoOps: ColorHistoryEntry['ops'] = [];
        for (let row = selectionRange.startRow; row <= selectionRange.endRow; row += 1) {
          colorUndoOps.push({ scope: 'ROW', row, prevColor: rowH[row] });
        }
        setUndoStack((prev) => [...prev, { type: 'color', entry: { ops: colorUndoOps } }]);
        setRowHighlightsBySheet((prev) => {
          const next = { ...(prev[sheetId] ?? {}) };
          for (let row = selectionRange.startRow; row <= selectionRange.endRow; row += 1) {
            if (color) {
              next[row] = color;
            } else {
              delete next[row];
            }
          }
          return { ...prev, [sheetId]: next };
        });
        const ops: HighlightOp[] = [];
        for (let row = selectionRange.startRow; row <= selectionRange.endRow; row += 1) {
          ops.push({
            scope: 'ROW',
            row,
            color: color ?? undefined,
            operation: color ? 'SET' : 'CLEAR',
          });
        }
        enqueueHighlightOps(ops);
        if (onHighlightCommit) {
          onHighlightCommit(buildHighlightPayload(recordColor, 'ROW', selectionRange));
        }
        return;
      }

      if (isFullColSelection) {
        const colorUndoOps: ColorHistoryEntry['ops'] = [];
        for (let col = selectionRange.startCol; col <= selectionRange.endCol; col += 1) {
          colorUndoOps.push({ scope: 'COLUMN', col, prevColor: colH[col] });
        }
        setUndoStack((prev) => [...prev, { type: 'color', entry: { ops: colorUndoOps } }]);
        setColHighlightsBySheet((prev) => {
          const next = { ...(prev[sheetId] ?? {}) };
          for (let col = selectionRange.startCol; col <= selectionRange.endCol; col += 1) {
            if (color) {
              next[col] = color;
            } else {
              delete next[col];
            }
          }
          return { ...prev, [sheetId]: next };
        });
        const ops: HighlightOp[] = [];
        for (let col = selectionRange.startCol; col <= selectionRange.endCol; col += 1) {
          ops.push({
            scope: 'COLUMN',
            col,
            color: color ?? undefined,
            operation: color ? 'SET' : 'CLEAR',
          });
        }
        enqueueHighlightOps(ops);
        if (onHighlightCommit) {
          onHighlightCommit(buildHighlightPayload(recordColor, 'COLUMN', selectionRange));
        }
        return;
      }

      const colorUndoOps: ColorHistoryEntry['ops'] = [];
      for (let row = effectiveSelectionRange.startRow; row <= effectiveSelectionRange.endRow; row += 1) {
        for (let col = effectiveSelectionRange.startCol; col <= effectiveSelectionRange.endCol; col += 1) {
          const key = getCellKey(row, col);
          colorUndoOps.push({
            scope: 'CELL',
            row,
            col,
            prevColor: cellH.get(key),
          });
        }
      }
      setUndoStack((prev) => [...prev, { type: 'color', entry: { ops: colorUndoOps } }]);
      setCellHighlightsBySheet((prev) => {
        const next = new Map(prev[sheetId] ?? new Map());
        for (let row = effectiveSelectionRange.startRow; row <= effectiveSelectionRange.endRow; row += 1) {
          for (let col = effectiveSelectionRange.startCol; col <= effectiveSelectionRange.endCol; col += 1) {
            const key = getCellKey(row, col);
            if (color) {
              next.set(key, color);
            } else {
              next.delete(key);
            }
          }
        }
        return { ...prev, [sheetId]: next };
      });
      const ops: HighlightOp[] = [];
      for (let row = effectiveSelectionRange.startRow; row <= effectiveSelectionRange.endRow; row += 1) {
        for (let col = effectiveSelectionRange.startCol; col <= effectiveSelectionRange.endCol; col += 1) {
          ops.push({
            scope: 'CELL',
            row,
            col,
            color: color ?? undefined,
            operation: color ? 'SET' : 'CLEAR',
          });
        }
      }
      enqueueHighlightOps(ops);
      if (onHighlightCommit) {
        const scope =
          effectiveSelectionRange.startRow === effectiveSelectionRange.endRow &&
          effectiveSelectionRange.startCol === effectiveSelectionRange.endCol
            ? 'CELL'
            : 'RANGE';
        onHighlightCommit(buildHighlightPayload(recordColor, scope, effectiveSelectionRange));
      }
    },
    [
      effectiveSelectionRange,
      selectionRange,
      colCount,
      rowCount,
      onHighlightCommit,
      buildHighlightPayload,
      sheetId,
      enqueueHighlightOps,
      rowHighlightsBySheet,
      colHighlightsBySheet,
      cellHighlightsBySheet,
    ]
  );

  const applyHighlightOperation = useCallback(
    (payload: ApplyHighlightParams) => {
      const color = payload.color === CLEAR_HIGHLIGHT ? null : payload.color;
      const headerRow = Math.max(0, (payload.header_row_index ?? 1) - 1);
      const fallback = payload.target?.fallback || {};

      if (payload.scope === 'COLUMN') {
        const resolved =
          resolveHeaderColumnByName(payload.target?.by_header) ??
          (fallback.col_index != null ? fallback.col_index - 1 : null);
        if (resolved == null || resolved < 0 || resolved >= colCount) return;
        setColHighlightsBySheet((prev) => {
          const next = { ...(prev[sheetId] ?? {}) };
          if (color) {
            next[resolved] = color;
          } else {
            delete next[resolved];
          }
          return { ...prev, [sheetId]: next };
        });
        enqueueHighlightOps([
          {
            scope: 'COLUMN',
            col: resolved,
            color: color ?? undefined,
            operation: color ? 'SET' : 'CLEAR',
          },
        ]);
        return;
      }

      if (payload.scope === 'ROW') {
        const row = fallback.row_index != null ? fallback.row_index - 1 : null;
        if (row == null || row < 0 || row >= rowCount) return;
        setRowHighlightsBySheet((prev) => {
          const next = { ...(prev[sheetId] ?? {}) };
          if (color) {
            next[row] = color;
          } else {
            delete next[row];
          }
          return { ...prev, [sheetId]: next };
        });
        enqueueHighlightOps([
          {
            scope: 'ROW',
            row,
            color: color ?? undefined,
            operation: color ? 'SET' : 'CLEAR',
          },
        ]);
        return;
      }

      if (payload.scope === 'CELL') {
        const row = fallback.row_index != null ? fallback.row_index - 1 : headerRow;
        const col =
          resolveHeaderColumnByName(payload.target?.by_header) ??
          (fallback.col_index != null ? fallback.col_index - 1 : null);
        if (row < 0 || row >= rowCount || col == null || col < 0 || col >= colCount) return;
        setCellHighlightsBySheet((prev) => {
          const next = new Map(prev[sheetId] ?? new Map());
          const key = getCellKey(row, col);
          if (color) {
            next.set(key, color);
          } else {
            next.delete(key);
          }
          return { ...prev, [sheetId]: next };
        });
        enqueueHighlightOps([
          {
            scope: 'CELL',
            row,
            col,
            color: color ?? undefined,
            operation: color ? 'SET' : 'CLEAR',
          },
        ]);
        return;
      }

      if (payload.scope === 'RANGE') {
        const byHeaders = payload.target?.by_headers || {};
        const startCol =
          resolveHeaderColumnByName(byHeaders.start) ??
          (fallback.start_col != null ? fallback.start_col - 1 : null);
        const endCol =
          resolveHeaderColumnByName(byHeaders.end) ??
          (fallback.end_col != null ? fallback.end_col - 1 : null);
        const startRow = fallback.start_row != null ? fallback.start_row - 1 : headerRow;
        const endRow = fallback.end_row != null ? fallback.end_row - 1 : headerRow;
        if (
          startCol == null ||
          endCol == null ||
          startRow < 0 ||
          endRow < 0 ||
          startRow >= rowCount ||
          endRow >= rowCount ||
          startCol >= colCount ||
          endCol >= colCount
        ) {
          return;
        }
        const rowStart = Math.min(startRow, endRow);
        const rowEnd = Math.max(startRow, endRow);
        const colStart = Math.min(startCol, endCol);
        const colEnd = Math.max(startCol, endCol);
        setCellHighlightsBySheet((prev) => {
          const next = new Map(prev[sheetId] ?? new Map());
          for (let row = rowStart; row <= rowEnd; row += 1) {
            for (let col = colStart; col <= colEnd; col += 1) {
              const key = getCellKey(row, col);
              if (color) {
                next.set(key, color);
              } else {
                next.delete(key);
              }
            }
          }
          return { ...prev, [sheetId]: next };
        });
        const ops: HighlightOp[] = [];
        for (let row = rowStart; row <= rowEnd; row += 1) {
          for (let col = colStart; col <= colEnd; col += 1) {
            ops.push({
              scope: 'CELL',
              row,
              col,
              color: color ?? undefined,
              operation: color ? 'SET' : 'CLEAR',
            });
          }
        }
        enqueueHighlightOps(ops);
      }
    },
    [colCount, rowCount, resolveHeaderColumnByName, sheetId, enqueueHighlightOps]
  );

  useImperativeHandle(
    ref,
    () => ({
      applyFormula: (row: number, col: number, value: string) =>
        submitFormulaBarValue(getCellKey(row, col), value),
      insertRow: (position: number, count: number = 1) => handleInsertRow(position, count),
      insertColumn: (position: number, count: number = 1) => handleInsertColumn(position, count),
      deleteColumn: (position: number, count: number = 1) => handleDeleteColumn(position, count),
      refresh: () => refreshSheet(),
      applyHighlightOperation: (payload: ApplyHighlightParams) => applyHighlightOperation(payload),
      applyRemoteCells: (cells) =>
        applyCellsFromResponse(cells, { source: 'remote' }),
      navigateToCell: (row: number, col: number) => navigateToCell(row, col),
    }),
    [
      submitFormulaBarValue,
      handleInsertRow,
      handleInsertColumn,
      handleDeleteColumn,
      refreshSheet,
      applyHighlightOperation,
      applyCellsFromResponse,
      navigateToCell,
    ]
  );

  const isAnomalyHighlighted = useCallback(
    (row: number, col: number) => {
      if (highlightLocations && highlightLocations.length > 0) {
        return highlightLocations.some((loc) => loc.row === row && loc.col === col);
      }
      return (
        highlightCell != null &&
        highlightCell.row === row &&
        highlightCell.col === col
      );
    },
    [highlightCell, highlightLocations]
  );

  const isRowHeaderSelected = useCallback(
    (row: number) => {
      if (!selectionRange) return false;
      return (
        row >= selectionRange.startRow &&
        row <= selectionRange.endRow &&
        selectionRange.startCol === 0 &&
        selectionRange.endCol === Math.max(0, colCount - 1)
      );
    },
    [selectionRange, colCount]
  );

  const isColumnHeaderSelected = useCallback(
    (col: number) => {
      if (!selectionRange) return false;
      return (
        col >= selectionRange.startCol &&
        col <= selectionRange.endCol &&
        selectionRange.startRow === 0 &&
        selectionRange.endRow === Math.max(0, rowCount - 1)
      );
    },
    [selectionRange, rowCount]
  );

  // Derived ranges for virtualized rendering (clamp so we never render 0 rows/cols when grid has content)
  const visibleStartRowRaw = Math.max(0, visibleRange.startRow);
  const visibleEndRowRaw =
    rowCount <= 0
      ? -1
      : Math.max(visibleStartRowRaw, Math.min(rowCount - 1, visibleRange.endRow));
  const visibleStartCol = Math.max(0, visibleRange.startCol);
  const visibleEndCol =
    colCount <= 0
      ? -1
      : Math.max(visibleStartCol, Math.min(colCount - 1, visibleRange.endCol));
  // Number of frozen rows (typically 0 or 1) – clamp to valid range
  const frozenRows = Math.max(0, Math.min(frozenRowCount, rowCount));
  const activeColumnFilters = useMemo(
    () => {
      const orderedCols = [
        ...columnFilterOrder,
        ...Object.keys(columnFilters).map(Number).filter((col) => !columnFilterOrder.includes(col)),
      ];
      return orderedCols
        .map((col) => {
          const parsed = parseColumnFilterExpression(columnFilters[col] ?? '');
          if (!parsed) return null;
          return { col, filter: parsed };
        })
        .filter((entry): entry is { col: number; filter: ParsedColumnFilter } => entry !== null);
    },
    [columnFilters, columnFilterOrder]
  );
  const hasActiveFilters = activeColumnFilters.length > 0;

  // For virtualized rendering of *non-frozen* rows, never start before the first non-frozen row.
  const visibleStartRow = Math.max(frozenRows, visibleStartRowRaw);
  const visibleEndRow =
    rowCount <= frozenRows
      ? frozenRows - 1
      : Math.max(visibleStartRow, visibleEndRowRaw);

  let visibleRowCount = Math.max(0, visibleEndRow - visibleStartRow + 1);
  let visibleColCount = Math.max(0, visibleEndCol - visibleStartCol + 1);
  if (rowCount > 0 && visibleRowCount === 0) visibleRowCount = 1;
  if (colCount > 0 && visibleColCount === 0) visibleColCount = 1;

  // Spacers only apply to the non-frozen region. Frozen rows are always rendered explicitly.
  let topSpacerHeight = 0;
  let bottomSpacerHeight = 0;
  if (rowCount > 0 && !hasActiveFilters) {
    if (frozenRows > 0) {
      const frozenHeight = getRowOffset(frozenRows);
      const nonFrozenStartOffset = getRowOffset(visibleStartRow);
      const nonFrozenEndOffset = getRowOffset(visibleEndRow + 1);
      topSpacerHeight = Math.max(0, nonFrozenStartOffset - frozenHeight);
      bottomSpacerHeight = Math.max(0, totalRowHeight - nonFrozenEndOffset);
    } else {
      topSpacerHeight = getRowOffset(visibleStartRow);
      bottomSpacerHeight = Math.max(0, totalRowHeight - getRowOffset(visibleEndRow + 1));
    }
  }
  const renderedNonFrozenRows = useMemo(() => {
    if (!hasActiveFilters) {
      return Array.from({ length: visibleRowCount }, (_, rowOffset) => visibleStartRow + rowOffset);
    }
    let rows = Array.from({ length: Math.max(0, rowCount - frozenRows) }, (_, idx) => frozenRows + idx);
    for (const { col, filter } of activeColumnFilters) {
      rows = rows.filter((row) => doesCellMatchFilter(row, col, filter));
      if (!rows.length) break;
    }
    return rows;
  }, [hasActiveFilters, visibleRowCount, visibleStartRow, frozenRows, rowCount, activeColumnFilters, doesCellMatchFilter]);
  const renderedNonFrozenHeight = useMemo(
    () => renderedNonFrozenRows.reduce((acc, row) => acc + getRowHeight(row), 0),
    [renderedNonFrozenRows, getRowHeight]
  );
  const bodyTableHeight = hasActiveFilters ? getRowOffset(frozenRows) + renderedNonFrozenHeight : totalRowHeight;
  const leftSpacerWidth = getColumnOffset(visibleStartCol);
  const rightSpacerWidth = Math.max(0, totalColumnWidth - getColumnOffset(visibleEndCol + 1));

  const totalColumns =
    1 + // row number column
    1 + // left spacer
    visibleColCount +
    1; // right spacer

  const cellBaseStyle: React.CSSProperties = {
    boxSizing: 'border-box',
  };

  const headerCellStyle: React.CSSProperties = {
    ...cellBaseStyle,
    height: `${HEADER_HEIGHT}px`,
    minHeight: `${HEADER_HEIGHT}px`,
  };

  const cellContentBaseStyle: React.CSSProperties = {
    padding: `${CELL_PADDING_Y}px ${CELL_PADDING_X}px`,
    boxSizing: 'border-box',
    fontSize: `${CELL_FONT_SIZE}px`,
    display: 'flex',
    alignItems: 'center',
    overflow: 'hidden',
    whiteSpace: 'nowrap',
    textOverflow: 'ellipsis',
  };

  const cellInputBaseStyle: React.CSSProperties = {
    padding: `${CELL_PADDING_Y}px ${CELL_PADDING_X}px`,
    boxSizing: 'border-box',
    fontSize: `${CELL_FONT_SIZE}px`,
    border: 'none',
    outline: 'none',
  };

  const getCellBaseStyle = (height: number): React.CSSProperties => ({
    ...cellBaseStyle,
    height: `${height}px`,
    minHeight: `${height}px`,
  });

  const getCellContentStyle = (height: number): React.CSSProperties => ({
    ...cellContentBaseStyle,
    height: `${height}px`,
    lineHeight: `${Math.max(0, height - CELL_PADDING_Y * 2)}px`,
  });

  const getCellInputStyle = (height: number): React.CSSProperties => ({
    ...cellInputBaseStyle,
    height: `${height}px`,
    lineHeight: `${Math.max(0, height - CELL_PADDING_Y * 2)}px`,
  });

  const getFrozenRowStickyTop = (row: number): number =>
    HEADER_HEIGHT + getRowOffset(row);

  const isFrozenRow = (row: number): boolean =>
    frozenRowCount > 0 && row < frozenRowCount;

  const showGridSpinner = isGridLoading || cellCanvasLoading;

  return (
    <div className={`relative h-full w-full flex flex-col${showGridSpinner ? ' pointer-events-none' : ''}`}>
      {/* Save status indicator */}
      {saveError && (
        <div className="absolute top-2 right-2 z-30 bg-red-50 border border-red-200 text-red-700 px-3 py-1 rounded text-xs">
          {saveError}
        </div>
      )}
      {isSaving && pendingOps.size > 0 && (
        <div className="absolute top-2 right-2 z-30 bg-[#3CCED7]/10 border border-[#3CCED7]/30 text-[#1a9ba3] px-3 py-1 rounded text-xs">
          Saving...
        </div>
      )}
      {(isImporting && importProgress) || hydrationStatus === 'hydrating' ? (
        <div className="absolute top-2 left-2 z-30 bg-yellow-50 border border-yellow-200 text-yellow-700 px-3 py-1 rounded text-xs">
          {hydrationStatus === 'hydrating'
            ? 'Preparing sheet...'
            : `Importing... ${importProgress!.current}/${importProgress!.total}`}
        </div>
      ) : null}

      {/* Unified toolbar: undo/redo, freeze, import/export, highlight & formatting */}
      <div className="flex items-center justify-between gap-3 overflow-x-auto px-2 py-1.5 border-b border-gray-200 bg-white">
        <div className="flex shrink-0 items-center gap-1.5">
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,.xlsx"
          className="hidden"
          onChange={handleFileChange}
        />
        <button
          type="button"
          onClick={() => setSheetsImportModalOpen(true)}
          disabled={isImporting}
          title="Import from Google Sheets"
          className="inline-flex h-8 items-center gap-1 rounded-md px-3 text-xs font-medium text-[#0E8A96] transition hover:bg-[#3CCED7]/10 disabled:opacity-60"
        >
          <FileSpreadsheet className="h-3.5 w-3.5" strokeWidth={2.3} aria-hidden="true" />
          Sheets Import
        </button>
        <button
          type="button"
          onClick={handleImportClick}
          disabled={isImporting}
            className="inline-flex h-8 items-center gap-1 rounded-md px-3 text-xs font-medium text-gray-700 transition hover:bg-gray-100 hover:text-gray-900 disabled:opacity-60"
        >
          <Download className="h-3.5 w-3.5" strokeWidth={2.3} aria-hidden="true" />
          Import
        </button>
        <span className="mx-1 inline-block h-5 w-px bg-gray-200" aria-hidden="true" />
        <button
          type="button"
          onClick={handleUnifiedUndo}
          disabled={!canUndo || isReverting}
          title="Undo (Ctrl+Z)"
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-600 transition hover:bg-gray-100 hover:text-gray-900 disabled:cursor-not-allowed disabled:opacity-40"
        >
            <Undo2 className="h-3.5 w-3.5" strokeWidth={2.3} />
        </button>
        <button
          type="button"
          onClick={handleUnifiedRedo}
          disabled={!canRedo || isReverting}
          title="Redo (Ctrl+Shift+Z)"
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-600 transition hover:bg-gray-100 hover:text-gray-900 disabled:cursor-not-allowed disabled:opacity-40"
        >
            <Redo2 className="h-3.5 w-3.5" strokeWidth={2.3} />
        </button>
        <button
          type="button"
          onClick={handleFreezeHeader}
          title={frozenRowCount > 0 ? 'Unfreeze header (Ctrl+Shift+F)' : 'Freeze header (Ctrl+Shift+F)'}
            className={`inline-flex h-8 w-8 items-center justify-center rounded-md transition text-xs ${
            frozenRowCount > 0
              ? 'bg-[#3CCED7]/10 text-[#0E8A96] ring-1 ring-[#3CCED7]/30'
              : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
          }`}
          data-testid="freeze-header-button"
        >
          <span className="flex items-center gap-0.5 text-sm font-semibold">
              <Snowflake className="h-3 w-3" strokeWidth={2.3} />
            <span>H</span>
          </span>
        </button>
        <div className="relative" ref={exportMenuRef}>
          <button
            type="button"
            ref={exportTriggerRef}
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              const rect = exportTriggerRef.current?.getBoundingClientRect();
              if (rect) {
                setExportMenuAnchor({
                  top: rect.bottom + 6,
                  left: rect.right,
                  width: rect.width,
                });
              }
              setExportMenuOpen((prev) => !prev);
            }}
            disabled={isImporting}
              className="inline-flex h-8 items-center gap-1 rounded-md px-3 text-xs font-medium text-gray-700 transition hover:bg-gray-100 hover:text-gray-900 disabled:opacity-60"
            aria-haspopup="menu"
            aria-expanded={exportMenuOpen}
            data-export-menu-trigger
          >
            <Upload className="h-3.5 w-3.5" strokeWidth={2.3} aria-hidden="true" />
            Export
          </button>
          {exportMenuOpen && exportMenuAnchor &&
            createPortal(
              <div
                className="fixed z-[1000] w-40 overflow-hidden rounded-lg bg-white shadow-lg ring-1 ring-gray-100"
                style={{
                  top: exportMenuAnchor.top,
                  left: exportMenuAnchor.left - exportMenuAnchor.width,
                }}
                role="menu"
                data-export-menu
              >
                <div className="h-[3px] w-full bg-gradient-to-r from-[#3CCED7] to-[#A6E661]" />
                <button
                  type="button"
                  onClick={() => {
                    handleExportCSV();
                    setExportMenuOpen(false);
                  }}
                  className="w-full px-3 py-2 text-left text-xs font-medium text-gray-700 transition hover:bg-gray-50"
                  role="menuitem"
                >
                  Export as CSV
                </button>
                <button
                  type="button"
                  onClick={() => {
                    handleExportXLSX();
                    setExportMenuOpen(false);
                  }}
                  className="w-full px-3 py-2 text-left text-xs font-medium text-gray-700 transition hover:bg-gray-50"
                  role="menuitem"
                >
                  Export as XLSX
                </button>
                <button
                  type="button"
                  onClick={() => {
                    handleExportGoogleSheets();
                    setExportMenuOpen(false);
                  }}
                  className="w-full px-3 py-2 text-left text-xs font-medium text-[#0E8A96] transition hover:bg-[#3CCED7]/10"
                  role="menuitem"
                >
                  Export to Google Sheets
                </button>
              </div>,
              document.body
            )}
        </div>
        <button
          type="button"
          onClick={() => {
            if (onOpenPivotBuilder) {
              const cellData = new Map<string, { rawInput: string; computedString?: string | null }>();
              cells.forEach((cell, key) => {
                cellData.set(key, { rawInput: cell.rawInput, computedString: cell.computedString });
              });
              onOpenPivotBuilder({ cells: cellData, rowCount, colCount });
            }
          }}
          title="Pivot Table"
          disabled={!onOpenPivotBuilder}
          className="inline-flex h-8 items-center gap-1 rounded-md px-3 text-xs font-medium text-gray-700 transition hover:bg-gray-100 hover:text-gray-900 disabled:opacity-50 disabled:cursor-not-allowed"
          data-testid="pivot-table-button"
        >
          <Table2 className="h-3.5 w-3.5" strokeWidth={2.3} />
          Pivot
        </button>
        </div>

        {/* Highlight & Text formatting controls */}
        <div className="flex shrink-0 items-center gap-1.5">
          <div className="relative" ref={highlightMenuRef}>
            <button
              type="button"
              ref={highlightTriggerRef}
              onClick={(e) => {
                e.stopPropagation();
                setHighlightMenuOpen((prev) => !prev);
              }}
              disabled={!hasSelection}
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-600 transition hover:bg-gray-100 hover:text-gray-900 disabled:opacity-60"
              aria-haspopup="menu"
              aria-expanded={highlightMenuOpen}
              data-highlight-menu-trigger
              data-testid="highlight-button"
              title={hasSelection ? 'Highlight' : 'Select a cell/row/column first'}
            >
              <span
                className="inline-block h-3 w-3 rounded"
                style={{ backgroundColor: selectedHighlight }}
              />
            </button>
              {highlightMenuOpen && (
                <div
                  className="absolute left-0 mt-2 w-44 overflow-hidden rounded-lg bg-white shadow-lg ring-1 ring-gray-100 z-30"
                  role="menu"
                  data-highlight-menu
                >
                  <div className="h-[3px] w-full bg-gradient-to-r from-[#3CCED7] to-[#A6E661]" />
                  {HIGHLIGHT_COLORS.map((color) => (
                    <button
                      key={color.id}
                      type="button"
                      onClick={() => {
                        setSelectedHighlight(color.value);
                        applyHighlightToSelection(color.value, color.value);
                        setHighlightMenuOpen(false);
                      }}
                      className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-medium text-gray-700 transition hover:bg-gray-50"
                      role="menuitem"
                      data-testid={`highlight-color-${color.id}`}
                    >
                      <span className="inline-block h-3 w-3 rounded" style={{ backgroundColor: color.value }} />
                      {color.label}
                    </button>
                  ))}
                  <button
                    type="button"
                    onClick={() => {
                      applyHighlightToSelection(null, CLEAR_HIGHLIGHT);
                      setHighlightMenuOpen(false);
                    }}
                    className="w-full px-3 py-2 text-left text-xs font-medium text-gray-500 transition hover:bg-gray-50"
                    role="menuitem"
                    data-testid="highlight-clear"
                  >
                    Clear
                  </button>
                </div>
              )}
            </div>
          <div className="flex items-center gap-1 border-l border-gray-200 pl-3">
            <button
              type="button"
              onClick={() => applyFormatToSelection({ bold: !formatStateForSelection?.bold })}
              disabled={!hasSelection}
              title="Bold"
              className={`inline-flex h-8 w-8 items-center justify-center rounded-md transition disabled:opacity-60 ${
                formatStateForSelection?.bold
                  ? 'bg-[#3CCED7]/10 text-[#0E8A96] ring-1 ring-[#3CCED7]/30'
                  : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
              }`}
              data-testid="format-bold"
            >
              <Bold className="h-3.5 w-3.5" strokeWidth={2.3} />
            </button>
            <button
              type="button"
              onClick={() => applyFormatToSelection({ italic: !formatStateForSelection?.italic })}
              disabled={!hasSelection}
              title="Italic"
              className={`inline-flex h-8 w-8 items-center justify-center rounded-md transition disabled:opacity-60 ${
                formatStateForSelection?.italic
                  ? 'bg-[#3CCED7]/10 text-[#0E8A96] ring-1 ring-[#3CCED7]/30'
                  : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
              }`}
              data-testid="format-italic"
            >
              <Italic className="h-3.5 w-3.5" strokeWidth={2.3} />
            </button>
            <button
              type="button"
              onClick={() => applyFormatToSelection({ strikethrough: !formatStateForSelection?.strikethrough })}
              disabled={!hasSelection}
              title="Strikethrough"
              className={`inline-flex h-8 w-8 items-center justify-center rounded-md transition disabled:opacity-60 ${
                formatStateForSelection?.strikethrough
                  ? 'bg-[#3CCED7]/10 text-[#0E8A96] ring-1 ring-[#3CCED7]/30'
                  : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
              }`}
              data-testid="format-strikethrough"
            >
              <Strikethrough className="h-3.5 w-3.5" strokeWidth={2.3} />
            </button>
            <div className="relative" ref={textColorMenuRef}>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setTextColorMenuOpen((prev) => !prev);
                }}
                disabled={!hasSelection}
                title="Text color"
                data-text-color-trigger
                className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-600 transition hover:bg-gray-100 hover:text-gray-900 disabled:opacity-60"
                data-testid="format-text-color"
              >
                <Palette className="h-4 w-4" strokeWidth={2.5} style={selectedTextColor ? { color: selectedTextColor } : undefined} />
              </button>
              {textColorMenuOpen && (
                <div
                  className="absolute left-0 mt-2 w-44 overflow-hidden rounded-lg bg-white shadow-lg ring-1 ring-gray-100 z-30"
                  role="menu"
                  data-text-color-menu
                >
                  <div className="h-[3px] w-full bg-gradient-to-r from-[#3CCED7] to-[#A6E661]" />
                  <div className="grid grid-cols-4 gap-1.5 p-2">
                    {TEXT_COLORS.map((c) => (
                      <button
                        key={c.id}
                        type="button"
                        onClick={() => {
                          setSelectedTextColor(c.value);
                          applyFormatToSelection({ textColor: c.value });
                          setTextColorMenuOpen(false);
                        }}
                        className="h-6 w-6 rounded-md border border-gray-200 transition hover:scale-110 hover:ring-2 hover:ring-[#3CCED7]"
                        style={{ backgroundColor: c.value }}
                        title={c.label}
                      />
                    ))}
                  </div>
                  <div className="border-t border-gray-100 px-2 py-1.5">
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedTextColor(null);
                        applyFormatToSelection({ textColor: null });
                        setTextColorMenuOpen(false);
                      }}
                      className="w-full rounded-md px-2 py-1 text-left text-xs font-medium text-gray-500 transition hover:bg-gray-50"
                    >
                      Clear color
                    </button>
                  </div>
                </div>
              )}
            </div>
            <BrandSelect
              value={selectedFontFamily ?? ''}
              onValueChange={(v) => {
                const next = v || null;
                setSelectedFontFamily(next);
                applyFormatToSelection({ fontFamily: next });
              }}
              disabled={!hasSelection}
              ariaLabel="Font family"
              testId="format-font-family"
              widthClass="min-w-[7rem] max-w-[8rem]"
              renderValue={(val) => {
                const match = FONT_FAMILIES.find((f) => f.value === val);
                return match?.label ?? 'Default';
              }}
              options={FONT_FAMILIES.map((f) => ({
                value: f.value,
                label: f.label,
                style: f.value ? { fontFamily: f.value } : undefined,
              }))}
            />
            <BrandSelect
              value={String(selectedFontSize ?? CELL_FONT_SIZE)}
              onValueChange={(v) => {
                const n = parseInt(v, 10) || CELL_FONT_SIZE;
                setSelectedFontSize(n);
                applyFormatToSelection({ fontSize: n });
              }}
              disabled={!hasSelection}
              ariaLabel="Font size"
              testId="format-font-size"
              widthClass="w-16"
              renderValue={(val) => val}
              options={FONT_SIZES.map((s) => ({ value: String(s), label: String(s) }))}
            />
          </div>
          <div className="flex items-center gap-1 border-l border-gray-200 pl-2">
            <div className="relative">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setCurrencyMenuOpen((prev) => !prev);
                }}
                disabled={!hasSelection}
                title="Currency"
                data-format-currency-trigger
                className={`inline-flex h-8 w-8 items-center justify-center rounded-md transition disabled:opacity-60 text-base font-medium ${
                  selectedNumberFormat?.type === 'CURRENCY'
                    ? 'bg-[#3CCED7]/10 text-[#0E8A96] ring-1 ring-[#3CCED7]/30'
                    : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
                }`}
                data-testid="format-currency"
              >
                ¥
              </button>
              {currencyMenuOpen && (
                <div
                  className="absolute left-0 mt-1 w-32 overflow-hidden rounded-lg bg-white shadow-lg ring-1 ring-gray-100 z-30"
                  role="menu"
                  data-currency-menu
                >
                  <div className="h-[3px] w-full bg-gradient-to-r from-[#3CCED7] to-[#A6E661]" />
                  <div className="py-1">
                    <button
                      type="button"
                      onClick={() => {
                        const next = selectedNumberFormat ? { ...selectedNumberFormat, type: 'GENERAL' as const, currencyCode: null } : null;
                        setSelectedNumberFormat(next);
                        applyFormatToSelection({ numberFormat: next });
                        setCurrencyMenuOpen(false);
                      }}
                      className="w-full px-3 py-1.5 text-left text-xs font-medium text-gray-700 transition hover:bg-gray-50"
                      role="menuitem"
                    >
                      General
                    </button>
                    {CURRENCIES.map((c) => (
                      <button
                        key={c.code}
                        type="button"
                        onClick={() => {
                          const next: NumberFormat = {
                            type: 'CURRENCY',
                            currencyCode: c.code,
                            decimalPlaces: selectedNumberFormat?.decimalPlaces ?? 2,
                          };
                          setSelectedNumberFormat(next);
                          applyFormatToSelection({ numberFormat: next });
                          setCurrencyMenuOpen(false);
                        }}
                        className="w-full px-3 py-1.5 text-left text-xs font-medium text-gray-700 transition hover:bg-gray-50"
                        role="menuitem"
                      >
                        {c.label}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
            <button
              type="button"
              onClick={() => {
                const next: NumberFormat = {
                  type: 'PERCENT',
                  decimalPlaces: selectedNumberFormat?.decimalPlaces ?? 2,
                };
                setSelectedNumberFormat(next);
                applyFormatToSelection({ numberFormat: next });
              }}
              disabled={!hasSelection}
              title="Percent"
              className={`inline-flex h-8 w-8 items-center justify-center rounded-md transition disabled:opacity-60 ${
                selectedNumberFormat?.type === 'PERCENT'
                  ? 'bg-[#3CCED7]/10 text-[#0E8A96] ring-1 ring-[#3CCED7]/30 font-semibold'
                  : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
              }`}
              data-testid="format-percent"
            >
              %
            </button>
            <button
              type="button"
              onClick={() => {
                const dp = Math.max(0, (selectedNumberFormat?.decimalPlaces ?? 2) - 1);
                const next: NumberFormat = selectedNumberFormat
                  ? { ...selectedNumberFormat, decimalPlaces: dp }
                  : { type: 'NUMBER', decimalPlaces: dp };
                setSelectedNumberFormat(next);
                applyFormatToSelection({ numberFormat: next });
              }}
              disabled={!hasSelection}
              title="Decrease decimals"
              className="inline-flex h-8 w-8 flex-col items-center justify-center rounded-md text-gray-600 transition hover:bg-gray-100 hover:text-gray-900 disabled:opacity-60 pt-0.5"
              data-testid="format-decimal-decrease"
            >
              <span className="text-xs font-medium leading-tight">.0</span>
              <ChevronLeft className="h-3 w-3 -mt-0.5" strokeWidth={2.5} />
            </button>
            <button
              type="button"
              onClick={() => {
                const dp = Math.min(10, (selectedNumberFormat?.decimalPlaces ?? 2) + 1);
                const next: NumberFormat = selectedNumberFormat
                  ? { ...selectedNumberFormat, decimalPlaces: dp }
                  : { type: 'NUMBER', decimalPlaces: dp };
                setSelectedNumberFormat(next);
                applyFormatToSelection({ numberFormat: next });
              }}
              disabled={!hasSelection}
              title="Increase decimals"
              className="inline-flex h-8 w-8 flex-col items-center justify-center rounded-md text-gray-600 transition hover:bg-gray-100 hover:text-gray-900 disabled:opacity-60 pt-0.5"
              data-testid="format-decimal-increase"
            >
              <span className="text-xs font-medium leading-tight">.00</span>
              <ChevronRight className="h-3 w-3 -mt-0.5" strokeWidth={2.5} />
            </button>
          </div>
        </div>
      </div>

      {headerMenu &&
        createPortal(
          <div
            className="fixed z-[1000] min-w-[180px] rounded-md border border-gray-200 bg-white shadow-lg"
            style={{ top: headerMenu.y, left: headerMenu.x }}
            data-header-context-menu
            role="menu"
          >
            {headerMenu.type === 'row' ? (
              <>
                <button
                  type="button"
                  onClick={() => {
                    const position = headerMenu.index;
                    setHeaderMenu(null);
                    onInsertRowCommit?.({ index: headerMenu.index + 1, position: 'above' });
                    void handleInsertRow(position, 1);
                  }}
                  className="w-full px-3 py-2 text-left text-xs font-semibold text-gray-700 hover:bg-gray-50"
                  role="menuitem"
                >
                  Insert row above
                </button>
                <button
                  type="button"
                  onClick={() => {
                    const position = headerMenu.index + 1;
                    setHeaderMenu(null);
                    onInsertRowCommit?.({ index: headerMenu.index + 1, position: 'below' });
                    void handleInsertRow(position, 1);
                  }}
                  className="w-full px-3 py-2 text-left text-xs font-semibold text-gray-700 hover:bg-gray-50"
                  role="menuitem"
                >
                  Insert row below
                </button>
                <button
                  type="button"
                  onClick={() => {
                    const position = headerMenu.index;
                    setHeaderMenu(null);
                    void handleDeleteRow(position, 1);
                  }}
                  className="w-full px-3 py-2 text-left text-xs font-semibold text-red-600 hover:bg-red-50"
                  role="menuitem"
                >
                  Delete row
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => {
                    const position = headerMenu.index;
                    setHeaderMenu(null);
                    onInsertColumnCommit?.({ index: headerMenu.index + 1, position: 'left' });
                    void handleInsertColumn(position, 1);
                  }}
                  className="w-full px-3 py-2 text-left text-xs font-semibold text-gray-700 hover:bg-gray-50"
                  role="menuitem"
                >
                  Insert column left
                </button>
                <button
                  type="button"
                  onClick={() => {
                    const position = headerMenu.index + 1;
                    setHeaderMenu(null);
                    onInsertColumnCommit?.({ index: headerMenu.index + 1, position: 'right' });
                    void handleInsertColumn(position, 1);
                  }}
                  className="w-full px-3 py-2 text-left text-xs font-semibold text-gray-700 hover:bg-gray-50"
                  role="menuitem"
                >
                  Insert column right
                </button>
                <button
                  type="button"
                  onClick={() => {
                    const position = headerMenu.index;
                    setHeaderMenu(null);
                    onDeleteColumnCommit?.({ index: headerMenu.index + 1 });
                    void handleDeleteColumn(position, 1);
                  }}
                  className="w-full px-3 py-2 text-left text-xs font-semibold text-red-600 hover:bg-red-50"
                  role="menuitem"
                >
                  Delete column
                </button>
              </>
            )}
          </div>,
          document.body
        )}

      {sortMenu &&
        createPortal(
          <div
            className="fixed z-[1000] min-w-[200px] rounded-md border border-gray-200 bg-white shadow-lg py-1"
            style={{ top: sortMenu.y, left: sortMenu.x }}
            data-sort-menu
            role="menu"
          >
            <div className="px-3 py-2 border-b border-gray-100">
              <div className="mb-1 flex items-center justify-between text-[11px] font-semibold text-gray-600">
                <span>Filter</span>
                {hasColumnFilter(sortMenu.colIndex) ? <Check className="h-3 w-3 text-[#3CCED7]" /> : null}
              </div>
              <input
                type="text"
                value={getActiveFilterExpression(sortMenu.colIndex)}
                onChange={(e) => handleColumnFilterChange(sortMenu.colIndex, e.target.value)}
                placeholder="e.g. >= 7, = abc"
                className="w-full rounded border border-gray-200 px-2 py-1 text-xs text-gray-900 focus:border-[#3CCED7] focus:outline-none"
                aria-label={`Filter column ${columnIndexToLabel(sortMenu.colIndex)}`}
              />
            </div>
            <button
              type="button"
              onClick={() => void handleColumnSort(sortMenu.colIndex, 'asc')}
              disabled={isSorting}
              className="flex w-full items-center justify-between px-3 py-2 text-left text-xs font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-60"
              role="menuitem"
            >
              <span>Sort A → Z</span>
              {getActiveSortDirection(sortMenu.colIndex) === 'asc' ? <Check className="h-3.5 w-3.5 text-[#3CCED7]" /> : null}
            </button>
            <button
              type="button"
              onClick={() => void handleColumnSort(sortMenu.colIndex, 'desc')}
              disabled={isSorting}
              className="flex w-full items-center justify-between px-3 py-2 text-left text-xs font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-60"
              role="menuitem"
            >
              <span>Sort Z → A</span>
              {getActiveSortDirection(sortMenu.colIndex) === 'desc' ? <Check className="h-3.5 w-3.5 text-[#3CCED7]" /> : null}
            </button>
          </div>,
          document.body
        )}

      <div className="flex min-w-0 items-center gap-2 px-2 py-2 border-b border-gray-200 bg-white">
        <span className="text-xs font-semibold text-gray-500">fx</span>
        <input
          type="text"
          value={formulaBarValue}
          placeholder="Enter value or formula"
          onFocus={() => {
            setIsFormulaBarEditing(true);
            if (!formulaBarTarget && activeCell) {
              setFormulaBarTarget(getCellKey(activeCell.row, activeCell.col));
            }
          }}
          onChange={handleFormulaBarChange}
          onBlur={handleFormulaBarCommit}
          onKeyDown={(e) => {
            e.stopPropagation();
            handleFormulaBarKeyDown(e);
          }}
          className="w-full rounded border border-gray-200 px-2 py-1 text-sm text-gray-900 focus:border-[#3CCED7] focus:outline-none"
          disabled={!activeCell}
        />
      </div>

      {sheetsImportModalOpen && (
        <Modal isOpen={true} onClose={() => { setSheetsImportModalOpen(false); setSheetsImportUrl(''); }}>
          <div className="w-[min(420px,calc(100vw-2rem))]">
            <div className="rounded-2xl bg-white shadow-2xl ring-1 ring-gray-100">
              <div className="px-6 pt-6 pb-4 border-b border-gray-100">
                <h2 className="text-lg font-semibold text-gray-900">Import from Google Sheets</h2>
                <p className="mt-1 text-sm text-gray-500">Paste the Google Sheets URL or spreadsheet ID</p>
              </div>
              <div className="px-6 py-5 flex flex-col gap-4">
                <input
                  type="text"
                  value={sheetsImportUrl}
                  onChange={(e) => setSheetsImportUrl(e.target.value)}
                  placeholder="https://docs.google.com/spreadsheets/d/..."
                  className="w-full rounded border border-gray-200 px-3 py-2 text-sm text-gray-900 focus:border-[#3CCED7] focus:outline-none"
                  disabled={sheetsImportLoading}
                  onKeyDown={(e) => { if (e.key === 'Enter') handleImportGoogleSheets(); }}
                  autoFocus
                />
                <div className="flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => { setSheetsImportModalOpen(false); setSheetsImportUrl(''); }}
                    disabled={sheetsImportLoading}
                    className="rounded px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-100 disabled:opacity-60"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={handleImportGoogleSheets}
                    disabled={sheetsImportLoading || !sheetsImportUrl.trim()}
                    className="rounded bg-green-600 px-4 py-2 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-60"
                  >
                    {sheetsImportLoading ? 'Importing...' : 'Import'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </Modal>
      )}

      {xlsxImport && (
        <Modal isOpen={true} onClose={handleCancelXlsxImport}>
          <div className="w-[min(420px,calc(100vw-2rem))]">
            <div className="rounded-2xl bg-white shadow-2xl ring-1 ring-gray-100">
              <div className="px-6 pt-6 pb-4 border-b border-gray-100">
                <h2 className="text-lg font-semibold text-gray-900">Select Worksheet</h2>
                <p className="text-sm text-gray-600">
                  Choose a worksheet to import into the current sheet.
                </p>
              </div>
              <div className="p-6 space-y-4">
                <select
                  value={selectedXlsxSheet}
                  onChange={(e) => setSelectedXlsxSheet(e.target.value)}
                  className="w-full rounded border border-gray-300 px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-[#3CCED7]"
                  disabled={isImporting}
                >
                  {xlsxImport.sheetNames.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
                <div className="flex items-center justify-end gap-2">
                  <button
                    type="button"
                    onClick={handleCancelXlsxImport}
                    className="rounded border border-gray-200 px-3 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-50"
                  >
                    {isImporting ? 'Cancel import' : 'Cancel'}
                  </button>
                  <button
                    type="button"
                    onClick={handleConfirmXlsxImport}
                    className="rounded bg-[#3CCED7] px-3 py-1 text-xs font-semibold text-white hover:bg-[#2AB5BD] disabled:opacity-60"
                    disabled={isImporting || !selectedXlsxSheet}
                  >
                    Import
                  </button>
                </div>
              </div>
            </div>
          </div>
        </Modal>
      )}

      {/* Scrollable Grid Container: only this div scrolls (page/body do not). min-h-0/min-w-0 so flex gives stable size; ResizeObserver on gridRef updates visible range on resize. */}
      <div
        ref={gridRef}
        className="flex-1 min-h-0 min-w-0 border border-gray-300 bg-white spreadsheet-scroll-container"
        style={{
          overflowX: 'auto',
          overflowY: 'auto',
          position: 'relative',
        }}
        onScroll={isGridLoading ? undefined : handleScroll}
        onKeyDown={showGridSpinner ? undefined : handleKeyDown}
        onCopy={showGridSpinner ? undefined : handleCopy}
        onPaste={showGridSpinner ? undefined : handlePaste}
        tabIndex={showGridSpinner ? -1 : 0}
        aria-busy={showGridSpinner}
      >
        {showGridSpinner ? (
          <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center">
            <div className="flex h-10 w-10 items-center justify-center rounded-full border border-gray-200 bg-white/95 shadow-sm backdrop-blur-sm">
              <Loader2 className="h-4 w-4 animate-spin text-[#0E8A96]" />
            </div>
          </div>
        ) : null}
        {/* Column headers in separate table to avoid thead/tbody gap with sticky. */}
        <div className="sticky top-0 z-10 shrink-0 bg-gray-200">
          <table
            className="border-collapse"
            style={{
              tableLayout: 'fixed',
              width: `${ROW_NUMBER_WIDTH + totalColumnWidth}px`,
            }}
          >
            <colgroup>
              <col style={{ width: `${ROW_NUMBER_WIDTH}px`, minWidth: `${ROW_NUMBER_WIDTH}px` }} />
              <col style={{ width: `${leftSpacerWidth}px`, minWidth: `${leftSpacerWidth}px` }} />
              {Array.from({ length: visibleColCount }).map((_, colIndex) => (
                <col
                  key={colIndex}
                  data-col-index={visibleStartCol + colIndex}
                  data-testid={`col-width-${visibleStartCol + colIndex}`}
                  style={{
                    width: `${getColumnWidth(visibleStartCol + colIndex)}px`,
                    minWidth: `${getColumnWidth(visibleStartCol + colIndex)}px`,
                  }}
                />
              ))}
              <col style={{ width: `${rightSpacerWidth}px`, minWidth: `${rightSpacerWidth}px` }} />
            </colgroup>
            <thead className="bg-gray-200">
            <tr>
              <th
                className="border border-gray-300 bg-gray-200 text-xs font-semibold text-gray-600 text-center sticky left-0 z-20"
                style={headerCellStyle}
                onMouseDown={(e) => e.preventDefault()}
                onClick={handleSelectAll}
                data-testid="select-all-cell"
              >
                {/* Empty corner cell */}
              </th>
              <th
                className="border border-gray-300 bg-gray-200 p-0"
                style={{ width: `${leftSpacerWidth}px`, ...headerCellStyle }}
              />
              {Array.from({ length: visibleColCount }).map((_, colOffset) => {
                const colIndex = visibleStartCol + colOffset;
                const colWidth = getColumnWidth(colIndex);
                return (
                <th
                  key={colIndex}
                  className={`border border-gray-300 text-xs font-semibold text-gray-600 text-center relative overflow-visible ${
                    isColumnHeaderSelected(colIndex) ? 'bg-[#3CCED7]/15' : 'bg-gray-200'
                  }`}
                  style={{ width: `${colWidth}px`, minWidth: `${colWidth}px`, ...headerCellStyle }}
                  onClick={() => handleColumnHeaderClick(colIndex)}
                  data-testid={`col-header-${colIndex}`}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    selectColumn(colIndex);
                    openHeaderMenu('col', colIndex, e.clientX, e.clientY);
                  }}
                >
                  <div className="flex items-center justify-center gap-0.5">
                    <span>{columnIndexToLabel(colIndex)}</span>
                    <button
                      type="button"
                      onClick={(e) => openSortMenu(colIndex, e)}
                      className="relative p-0.5 rounded hover:bg-gray-300/80 text-gray-500 hover:text-gray-700"
                      aria-label={`Sort column ${columnIndexToLabel(colIndex)}`}
                      data-testid={`col-sort-${colIndex}`}
                      data-col-sort-trigger
                    >
                      <ChevronDown className="h-3 w-3" />
                      {hasColumnFilter(colIndex) ? (
                        <Check className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full bg-white text-[#3CCED7]" />
                      ) : null}
                    </button>
                  </div>
                  <div
                    data-testid={`col-resize-handle-${colIndex}`}
                    className="absolute top-0"
                    style={{
                      right: `-${RESIZE_HANDLE_SIZE / 2}px`,
                      width: `${RESIZE_HANDLE_SIZE}px`,
                      height: '100%',
                      cursor: 'col-resize',
                    }}
                    onPointerDown={(e) => startResize(e, 'col', colIndex)}
                    onPointerMove={handleResizePointerMove}
                    onPointerUp={handleResizePointerUp}
                    onPointerCancel={handleResizePointerUp}
                  />
                </th>
                );
              })}
              <th
                className="border border-gray-300 bg-gray-200 p-0"
                style={{ width: `${rightSpacerWidth}px`, ...headerCellStyle }}
              />
            </tr>
          </thead>
          </table>
        </div>

        {/* Body table: same colgroup for column alignment. */}
        <table
          className="border-collapse"
          style={{
            tableLayout: 'fixed',
            width: `${ROW_NUMBER_WIDTH + totalColumnWidth}px`,
            height: `${bodyTableHeight}px`,
            minHeight: `${bodyTableHeight}px`,
            //marginTop: '1px',
          }}
        >
          <colgroup>
            <col style={{ width: `${ROW_NUMBER_WIDTH}px`, minWidth: `${ROW_NUMBER_WIDTH}px` }} />
            <col style={{ width: `${leftSpacerWidth}px`, minWidth: `${leftSpacerWidth}px` }} />
            {Array.from({ length: visibleColCount }).map((_, colIndex) => (
              <col
                key={colIndex}
                data-col-index={visibleStartCol + colIndex}
                data-testid={`col-width-body-${visibleStartCol + colIndex}`}
                style={{
                  width: `${getColumnWidth(visibleStartCol + colIndex)}px`,
                  minWidth: `${getColumnWidth(visibleStartCol + colIndex)}px`,
                }}
              />
            ))}
            <col style={{ width: `${rightSpacerWidth}px`, minWidth: `${rightSpacerWidth}px` }} />
          </colgroup>
          <tbody>
            {/* Frozen rows: always rendered so they remain sticky even when scrolled far down. */}
            {Array.from({ length: frozenRows }).map((_, rowIndex) => {
              const row = rowIndex; // 0-based for API
              const rowHeight = getRowHeight(row);
              const rowBaseStyle = getCellBaseStyle(rowHeight);
              const frozen = isFrozenRow(row);
              const frozenStickyStyle: React.CSSProperties = frozen
                ? {
                    position: 'sticky',
                    top: `${getFrozenRowStickyTop(row)}px`,
                    zIndex: 15,
                    backgroundColor: isRowHeaderSelected(row) ? 'rgb(191 219 254)' : 'rgb(243 244 246)',
                  }
                : {};
              const frozenDataStickyStyle: React.CSSProperties = frozen
                ? {
                    position: 'sticky',
                    top: `${getFrozenRowStickyTop(row)}px`,
                    zIndex: 14,
                  }
                : {};
              return (
                <tr key={row}>
                  {/* Row Number */}
                  <td
                    className={`border border-gray-300 text-xs font-semibold text-gray-600 text-center sticky left-0 z-10 relative overflow-visible ${
                      isRowHeaderSelected(row) ? 'bg-[#3CCED7]/15' : 'bg-gray-100'
                    }`}
                    style={{ ...rowBaseStyle, ...frozenStickyStyle }}
                    data-testid={`row-header-${row}`}
                    onClick={() => handleRowHeaderClick(row)}
                    onContextMenu={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      selectRow(row);
                      openHeaderMenu('row', row, e.clientX, e.clientY);
                    }}
                  >
                    {row + 1}
                    <div
                      data-testid={`row-resize-handle-${row}`}
                      className="absolute left-0"
                      style={{
                        bottom: `-${RESIZE_HANDLE_SIZE / 2}px`,
                        width: '100%',
                        height: `${RESIZE_HANDLE_SIZE}px`,
                        cursor: 'row-resize',
                      }}
                      onPointerDown={(e) => startResize(e, 'row', row)}
                      onPointerMove={handleResizePointerMove}
                      onPointerUp={handleResizePointerUp}
                      onPointerCancel={handleResizePointerUp}
                    />
                  </td>

                  {/* Left spacer */}
                  <td
                    className="border border-gray-300 p-0"
                    style={{
                      width: `${leftSpacerWidth}px`,
                      ...rowBaseStyle,
                      ...(frozen ? { ...frozenDataStickyStyle, backgroundColor: 'white' } : {}),
                    }}
                  />

                  {/* Data Cells */}
                  {Array.from({ length: visibleColCount }).map((_, colOffset) => {
                    const col = visibleStartCol + colOffset; // 0-based for API
                    const colWidth = getColumnWidth(col);
                    const key = getCellKey(row, col);
                    const isActive = activeCell && activeCell.row === row && activeCell.col === col;
                    const isHighlighted = isAnomalyHighlighted(row, col);
                    const isInSelection = isCellInSelection(row, col);
                    const isEditing = editingCell === key;
                    const showFillHandle = Boolean(
                      isActive && isSingleCellSelection && !isEditing && !isFilling
                    );
                    const displayValue = isEditing ? editValue : getCellDisplayValue(row, col);
                    const sparkline = isEditing ? null : getCellSparkline(row, col);
                    const highlightColor = getHighlightColor(row, col);
                    const hasHighlight = Boolean(highlightColor);
                    const remotePresence = resolveRemoteCellPresence(remotePresenceUsers, row, col);
                    
                    // Determine cell styling based on selection state
                    let cellClassName = 'border border-gray-300 p-0 relative align-top';
                    if (isEditing) {
                      cellClassName += ' ring-2 ring-[#3CCED7] ring-inset';
                    } else if (isActive && isInSelection) {
                      // Active cell within selection: thicker border
                      cellClassName += ' ring-2 ring-[#3CCED7] ring-inset';
                      if (!hasHighlight) {
                        cellClassName += ' bg-[#3CCED7]/10';
                      }
                    } else if (isActive) {
                      // Active cell without selection
                      cellClassName += ' ring-2 ring-[#3CCED7] ring-inset';
                    } else if (isInSelection) {
                      // Cell in selection range (but not active)
                      if (!hasHighlight) {
                        cellClassName += ' bg-[#3CCED7]/15';
                      }
                    }
                    if (isCellInFillPreview(row, col)) {
                      if (!hasHighlight) {
                        cellClassName += ' bg-[#3CCED7]/10';
                      }
                    }
                    if (isHighlighted) {
                      cellClassName += ' ring-2 ring-amber-400 ring-inset bg-amber-50';
                    }

                    return (
                      <td
                        key={`${row}-${col}`}
                        className={cellClassName}
                        onMouseDown={(e) => handleCellMouseDown(e, row, col)}
                        onDoubleClick={() => handleCellDoubleClick(row, col)}
                        style={{
                          width: `${colWidth}px`,
                          minWidth: `${colWidth}px`,
                          ...rowBaseStyle,
                          ...(frozen
                            ? {
                                ...frozenDataStickyStyle,
                                backgroundColor:
                                  highlightColor ??
                                  (isInSelection
                                    ? isActive
                                      ? 'rgb(239 246 255)'
                                      : 'rgb(219 234 254)'
                                    : 'white'),
                              }
                            : {}),
                          ...(!frozen && highlightColor ? { backgroundColor: highlightColor } : {}),
                          ...(remotePresence.selectionOwner
                            ? {
                                backgroundImage: `linear-gradient(${remotePresence.selectionOwner.color}1f, ${remotePresence.selectionOwner.color}1f)`,
                              }
                            : {}),
                        }}
                        data-row={row}
                        data-col={col}
                      >
                        {isEditing ? (
                          <input
                            ref={inputRef}
                            type="text"
                            value={editValue}
                            onChange={(e) => setEditValue(e.target.value)}
                            onBlur={handleInputBlur}
                            // Stop propagation so grid-level handlers never see
                            // key events while editing. The input's own handler
                            // (handleInputKeyDown) takes care of Enter/Escape.
                            onKeyDown={(e) => {
                              e.stopPropagation();
                              handleInputKeyDown(e);
                            }}
                            className="w-full"
                            style={{ width: `${colWidth}px`, minWidth: `${colWidth}px`, ...getCellInputStyle(rowHeight) }}
                          />
                        ) : (
                          <div
                            className="text-gray-900"
                            style={{
                              ...getCellContentStyle(rowHeight),
                              fontWeight: getCellFormat(row, col).bold ? 700 : undefined,
                              fontStyle: getCellFormat(row, col).italic ? 'italic' : undefined,
                              textDecoration: getCellFormat(row, col).strikethrough ? 'line-through' : undefined,
                              color: getCellFormat(row, col).textColor ?? undefined,
                              fontFamily: getCellFormat(row, col).fontFamily ?? undefined,
                              fontSize: getCellFormat(row, col).fontSize != null ? `${getCellFormat(row, col).fontSize}px` : undefined,
                            }}
                          >
                            {sparkline ? (
                              <SparklineCell payload={sparkline} width={colWidth} height={rowHeight} />
                            ) : (
                              displayValue
                            )}
                          </div>
                        )}
                        {remotePresence.cursors.map((user) => (
                          <div
                            key={`${user.userId}:${user.clientId}`}
                            data-testid="sheet-remote-cursor"
                            data-user-id={user.userId}
                            data-client-id={user.clientId}
                            data-row={row}
                            data-col={col}
                            aria-label={`${user.username} cursor`}
                            title={user.username}
                            className="pointer-events-none absolute inset-0 z-[5]"
                            style={{ boxShadow: `inset 0 0 0 2px ${user.color}` }}
                          />
                        ))}
                        {showFillHandle && (
                          <div
                            className="absolute bottom-0 right-0 h-2 w-2 bg-[#3CCED7] border border-white cursor-crosshair"
                            onPointerDown={(e) => handleFillHandlePointerDown(e, row, col)}
                          />
                        )}
                      </td>
                    );
                  })}

                  {/* Right spacer */}
                  <td
                    className="border border-gray-300 p-0"
                    style={{
                      width: `${rightSpacerWidth}px`,
                      ...rowBaseStyle,
                      ...(frozen ? { ...frozenDataStickyStyle, backgroundColor: 'white' } : {}),
                    }}
                  />
                </tr>
              );
            })}

            {/* Spacer for non-frozen rows that are scrolled out above the current viewport */}
            {topSpacerHeight > 0 && (
              <tr>
                <td
                  colSpan={totalColumns}
                  style={{ height: `${topSpacerHeight}px` }}
                />
              </tr>
            )}

            {/* Non-frozen rows: virtualized by viewport unless filters are active */}
            {renderedNonFrozenRows.map((row) => {
              const rowHeight = getRowHeight(row);
              const rowBaseStyle = getCellBaseStyle(rowHeight);
              const frozen = isFrozenRow(row);
              const frozenStickyStyle: React.CSSProperties = frozen
                ? {
                    position: 'sticky',
                    top: `${getFrozenRowStickyTop(row)}px`,
                    zIndex: 15,
                    backgroundColor: isRowHeaderSelected(row) ? 'rgb(191 219 254)' : 'rgb(243 244 246)',
                  }
                : {};
              const frozenDataStickyStyle: React.CSSProperties = frozen
                ? {
                    position: 'sticky',
                    top: `${getFrozenRowStickyTop(row)}px`,
                    zIndex: 14,
                  }
                : {};
              return (
                <tr key={row}>
                  {/* Row Number */}
                  <td
                    className={`border border-gray-300 text-xs font-semibold text-gray-600 text-center sticky left-0 z-10 relative overflow-visible ${
                      isRowHeaderSelected(row) ? 'bg-[#3CCED7]/15' : 'bg-gray-100'
                    }`}
                    style={{ ...rowBaseStyle, ...frozenStickyStyle }}
                    data-testid={`row-header-${row}`}
                    onClick={() => handleRowHeaderClick(row)}
                    onContextMenu={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      selectRow(row);
                      openHeaderMenu('row', row, e.clientX, e.clientY);
                    }}
                  >
                    {row + 1}
                    <div
                      data-testid={`row-resize-handle-${row}`}
                      className="absolute left-0"
                      style={{
                        bottom: `-${RESIZE_HANDLE_SIZE / 2}px`,
                        width: '100%',
                        height: `${RESIZE_HANDLE_SIZE}px`,
                        cursor: 'row-resize',
                      }}
                      onPointerDown={(e) => startResize(e, 'row', row)}
                      onPointerMove={handleResizePointerMove}
                      onPointerUp={handleResizePointerUp}
                      onPointerCancel={handleResizePointerUp}
                    />
                  </td>

                  {/* Left spacer */}
                  <td
                    className="border border-gray-300 p-0"
                    style={{
                      width: `${leftSpacerWidth}px`,
                      ...rowBaseStyle,
                      ...(frozen ? { ...frozenDataStickyStyle, backgroundColor: 'white' } : {}),
                    }}
                  />

                  {/* Data Cells */}
                  {Array.from({ length: visibleColCount }).map((_, colOffset) => {
                    const col = visibleStartCol + colOffset; // 0-based for API
                    const colWidth = getColumnWidth(col);
                    const key = getCellKey(row, col);
                    const isActive = activeCell && activeCell.row === row && activeCell.col === col;
                    const isHighlighted = isAnomalyHighlighted(row, col);
                    const isInSelection = isCellInSelection(row, col);
                    const isEditing = editingCell === key;
                    const showFillHandle = Boolean(
                      isActive && isSingleCellSelection && !isEditing && !isFilling
                    );
                    const displayValue = isEditing ? editValue : getCellDisplayValue(row, col);
                    const sparkline = isEditing ? null : getCellSparkline(row, col);
                    const highlightColor = getHighlightColor(row, col);
                    const hasHighlight = Boolean(highlightColor);
                    const remotePresence = resolveRemoteCellPresence(remotePresenceUsers, row, col);
                    
                    // Determine cell styling based on selection state
                    let cellClassName = 'border border-gray-300 p-0 relative align-top';
                    if (isEditing) {
                      cellClassName += ' ring-2 ring-[#3CCED7] ring-inset';
                    } else if (isActive && isInSelection) {
                      // Active cell within selection: thicker border
                      cellClassName += ' ring-2 ring-[#3CCED7] ring-inset';
                      if (!hasHighlight) {
                        cellClassName += ' bg-[#3CCED7]/10';
                      }
                    } else if (isActive) {
                      // Active cell without selection
                      cellClassName += ' ring-2 ring-[#3CCED7] ring-inset';
                    } else if (isInSelection) {
                      // Cell in selection range (but not active)
                      if (!hasHighlight) {
                        cellClassName += ' bg-[#3CCED7]/15';
                      }
                    }
                    if (isCellInFillPreview(row, col)) {
                      if (!hasHighlight) {
                        cellClassName += ' bg-[#3CCED7]/10';
                      }
                    }
                    if (isHighlighted) {
                      cellClassName += ' ring-2 ring-amber-400 ring-inset bg-amber-50';
                    }

                    return (
                      <td
                        key={`${row}-${col}`}
                        className={cellClassName}
                        onMouseDown={(e) => handleCellMouseDown(e, row, col)}
                        onDoubleClick={() => handleCellDoubleClick(row, col)}
                        style={{
                          width: `${colWidth}px`,
                          minWidth: `${colWidth}px`,
                          ...rowBaseStyle,
                          ...(frozen
                            ? {
                                ...frozenDataStickyStyle,
                                backgroundColor:
                                  highlightColor ??
                                  (isInSelection
                                    ? isActive
                                      ? 'rgb(239 246 255)'
                                      : 'rgb(219 234 254)'
                                    : 'white'),
                              }
                            : {}),
                          ...(!frozen && highlightColor ? { backgroundColor: highlightColor } : {}),
                          ...(remotePresence.selectionOwner
                            ? {
                                backgroundImage: `linear-gradient(${remotePresence.selectionOwner.color}1f, ${remotePresence.selectionOwner.color}1f)`,
                              }
                            : {}),
                        }}
                        data-row={row}
                        data-col={col}
                      >
                        {isEditing ? (
                          <input
                            ref={inputRef}
                            type="text"
                            value={editValue}
                            onChange={(e) => setEditValue(e.target.value)}
                            onBlur={handleInputBlur}
                            // Stop propagation so grid-level handlers never see
                            // key events while editing. The input's own handler
                            // (handleInputKeyDown) takes care of Enter/Escape.
                            onKeyDown={(e) => {
                              e.stopPropagation();
                              handleInputKeyDown(e);
                            }}
                            className="w-full"
                            style={{ width: `${colWidth}px`, minWidth: `${colWidth}px`, ...getCellInputStyle(rowHeight) }}
                          />
                        ) : (
                          <div
                            className="text-gray-900"
                            style={{
                              ...getCellContentStyle(rowHeight),
                              fontWeight: getCellFormat(row, col).bold ? 700 : undefined,
                              fontStyle: getCellFormat(row, col).italic ? 'italic' : undefined,
                              textDecoration: getCellFormat(row, col).strikethrough ? 'line-through' : undefined,
                              color: getCellFormat(row, col).textColor ?? undefined,
                              fontFamily: getCellFormat(row, col).fontFamily ?? undefined,
                              fontSize: getCellFormat(row, col).fontSize != null ? `${getCellFormat(row, col).fontSize}px` : undefined,
                            }}
                          >
                            {sparkline ? (
                              <SparklineCell payload={sparkline} width={colWidth} height={rowHeight} />
                            ) : (
                              displayValue
                            )}
                          </div>
                        )}
                        {remotePresence.cursors.map((user) => (
                          <div
                            key={`${user.userId}:${user.clientId}`}
                            data-testid="sheet-remote-cursor"
                            data-user-id={user.userId}
                            data-client-id={user.clientId}
                            data-row={row}
                            data-col={col}
                            aria-label={`${user.username} cursor`}
                            title={user.username}
                            className="pointer-events-none absolute inset-0 z-[5]"
                            style={{ boxShadow: `inset 0 0 0 2px ${user.color}` }}
                          />
                        ))}
                        {showFillHandle && (
                          <div
                            className="absolute bottom-0 right-0 h-2 w-2 bg-[#3CCED7] border border-white cursor-crosshair"
                            onPointerDown={(e) => handleFillHandlePointerDown(e, row, col)}
                          />
                        )}
                      </td>
                    );
                  })}

                  {/* Right spacer */}
                  <td
                    className="border border-gray-300 p-0"
                    style={{
                      width: `${rightSpacerWidth}px`,
                      ...rowBaseStyle,
                      ...(frozen ? { ...frozenDataStickyStyle, backgroundColor: 'white' } : {}),
                    }}
                  />
                </tr>
              );
            })}

            {bottomSpacerHeight > 0 && (
              <tr>
                <td
                  colSpan={totalColumns}
                  style={{ height: `${bottomSpacerHeight}px` }}
                />
              </tr>
            )}
          </tbody>
        </table>

        {/* Add Rows UI - shown when near bottom of grid */}
        {showAddRowsUI && rowCount < MAX_ROWS && (
          <div className="sticky bottom-0 left-0 right-0 z-20 bg-white border-t border-gray-300 px-4 py-3 shadow-lg">
            <div className="flex items-center gap-3 max-w-md mx-auto">
              <span className="text-sm text-gray-700">Add rows:</span>
              <input
                type="number"
                min="1"
                max={MAX_ROWS - rowCount}
                value={addRowsInputValue}
                onChange={(e) => setAddRowsInputValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    handleAddRows();
                  } else if (e.key === 'Escape') {
                    setShowAddRowsUI(false);
                  }
                }}
                className="w-24 rounded border border-gray-300 px-2 py-1 text-sm text-gray-900 focus:border-[#3CCED7] focus:outline-none"
                autoFocus
              />
              <button
                type="button"
                onClick={handleAddRows}
                className="rounded bg-[#3CCED7] px-3 py-1 text-sm font-semibold text-white hover:bg-[#2AB5BD]"
              >
                Add
              </button>
              <button
                type="button"
                onClick={() => {
                  setShowAddRowsUI(false);
                  setAddRowsInputValue('1000');
                }}
                className="rounded border border-gray-300 px-3 py-1 text-sm font-semibold text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
              <span className="text-xs text-gray-500 ml-auto">
                {rowCount.toLocaleString()} / {MAX_ROWS.toLocaleString()} rows
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
});

SpreadsheetGrid.displayName = 'SpreadsheetGrid';

export default SpreadsheetGrid;
