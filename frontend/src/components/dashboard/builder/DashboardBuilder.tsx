'use client';

import { DndContext, DragOverlay, KeyboardSensor, PointerSensor, useDraggable, useSensor, useSensors, type DragEndEvent, type DragMoveEvent, type DragStartEvent } from '@dnd-kit/core';
import { GripVertical, Plus, RotateCcw, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useRef, useState, type PointerEvent } from 'react';
import { DashboardAPI } from '@/lib/api/dashboardApi';
import type { DashboardWidgetPosition as Widget } from '@/types/dashboardLayout';
import { GRID_COLUMNS, ROW_HEIGHT, addWidget, moveWidget, removeWidget, resizeWidget } from './layoutReducer';
import { widgetById, widgetRegistry, type WidgetContext } from './widgetRegistry';
import { WorkspaceDashboardProvider } from '@/components/projects/WorkspaceDashboard';
import { DashboardTileControlsProvider } from './DashboardTileControls';

const GAP = 12;
const ROW_STEP = ROW_HEIGHT + GAP;

function WidgetTile({ widget, context, onResize, onResizeStart, onResizePreview, onResizeEnd, onRemove, columnStep, dragging, resizing }: {
  widget: Widget;
  context: WidgetContext;
  onResize: (id: string, dw: number, dh: number) => void;
  onResizeStart: (id: string) => void;
  onResizePreview: (id: string, dw: number, dh: number) => void;
  onResizeEnd: () => void;
  onRemove: (id: string) => void;
  columnStep: number;
  dragging: boolean;
  resizing: boolean;
}) {
  const definition = widgetById[widget.id];
  const { attributes, listeners, setNodeRef, setActivatorNodeRef } = useDraggable({ id: widget.id });
  const resizeCleanup = useRef<(() => void) | null>(null);
  const onResizeEndRef = useRef(onResizeEnd);
  onResizeEndRef.current = onResizeEnd;
  const resizeCellRef = useRef('0:0');
  const title = definition?.title ?? widget.id;
  const dragHandle = (
    <button
      ref={setActivatorNodeRef}
      type="button"
      aria-label={`Move ${title}`}
      title={`Move ${title}`}
      className="flex h-7 w-7 shrink-0 touch-none cursor-grab items-center justify-center rounded bg-white text-gray-500 opacity-0 transition-colors group-hover:opacity-100 group-focus-within:opacity-100 hover:text-gray-700 active:cursor-grabbing focus-visible:opacity-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500 [@media(hover:none)]:opacity-80"
      {...attributes}
      {...listeners}
    >
      <GripVertical className="h-4 w-4" />
    </button>
  );
  const removeButton = (
    <button
      type="button"
      aria-label={`Remove ${title}`}
      title={`Remove ${title}`}
      onClick={() => onRemove(widget.id)}
      className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-gray-400 opacity-0 transition-colors group-hover:opacity-100 group-focus-within:opacity-100 hover:text-red-600 focus-visible:opacity-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500 [@media(hover:none)]:opacity-80"
    >
      <Trash2 className="h-4 w-4" />
    </button>
  );

  useEffect(() => () => {
    if (resizeCleanup.current) {
      resizeCleanup.current();
      onResizeEndRef.current();
    }
  }, []);

  const beginResize = (event: PointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0 || resizeCleanup.current) return;
    event.preventDefault();
    event.stopPropagation();
    const { pointerId, clientX: startX, clientY: startY } = event;
    const grid = event.currentTarget.closest<HTMLElement>('.dashboard-builder-grid');
    grid?.setPointerCapture(pointerId);
    resizeCellRef.current = '0:0';
    onResizeStart(widget.id);

    const delta = (pointer: globalThis.PointerEvent) => ({
      dw: Math.round((pointer.clientX - startX) / columnStep),
      dh: Math.round((pointer.clientY - startY) / ROW_STEP),
    });
    const move = (pointer: globalThis.PointerEvent) => {
      if (pointer.pointerId !== pointerId) return;
      const { dw, dh } = delta(pointer);
      const cell = `${dw}:${dh}`;
      if (cell === resizeCellRef.current) return;
      resizeCellRef.current = cell;
      onResizePreview(widget.id, dw, dh);
    };
    const cleanup = () => {
      window.removeEventListener('pointermove', move, true);
      window.removeEventListener('pointerup', finish, true);
      window.removeEventListener('pointercancel', cancel, true);
      if (grid?.hasPointerCapture(pointerId)) grid.releasePointerCapture(pointerId);
      resizeCleanup.current = null;
    };
    const finish = (pointer: globalThis.PointerEvent) => {
      if (pointer.pointerId !== pointerId) return;
      const { dw, dh } = delta(pointer);
      cleanup();
      onResizeEnd();
      onResize(widget.id, dw, dh);
    };
    const cancel = (pointer: globalThis.PointerEvent) => {
      if (pointer.pointerId !== pointerId) return;
      cleanup();
      onResizeEnd();
    };
    resizeCleanup.current = cleanup;
    // The grid stays mounted while previews move the handle; keep capture there.
    window.addEventListener('pointermove', move, true);
    window.addEventListener('pointerup', finish, true);
    window.addEventListener('pointercancel', cancel, true);
  };

  return (
    <section
      ref={setNodeRef}
      data-testid={`dashboard-widget-${widget.id}`}
      className={`group relative h-full min-h-0 min-w-0 overflow-visible rounded-xl hover:z-20 focus-within:z-20 ${dragging ? 'z-10 bg-cyan-50/40 outline outline-2 outline-dashed outline-cyan-400' : resizing ? 'z-10' : ''}`}
      style={{
        gridColumn: `${widget.x + 1} / span ${widget.w}`,
        gridRow: `${widget.y + 1} / span ${widget.h}`,
      }}
    >
      <div className="h-full min-h-0 overflow-auto p-px" id={widget.id}>
        <DashboardTileControlsProvider value={{ widgetId: widget.id, dragHandle, removeButton }}>
          {definition ? definition.render(context) : <p className="p-3 text-sm text-gray-500">This widget is unavailable.</p>}
        </DashboardTileControlsProvider>
      </div>
      <button
        type="button"
        aria-label={`Resize ${definition?.title ?? widget.id}`}
        className="absolute bottom-0 right-0 z-10 flex h-10 w-10 cursor-nwse-resize touch-none items-end justify-end rounded-tl-md p-2 text-gray-500 opacity-0 group-hover:opacity-100 hover:text-cyan-700 focus-visible:text-cyan-700 focus-visible:opacity-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500 [@media(hover:none)]:opacity-60"
        onPointerDown={beginResize}
        onKeyDown={(event) => {
          const keys: Record<string, [number, number]> = { ArrowRight: [1, 0], ArrowLeft: [-1, 0], ArrowDown: [0, 1], ArrowUp: [0, -1] };
          const delta = keys[event.key];
          if (delta) { event.preventDefault(); onResize(widget.id, delta[0], delta[1]); }
        }}
      ><svg viewBox="0 0 20 20" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true"><path d="M5 15 15 5M10 15l5-5" /></svg></button>
    </section>
  );
}

