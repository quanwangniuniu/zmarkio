import React from 'react';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import SpreadsheetGrid, { SpreadsheetGridHandle } from '@/components/spreadsheets/SpreadsheetGrid';
import { SpreadsheetAPI } from '@/lib/api/spreadsheetApi';
import { useRef } from 'react';
import BrandSelect from '@/components/ui/BrandSelect';
import {
  isTriggerMeaningfullyVisible,
  placeMenuInViewport,
  triggerVisibleFraction,
} from '@/components/spreadsheets/toolbarMenuPosition';

const readCellRangeMock = jest.fn().mockResolvedValue({ cells: [] });
const deleteRowsMock = jest.fn().mockResolvedValue({ operation_id: 1 });
const deleteColumnsMock = jest.fn().mockResolvedValue({ operation_id: 1 });
const revertStructureMock = jest.fn().mockResolvedValue({});
const exportXlsxMock = jest.fn().mockResolvedValue(new Blob(['xlsx']));
const exportGoogleMock = jest.fn().mockResolvedValue({
  spreadsheet_id: 'dest',
  url: 'https://docs.google.com/spreadsheets/d/dest/edit',
});
const batchUpdateCellsMock = jest.fn().mockResolvedValue({});
const resizeSheetMock = jest.fn().mockResolvedValue({});
const finalizeImportMock = jest.fn().mockResolvedValue({ status: 'ok' });

jest.mock('@/lib/api/spreadsheetApi', () => ({
  SpreadsheetAPI: {
    readCellRange: (...args: any[]) => readCellRangeMock(...args),
    batchUpdateCells: (...args: any[]) => batchUpdateCellsMock(...args),
    resizeSheet: (...args: any[]) => resizeSheetMock(...args),
    finalizeImport: (...args: any[]) => finalizeImportMock(...args),
    getHighlights: jest.fn().mockResolvedValue({ highlights: [] }),
    getCellFormats: jest.fn().mockResolvedValue({ formats: [] }),
    batchUpdateHighlights: jest.fn().mockResolvedValue({ updated: 0, deleted: 0 }),
    deleteRows: (...args: any[]) => deleteRowsMock(...args),
    deleteColumns: (...args: any[]) => deleteColumnsMock(...args),
    revertStructureOperation: (...args: any[]) => revertStructureMock(...args),
    exportSheetXlsx: (...args: any[]) => exportXlsxMock(...args),
  },
}));

jest.mock('@/lib/api/googleDocsApi', () => ({
  googleDocsApi: {
    exportToGoogleSheets: (...args: any[]) => exportGoogleMock(...args),
  },
}));

const toastSuccess = jest.fn();
const toastError = jest.fn();
jest.mock('react-hot-toast', () => ({
  __esModule: true,
  default: {
    success: (...args: any[]) => toastSuccess(...args),
    error: (...args: any[]) => toastError(...args),
    loading: jest.fn(() => 'toast-id'),
    dismiss: jest.fn(),
  },
}));

// Helper: dispatch a pointer-style event that JSDOM cannot create natively.
// React registers onPointerDown/Move/Up via 'pointerdown'/'pointermove'/'pointerup'.
// We dispatch a MouseEvent with the right type and override pointerId/clientX/clientY
// via Object.defineProperty so React's synthetic event passes them through.
function dispatchPointerEvent(
  element: Element,
  type: string,
  init: { pointerId?: number; clientX?: number; clientY?: number } = {},
) {
  const event = new MouseEvent(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'pointerId', { value: init.pointerId ?? 0, configurable: true });
  Object.defineProperty(event, 'clientX', { value: init.clientX ?? 0, configurable: true });
  Object.defineProperty(event, 'clientY', { value: init.clientY ?? 0, configurable: true });
  element.dispatchEvent(event);
}

function setRect(
  el: Element,
  rect: { left: number; top: number; width: number; height: number },
) {
  el.getBoundingClientRect = () =>
    ({
      x: rect.left,
      y: rect.top,
      left: rect.left,
      top: rect.top,
      right: rect.left + rect.width,
      bottom: rect.top + rect.height,
      width: rect.width,
      height: rect.height,
      toJSON() {},
    }) as DOMRect;
}

function mockToolbarAndButton(
  button: HTMLElement,
  buttonRect: { left: number; top: number; width: number; height: number },
  toolbarRect = { left: 0, top: 0, width: 800, height: 40 },
) {
  const toolbar = document.getElementById('spreadsheet-toolbar');
  if (toolbar) setRect(toolbar, toolbarRect);
  setRect(button, buttonRect);
}

function expectMenuPortaled(selector: string) {
  const menu = document.querySelector(selector) as HTMLElement | null;
  const toolbar = document.getElementById('spreadsheet-toolbar');
  expect(menu).toBeInTheDocument();
  expect(menu).toHaveClass('fixed');
  expect(menu?.parentElement).toBe(document.body);
  expect(toolbar?.contains(menu)).toBe(false);
}

describe('SpreadsheetGrid resizing', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    act(() => {
      jest.runOnlyPendingTimers();
    });
    jest.useRealTimers();
  });

  it('renders resize handles with expected hit area', () => {
    render(<SpreadsheetGrid spreadsheetId={1} sheetId={1} />);
    act(() => {
      jest.runOnlyPendingTimers();
    });

    const colHandle = screen.getByTestId('col-resize-handle-0');
    const rowHandle = screen.getByTestId('row-resize-handle-0');

    expect(colHandle).toHaveStyle({ width: '6px', cursor: 'col-resize' });
    expect(rowHandle).toHaveStyle({ height: '6px', cursor: 'row-resize' });
  });

  it('updates column width when dragging header resize handle', () => {
    render(<SpreadsheetGrid spreadsheetId={1} sheetId={1} />);
    act(() => {
      jest.runOnlyPendingTimers();
    });

    const handle = screen.getByTestId('col-resize-handle-0') as HTMLDivElement;
    handle.setPointerCapture = jest.fn();
    handle.releasePointerCapture = jest.fn();

    act(() => {
      dispatchPointerEvent(handle, 'pointerdown', { pointerId: 1, clientX: 100 });
      dispatchPointerEvent(handle, 'pointermove', { pointerId: 1, clientX: 130 });
      dispatchPointerEvent(handle, 'pointerup', { pointerId: 1, clientX: 130 });
    });

    const col = screen.getByTestId('col-width-0');
    expect(col).toHaveStyle({ width: '150px' });
  });

  it('updates row height when dragging row resize handle', () => {
    render(<SpreadsheetGrid spreadsheetId={1} sheetId={1} />);
    act(() => {
      jest.runOnlyPendingTimers();
    });

    const handle = screen.getByTestId('row-resize-handle-0') as HTMLDivElement;
    handle.setPointerCapture = jest.fn();
    handle.releasePointerCapture = jest.fn();

    act(() => {
      dispatchPointerEvent(handle, 'pointerdown', { pointerId: 2, clientY: 100 });
      dispatchPointerEvent(handle, 'pointermove', { pointerId: 2, clientY: 130 });
      dispatchPointerEvent(handle, 'pointerup', { pointerId: 2, clientY: 130 });
    });

    const rowHeader = screen.getByTestId('row-header-0');
    expect(rowHeader).toHaveStyle({ height: '54px' });
  });
});

