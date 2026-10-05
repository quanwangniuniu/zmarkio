'use client';

import { DndContext, DragOverlay, KeyboardSensor, PointerSensor, useDraggable, useDroppable, useSensor, useSensors, type DragEndEvent, type DragMoveEvent, type DragStartEvent } from '@dnd-kit/core';
import { GripVertical, Pencil, Plus, RotateCcw, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useRef, useState, type CSSProperties, type PointerEvent, type ReactNode, type RefObject } from 'react';
import { DashboardAPI } from '@/lib/api/dashboardApi';
import type { DashboardWidgetPosition as Widget } from '@/types/dashboardLayout';
import { GRID_COLUMNS, ROW_HEIGHT, RESIZE_STEP, addWidget, dropWidget, moveWidget, removeWidget, resizeDelta, resizeWidget } from './layoutReducer';
import { createWidget, getWidgetDefinition, widgetById, widgetRegistry, type WidgetContext } from './widgetRegistry';
import { isSectionTitle } from './sectionTitle';
import { WorkspaceDashboardProvider } from '@/components/projects/WorkspaceDashboard';
import { DashboardTileControlsProvider } from './DashboardTileControls';

const GAP = 12;
const ROW_STEP = ROW_HEIGHT + GAP;

/** Fractional grid units retain responsive widths and existing saved layouts. */
function widgetStyle(widget: Widget): CSSProperties {
  return {
    position: 'absolute',
    left: `calc(${widget.x / GRID_COLUMNS * 100}% + ${widget.x * GAP / GRID_COLUMNS}px)`,
    top: widget.y * ROW_STEP,
    width: `calc(${widget.w / GRID_COLUMNS * 100}% + ${widget.w * GAP / GRID_COLUMNS - GAP}px)`,
    height: widget.h * ROW_STEP - GAP,
  };
}

function SectionTitle({ widget, editing, onChange, children }: { widget: Widget; editing: boolean; onChange: (id: string, title: string) => void; children: ReactNode }) {
  const [draft, setDraft] = useState(widget.title ?? 'Section title');
  useEffect(() => setDraft(widget.title ?? 'Section title'), [widget.title]);
  const save = () => {
    const title = draft.trim();
    if (title) { setDraft(title); onChange(widget.id, title); }
    else setDraft(widget.title ?? 'Section title');
  };
  return <div className="relative flex h-full min-w-0 items-center pr-20">
    {editing ? <input aria-label="Section title text" title="Edit section title" maxLength={80} value={draft}
      className="w-56 min-w-0 max-w-full rounded border-0 bg-transparent p-0 text-[10px] font-semibold uppercase tracking-[0.06em] text-gray-400 focus:outline-none focus:ring-1 focus:ring-cyan-500"
      onChange={(event) => setDraft(event.target.value)} onBlur={save}
      onKeyDown={(event) => {
        if (event.key === 'Enter') event.currentTarget.blur();
        if (event.key === 'Escape') { setDraft(widget.title ?? 'Section title'); event.preventDefault(); }
      }} /> : <h3 className="w-56 min-w-0 max-w-full truncate text-[10px] font-semibold uppercase tracking-[0.06em] text-gray-400">{widget.title ?? 'Section title'}</h3>}
    <div className="absolute right-1 top-1 z-20 flex items-center gap-1">{children}</div>
  </div>;
}