export default function DashboardBuilder(context: WidgetContext) {
  const { projectId } = context;
  const [widgets, setWidgets] = useState<Widget[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [saveState, setSaveState] = useState<'saved' | 'saving' | 'failed'>('saved');
  const [pickerOpen, setPickerOpen] = useState(false);
  const [gridWidth, setGridWidth] = useState(1200);
  const [dragPreview, setDragPreview] = useState<Widget[] | null>(null);
  const [activeDragId, setActiveDragId] = useState<string | null>(null);
  const [resizePreview, setResizePreview] = useState<Widget[] | null>(null);
  const [activeResizeId, setActiveResizeId] = useState<string | null>(null);
  const [gestureGridMinHeight, setGestureGridMinHeight] = useState<number | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const gestureScrollRef = useRef<{ element: HTMLElement; overflowAnchor: string } | null>(null);
  const dragCellRef = useRef('0:0');
  const widgetsRef = useRef<Widget[]>([]);
  const pendingRef = useRef<Widget[] | null>(null);
  const savingRef = useRef(false);
  const loadVersionRef = useRef(0);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }), useSensor(KeyboardSensor));

  const load = useCallback(async () => {
    const version = ++loadVersionRef.current;
    setLoadError(false);
    try {
      const { data } = await DashboardAPI.getLayout(projectId);
      if (version !== loadVersionRef.current || pendingRef.current) return;
      if (String(projectId) !== String(data.project_id) && String(projectId) !== data.project_slug) {
        throw new Error('Dashboard layout belongs to a different project');
      }
      if (!Array.isArray(data.widgets)) throw new Error('Invalid dashboard layout response');
      widgetsRef.current = data.widgets;
      setWidgets(data.widgets);
      setLoaded(true);
    } catch {
      if (version === loadVersionRef.current) setLoadError(true);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
    return () => { loadVersionRef.current += 1; };
  }, [load]);

  useEffect(() => {
    if (!loaded || !gridRef.current) return;
    const grid = gridRef.current;
    const measure = () => setGridWidth(grid.clientWidth);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(grid);
    return () => observer.disconnect();
  }, [loaded]);

  useEffect(() => () => {
    if (gestureScrollRef.current) {
      gestureScrollRef.current.element.style.overflowAnchor = gestureScrollRef.current.overflowAnchor;
      gestureScrollRef.current = null;
    }
  }, []);

  const flush = useCallback(async () => {
    if (savingRef.current || !pendingRef.current) return;
    savingRef.current = true;
    setSaveState('saving');
    try {
      while (pendingRef.current) {
        const submitted: Widget[] = pendingRef.current;
        await DashboardAPI.saveLayout(projectId, submitted);
        if (pendingRef.current === submitted) pendingRef.current = null;
      }
      setSaveState('saved');
    } catch {
      // Keep the newest local layout for a manual retry or the next gesture.
      setSaveState('failed');
    } finally {
      savingRef.current = false;
    }
  }, [projectId]);

  const commit = (update: (current: Widget[]) => Widget[]) => {
    const next = update(widgetsRef.current);
    setResizePreview(null);
    setDragPreview(null);
    setActiveResizeId(null);
    setActiveDragId(null);
    endGesture();
    if (JSON.stringify(next) === JSON.stringify(widgetsRef.current)) return;
    widgetsRef.current = next;
    setWidgets(next);
    pendingRef.current = next;
    void flush();
  };

  const columnStep = (gridWidth + GAP) / GRID_COLUMNS;
  const snappedDelta = (event: DragMoveEvent | DragEndEvent) => ({
    dx: window.innerWidth < 768 ? 0 : Math.round(event.delta.x / columnStep),
    dy: Math.round(event.delta.y / ROW_STEP),
  });
  const beginGesture = () => {
    const grid = gridRef.current;
    setGestureGridMinHeight(grid?.getBoundingClientRect().height ?? null);
    const scrollContainer = grid?.closest('main');
    if (scrollContainer) {
      gestureScrollRef.current = { element: scrollContainer, overflowAnchor: scrollContainer.style.overflowAnchor };
      scrollContainer.style.overflowAnchor = 'none';
    }
  };
  const endGesture = () => {
    setGestureGridMinHeight(null);
    if (gestureScrollRef.current) {
      gestureScrollRef.current.element.style.overflowAnchor = gestureScrollRef.current.overflowAnchor;
      gestureScrollRef.current = null;
    }
  };
  const onDragStart = (event: DragStartEvent) => {
    beginGesture();
    setActiveDragId(String(event.active.id));
    setDragPreview(widgetsRef.current);
    dragCellRef.current = '0:0';
  };
  const onDragMove = (event: DragMoveEvent) => {
    const { dx, dy } = snappedDelta(event);
    const cell = `${dx}:${dy}`;
    if (cell === dragCellRef.current) return;
    dragCellRef.current = cell;
    setDragPreview(moveWidget(widgetsRef.current, String(event.active.id), dx, dy));
  };
  const onDragEnd = (event: DragEndEvent) => {
    const { dx, dy } = snappedDelta(event);
    setDragPreview(null);
    setActiveDragId(null);
    endGesture();
    if (!event.active) return;
    commit((current) => moveWidget(current, String(event.active.id), dx, dy));
  };
  const startResize = (id: string) => {
    beginGesture();
    setActiveResizeId(id);
    setResizePreview(widgetsRef.current);
  };
  const endResize = () => {
    setResizePreview(null);
    setActiveResizeId(null);
    endGesture();
  };
  const visibleWidgets = activeResizeId !== null && resizePreview
    ? resizePreview
    : activeDragId !== null && dragPreview
      ? dragPreview
      : widgets;

  if (loadError) return <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">Dashboard layout could not be loaded. <button type="button" onClick={() => void load()} className="underline">Retry</button></div>;
  if (!loaded) return <div className="rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-500">Loading dashboard layout…</div>;

  return (
    <WorkspaceDashboardProvider projectId={projectId}>
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold text-gray-900">Overview</h2>
        <div className="flex items-center gap-2">
          <span role={saveState === 'failed' ? 'alert' : 'status'} className={`text-xs ${saveState === 'failed' ? 'text-red-700' : 'text-gray-500'}`}>
            {saveState === 'failed' ? 'Could not save layout' : saveState === 'saving' ? 'Saving…' : 'Saved'}
          </span>
          {saveState === 'failed' && <button type="button" onClick={() => void flush()} className="flex items-center gap-1 rounded border border-red-200 px-2 py-1 text-xs text-red-700"><RotateCcw className="h-3 w-3" />Retry</button>}
          <button type="button" aria-expanded={pickerOpen} onClick={() => setPickerOpen(!pickerOpen)} className="flex items-center gap-1 rounded-md border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"><Plus className="h-3.5 w-3.5" /> Add widget</button>
        </div>
      </div>
      {pickerOpen && <div className="mb-3 flex flex-wrap gap-2 rounded-lg border border-gray-200 bg-gray-50 p-2" aria-label="Widget picker">
        {widgetRegistry.filter((definition) => !widgets.some((widget) => widget.id === definition.id)).map((definition) => <button type="button" key={definition.id} onClick={() => {
          commit((current) => addWidget(current, definition.defaultPosition));
          if (widgetRegistry.every((item) => widgetsRef.current.some((widget) => widget.id === item.id))) setPickerOpen(false);
        }} className="rounded border border-gray-200 bg-white px-2 py-1 text-xs text-gray-700 hover:border-cyan-400">{definition.title}</button>)}
        {widgetRegistry.every((definition) => widgets.some((widget) => widget.id === definition.id)) && <span className="text-xs text-gray-500">All widgets are on your dashboard.</span>}
      </div>}
      <DndContext sensors={sensors} onDragStart={onDragStart} onDragMove={onDragMove} onDragEnd={onDragEnd} onDragCancel={() => { setDragPreview(null); setActiveDragId(null); endGesture(); }}>
        <div style={{ minHeight: gestureGridMinHeight ?? undefined }}>
          <div ref={gridRef} className="dashboard-builder-grid grid grid-cols-12 gap-3" style={{ gridAutoRows: `${ROW_HEIGHT}px` }}>
            {visibleWidgets.map((widget) => <WidgetTile key={widget.id} widget={widget} context={context} columnStep={columnStep} dragging={activeDragId === widget.id} resizing={activeResizeId === widget.id} onResizeStart={startResize} onResizePreview={(id, dw, dh) => setResizePreview(resizeWidget(widgetsRef.current, id, dw, dh))} onResizeEnd={endResize} onResize={(id, dw, dh) => commit((current) => resizeWidget(current, id, dw, dh))} onRemove={(id) => commit((current) => removeWidget(current, id))} />)}
          </div>
        </div>
        <DragOverlay dropAnimation={null}>
          {activeDragId && <div className="pointer-events-none flex items-center gap-1 rounded-md border border-cyan-300 bg-white px-3 py-2 text-xs font-medium text-gray-700 shadow-lg"><GripVertical className="h-3.5 w-3.5 text-cyan-600" />{widgetById[activeDragId]?.title ?? activeDragId}</div>}
        </DragOverlay>
      </DndContext>
      {widgets.length === 0 && <p className="rounded-lg border border-dashed border-gray-200 p-6 text-center text-sm text-gray-500">Your dashboard is empty. Add a widget to get started.</p>}
    </div>
    </WorkspaceDashboardProvider>
  );
}