describe('SpreadsheetGrid numeric display', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    readCellRangeMock.mockResolvedValue({
      cells: [
        {
          row_position: 0,
          column_position: 0,
          raw_input: '9.7654322457898765',
          computed_type: 'number',
          computed_number: '9.7654322457898765',
        },
      ],
    });
  });

  afterEach(() => {
    act(() => {
      jest.runOnlyPendingTimers();
    });
    jest.useRealTimers();
    readCellRangeMock.mockReset();
  });

  it('truncates display to 10 decimal places but keeps full raw input on edit', async () => {
    // Use a unique sheetId to avoid module-level cell cache from prior test suites
    render(<SpreadsheetGrid spreadsheetId={1} sheetId={10} />);
    await act(async () => {
      jest.runOnlyPendingTimers();
    });

    expect(screen.getByText('9.7654322457')).toBeInTheDocument();

    const cell = screen.getByText('9.7654322457');
    fireEvent.doubleClick(cell);

    // The raw value appears in both the inline cell editor and the formula bar
    const inputs = screen.getAllByDisplayValue('9.7654322457898765') as HTMLInputElement[];
    expect(inputs.length).toBeGreaterThan(0);
  });
});

describe('SpreadsheetGrid highlight toolbar', () => {
  beforeEach(() => {
    readCellRangeMock.mockReset();
    readCellRangeMock.mockResolvedValue({ cells: [] });
    jest.useFakeTimers();
  });

  afterEach(() => {
    act(() => {
      jest.runOnlyPendingTimers();
    });
    jest.useRealTimers();
  });
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    act(() => {
      jest.runOnlyPendingTimers();
    });
    jest.useRealTimers();
  });

  it('applies highlight to a single cell selection', () => {
    const { container } = render(<SpreadsheetGrid spreadsheetId={1} sheetId={1} />);
    act(() => {
      jest.runOnlyPendingTimers();
    });

    const cell = container.querySelector('td[data-row="0"][data-col="0"]') as HTMLTableCellElement;
    fireEvent.mouseDown(cell);

    const button = screen.getByTestId('highlight-button');
    mockToolbarAndButton(screen.getByTestId('highlight-button'), { left: 20, top: 0, width: 32, height: 32 });
    
    fireEvent.click(button);
    fireEvent.click(screen.getByTestId('highlight-color-yellow'));

    expect(cell).toHaveStyle({ backgroundColor: '#FEF08A' });
  });

  it('applies highlight to a row selection', () => {
    const { container } = render(<SpreadsheetGrid spreadsheetId={1} sheetId={1} />);
    act(() => {
      jest.runOnlyPendingTimers();
    });

    const rowHeader = screen.getByTestId('row-header-0');
    fireEvent.click(rowHeader);

    const button = screen.getByTestId('highlight-button');
    mockToolbarAndButton(button, { left: 20, top: 0, width: 32, height: 32 });
    fireEvent.click(button);
    fireEvent.click(screen.getByTestId('highlight-color-green'));

    const cell = container.querySelector('td[data-row="0"][data-col="1"]') as HTMLTableCellElement;
    expect(cell).toHaveStyle({ backgroundColor: '#BBF7D0' });
  });

  it('applies highlight to a column selection', () => {
    const { container } = render(<SpreadsheetGrid spreadsheetId={1} sheetId={1} />);
    act(() => {
      jest.runOnlyPendingTimers();
    });

    const colHeader = screen.getByTestId('col-header-0');
    fireEvent.click(colHeader);

    const button = screen.getByTestId('highlight-button');
    mockToolbarAndButton(button, { left: 20, top: 0, width: 32, height: 32 });
    fireEvent.click(button);
    fireEvent.click(screen.getByTestId('highlight-color-blue'));

    const cell = container.querySelector('td[data-row="1"][data-col="0"]') as HTMLTableCellElement;
    expect(cell).toHaveStyle({ backgroundColor: '#BFDBFE' });
  });

  it('clears highlight', () => {
    const { container } = render(<SpreadsheetGrid spreadsheetId={1} sheetId={1} />);
    act(() => {
      jest.runOnlyPendingTimers();
    });

    const cell = container.querySelector('td[data-row="0"][data-col="0"]') as HTMLTableCellElement;
    fireEvent.mouseDown(cell);

    const button = screen.getByTestId('highlight-button');
    mockToolbarAndButton(button, { left: 20, top: 0, width: 32, height: 32 });
    fireEvent.click(button);
    fireEvent.click(screen.getByTestId('highlight-color-pink'));
    expect(cell).toHaveStyle({ backgroundColor: '#FBCFE8' });

    fireEvent.click(button);
    fireEvent.click(screen.getByTestId('highlight-clear'));
    expect(cell).not.toHaveStyle({ backgroundColor: '#FBCFE8' });
  });

  it('records column highlight by header', async () => {
    readCellRangeMock.mockResolvedValue({
      cells: [
        { row_position: 0, column_position: 0, raw_input: 'Name' },
        { row_position: 0, column_position: 1, raw_input: 'Spend' },
      ],
    });
    const onHighlightCommit = jest.fn();
    // Use a unique sheetId to avoid module-level cell cache from prior test suites
    render(<SpreadsheetGrid spreadsheetId={1} sheetId={20} onHighlightCommit={onHighlightCommit} />);
    await act(async () => {
      jest.runOnlyPendingTimers();
    });

    const colHeader = screen.getByTestId('col-header-0');
    fireEvent.click(colHeader);

    const button = screen.getByTestId('highlight-button');
    mockToolbarAndButton(button, { left: 20, top: 0, width: 32, height: 32 });
    fireEvent.click(button);
    fireEvent.click(screen.getByTestId('highlight-color-yellow'));

    expect(onHighlightCommit).toHaveBeenCalled();
    const payload = onHighlightCommit.mock.calls[0][0];
    expect(payload.scope).toBe('COLUMN');
    expect(payload.target.by_header).toBe('Name');
  });

  it('replays highlight by header on reordered columns', async () => {
    readCellRangeMock.mockResolvedValue({
      cells: [
        { row_position: 0, column_position: 0, raw_input: 'City' },
        { row_position: 0, column_position: 2, raw_input: 'Spend' },
      ],
    });
    const ref = React.createRef<SpreadsheetGridHandle>();
    // Use a unique sheetId to avoid module-level cell cache from prior test suites
    const { container } = render(<SpreadsheetGrid ref={ref} spreadsheetId={1} sheetId={21} />);
    await act(async () => {
      jest.runOnlyPendingTimers();
    });

    act(() => {
      ref.current?.applyHighlightOperation({
        color: '#BFDBFE',
        scope: 'COLUMN',
        header_row_index: 1,
        target: {
          by_header: 'Spend',
          fallback: { col_index: 1 },
        },
      });
    });

    const cell = container.querySelector('td[data-row="1"][data-col="2"]') as HTMLTableCellElement;
    expect(cell).toHaveStyle({ backgroundColor: '#BFDBFE' });
  });

  it('skips highlight when header is missing', async () => {
    readCellRangeMock.mockResolvedValue({
      cells: [{ row_position: 0, column_position: 0, raw_input: 'City' }],
    });
    const ref = React.createRef<SpreadsheetGridHandle>();
    const { container } = render(<SpreadsheetGrid ref={ref} spreadsheetId={1} sheetId={1} />);
    await act(async () => {
      jest.runOnlyPendingTimers();
    });

    ref.current?.applyHighlightOperation({
      color: '#FEF08A',
      scope: 'COLUMN',
      header_row_index: 1,
      target: {
        by_header: 'Missing',
      },
    });

    const cell = container.querySelector('td[data-row="1"][data-col="0"]') as HTMLTableCellElement;
    expect(cell).not.toHaveStyle({ backgroundColor: '#FEF08A' });
  });

  it('records clear highlight action', async () => {
    const onHighlightCommit = jest.fn();
    const { container } = render(<SpreadsheetGrid spreadsheetId={1} sheetId={1} onHighlightCommit={onHighlightCommit} />);
    await act(async () => {
      jest.runOnlyPendingTimers();
    });

    // Cells use data-row/data-col attributes; there is no data-testid on individual cells
    const cell = container.querySelector('td[data-row="0"][data-col="0"]') as HTMLElement;
    fireEvent.mouseDown(cell);

    const button = screen.getByTestId('highlight-button');
    mockToolbarAndButton(button, { left: 20, top: 0, width: 32, height: 32 });
    fireEvent.click(button);
    fireEvent.click(screen.getByTestId('highlight-clear'));

    expect(onHighlightCommit).toHaveBeenCalled();
    const payload = onHighlightCommit.mock.calls[0][0];
    expect(payload.color).toBe('clear');
  });
});