function WidgetTile({ widget, editing, context, onResize, onResizeStart, onResizePreview, onResizeEnd, onRemove, onTitleChange, columnStep, dragging, resizing }: {
  widget: Widget;
  editing: boolean;
  context: WidgetContext;
  onResize: (id: string, dw: number, dh: number) => void;
  onResizeStart: (id: string) => void;
  onResizePreview: (id: string, dw: number, dh: number) => void;
  onResizeEnd: () => void;
  onRemove: (id: string) => void;
  onTitleChange: (id: string, title: string) => void;
  columnStep: number;
  dragging: boolean;
  resizing: boolean;
}) {
  const definition = getWidgetDefinition(widget.id);
  const { attributes, listeners, setNodeRef, setActivatorNodeRef } = useDraggable({ id: widget.id, disabled: !editing });
  const resizeCleanup = useRef<(() => void) | null>(null);
  const onResizeEndRef = useRef(onResizeEnd);
  onResizeEndRef.current = onResizeEnd;
  const resizeCellRef = useRef('0:0');
  const title = widget.title ?? definition?.title ?? widget.id;
  const dragHandle = editing ? (
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
  ) : null;
  const removeButton = editing ? (
    <button
      type="button"
      aria-label={`Remove ${title}`}
      title={`Remove ${title}`}
      onClick={() => onRemove(widget.id)}
      className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-gray-400 opacity-0 transition-colors group-hover:opacity-100 group-focus-within:opacity-100 hover:text-red-600 focus-visible:opacity-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500 [@media(hover:none)]:opacity-80"
    >
      <Trash2 className="h-4 w-4" />
    </button>
  ) : null;

  useEffect(() => () => {
    if (resizeCleanup.current) {
      resizeCleanup.current();
      onResizeEndRef.current();
    }
  }, [editing]);

  const beginResize = (event: PointerEvent<HTMLButtonElement>) => {
    if (!editing || event.button !== 0 || resizeCleanup.current) return;
    event.preventDefault();
    event.stopPropagation();
    const { pointerId, clientX: startX, clientY: startY } = event;
    const grid = event.currentTarget.closest<HTMLElement>('.dashboard-builder-grid');
    grid?.setPointerCapture(pointerId);
    resizeCellRef.current = '0:0';
    onResizeStart(widget.id);

    const delta = (pointer: globalThis.PointerEvent) => resizeDelta(pointer.clientX - startX, pointer.clientY - startY, columnStep);
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
      data-section-title={isSectionTitle(widget.id) ? true : undefined}
      className={`group relative h-full min-h-0 min-w-0 overflow-visible rounded-xl hover:z-20 focus-within:z-20 ${dragging ? 'z-10 bg-cyan-50/40 outline outline-2 outline-dashed outline-cyan-400' : resizing ? 'z-10' : ''}`}
      style={widgetStyle(widget)}
    >
      <div className="h-full min-h-0 overflow-auto p-px" id={widget.id}>
        <DashboardTileControlsProvider value={editing ? { widgetId: widget.id, dragHandle, removeButton } : null}>
          {isSectionTitle(widget.id) ? <SectionTitle widget={widget} editing={editing} onChange={onTitleChange}>{dragHandle}{removeButton}</SectionTitle> : definition ? definition.render(context) : <p className="p-3 text-sm text-gray-500">This widget is unavailable.</p>}
        </DashboardTileControlsProvider>
      </div>
      {editing && <button
        type="button"
        aria-label={`Resize ${title}`}
        className={`absolute bottom-0 right-0 z-10 flex ${isSectionTitle(widget.id) ? 'h-4 w-4' : 'h-10 w-10 p-2'} cursor-nwse-resize touch-none items-end justify-end rounded-tl-md text-gray-500 opacity-0 group-hover:opacity-100 hover:text-cyan-700 focus-visible:text-cyan-700 focus-visible:opacity-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500 [@media(hover:none)]:opacity-60`}
        onPointerDown={beginResize}
        onKeyDown={(event) => {
          const keys: Record<string, [number, number]> = { ArrowRight: [1, 0], ArrowLeft: [-1, 0], ArrowDown: [0, 1], ArrowUp: [0, -1] };
          const delta = keys[event.key];
          if (delta) {
            event.preventDefault();
            const { dw, dh } = resizeDelta(delta[0] * RESIZE_STEP, delta[1] * RESIZE_STEP, columnStep);
            onResize(widget.id, dw, dh);
          }
        }}
      ><svg viewBox="0 0 20 20" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true"><path d="M5 15 15 5M10 15l5-5" /></svg></button>}
    </section>
  );
}

function PaletteWidget({ id, title, onAdd }: { id: string; title: string; onAdd: () => void }) {
  const { setNodeRef, attributes, listeners, isDragging } = useDraggable({ id: `palette:${id}` });
  return <div ref={setNodeRef} className={`flex items-center rounded border border-gray-200 bg-white text-xs text-gray-700 ${isDragging ? 'opacity-40' : 'hover:border-cyan-400'}`}>
    <button type="button" aria-label={`Drag ${title} onto dashboard`} onClick={onAdd} {...attributes} {...listeners}
      className="flex touch-none cursor-grab items-center gap-1.5 rounded px-2 py-1.5 active:cursor-grabbing focus-visible:outline focus-visible:outline-cyan-500">
      <GripVertical className="h-3.5 w-3.5 text-gray-400" />{title}
    </button>
    <button type="button" aria-label={`Add ${title}`} onClick={onAdd} className="rounded p-1.5 text-gray-500 hover:bg-cyan-50 hover:text-cyan-700"><Plus className="h-3.5 w-3.5" /></button>
  </div>;
}

function Canvas({ gridRef, children, minHeight }: { gridRef: RefObject<HTMLDivElement>; children: ReactNode; minHeight: number }) {
  const { setNodeRef } = useDroppable({ id: 'dashboard-canvas' });
  return <div ref={(node) => { setNodeRef(node); (gridRef as React.MutableRefObject<HTMLDivElement | null>).current = node; }}
    data-testid="dashboard-canvas" className="dashboard-builder-grid relative" style={{ height: minHeight, minHeight }}>
    {children}
  </div>;
}

export default function DashboardBuilder(context: WidgetContext) {
  const { projectId } = context;
  const [widgets, setWidgets] = useState<Widget[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [saveState, setSaveState] = useState<'saved' | 'saving' | 'failed'>('saved');
  const [editing, setEditing] = useState(false);
  const editingRef = useRef(editing);
  editingRef.current = editing;
  const [gridWidth, setGridWidth] = useState(1200);
  const [dragPreview, setDragPreview] = useState<Widget[] | null>(null);
  const [activeDragId, setActiveDragId] = useState<string | null>(null);
  const [resizePreview, setResizePreview] = useState<Widget[] | null>(null);
  const [activeResizeId, setActiveResizeId] = useState<string | null>(null);
  const [gestureGridMinHeight, setGestureGridMinHeight] = useState<number | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const gestureScrollRef = useRef<{ element: HTMLElement; overflowAnchor: string } | null>(null);
  const dragCellRef = useRef('0:0');
  const dragActiveRef = useRef(false);
  const paletteRef = useRef<Widget | null>(null);
  const pointerRef = useRef<{ x: number; y: number } | null>(null);
  const [paletteDrag, setPaletteDrag] = useState(false);
  const widgetsRef = useRef<Widget[]>([]);
  const pendingRef = useRef<Widget[] | null>(null);
  const savingRef = useRef(false);
  const loadVersionRef = useRef(0);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }), useSensor(KeyboardSensor));

  useEffect(() => {
    const rememberPointer = (event: globalThis.PointerEvent) => { pointerRef.current = { x: event.clientX, y: event.clientY }; };
    // Keep viewport coordinates exact when the dashboard scrolls during a palette drag.
    window.addEventListener('pointermove', rememberPointer, true);
    window.addEventListener('pointerup', rememberPointer, true);
    return () => {
      window.removeEventListener('pointermove', rememberPointer, true);
      window.removeEventListener('pointerup', rememberPointer, true);
    };
  }, []);

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
    if (!editingRef.current) return;
    const next = update(widgetsRef.current);
    if (JSON.stringify(next) === JSON.stringify(widgetsRef.current)) return;
    widgetsRef.current = next;
    setWidgets(next);
    pendingRef.current = next;
    void flush();
  };

  const columnStep = (gridWidth + GAP) / GRID_COLUMNS;
  const displayedWidgets = resizePreview ?? dragPreview ?? widgets;
  const layoutHeight = Math.max(0, ...displayedWidgets.map((widget) => (widget.y + widget.h) * ROW_STEP - GAP));
  const snappedDelta = (event: DragMoveEvent | DragEndEvent) => {
    const { dw, dh } = resizeDelta(event.delta.x, event.delta.y, columnStep);
    const widget = widgetsRef.current.find((item) => item.id === String(event.active.id));
    let dx = window.innerWidth < 768 ? 0 : dw;
    let dy = dh;
    // Snap to the canvas edges when a rounded 10px step lands just beside them.
    if (widget) {
      if ((widget.x + dx) * columnStep < RESIZE_STEP / 2) dx = -widget.x;
      else if ((GRID_COLUMNS - widget.w - widget.x - dx) * columnStep < RESIZE_STEP / 2) dx = GRID_COLUMNS - widget.w - widget.x;
      if ((widget.y + dy) * ROW_STEP < RESIZE_STEP / 2) dy = -widget.y;
    }
    return { dx, dy };
  };
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
  const paletteTarget = (event: DragMoveEvent | DragEndEvent): Widget | null => {
    const incoming = paletteRef.current;
    const grid = gridRef.current;
    if (!incoming || !grid) return null;
    const rect = grid.getBoundingClientRect();
    const pointer = event.activatorEvent as globalThis.PointerEvent;
    const keyboard = event.activatorEvent.type === 'keydown';
    const translated = event.active.rect.current.translated;
    const x = keyboard ? translated?.left ?? rect.left : pointerRef.current?.x ?? pointer.clientX + event.delta.x;
    const y = keyboard ? translated?.top ?? rect.top : pointerRef.current?.y ?? pointer.clientY + event.delta.y;
    if (x < rect.left || x > rect.right || y < rect.top || y > rect.bottom) return null;
    return { ...incoming,
      x: window.innerWidth < 768 ? 0 : Math.max(0, Math.min(GRID_COLUMNS - incoming.w, Math.floor((x - rect.left) / columnStep))),
      y: Math.max(0, Math.min(999, Math.floor((y - rect.top) / ROW_STEP))),
    };
  };
  const onDragStart = (event: DragStartEvent) => {
    if (!editingRef.current) return;
    dragActiveRef.current = true;
    beginGesture();
    const dragId = String(event.active.id);
    const fromPalette = dragId.startsWith('palette:');
    const id = fromPalette ? dragId.slice(8) : dragId;
    paletteRef.current = fromPalette ? createWidget(widgetById[id]) : null;
    setPaletteDrag(fromPalette);
    setActiveDragId(paletteRef.current?.id ?? id);
    setDragPreview(widgetsRef.current);
    dragCellRef.current = fromPalette ? '' : '0:0';
  };
  const onDragMove = (event: DragMoveEvent) => {
    if (!editingRef.current || !dragActiveRef.current) return;
    if (paletteRef.current) {
      const target = paletteTarget(event);
      const cell = target ? `${target.x}:${target.y}` : 'outside';
      if (cell === dragCellRef.current) return;
      dragCellRef.current = cell;
      setDragPreview(target ? dropWidget(widgetsRef.current, target, target.x, target.y) : widgetsRef.current);
      return;
    }
    const { dx, dy } = snappedDelta(event);
    const cell = `${dx}:${dy}`;
    if (cell === dragCellRef.current) return;
    dragCellRef.current = cell;
    setDragPreview(moveWidget(widgetsRef.current, String(event.active.id), dx, dy));
  };
  const onDragEnd = (event: DragEndEvent) => {
    if (!editingRef.current || !dragActiveRef.current) return;
    dragActiveRef.current = false;
    if (paletteRef.current) {
      const target = paletteTarget(event);
      if (target) commit((current) => dropWidget(current, target, target.x, target.y));
    } else {
      const { dx, dy } = snappedDelta(event);
      commit((current) => moveWidget(current, String(event.active.id), dx, dy));
    }
    paletteRef.current = null;
    setPaletteDrag(false);
    setDragPreview(null);
    setActiveDragId(null);
    endGesture();
  };
  const startResize = (id: string) => {
    if (!editingRef.current) return;
    beginGesture();
    setActiveResizeId(id);
    setResizePreview(widgetsRef.current);
  };
  const endResize = () => {
    setResizePreview(null);
    setActiveResizeId(null);
    endGesture();
  };

  const cancelGesture = () => {
    dragActiveRef.current = false;
    paletteRef.current = null;
    setPaletteDrag(false);
    setDragPreview(null);
    setActiveDragId(null);
    endResize();
  };
  const toggleEditing = () => {
    if (editing) cancelGesture();
    // Block stale pointer/keyboard callbacks immediately when leaving editing.
    editingRef.current = !editing;
    setEditing(!editing);
  };

  if (loadError) return <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">Dashboard layout could not be loaded. <button type="button" onClick={() => void load()} className="underline">Retry</button></div>;
  if (!loaded) return <div className="rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-500">Loading dashboard layout…</div>;

  return (
    <WorkspaceDashboardProvider projectId={projectId}>
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-end gap-2">
        <h1 className="min-w-0 max-w-full break-words text-2xl font-semibold text-gray-900">{context.projectName?.trim() || 'Overview'}</h1>
        {editing && <p role="note" className="min-w-[16rem] flex-1 text-xs text-gray-500"><strong className="font-medium text-gray-600">Tips:</strong> Drag any component onto the dashboard to customize your layout. You can also rename section titles.</p>}
        <div className="ml-auto flex shrink-0 items-center gap-2">
          <span role={saveState === 'failed' ? 'alert' : 'status'} className={`text-xs ${saveState === 'failed' ? 'text-red-700' : 'text-gray-500'}`}>
            {saveState === 'failed' ? 'Could not save layout' : saveState === 'saving' ? 'Saving…' : 'Saved'}
          </span>
          {saveState === 'failed' && <button type="button" onClick={() => void flush()} className="flex items-center gap-1 rounded border border-red-200 px-2 py-1 text-xs text-red-700"><RotateCcw className="h-3 w-3" />Retry</button>}
          <button type="button" aria-expanded={editing} aria-pressed={editing} onClick={toggleEditing} className="flex items-center gap-1 rounded-md border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"><Pencil className="h-3.5 w-3.5" /> Customize</button>
        </div>
      </div>
      <DndContext sensors={editing ? sensors : []} onDragStart={onDragStart} onDragMove={onDragMove} onDragEnd={onDragEnd} onDragCancel={cancelGesture}>
      {editing && <div className="mb-3 flex flex-wrap gap-2 rounded-lg border border-gray-200 bg-gray-50 p-2" aria-label="Widget picker">
        {widgetRegistry.filter((definition) => !widgets.some((widget) => widget.id === definition.id)).map((definition) => <PaletteWidget key={definition.id} id={definition.id} title={definition.title} onAdd={() => commit((current) => addWidget(current, createWidget(definition)))} />)}
      </div>}
        <div style={{ minHeight: gestureGridMinHeight ?? undefined }}>
          <Canvas gridRef={gridRef} minHeight={Math.max(layoutHeight, gestureGridMinHeight ?? 0, paletteDrag ? (Math.max(0, ...widgets.map((widget) => widget.y + widget.h)) + (paletteRef.current?.h ?? 4)) * ROW_STEP : ROW_STEP * 4)}>
            {displayedWidgets.map((widget) => paletteDrag && widget.id === activeDragId ? <div key={widget.id} data-testid="widget-drop-preview" aria-hidden="true" className="pointer-events-none rounded-xl border-2 border-dashed border-cyan-400 bg-cyan-50/50" style={widgetStyle(widget)} /> : <WidgetTile key={widget.id} widget={widget} editing={editing} context={context} columnStep={columnStep} dragging={activeDragId === widget.id} resizing={activeResizeId === widget.id} onTitleChange={(id, title) => commit((current) => current.map((item) => item.id === id ? { ...item, title } : item))} onResizeStart={startResize} onResizePreview={(id, dw, dh) => { if (editingRef.current) setResizePreview(resizeWidget(widgetsRef.current, id, dw, dh)); }} onResizeEnd={endResize} onResize={(id, dw, dh) => commit((current) => resizeWidget(current, id, dw, dh))} onRemove={(id) => { setResizePreview(null); setDragPreview(null); setActiveResizeId(null); endGesture(); commit((current) => removeWidget(current, id)); }} />)}
          </Canvas>
        </div>
        <DragOverlay dropAnimation={null}>
          {activeDragId && <div className="pointer-events-none flex items-center gap-1 rounded-md border border-cyan-300 bg-white px-3 py-2 text-xs font-medium text-gray-700 shadow-lg"><GripVertical className="h-3.5 w-3.5 text-cyan-600" />{widgets.find((widget) => widget.id === activeDragId)?.title ?? getWidgetDefinition(activeDragId)?.title ?? activeDragId}</div>}
        </DragOverlay>
      </DndContext>
      {widgets.length === 0 && <p className="rounded-lg border border-dashed border-gray-200 p-6 text-center text-sm text-gray-500">Your dashboard is empty. Add a widget to get started.</p>}
    </div>
    </WorkspaceDashboardProvider>
  );
}