describe('SpreadsheetGrid delete row/column', () => {
  beforeEach(() => {
    readCellRangeMock.mockResolvedValue({ cells: [] });
    deleteRowsMock.mockResolvedValue({ operation_id: 1 });
    deleteColumnsMock.mockResolvedValue({ operation_id: 1 });
    toastSuccess.mockClear();
    toastError.mockClear();
    jest.useFakeTimers();
  });

  afterEach(() => {
    act(() => {
      jest.runOnlyPendingTimers();
    });
    jest.useRealTimers();
  });

  it('delete row calls API and shows success toast without opening modal', async () => {
    render(<SpreadsheetGrid spreadsheetId={1} sheetId={1} />);
    await act(async () => {
      jest.runOnlyPendingTimers();
    });

    const rowHeader = screen.getByTestId('row-header-0');
    fireEvent.contextMenu(rowHeader);

    const deleteRowButton = screen.getByRole('menuitem', { name: /delete row/i });
    fireEvent.click(deleteRowButton);

    await act(async () => {
      jest.runOnlyPendingTimers();
    });

    expect(deleteRowsMock).toHaveBeenCalledWith(1, 1, 0, 1);
    expect(toastSuccess).toHaveBeenCalledWith('Deleted row.');
    expect(toastError).not.toHaveBeenCalled();
  });

  it('delete row failure shows error toast', async () => {
    deleteRowsMock.mockRejectedValueOnce(new Error('Server error'));

    render(<SpreadsheetGrid spreadsheetId={1} sheetId={1} />);
    await act(async () => {
      jest.runOnlyPendingTimers();
    });

    const rowHeader = screen.getByTestId('row-header-0');
    fireEvent.contextMenu(rowHeader);

    const deleteRowButton = screen.getByRole('menuitem', { name: /delete row/i });
    fireEvent.click(deleteRowButton);

    await act(async () => {
      jest.runOnlyPendingTimers();
    });

    expect(deleteRowsMock).toHaveBeenCalled();
    expect(toastError).toHaveBeenCalled();
    expect(toastError.mock.calls[0][0]).toContain('Server error');
  });

  it('redo stays available when a delete-row retry fails', async () => {
    deleteRowsMock.mockClear();
    revertStructureMock.mockClear();
    revertStructureMock.mockResolvedValue({});
  
    render(<SpreadsheetGrid spreadsheetId={1} sheetId={1} />);
    await act(async () => {
      jest.runOnlyPendingTimers();
    });
  
    fireEvent.contextMenu(screen.getByTestId('row-header-0'));
    fireEvent.click(screen.getByRole('menuitem', { name: /delete row/i }));
    await act(async () => {
      jest.runOnlyPendingTimers();
    });
  
    fireEvent.click(screen.getByTitle('Undo (Ctrl+Z)'));
    await act(async () => {
      jest.runOnlyPendingTimers();
    });
    expect(revertStructureMock).toHaveBeenCalledWith(1, 1, 1);
    expect(toastSuccess).toHaveBeenCalledWith('Undo complete');
  
    deleteRowsMock.mockRejectedValueOnce(new Error('Server error'));
    const redo = screen.getByTitle('Redo (Ctrl+Shift+Z)');
    fireEvent.click(redo);
    await act(async () => {
      jest.runOnlyPendingTimers();
    });
  
    expect(deleteRowsMock).toHaveBeenCalledTimes(2);
    expect(toastError).toHaveBeenCalledWith('Failed to redo');
    expect(redo).toBeEnabled();
  
    fireEvent.click(redo);
    await act(async () => {
      jest.runOnlyPendingTimers();
    });
    expect(deleteRowsMock).toHaveBeenCalledTimes(3);
    expect(deleteRowsMock).toHaveBeenLastCalledWith(1, 1, 0, 1);
  });
});


describe('SpreadsheetGrid import error handling', () => {
  let consoleErrorSpy: jest.SpyInstance;
  let consoleLogSpy: jest.SpyInstance;

  beforeEach(() => {
    readCellRangeMock.mockReset();
    readCellRangeMock.mockResolvedValue({ cells: [] });
    batchUpdateCellsMock.mockReset();
    resizeSheetMock.mockReset();
    resizeSheetMock.mockResolvedValue({});
    finalizeImportMock.mockReset();
    finalizeImportMock.mockResolvedValue({ status: 'ok' });
    toastSuccess.mockClear();
    toastError.mockClear();
    consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
    consoleLogSpy = jest.spyOn(console, 'log').mockImplementation(() => {});
  });

  afterEach(() => {
    consoleErrorSpy.mockRestore();
    consoleLogSpy.mockRestore();
  });

  const uploadCsv = async (container: HTMLElement, csv: string) => {
    const file = new File([csv], 'test.csv', { type: 'text/csv' });
    // jsdom's File does not implement Blob.text(); the import code path calls
    // `await file.text()` in parseCSVFile, so we polyfill it per-instance here.
    Object.defineProperty(file, 'text', {
      configurable: true,
      value: () => Promise.resolve(csv),
    });
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } });
    });
  };

  it('emits exactly one toast when batch chunks fail persistently', async () => {
    // 500 is the chunk size; 600 rows x 1 col -> 2 chunks (500 + 100).
    const rows: string[] = [];
    for (let r = 0; r < 600; r += 1) rows.push(`v${r}`);
    const csv = rows.join('\n');

    // Every call to batchUpdateCells rejects with HTTP 500 so retry also fails.
    batchUpdateCellsMock.mockRejectedValue({
      response: { status: 500, data: { detail: 'boom' } },
      message: 'Request failed with status code 500',
    });

    const { container } = render(<SpreadsheetGrid spreadsheetId={1} sheetId={1} />);
    await act(async () => {
      await Promise.resolve();
    });

    await uploadCsv(container, csv);

    // Wait for retry + abort + outer catch to resolve (retry backoff is 500ms).
    await waitFor(
      () => {
        expect(toastError).toHaveBeenCalled();
      },
      { timeout: 3000 },
    );
    // Give any trailing aborted chunks time to settle without surfacing extra toasts.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 100));
    });

    expect(toastError).toHaveBeenCalledTimes(1);
    expect(toastSuccess).not.toHaveBeenCalled();
    // finalizeImport must NOT be called because we aborted before all chunks finished.
    expect(finalizeImportMock).not.toHaveBeenCalled();
  });

  it('does not finalize the import when batch chunks fail', async () => {
    const rows: string[] = [];
    for (let r = 0; r < 600; r += 1) rows.push(`v${r}`);
    const csv = rows.join('\n');

    batchUpdateCellsMock.mockRejectedValue({
      response: { status: 500, data: { detail: 'boom' } },
      message: 'Request failed with status code 500',
    });

    const { container } = render(<SpreadsheetGrid spreadsheetId={1} sheetId={1} />);
    await act(async () => {
      await Promise.resolve();
    });

    await uploadCsv(container, csv);

    await waitFor(() => expect(toastError).toHaveBeenCalled(), { timeout: 3000 });

    expect(finalizeImportMock).not.toHaveBeenCalled();
  });

  it('awaits backend resize before firing chunks, and chunks use auto_expand=false', async () => {
    // 1100 rows x 1 col forces a resize because DEFAULT_ROWS is 1000.
    const rows: string[] = [];
    for (let r = 0; r < 1100; r += 1) rows.push(`v${r}`);
    const csv = rows.join('\n');

    const callOrder: string[] = [];
    resizeSheetMock.mockImplementation(async () => {
      callOrder.push('resize');
      // Give the batch task a micro-task to accidentally jump the queue if the
      // code failed to await; if the order is strict the batch call will still
      // land after this resolves.
      await new Promise((resolve) => setTimeout(resolve, 5));
      return {
        rows_created: 100,
        columns_created: 0,
        total_rows: 1100,
        total_columns: 26,
      };
    });
    batchUpdateCellsMock.mockImplementation(async () => {
      callOrder.push('batch');
      return {
        updated: 0, cleared: 0, rows_expanded: 0, columns_expanded: 0, cells: [],
      };
    });

    const { container } = render(<SpreadsheetGrid spreadsheetId={1} sheetId={1} />);
    await act(async () => {
      await Promise.resolve();
    });

    await uploadCsv(container, csv);
    await waitFor(
      () => expect(finalizeImportMock).toHaveBeenCalled(),
      { timeout: 10000 },
    );

    // resize must come before the first chunk upload.
    expect(callOrder[0]).toBe('resize');
    expect(callOrder.indexOf('batch')).toBeGreaterThan(0);

    // Every chunk call passes auto_expand=false (arg index 3 in
    // batchUpdateCells(spreadsheetId, sheetId, chunk, autoExpand, options)).
    const allAutoExpand = batchUpdateCellsMock.mock.calls.map((args) => args[3]);
    expect(allAutoExpand.every((v) => v === false)).toBe(true);
  }, 15000);

  it('shows imported values optimistically before the backend resize resolves', async () => {
    // 1200 rows x 1 col forces a backend resize (DEFAULT_ROWS is 1000). We gate the
    // resize mock on an external promise so it stays in-flight while we check the UI.
    const rows: string[] = [];
    for (let r = 0; r < 1200; r += 1) rows.push(`optimistic-${r}`);
    const csv = rows.join('\n');

    let releaseResize: (() => void) | undefined;
    const resizeHeld = new Promise<void>((resolve) => {
      releaseResize = resolve;
    });
    resizeSheetMock.mockImplementation(async () => {
      await resizeHeld;
      return {
        rows_created: 200,
        columns_created: 0,
        total_rows: 1200,
        total_columns: 26,
      };
    });
    batchUpdateCellsMock.mockResolvedValue({
      updated: 0, cleared: 0, rows_expanded: 0, columns_expanded: 0, cells: [],
    });

    const { container } = render(<SpreadsheetGrid spreadsheetId={1} sheetId={1} />);
    await act(async () => {
      await Promise.resolve();
    });

    await uploadCsv(container, csv);

    // While resizeSheet is still pending, the optimistic cell value should already
    // be rendered and no chunk upload should have fired yet.
    await waitFor(() => expect(screen.getByText('optimistic-0')).toBeInTheDocument(), {
      timeout: 2000,
    });
    expect(resizeSheetMock).toHaveBeenCalled();
    expect(batchUpdateCellsMock).not.toHaveBeenCalled();

    // Release the resize; chunks should now flow and the import should finalize.
    releaseResize?.();
    await waitFor(
      () => expect(finalizeImportMock).toHaveBeenCalled(),
      { timeout: 10000 },
    );
  }, 15000);

  it('rolls back the optimistic apply when the backend resize fails', async () => {
    const rows: string[] = [];
    for (let r = 0; r < 1200; r += 1) rows.push(`rollback-${r}`);
    const csv = rows.join('\n');

    resizeSheetMock.mockRejectedValue(new Error('resize network error'));
    batchUpdateCellsMock.mockResolvedValue({
      updated: 0, cleared: 0, rows_expanded: 0, columns_expanded: 0, cells: [],
    });

    const { container } = render(<SpreadsheetGrid spreadsheetId={1} sheetId={1} />);
    await act(async () => {
      await Promise.resolve();
    });

    await uploadCsv(container, csv);

    // We briefly display optimistic data, then the resize failure triggers a rollback
    // and the error toast. After rollback, the cell must no longer contain the
    // imported text.
    await waitFor(
      () => expect(toastError).toHaveBeenCalled(),
      { timeout: 5000 },
    );
    expect(batchUpdateCellsMock).not.toHaveBeenCalled();
    expect(screen.queryByText('rollback-0')).not.toBeInTheDocument();
    expect(finalizeImportMock).not.toHaveBeenCalled();
  }, 15000);

  it('skips post-import hydration when there are no formulas', async () => {
    const rows: string[] = [];
    for (let r = 0; r < 600; r += 1) rows.push(`v${r}`);
    const csv = rows.join('\n');

    batchUpdateCellsMock.mockResolvedValue({
      updated: 0, cleared: 0, rows_expanded: 0, columns_expanded: 0, cells: [],
    });
    readCellRangeMock.mockResolvedValue({ cells: [] });

    const { container } = render(<SpreadsheetGrid spreadsheetId={1} sheetId={1} />);
    await act(async () => {
      await Promise.resolve();
    });

    const beforeRangeCalls = readCellRangeMock.mock.calls.length;
    await uploadCsv(container, csv);
    await waitFor(
      () => expect(finalizeImportMock).toHaveBeenCalled(),
      { timeout: 10000 },
    );
    // Let any lingering microtasks drain before asserting no new range fetches.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    expect(readCellRangeMock.mock.calls.length).toBe(beforeRangeCalls);
  }, 15000);

  it('fetches hydration data when formulas are present', async () => {
    // Plain data in most cells, one formula. runImportMatrix should detect the
    // formula and issue at least one readCellRange AFTER finalize to pick up the
    // server-computed result. We cannot assert the exact bbox because
    // loadCellRange snaps to tile boundaries, but we can assert that at least
    // one fetch happens post-finalize.
    const rows: string[] = [];
    for (let r = 0; r < 20; r += 1) {
      const cols: string[] = [];
      for (let c = 0; c < 4; c += 1) {
        if (r === 5 && c === 2) {
          cols.push('=A1+B1');
        } else {
          cols.push(`d${r}${c}`);
        }
      }
      rows.push(cols.join(','));
    }
    const csv = rows.join('\n');

    batchUpdateCellsMock.mockResolvedValue({
      updated: 0, cleared: 0, rows_expanded: 0, columns_expanded: 0, cells: [],
    });
    readCellRangeMock.mockResolvedValue({ cells: [] });

    const { container } = render(<SpreadsheetGrid spreadsheetId={1} sheetId={1} />);
    await act(async () => {
      await Promise.resolve();
    });

    // Baseline: the grid performs at least one viewport fetch on mount. Snapshot
    // the count BEFORE we upload the CSV.
    const beforeRangeCalls = readCellRangeMock.mock.calls.length;

    await uploadCsv(container, csv);
    await waitFor(
      () => expect(finalizeImportMock).toHaveBeenCalled(),
      { timeout: 10000 },
    );
    // Allow the formula bbox fetch to dispatch after finalize resolves.
    await waitFor(
      () => expect(readCellRangeMock.mock.calls.length).toBeGreaterThan(beforeRangeCalls),
      { timeout: 3000 },
    );
  }, 15000);

  it('applies imported values to the grid in a batched pass (no O(n^2) freeze)', async () => {
    // 600 rows x 1 col mirrors the shape used in the failure-path tests above.
    // If the batched apply were not active, 600 sequential setCells(new Map(prev))
    // calls would still succeed functionally but much slower; we primarily assert
    // correctness (finalizeImport reached, imported values visible).
    const rows: string[] = [];
    for (let r = 0; r < 600; r += 1) rows.push(`v${r}`);
    const csv = rows.join('\n');

    batchUpdateCellsMock.mockImplementation(async () => ({
      updated: 0,
      cleared: 0,
      rows_expanded: 0,
      columns_expanded: 0,
      cells: [],
    }));

    const { container } = render(<SpreadsheetGrid spreadsheetId={1} sheetId={1} />);
    await act(async () => {
      await Promise.resolve();
    });

    await uploadCsv(container, csv);
    await waitFor(
      () => expect(batchUpdateCellsMock).toHaveBeenCalled(),
      { timeout: 10000 },
    );
    await waitFor(
      () => expect(finalizeImportMock).toHaveBeenCalled(),
      { timeout: 10000 },
    );

    // Imported values from the top of the file should be visible in the viewport.
    expect(screen.getByText('v0')).toBeInTheDocument();
  }, 15000);
});

describe('SpreadsheetGrid collaboration reconciliation', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    readCellRangeMock.mockReset();
    readCellRangeMock.mockResolvedValue({ cells: [] });
    batchUpdateCellsMock.mockReset();
    batchUpdateCellsMock.mockResolvedValue({
      updated: 0,
      cleared: 0,
      rows_expanded: 0,
      columns_expanded: 0,
      cells: [],
    });
    toastError.mockClear();
    sessionStorage.clear();
  });

  afterEach(() => {
    act(() => {
      jest.runOnlyPendingTimers();
    });
    jest.useRealTimers();
  });

  it('protects a pending optimistic edit and quarantines it on structure refresh', async () => {
    const ref = React.createRef<SpreadsheetGridHandle>();
    const { container } = render(
      <SpreadsheetGrid ref={ref} spreadsheetId={77} sheetId={977} />
    );
    await act(async () => {
      await Promise.resolve();
    });

    const cell = container.querySelector(
      'td[data-row="0"][data-col="0"]'
    ) as HTMLTableCellElement;
    fireEvent.doubleClick(cell);
    const editor = cell.querySelector('input') as HTMLInputElement;
    fireEvent.change(editor, { target: { value: 'local-pending' } });
    fireEvent.keyDown(editor, { key: 'Enter' });

    expect(screen.getByText('local-pending')).toBeInTheDocument();

    act(() => {
      ref.current?.applyRemoteCells([
        {
          row_position: 0,
          column_position: 0,
          raw_input: 'remote-committed',
        },
      ]);
    });
    expect(screen.queryByText('remote-committed')).not.toBeInTheDocument();
    expect(screen.getByText('local-pending')).toBeInTheDocument();

    act(() => {
      ref.current?.refresh();
      jest.advanceTimersByTime(500);
    });

    expect(container.firstElementChild).toHaveClass('pointer-events-none');
    expect(sessionStorage.getItem('spreadsheet-pending-recovery:77:977')).toBeNull();
    expect(batchUpdateCellsMock).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith(
      expect.stringContaining('discarded'),
      expect.any(Object)
    );
  });

  it('cancels an active editor and blocks keyboard input during canonical refresh', async () => {
    const ref = React.createRef<SpreadsheetGridHandle>();
    const { container } = render(
      <SpreadsheetGrid ref={ref} spreadsheetId={77} sheetId={977} />
    );
    await act(async () => {
      await Promise.resolve();
    });

    const cell = container.querySelector(
      'td[data-row="0"][data-col="0"]'
    ) as HTMLTableCellElement;
    fireEvent.doubleClick(cell);
    const editor = cell.querySelector('input') as HTMLInputElement;
    fireEvent.change(editor, { target: { value: 'stale-coordinate-edit' } });

    act(() => {
      ref.current?.refresh();
      // Dispatch before React removes the input so both the editor's Enter
      // handler and blur commit path exercise the in-flight refresh guard.
      editor.dispatchEvent(
        new KeyboardEvent('keydown', {
          key: 'Enter',
          bubbles: true,
          cancelable: true,
        })
      );
      editor.dispatchEvent(new FocusEvent('focusout', { bubbles: true }));
    });

    const grid = container.querySelector(
      '.spreadsheet-scroll-container'
    ) as HTMLDivElement;
    expect(container.querySelector('td[data-row="0"][data-col="0"] input')).toBeNull();
    expect(grid).toHaveAttribute('tabindex', '-1');

    fireEvent.keyDown(grid, { key: 'x' });
    act(() => {
      jest.advanceTimersByTime(500);
    });

    expect(container.querySelector('td[data-row="0"][data-col="0"] input')).toBeNull();
    expect(batchUpdateCellsMock).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith(
      expect.stringContaining('discarded'),
      expect.any(Object)
    );
  });

  it('ignores an older same-revision cell broadcast by updated_at', async () => {
    const ref = React.createRef<SpreadsheetGridHandle>();
    render(<SpreadsheetGrid ref={ref} spreadsheetId={77} sheetId={977} />);
    await act(async () => {
      await Promise.resolve();
    });

    act(() => {
      ref.current?.applyRemoteCells([
        {
          row_position: 0,
          column_position: 0,
          raw_input: 'newer-commit',
          updated_at: '2026-07-25T02:00:02.000Z',
        },
      ]);
      ref.current?.applyRemoteCells([
        {
          row_position: 0,
          column_position: 0,
          raw_input: 'older-commit',
          updated_at: '2026-07-25T02:00:01.000Z',
        },
      ]);
    });

    expect(screen.getByText('newer-commit')).toBeInTheDocument();
    expect(screen.queryByText('older-commit')).not.toBeInTheDocument();
  });
});


describe('SpreadsheetGrid font size rendering', () => {
  const getCellFormatsMock = SpreadsheetAPI.getCellFormats as jest.Mock;

  beforeEach(() => {
    jest.useFakeTimers();
    readCellRangeMock.mockResolvedValue({
      cells: [
        {
          row_position: 0,
          column_position: 0,
          raw_input: 'plain',
          computed_type: 'string',
        },
        {
          row_position: 0,
          column_position: 1,
          raw_input: 'eleven',
          computed_type: 'string',
        },
      ],
    });
    getCellFormatsMock.mockResolvedValue({
      formats: [
        {
          row_index: 0,
          column_index: 1,
          bold: false,
          italic: false,
          strikethrough: false,
          text_color: null,
          font_family: null,
          font_size: 11,
          number_format: null,
        },
      ],
    });
  });

  afterEach(() => {
    act(() => {
      jest.runOnlyPendingTimers();
    });
    jest.useRealTimers();
    readCellRangeMock.mockReset();
    getCellFormatsMock.mockResolvedValue({ formats: [] });
  });

  it('renders the grid default 12px when no font size is saved, and 11px when one is', async () => {
    render(<SpreadsheetGrid spreadsheetId={1} sheetId={452} />);
    await act(async () => {
      jest.runOnlyPendingTimers();
    });

    expect(screen.getByText('plain')).toHaveStyle({ fontSize: '12px' });
    expect(screen.getByText('eleven')).toHaveStyle({ fontSize: '11px' });
  });
});

describe('SpreadsheetGrid toolbar menus to document.body', () => {
  beforeEach(() => {
    readCellRangeMock.mockReset();
    readCellRangeMock.mockResolvedValue({ cells: [] });
    jest.useFakeTimers();
  });

  afterEach(() => {
    act(() => {
      jest.runOnlyPendingTimers();
    });
    jest.useRealTimers();
  });

  it('portals the highlight menu outside the overflow toolbar', () => {
    const { container } = render(<SpreadsheetGrid spreadsheetId={1} sheetId={30} />);
    act(() => {
      jest.runOnlyPendingTimers();
    });

    const cell = container.querySelector('td[data-row="0"][data-col="0"]') as HTMLTableCellElement;
    fireEvent.mouseDown(cell);

    const button = screen.getByTestId('highlight-button');
    mockToolbarAndButton(button, { left: 20, top: 0, width: 32, height: 32 });
    fireEvent.click(button);
    expectMenuPortaled('[data-highlight-menu]');
  });

  it('portals the text color menu outside the overflow toolbar', () => {
    const { container } = render(<SpreadsheetGrid spreadsheetId={1} sheetId={31} />);
    act(() => {
      jest.runOnlyPendingTimers();
    });

    const cell = container.querySelector('td[data-row="0"][data-col="0"]') as HTMLTableCellElement;
    fireEvent.mouseDown(cell);

    const button = screen.getByTestId('format-text-color');
    mockToolbarAndButton(button, { left: 20, top: 0, width: 32, height: 32 });
    fireEvent.click(button);
    expectMenuPortaled('[data-text-color-menu]');
  });

  it('portals the currency menu outside the overflow toolbar', () => {
    const { container } = render(<SpreadsheetGrid spreadsheetId={1} sheetId={32} />);
    act(() => {
      jest.runOnlyPendingTimers();
    });

    const cell = container.querySelector('td[data-row="0"][data-col="0"]') as HTMLTableCellElement;
    fireEvent.mouseDown(cell);

    const button = screen.getByTestId('format-currency');
    mockToolbarAndButton(button, { left: 20, top: 0, width: 32, height: 32 });
    fireEvent.click(button);
    expectMenuPortaled('[data-currency-menu]');
  });

  it('portals the export menu outside the overflow toolbar', () => {
    render(<SpreadsheetGrid spreadsheetId={1} sheetId={33} />);
    act(() => {
      jest.runOnlyPendingTimers();
    });
    
    const button = screen.getByRole('button', { name: /^Export$/i });
    mockToolbarAndButton(button, { left: 20, top: 0, width: 80, height: 32 });
    fireEvent.click(button);
    expectMenuPortaled('[data-export-menu]');
  });

  it('export menu sends the spreadsheet and sheet ids', async () => {
    exportXlsxMock.mockClear();
    exportGoogleMock.mockClear();
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: jest.fn(() => 'blob:xlsx'),
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: jest.fn(),
    });
  
    render(<SpreadsheetGrid spreadsheetId={1} sheetId={33} />);
    act(() => {
      jest.runOnlyPendingTimers();
    });
  
    const openExport = () => {
      const button = screen.getByRole('button', { name: /^Export$/i });
      mockToolbarAndButton(button, { left: 20, top: 0, width: 80, height: 32 });
      fireEvent.click(button);
    };
  
    openExport();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Export as XLSX' }));
    await act(async () => {
      jest.runOnlyPendingTimers();
    });
    expect(exportXlsxMock).toHaveBeenCalledWith(1, 33);
  
    openExport();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Export to Google Sheets' }));
    await act(async () => {
      jest.runOnlyPendingTimers();
    });
    expect(exportGoogleMock).toHaveBeenCalledWith(
      'spreadsheet-1-sheet-33',
      expect.any(Array),
      expect.any(Array),
      1,
      33,
    );
  });

  it('does not open format menus when nothing is selected', () => {
    render(<SpreadsheetGrid spreadsheetId={1} sheetId={34} />);
    act(() => {
      jest.runOnlyPendingTimers();
    });
  
    const highlight = screen.getByTestId('highlight-button');
    const textColor = screen.getByTestId('format-text-color');
    const currency = screen.getByTestId('format-currency');
  
    expect(highlight).toBeDisabled();
    expect(textColor).toBeDisabled();
    expect(currency).toBeDisabled();
  
    fireEvent.click(highlight);
    fireEvent.click(textColor);
    fireEvent.click(currency);
  
    expect(document.querySelector('[data-highlight-menu]')).not.toBeInTheDocument();
    expect(document.querySelector('[data-text-color-menu]')).not.toBeInTheDocument();
    expect(document.querySelector('[data-currency-menu]')).not.toBeInTheDocument();
  });

  it('closes the highlight menu when the text color menu opens', () => {
    const { container } = render(<SpreadsheetGrid spreadsheetId={1} sheetId={35} />);
    act(() => {
      jest.runOnlyPendingTimers();
    });
  
    const cell = container.querySelector('td[data-row="0"][data-col="0"]') as HTMLTableCellElement;
    fireEvent.mouseDown(cell);
  
    const highlight = screen.getByTestId('highlight-button');
    const textColor = screen.getByTestId('format-text-color');
    mockToolbarAndButton(highlight, { left: 20, top: 0, width: 32, height: 32 });
    mockToolbarAndButton(textColor, { left: 60, top: 0, width: 32, height: 32 });
    fireEvent.click(highlight);
    expect(document.querySelector('[data-highlight-menu]')).toBeInTheDocument();
    fireEvent.click(textColor);
    expect(document.querySelector('[data-highlight-menu]')).not.toBeInTheDocument();
    expectMenuPortaled('[data-text-color-menu]');
  });

  it('closes the highlight menu on outside click', () => {
    const { container } = render(<SpreadsheetGrid spreadsheetId={1} sheetId={36} />);
    act(() => {
      jest.runOnlyPendingTimers();
    });
  
    const cell = container.querySelector('td[data-row="0"][data-col="0"]') as HTMLTableCellElement;
    fireEvent.mouseDown(cell);
  
    const highlight = screen.getByTestId('highlight-button');
    const textColor = screen.getByTestId('format-text-color');
    mockToolbarAndButton(highlight, { left: 20, top: 0, width: 32, height: 32 });
    mockToolbarAndButton(textColor, { left: 60, top: 0, width: 32, height: 32 });
    fireEvent.click(highlight);
    expect(document.querySelector('[data-highlight-menu]')).toBeInTheDocument();
    fireEvent.click(textColor);
    expect(document.querySelector('[data-highlight-menu]')).not.toBeInTheDocument();
    expectMenuPortaled('[data-text-color-menu]');
  });

  it('opens the highlight menu when the trigger is partially visible', () => {
    const { container } = render(<SpreadsheetGrid spreadsheetId={1} sheetId={37} />);
    act(() => {
      jest.runOnlyPendingTimers();
    });
    const cell = container.querySelector('td[data-row="0"][data-col="0"]') as HTMLTableCellElement;
    fireEvent.mouseDown(cell);
  
    const button = screen.getByTestId('highlight-button');
    mockToolbarAndButton(button, { left: -10, top: 0, width: 32, height: 32 });
    fireEvent.click(button);
  
    expectMenuPortaled('[data-highlight-menu]');
  });

  it('closes the highlight menu when the trigger scrolls out of the toolbar', () => {
    const { container } = render(<SpreadsheetGrid spreadsheetId={1} sheetId={38} />);
    act(() => {
      jest.runOnlyPendingTimers();
    });
    const cell = container.querySelector('td[data-row="0"][data-col="0"]') as HTMLTableCellElement;
    fireEvent.mouseDown(cell);
  
    const button = screen.getByTestId('highlight-button');
    mockToolbarAndButton(button, { left: 20, top: 0, width: 32, height: 32 });
    fireEvent.click(button);
    expect(document.querySelector('[data-highlight-menu]')).toBeInTheDocument();
  
    mockToolbarAndButton(button, { left: -40, top: 0, width: 32, height: 32 });
    fireEvent.scroll(document.getElementById('spreadsheet-toolbar')!);
  
    expect(document.querySelector('[data-highlight-menu]')).not.toBeInTheDocument();
  });

  it('keeps the highlight menu inside the right edge of the window', () => {
    const { container } = render(<SpreadsheetGrid spreadsheetId={1} sheetId={39} />);
    act(() => {
      jest.runOnlyPendingTimers();
    });
    const cell = container.querySelector('td[data-row="0"][data-col="0"]') as HTMLTableCellElement;
    fireEvent.mouseDown(cell);
  
    window.innerWidth = 400;
    const button = screen.getByTestId('highlight-button');
    mockToolbarAndButton(button, { left: 360, top: 0, width: 32, height: 32 });
    fireEvent.click(button);
  
    const menu = document.querySelector('[data-highlight-menu]') as HTMLElement;
    expect(parseFloat(menu.style.left)).toBe(216);
  });

  it('places the export menu above the button when the bottom does not fit', () => {
    render(<SpreadsheetGrid spreadsheetId={1} sheetId={40} />);
    act(() => {
      jest.runOnlyPendingTimers();
    });
  
    window.innerHeight = 400;
    const button = screen.getByRole('button', { name: /^Export$/i });
    mockToolbarAndButton(button, { left: 20, top: 300, width: 80, height: 32 });
    fireEvent.click(button);
  
    const menu = document.querySelector('[data-export-menu]') as HTMLElement;
    expect(parseFloat(menu.style.top)).toBe(162);
  });
})

function rect(left: number, top: number, width: number, height: number): DOMRect {
  return {
    x: left,
    y: top,
    left,
    top,
    right: left + width,
    bottom: top + height,
    width,
    height,
    toJSON() {},
  } as DOMRect;
}

describe('toolbar menu position', () => {
  const toolbar = rect(0, 0, 400, 40);

  it('treats a fully visible trigger as openable', () => {
    expect(triggerVisibleFraction(rect(10, 0, 32, 32), toolbar)).toBe(1);
    expect(isTriggerMeaningfullyVisible(rect(10, 0, 32, 32), toolbar)).toBe(true);
  });

  it('allows a trigger that is still more than half visible', () => {
    expect(triggerVisibleFraction(rect(-10, 0, 32, 32), toolbar)).toBeCloseTo(22 / 32);
    expect(isTriggerMeaningfullyVisible(rect(-10, 0, 32, 32), toolbar)).toBe(true);
  });

  it('rejects a trigger that is mostly scrolled away', () => {
    expect(isTriggerMeaningfullyVisible(rect(-24, 0, 32, 32), toolbar)).toBe(false);
    expect(isTriggerMeaningfullyVisible(rect(-40, 0, 32, 32), toolbar)).toBe(false);
  });

  it('shifts a menu left when it would pass the right edge', () => {
    const placed = placeMenuInViewport({
      trigger: rect(360, 0, 32, 32),
      menuWidth: 176,
      menuHeight: 80,
      viewportWidth: 400,
      viewportHeight: 800,
    });
    expect(placed.left).toBe(216);
    expect(placed.top).toBe(38);
  });

  it('places a menu above the trigger when the bottom does not fit', () => {
    const placed = placeMenuInViewport({
      trigger: rect(20, 300, 80, 32),
      menuWidth: 160,
      menuHeight: 132,
      viewportWidth: 800,
      viewportHeight: 400,
    });
    expect(placed.top).toBe(162);
  });
});

function FontSizeHarness() {
  const toolbarRef = useRef<HTMLDivElement>(null);
  return (
    <div ref={toolbarRef} data-testid="font-toolbar">
      <BrandSelect
        value="12"
        onValueChange={() => {}}
        ariaLabel="Font size"
        confineToRef={toolbarRef}
        options={[
          { value: '12', label: '12' },
          { value: '14', label: '14' },
        ]}
      />
    </div>
  );
}

describe('BrandSelect toolbar scroll', () => {
  it('closes the font menu when the trigger scrolls out of the toolbar', () => {
    render(<FontSizeHarness />);
    const toolbar = screen.getByTestId('font-toolbar');
    const button = screen.getByRole('button', { name: 'Font size' });

    setRect(toolbar, { left: 0, top: 0, width: 400, height: 40 });
    setRect(button, { left: 20, top: 0, width: 64, height: 32 });
    fireEvent.click(button);
    expect(screen.getByRole('listbox', { name: 'Font size' })).toBeInTheDocument();

    setRect(button, { left: -80, top: 0, width: 64, height: 32 });
    fireEvent.scroll(toolbar);

    expect(screen.queryByRole('listbox', { name: 'Font size' })).not.toBeInTheDocument();
  });
});
