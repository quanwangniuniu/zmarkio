'use client';

import {
  DndContext, DragOverlay, KeyboardSensor, PointerSensor, pointerWithin,
  useDraggable, useDroppable, useSensor, useSensors,
  type CollisionDetection, type DragEndEvent, type DragMoveEvent, type DragStartEvent,
} from '@dnd-kit/core';
import { ChevronRight, GripVertical, Layers3, Pencil, Plus, RotateCcw, Trash2, X } from 'lucide-react';
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type PointerEvent, type ReactNode } from 'react';
import { DashboardAPI } from '@/lib/api/dashboardApi';
import type { DashboardGroup, DashboardItem, DashboardLayoutDocument, DashboardWidget } from '@/types/dashboardLayout';
import {
  GRID_COLUMNS, ROW_GAP, ROW_HEIGHT, ROW_STEP, addGroup, allWidgetIds, findLocation, fitWidgetToVacancy, legacyDocument,
  moveLayoutItem, placeWidgetInContainer, removeLayoutItem, reorderLayoutGroup, resizeLayoutItem, snapNearPrevious, updateLayoutItem,
} from './layoutReducer';
import { widgetById, widgetRegistry, type WidgetContext } from './widgetRegistry';
import { WorkspaceDashboardProvider } from '@/components/projects/WorkspaceDashboard';
import { DashboardTileControlsProvider } from './DashboardTileControls';

const GAP = 12;
const CANVAS_DROP_ID = 'dashboard-canvas';
const groupDropId = (id: string) => `dashboard-group:${id}`;
const tileDragId = (id: string) => `tile:${id}`;
const paletteDragId = (id: string) => `palette:${id}`;
const layerDragId = (id: string) => `layer:${id}`;
const layerGroupDragId = (id: string) => `layer-group-drag:${id}`;
const layerItemDropId = (id: string) => `layer-item:${id}`;
const layerGroupDropId = (id: string) => `layer-group:${id}`;
const LAYER_ROOT_DROP_ID = 'layer-root';
const FLOATING_PANEL_WIDTH = 320;

function clampPanelPosition(left: number, top: number) {
  const width = Math.min(FLOATING_PANEL_WIDTH, Math.max(0, window.innerWidth - 24));
  return {
    left: Math.max(12, Math.min(left, window.innerWidth - width - 12)),
    top: Math.max(12, Math.min(top, window.innerHeight - 180)),
  };
}

function saveErrorMessage(error: unknown): string {
  const response = (error as { response?: { status?: number; data?: unknown } })?.response;
  const firstDetail = (value: unknown): string | null => {
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) return value.map(firstDetail).find(Boolean) ?? null;
    if (value && typeof value === 'object') {
      const entry = Object.entries(value).find(([, detail]) => firstDetail(detail));
      if (entry) return `${entry[0]}: ${firstDetail(entry[1])}`;
    }
    return null;
  };
  const detail = firstDetail(response?.data);
  return detail ? `Could not save layout: ${detail.slice(0, 160)}`
    : response?.status ? `Could not save layout (${response.status})` : 'Could not save layout';
}

/** A nested group wins over the surrounding canvas when the pointer is inside both. */
const collisionDetection: CollisionDetection = (args) => {
  const matches = pointerWithin(args);
  const panel = globalThis.document.querySelector<HTMLElement>('[data-floating-widgets-panel]');
  const panelRect = panel?.getBoundingClientRect();
  const pointer = args.pointerCoordinates;
  if (panelRect && pointer && pointer.x >= panelRect.left && pointer.x <= panelRect.right && pointer.y >= panelRect.top && pointer.y <= panelRect.bottom) {
    const item = matches.find((match) => String(match.id).startsWith('layer-item:'));
    const group = matches.find((match) => String(match.id).startsWith('layer-group:'));
    const root = matches.find((match) => String(match.id) === LAYER_ROOT_DROP_ID);
    if (String(args.active.id).startsWith('layer-group-drag:')) return group ? [group] : root ? [root] : [];
    return item ? [item] : group ? [group] : root ? [root] : [];
  }
  if (String(args.active.id).startsWith('layer:') || String(args.active.id).startsWith('palette:')) {
    const item = matches.find((match) => String(match.id).startsWith('layer-item:'));
    const group = matches.find((match) => String(match.id).startsWith('layer-group:'));
    if (item || group) return item ? [item] : [group!];
  }
  const group = matches.find((match) => String(match.id).startsWith('dashboard-group:'));
  return group ? [group] : matches;
};

function ResizeHandle({ title, columnStep, onPreview, onCommit, onStart, onEnd }: {
  title: string;
  columnStep: number;
  onPreview: (dw: number, dh: number) => void;
  onCommit: (dw: number, dh: number) => void;
  onStart: () => void;
  onEnd: () => void;
}) {
  const start = useRef<{ x: number; y: number } | null>(null);
  const lastCell = useRef('0:0');
  const delta = (event: PointerEvent<HTMLElement>) => start.current ? {
    dw: Math.round((event.clientX - start.current.x) / columnStep),
    dh: Math.round((event.clientY - start.current.y) / ROW_STEP),
  } : null;
  return <button
    type="button"
    aria-label={`Resize ${title}`}
    className="absolute bottom-0 right-0 z-30 flex h-9 w-9 cursor-nwse-resize touch-none items-end justify-end rounded-tl-md p-2 text-gray-500 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500 [@media(hover:none)]:opacity-70"
    onPointerDown={(event) => {
      event.stopPropagation();
      start.current = { x: event.clientX, y: event.clientY };
      lastCell.current = '0:0';
      onStart();
      event.currentTarget.setPointerCapture(event.pointerId);
    }}
    onPointerMove={(event) => {
      const change = delta(event);
      if (!change) return;
      const cell = `${change.dw}:${change.dh}`;
      if (cell === lastCell.current) return;
      lastCell.current = cell;
      onPreview(change.dw, change.dh);
    }}
    onPointerUp={(event) => {
      const change = delta(event);
      start.current = null;
      onEnd();
      if (change) onCommit(change.dw, change.dh);
    }}
    onPointerCancel={() => { start.current = null; onEnd(); }}
    onKeyDown={(event) => {
      const keys: Record<string, [number, number]> = { ArrowRight: [1, 0], ArrowLeft: [-1, 0], ArrowDown: [0, 1], ArrowUp: [0, -1] };
      const change = keys[event.key];
      if (change) { event.preventDefault(); onCommit(...change); }
    }}
  ><svg viewBox="0 0 20 20" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true"><path d="M5 15 15 5M10 15l5-5" /></svg></button>;
}

interface TileActions {
  editMode: boolean;
  onEdit: (id: string) => void;
  onRemove: (id: string) => void;
  onResizeStart: (id: string) => void;
  onResizePreview: (id: string, dw: number, dh: number) => void;
  onResizeEnd: () => void;
  onResize: (id: string, dw: number, dh: number) => void;
  activeDragId: string | null;
  activeResizeId: string | null;
  selectedId: string | null;
}

interface DropIndicator {
  groupId: string | null;
  x: number;
  y: number;
  w: number;
  h: number;
}

function DropPreview({ indicator }: { indicator: DropIndicator }) {
  return <div aria-hidden="true" data-testid="dashboard-drop-preview"
    className="pointer-events-none z-0 rounded-xl border-2 border-dashed border-cyan-400 bg-cyan-100/50"
    style={{ gridColumn: `${indicator.x + 1} / span ${indicator.w}`, gridRow: `${indicator.y + 1} / span ${indicator.h}` }} />;
}

function WidgetTile({ widget, context, columnStep, actions }: { widget: DashboardWidget; context: WidgetContext; columnStep: number; actions: TileActions }) {
  const definition = widgetById[widget.id];
  const title = widget.title ?? definition?.title ?? widget.id;
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform } = useDraggable({ id: tileDragId(widget.id) });
  const dragHandle = <button
    ref={setActivatorNodeRef} type="button" aria-label={`Move ${title}`} title={`Move ${title}`}
    className="-ml-2 flex h-7 w-7 shrink-0 touch-none cursor-grab items-center justify-center rounded text-gray-500 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 active:cursor-grabbing focus-visible:opacity-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500 [@media(hover:none)]:opacity-80"
    {...attributes} {...listeners}
  ><GripVertical className="h-4 w-4" /></button>;
  const removeButton = <button type="button" aria-label={`Remove ${title}`} title={`Remove ${title}`} onClick={() => actions.onRemove(widget.id)}
    className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-gray-400 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 hover:text-red-600 focus-visible:opacity-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500 [@media(hover:none)]:opacity-80"
  ><Trash2 className="h-4 w-4" /></button>;
  const accent = widget.settings?.accent ?? 'slate';
  return <section
    ref={setNodeRef}
    data-testid={`dashboard-widget-${widget.id}`}
    className={`group relative z-10 h-full min-h-0 min-w-0 overflow-visible rounded-xl hover:z-20 focus-within:z-20 ${accent === 'lime' ? 'ring-1 ring-lime-300' : accent === 'cyan' ? 'ring-1 ring-cyan-300' : ''} ${actions.activeDragId === widget.id ? 'z-30 cursor-grabbing shadow-xl' : actions.activeResizeId === widget.id ? 'z-20' : ''}`}
    style={{ gridColumn: `${widget.x + 1} / span ${widget.w}`, gridRow: `${widget.y + 1} / span ${widget.h}`,
      transform: actions.activeDragId === widget.id && transform ? `translate3d(${transform.x}px, ${transform.y}px, 0)` : undefined,
      pointerEvents: actions.activeDragId === widget.id ? 'none' : undefined }}
  >
    <div className="h-full min-h-0 overflow-auto p-px" id={widget.id}>
      <DashboardTileControlsProvider value={{ widgetId: widget.id, title, dragHandle, removeButton }}>
        {definition ? definition.render(context) : <p className="p-3 text-sm text-gray-500">This widget is unavailable.</p>}
      </DashboardTileControlsProvider>
    </div>
    <ResizeHandle title={title} columnStep={columnStep} onStart={() => actions.onResizeStart(widget.id)}
      onPreview={(dw, dh) => actions.onResizePreview(widget.id, dw, dh)} onEnd={actions.onResizeEnd}
      onCommit={(dw, dh) => actions.onResize(widget.id, dw, dh)} />
  </section>;
}

function GroupTile({ group, context, actions, draggedWidget, dropIndicator }: { group: DashboardGroup; context: WidgetContext; actions: TileActions; draggedWidget: DashboardWidget | null; dropIndicator: DropIndicator | null }) {
  const { setNodeRef: setDropRef } = useDroppable({ id: groupDropId(group.id) });
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform } = useDraggable({ id: tileDragId(group.id) });
  const isDropTarget = dropIndicator?.groupId === group.id && actions.activeDragId !== group.id;
  const [innerWidth, setInnerWidth] = useState(900);
  const innerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const grid = innerRef.current;
    if (!grid) return;
    const measure = () => setInnerWidth(grid.clientWidth);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(grid);
    return () => observer.disconnect();
  }, []);
  return <section
    ref={setNodeRef}
    data-testid={`dashboard-group-${group.id}`}
    data-drop-target={isDropTarget ? 'true' : undefined}
    className={`group relative z-10 min-h-0 min-w-0 p-1 transition-colors duration-150 hover:z-20 focus-within:z-20 ${isDropTarget ? 'rounded-xl bg-cyan-100/50 ring-2 ring-cyan-400/80' : actions.editMode ? actions.selectedId === group.id ? 'rounded-xl bg-transparent ring-2 ring-cyan-400' : 'rounded-xl bg-transparent outline-2 outline-dashed outline-cyan-300' : 'bg-transparent'} ${actions.activeDragId === group.id || (draggedWidget && group.children.some((child) => child.id === draggedWidget.id)) ? 'z-30 overflow-visible' : 'overflow-hidden'}`}
    style={{ gridColumn: `${group.x + 1} / span ${group.w}`, gridRow: `${group.y + 1} / span ${group.h}`, height: group.h * ROW_STEP - ROW_GAP,
      transform: actions.activeDragId === group.id && transform ? `translate3d(${transform.x}px, ${transform.y}px, 0)` : undefined,
      pointerEvents: actions.activeDragId === group.id ? 'none' : undefined }}
  >
    <header className="flex h-7 items-center gap-2">
      <h3 className="min-w-0 flex-1 truncate text-[10px] font-semibold uppercase tracking-[0.06em] text-gray-400">{group.title}</h3>
      <button ref={setActivatorNodeRef} type="button" aria-label={`Move group ${group.title}`} title={`Move group ${group.title}`}
        className="flex h-6 w-6 shrink-0 touch-none cursor-grab items-center justify-center rounded text-gray-500 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500 [@media(hover:none)]:opacity-80"
        {...attributes} {...listeners}><GripVertical className="h-4 w-4" /></button>
      <button type="button" aria-label={`Edit group ${group.title}`}
        onPointerDown={(event) => { event.stopPropagation(); if (event.button === 0) actions.onEdit(group.id); }}
        onClick={(event) => { event.stopPropagation(); if (event.detail === 0) actions.onEdit(group.id); }}
        className="rounded p-1 text-xs text-gray-600 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"><Pencil className="h-3.5 w-3.5" /></button>
      <button type="button" aria-label={`Ungroup ${group.title}`} title="Ungroup; keep all cards"
        onClick={(event) => { event.stopPropagation(); actions.onRemove(group.id); }}
        className="rounded p-1 text-xs text-gray-500 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 hover:text-red-600">Ungroup</button>
    </header>
    <div ref={setDropRef} className="min-h-[48px] rounded-lg" style={{ height: 'calc(100% - 28px)' }} aria-label={`Drop widgets into ${group.title}`}>
      <div ref={innerRef} data-group-grid={group.id} className="grid h-full min-h-[48px] grid-cols-12 gap-x-3 gap-y-2" style={{ gridAutoRows: `${ROW_HEIGHT}px` }}>
        {group.children.map((widget) => <WidgetTile key={widget.id} widget={draggedWidget?.id === widget.id ? draggedWidget : widget} context={context} columnStep={(innerWidth + GAP) / GRID_COLUMNS} actions={actions} />)}
        {dropIndicator?.groupId === group.id && <DropPreview indicator={dropIndicator} />}
        {group.children.length === 0 && <div className="col-span-12 flex items-center justify-center rounded-lg border border-dashed border-gray-200 text-xs text-gray-400">Drag a widget here</div>}
      </div>
    </div>
  </section>;
}

function PaletteCard({ id, title, disabled, draggable = true, onAdd, icon }: { id: string; title: string; disabled?: boolean; draggable?: boolean; onAdd: () => void; icon?: ReactNode }) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: paletteDragId(id), disabled: disabled || !draggable });
  return <div ref={setNodeRef} className={`flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-2 py-2 text-sm ${disabled ? 'opacity-40' : isDragging ? 'opacity-60' : 'hover:border-cyan-300'}`}>
    {draggable && <button type="button" aria-label={`Drag ${title} onto dashboard`} disabled={disabled}
      className="touch-none cursor-grab rounded p-1 text-gray-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500"
      {...attributes} {...listeners}><GripVertical className="h-4 w-4" /></button>}
    {icon ?? <span className="h-2 w-2 rounded-full bg-cyan-500" />}
    <span className="min-w-0 flex-1 truncate">{title}</span>
    <button type="button" aria-label={`Add ${title}`} disabled={disabled} onClick={onAdd}
      className="rounded p-1 text-gray-500 hover:bg-cyan-50 hover:text-cyan-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500"><Plus className="h-4 w-4" /></button>
  </div>;
}

function NewGroupButton({ onAdd }: { onAdd: () => void }) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef } = useDraggable({ id: paletteDragId('group') });
  return <button ref={(node) => { setNodeRef(node); setActivatorNodeRef(node); }} type="button" aria-label="New titled group" title="New titled group"
    onClick={onAdd} className="relative flex h-7 w-7 shrink-0 touch-none cursor-grab items-center justify-center rounded text-cyan-700 hover:bg-cyan-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500"
    {...attributes} {...listeners}>
    <Layers3 className="h-4 w-4" />
    <Plus className="absolute bottom-0 right-0 h-2.5 w-2.5 rounded-full bg-white stroke-[3]" />
  </button>;
}

function LayerWidgetRow({ widget, dropTarget, onRemove }: { widget: DashboardWidget; dropTarget: boolean; onRemove: () => void }) {
  const title = widget.title ?? widgetById[widget.id]?.title ?? widget.id;
  const { setNodeRef: setDropRef } = useDroppable({ id: layerItemDropId(widget.id) });
  const { attributes, listeners, setNodeRef: setDragRef, setActivatorNodeRef, isDragging } = useDraggable({ id: layerDragId(widget.id) });
  return <li ref={setDropRef} data-testid={`widget-panel-item-${widget.id}`}
    className={`flex min-w-0 items-center gap-1 rounded-md py-1 pr-1 text-xs text-gray-600 ${dropTarget ? 'bg-cyan-100 ring-1 ring-cyan-400' : ''} ${isDragging ? 'opacity-40' : ''}`}>
    <button ref={(node) => { setDragRef(node); setActivatorNodeRef(node); }} type="button" aria-label={`Move ${title} in Widgets`}
      className="shrink-0 touch-none cursor-grab rounded p-0.5 text-gray-400 hover:text-cyan-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500"
      {...attributes} {...listeners}><GripVertical className="h-3.5 w-3.5" /></button>
    <span className="min-w-0 flex-1 truncate">{title}</span>
    <button type="button" aria-label={`Remove ${title} from Widgets`} onClick={onRemove}
      className="shrink-0 rounded p-0.5 text-gray-400 hover:bg-red-50 hover:text-red-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-red-500"><Trash2 className="h-3.5 w-3.5" /></button>
  </li>;
}

function LayerGroupRow({ group, collapsed, dropTarget, insertionSide, itemDropTarget, showAdd, availableWidgets, onToggle, onEdit, onAdd, onAddWidget, onRemoveWidget }: {
  group: DashboardGroup;
  collapsed: boolean;
  dropTarget: boolean;
  insertionSide: 'before' | 'after' | null;
  itemDropTarget: string | null;
  showAdd: boolean;
  availableWidgets: { id: string; title: string }[];
  onToggle: () => void;
  onEdit: () => void;
  onAdd: () => void;
  onAddWidget: (id: string) => void;
  onRemoveWidget: (id: string) => void;
}) {
  const { setNodeRef } = useDroppable({ id: layerGroupDropId(group.id) });
  const { attributes, listeners, setNodeRef: setDragRef, setActivatorNodeRef, isDragging } = useDraggable({ id: layerGroupDragId(group.id) });
  return <div ref={setNodeRef} data-testid={`widget-panel-group-${group.id}`}
    className={`relative rounded-lg border bg-white ${dropTarget ? 'border-cyan-400 bg-cyan-50 ring-1 ring-cyan-400' : 'border-gray-200'} ${isDragging ? 'opacity-40' : ''}`}>
    {insertionSide && <div data-testid="group-insert-indicator" data-side={insertionSide}
      className={`pointer-events-none absolute inset-x-1 z-10 h-1 rounded-full bg-cyan-400 shadow-sm ${insertionSide === 'before' ? '-top-1' : '-bottom-1'}`} />}
    <div className="flex min-w-0 items-center gap-1 px-2 py-1.5">
      <button ref={(node) => { setDragRef(node); setActivatorNodeRef(node); }} type="button" aria-label={`Move group ${group.title} in Widgets`}
        className="shrink-0 touch-none cursor-grab rounded p-0.5 text-gray-400 hover:text-cyan-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500"
        {...attributes} {...listeners}><GripVertical className="h-3.5 w-3.5" /></button>
      <button type="button" aria-label={`${collapsed ? 'Expand' : 'Collapse'} ${group.title}`} aria-expanded={!collapsed} onClick={onToggle}
        className="flex min-w-0 flex-1 items-center gap-2 rounded py-1 text-left text-sm font-medium text-gray-800 hover:text-cyan-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500">
        <ChevronRight className={`h-3.5 w-3.5 shrink-0 text-gray-500 transition-transform ${collapsed ? '' : 'rotate-90'}`} />
        <Layers3 className="h-4 w-4 shrink-0 text-cyan-600" />
        <span className="min-w-0 flex-1 truncate">{group.title}</span>
      </button>
      <button type="button" aria-label={`${showAdd ? 'Close add menu for' : 'Add widget to'} ${group.title}`} aria-expanded={showAdd} onClick={onAdd}
        className="rounded p-1 text-gray-500 hover:bg-cyan-50 hover:text-cyan-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500"><Plus className="h-3.5 w-3.5" /></button>
      <button type="button" aria-label={`Edit group ${group.title} in Widgets`} onClick={onEdit}
        className="rounded p-1 text-gray-500 hover:bg-cyan-50 hover:text-cyan-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500"><Pencil className="h-3.5 w-3.5" /></button>
    </div>
    {showAdd && <div aria-label={`Add widgets to ${group.title}`} className="mx-2 mb-2 max-h-44 space-y-1 overflow-y-auto rounded-md border border-cyan-100 bg-cyan-50 p-1">
      {availableWidgets.map((widget) => <button key={widget.id} type="button" onClick={() => onAddWidget(widget.id)}
        className="block w-full rounded px-2 py-1.5 text-left text-xs text-gray-700 hover:bg-white hover:text-cyan-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500">+ {widget.title}</button>)}
      {availableWidgets.length === 0 && <p className="px-2 py-1 text-xs text-gray-500">All widgets are already on the dashboard.</p>}
    </div>}
    {!collapsed && <ul aria-label={`Widgets in ${group.title}`} className="mb-2 ml-5 mr-2 border-l border-cyan-200 pl-2">
      {group.children.map((child) => <LayerWidgetRow key={child.id} widget={child} dropTarget={itemDropTarget === child.id} onRemove={() => onRemoveWidget(child.id)} />)}
      {group.children.length === 0 && <li className="py-1 text-xs text-gray-400">Empty group</li>}
    </ul>}
  </div>;
}

function LayerRoot({ children, dropTarget }: { children: ReactNode; dropTarget: boolean }) {
  const { setNodeRef } = useDroppable({ id: LAYER_ROOT_DROP_ID });
  return <section ref={setNodeRef} aria-label="Widgets on dashboard" className={`mt-5 border-t border-gray-100 pt-4 ${dropTarget ? 'rounded-md bg-cyan-50' : ''}`}>
    <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-gray-500">On dashboard</h3>
    <div className="space-y-1.5">{children}</div>
    <div data-testid="widget-panel-ungroup-drop" className={`mt-2 rounded-md border border-dashed px-2 py-2 text-center text-xs ${dropTarget ? 'border-cyan-400 text-cyan-700' : 'border-gray-200 text-gray-400'}`}>
      Drop here to move outside groups
    </div>
  </section>;
}

function CanvasDropGrid({ gridRef, children }: { gridRef: React.MutableRefObject<HTMLDivElement | null>; children: ReactNode }) {
  const { setNodeRef } = useDroppable({ id: CANVAS_DROP_ID });
  return <div ref={(node) => { gridRef.current = node; setNodeRef(node); }}
    aria-label="Dashboard canvas" data-testid="dashboard-canvas"
    className="dashboard-builder-grid grid min-h-48 grid-cols-12 gap-x-3 gap-y-2 rounded-xl"
    style={{ gridAutoRows: `${ROW_HEIGHT}px` }}>{children}</div>;
}

export default function DashboardBuilder(context: WidgetContext) {
  const { projectId } = context;
  const [document, setDocument] = useState<DashboardLayoutDocument>({ version: 2, items: [] });
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [saveState, setSaveState] = useState<'saved' | 'saving' | 'failed'>('saved');
  const [saveError, setSaveError] = useState('Could not save layout');
  const [gridWidth, setGridWidth] = useState(1200);
  const [preview, setPreview] = useState<DashboardLayoutDocument | null>(null);
  const [activeDragId, setActiveDragId] = useState<string | null>(null);
  const [dropIndicator, setDropIndicator] = useState<DropIndicator | null>(null);
  const [activeResizeId, setActiveResizeId] = useState<string | null>(null);
  const [layoutOpen, setLayoutOpen] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [collapsedGroupIds, setCollapsedGroupIds] = useState<Set<string>>(() => new Set());
  const [addingToGroupId, setAddingToGroupId] = useState<string | null>(null);
  const [layerDropTarget, setLayerDropTarget] = useState<string | null>(null);
  const [groupInsertion, setGroupInsertion] = useState<{ targetId: string; side: 'before' | 'after' } | null>(null);
  const [titleDraft, setTitleDraft] = useState('');
  const [gestureGridMinHeight, setGestureGridMinHeight] = useState<number | null>(null);
  const gridRef = useRef<HTMLDivElement | null>(null);
  const builderRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLElement | null>(null);
  const panelDragRef = useRef<{ pointerId: number; x: number; y: number; left: number; top: number } | null>(null);
  const [panelPosition, setPanelPosition] = useState<{ left: number; top: number } | null>(null);
  const gestureScrollRef = useRef<{ element: HTMLElement; overflowAnchor: string } | null>(null);
  const documentRef = useRef(document);
  const pendingRef = useRef<DashboardLayoutDocument | null>(null);
  const savingRef = useRef(false);
  const loadVersionRef = useRef(0);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }), useSensor(KeyboardSensor));

  const load = useCallback(async () => {
    const version = ++loadVersionRef.current;
    setLoadError(false);
    try {
      const { data } = await DashboardAPI.getLayout(projectId);
      if (version !== loadVersionRef.current || pendingRef.current) return;
      const next = (data.version === 2 || data.version === 3) && data.items
        ? { version: data.version, items: data.items }
        : legacyDocument(data.widgets ?? [], widgetRegistry.filter((definition) => definition.group).map((definition) => definition.defaultPosition));
      documentRef.current = next;
      setDocument(next);
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
  useLayoutEffect(() => {
    if (!layoutOpen) return;
    const bounds = builderRef.current?.getBoundingClientRect();
    if (!bounds) return;
    setPanelPosition((current) => current
      ? clampPanelPosition(current.left, current.top)
      : clampPanelPosition(window.innerWidth - FLOATING_PANEL_WIDTH - 12, bounds.top + 8));
  }, [layoutOpen]);
  useEffect(() => {
    if (!layoutOpen) return;
    const keepOnScreen = () => setPanelPosition((current) => current && clampPanelPosition(current.left, current.top));
    window.addEventListener('resize', keepOnScreen);
    return () => window.removeEventListener('resize', keepOnScreen);
  }, [layoutOpen]);
  useEffect(() => () => {
    if (gestureScrollRef.current) gestureScrollRef.current.element.style.overflowAnchor = gestureScrollRef.current.overflowAnchor;
  }, []);
  useEffect(() => {
    if (!selectedId) { setTitleDraft(''); return; }
    const selected = findLocation(documentRef.current, selectedId)?.item;
    if (selected) setTitleDraft(selected.kind === 'group' ? selected.title : selected.title ?? widgetById[selected.id]?.title ?? selected.id);
  }, [selectedId]); // Deliberately retain the draft while the selected item's layout moves.

  const flush = useCallback(async () => {
    if (savingRef.current || !pendingRef.current) return;
    savingRef.current = true;
    setSaveState('saving');
    try {
      while (pendingRef.current) {
        const submitted: DashboardLayoutDocument = pendingRef.current;
        await DashboardAPI.saveLayout(projectId, submitted);
        if (pendingRef.current === submitted) pendingRef.current = null;
      }
      setSaveState('saved');
    } catch (error) {
      setSaveError(saveErrorMessage(error));
      setSaveState('failed');
    } finally {
      savingRef.current = false;
    }
  }, [projectId]);

  const commit = (update: (current: DashboardLayoutDocument) => DashboardLayoutDocument) => {
    const next = update(documentRef.current);
    if (JSON.stringify(next) === JSON.stringify(documentRef.current)) return;
    loadVersionRef.current += 1;
    documentRef.current = next;
    setDocument(next);
    pendingRef.current = next;
    void flush();
  };
  const newGroupId = () => `group-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  const addFromPalette = (id: string, groupId: string | null = null, cell?: { x: number; y: number }) => {
    if (id === 'group') { commit((current) => addGroup(current, newGroupId(), 'New group', cell)); return; }
    const definition = widgetById[id];
    if (!definition || definition.legacy) return;
    commit((current) => {
      const widget: DashboardWidget = { kind: 'widget', ...definition.defaultPosition };
      const peers = groupId
        ? current.items.find((item): item is DashboardGroup => item.kind === 'group' && item.id === groupId)?.children
        : current.items;
      const placed = cell && peers ? fitWidgetToVacancy(peers, widget, cell) : widget;
      return placeWidgetInContainer(current, placed, groupId, cell ? { x: placed.x, y: placed.y } : undefined);
    });
  };
  const beginGesture = () => {
    const grid = gridRef.current;
    setGestureGridMinHeight(grid?.getBoundingClientRect().height ?? null);
    const scrollContainer = grid?.closest('main');
    if (scrollContainer && !gestureScrollRef.current) {
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
  const cellDelta = (event: DragMoveEvent | DragEndEvent, id: string) => {
    const source = findLocation(documentRef.current, id);
    const groupId = source?.groupId;
    const width = groupId ? globalThis.document.querySelector<HTMLElement>(`[data-group-grid="${groupId}"]`)?.clientWidth ?? gridWidth : gridWidth;
    const dx = window.innerWidth < 768 ? 0 : Math.round(event.delta.x / ((width + GAP) / GRID_COLUMNS));
    const regularDy = Math.round(event.delta.y / ROW_STEP);
    if (!source) return { dx, dy: regularDy };
    const peers = groupId
      ? (documentRef.current.items.find((item): item is DashboardGroup => item.kind === 'group' && item.id === groupId)?.children ?? [])
      : documentRef.current.items;
    const nextX = Math.max(0, Math.min(GRID_COLUMNS - source.item.w, source.item.x + dx));
    const snappedY = snapNearPrevious(peers, id, nextX, source.item.w, source.item.y * ROW_STEP + event.delta.y);
    return { dx, dy: snappedY === null ? regularDy : snappedY - source.item.y };
  };
  const dropCell = (event: DragMoveEvent | DragEndEvent, destination: string) => {
    const origin = event.activatorEvent;
    if (!('clientX' in origin) || !('clientY' in origin)) return undefined;
    const target = destination === CANVAS_DROP_ID ? gridRef.current : globalThis.document.querySelector<HTMLElement>(`[data-group-grid="${destination.slice(groupDropId('').length)}"]`);
    if (!target) return undefined;
    const rect = target.getBoundingClientRect();
    const x = Math.max(0, Math.min(11, Math.floor((Number(origin.clientX) + event.delta.x - rect.left) / ((rect.width + GAP) / GRID_COLUMNS))));
    const y = Math.max(0, Math.min(999, Math.floor((Number(origin.clientY) + event.delta.y - rect.top) / ROW_STEP)));
    return { x, y };
  };
  const targetAtPointer = (event: DragMoveEvent | DragEndEvent, draggingGroup = false) => {
    const origin = event.activatorEvent;
    if (!('clientX' in origin) || !('clientY' in origin)) return String(event.over?.id ?? '');
    const x = Number(origin.clientX) + event.delta.x;
    const y = Number(origin.clientY) + event.delta.y;
    const panel = panelRef.current?.getBoundingClientRect();
    if (panel && x >= panel.left && x <= panel.right && y >= panel.top && y <= panel.bottom) return String(event.over?.id ?? '');
    for (const item of draggingGroup ? [] : documentRef.current.items) {
      if (item.kind !== 'group') continue;
      const group = globalThis.document.querySelector<HTMLElement>(`[data-testid="dashboard-group-${item.id}"]`);
      const rect = group?.getBoundingClientRect();
      if (rect && x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.top + item.h * ROW_STEP - ROW_GAP) return groupDropId(item.id);
    }
    const rect = gridRef.current?.getBoundingClientRect();
    if (rect && x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) return CANVAS_DROP_ID;
    return String(event.over?.id ?? '');
  };
  const insertionAtPointer = (event: DragMoveEvent | DragEndEvent, target: string) => {
    if (!target.startsWith('layer-group:')) return null;
    const targetId = target.slice(layerGroupDropId('').length);
    const origin = event.activatorEvent;
    const row = globalThis.document.querySelector<HTMLElement>(`[data-testid="widget-panel-group-${targetId}"]`);
    const rect = row?.getBoundingClientRect();
    const pointerY = 'clientY' in origin ? Number(origin.clientY) + event.delta.y : null;
    return { targetId, side: (rect && pointerY !== null && pointerY >= rect.top + rect.height / 2 ? 'after' : 'before') as 'before' | 'after' };
  };
  const onDragStart = (event: DragStartEvent) => {
    beginGesture();
    const id = String(event.active.id);
    setActiveDragId(id.startsWith('tile:') ? id.slice(5) : id);
    setDropIndicator(null);
    setLayerDropTarget(null);
    setGroupInsertion(null);
  };
  const onDragMove = (event: DragMoveEvent) => {
    const active = String(event.active.id);
    if (active.startsWith('layer-group-drag:')) {
      const source = findLocation(documentRef.current, active.slice(layerGroupDragId('').length));
      const target = targetAtPointer(event, true);
      const insertion = insertionAtPointer(event, target);
      setGroupInsertion(insertion?.targetId === source?.item.id ? null : insertion);
      setLayerDropTarget(target === LAYER_ROOT_DROP_ID ? target : null);
      const cell = target === CANVAS_DROP_ID ? dropCell(event, target) : null;
      setDropIndicator(source?.item.kind === 'group' && cell
        ? { groupId: null, x: 0, y: cell.y, w: source.item.w, h: source.item.h } : null);
      return;
    }
    if (active.startsWith('layer:')) {
      const target = targetAtPointer(event);
      setLayerDropTarget(target.startsWith('layer-') ? target : null);
      setPreview(null);
      const source = findLocation(documentRef.current, active.slice(6));
      const cell = dropCell(event, target);
      setDropIndicator(source?.item.kind === 'widget' && cell && (target === CANVAS_DROP_ID || target.startsWith('dashboard-group:'))
        ? { groupId: target === CANVAS_DROP_ID ? null : target.slice(groupDropId('').length),
          x: Math.min(cell.x, GRID_COLUMNS - source.item.w), y: cell.y, w: source.item.w, h: source.item.h }
        : null);
      return;
    }
    const id = active.startsWith('tile:') ? active.slice(5) : '';
    const group = active === paletteDragId('group') || findLocation(documentRef.current, id)?.item.kind === 'group';
    const target = targetAtPointer(event, group);
    if (active.startsWith('palette:')) {
      setLayerDropTarget(target.startsWith('layer-') ? target : null);
      const definition = widgetById[active.slice(8)];
      const cell = dropCell(event, target);
      const position = active === paletteDragId('group') ? { w: 12, h: 3 } : definition?.defaultPosition;
      const groupId = target.startsWith('dashboard-group:') ? target.slice(groupDropId('').length) : null;
      const peers = groupId
        ? documentRef.current.items.find((item): item is DashboardGroup => item.kind === 'group' && item.id === groupId)?.children
        : documentRef.current.items;
      const fitted = definition && cell && peers && active !== paletteDragId('group')
        ? fitWidgetToVacancy(peers, definition.defaultPosition, cell) : null;
      setDropIndicator(position && cell && (target === CANVAS_DROP_ID || (!group && target.startsWith('dashboard-group:')))
        ? { groupId, x: fitted?.x ?? Math.min(cell.x, GRID_COLUMNS - position.w), y: cell.y,
          w: fitted?.w ?? position.w, h: position.h }
        : null);
      return;
    }
    if (!active.startsWith('tile:')) return;
    const source = findLocation(documentRef.current, id);
    if (source?.item.kind === 'group') {
      const { dx, dy } = cellDelta(event, id);
      const destination = findLocation(moveLayoutItem(documentRef.current, id, dx, dy), id)?.item;
      setDropIndicator(target === CANVAS_DROP_ID && destination ? {
        groupId: null, x: destination.x, y: destination.y, w: destination.w, h: destination.h,
      } : null);
      return;
    }
    if (!source || source.item.kind !== 'widget' || (target !== CANVAS_DROP_ID && !target.startsWith('dashboard-group:'))) {
      setPreview(null);
      setDropIndicator(null);
      return;
    }
    const sameContainer = source?.groupId ? target === groupDropId(source.groupId) : target === CANVAS_DROP_ID;
    if (!sameContainer) {
      setPreview(null);
      const cell = dropCell(event, target);
      setDropIndicator(cell ? {
        groupId: target === CANVAS_DROP_ID ? null : target.slice(groupDropId('').length),
        x: Math.min(cell.x, GRID_COLUMNS - source.item.w), y: cell.y,
        w: source.item.w, h: source.item.h,
      } : null);
      return;
    }
    const { dx, dy } = cellDelta(event, id);
    const next = moveLayoutItem(documentRef.current, id, dx, dy);
    const destination = findLocation(next, id)?.item;
    setDropIndicator(destination ? {
      groupId: source.groupId, x: destination.x, y: destination.y, w: destination.w, h: destination.h,
    } : null);
    const shiftsGroup = documentRef.current.items.some((item) => {
      if (item.kind !== 'group') return false;
      const moved = next.items.find((candidate) => candidate.id === item.id);
      return moved?.x !== item.x || moved?.y !== item.y || moved?.h !== item.h;
    });
    // Keep group drop zones stationary while dragging a card toward them.
    setPreview(shiftsGroup ? null : next);
  };
  const onDragEnd = (event: DragEndEvent) => {
    const active = String(event.active.id);
    const id = active.startsWith('tile:') ? active.slice(5) : '';
    const group = active === paletteDragId('group') || active.startsWith('layer-group-drag:') || findLocation(documentRef.current, id)?.item.kind === 'group';
    const target = targetAtPointer(event, group);
    setActiveDragId(null);
    setPreview(null);
    setDropIndicator(null);
    setLayerDropTarget(null);
    setGroupInsertion(null);
    endGesture();
    if (!target) return;
    if (active.startsWith('layer-group-drag:')) {
      const sourceId = active.slice(layerGroupDragId('').length);
      const source = findLocation(documentRef.current, sourceId)?.item;
      if (source?.kind !== 'group') return;
      if (target.startsWith('layer-group:')) {
        const insertion = insertionAtPointer(event, target);
        if (insertion && insertion.targetId !== source.id)
          commit((current) => reorderLayoutGroup(current, source.id, insertion.targetId, insertion.side));
      } else if (target === LAYER_ROOT_DROP_ID) {
        const bottom = Math.max(0, ...documentRef.current.items.filter((item) => item.id !== source.id).map((item) => item.y + item.h));
        commit((current) => moveLayoutItem(current, source.id, 0, bottom - source.y));
      } else if (target === CANVAS_DROP_ID) {
        const cell = dropCell(event, target);
        if (cell) commit((current) => moveLayoutItem(current, source.id, -source.x, cell.y - source.y));
      }
      return;
    }
    if (active.startsWith('layer:')) {
      const source = findLocation(documentRef.current, active.slice(6));
      if (source?.item.kind !== 'widget') return;
      if (target.startsWith('layer-item:')) {
        const destination = findLocation(documentRef.current, target.slice(layerItemDropId('').length));
        if (!destination || destination.item.id === source.item.id) return;
        commit((current) => placeWidgetInContainer(current, source.item as DashboardWidget, destination.groupId,
          { x: destination.item.x, y: destination.item.y }));
      } else if (target.startsWith('layer-group:')) {
        commit((current) => placeWidgetInContainer(current, source.item as DashboardWidget, target.slice(layerGroupDropId('').length)));
      } else if (target === LAYER_ROOT_DROP_ID) {
        commit((current) => placeWidgetInContainer(current, source.item as DashboardWidget, null));
      } else if (target === CANVAS_DROP_ID || target.startsWith('dashboard-group:')) {
        const destination = target === CANVAS_DROP_ID ? null : target.slice(groupDropId('').length);
        commit((current) => placeWidgetInContainer(current, source.item as DashboardWidget, destination, dropCell(event, target)));
      }
      return;
    }
    if (active.startsWith('palette:')) {
      const id = active.slice(8);
      if (id === 'group') {
        if (target === CANVAS_DROP_ID || target === LAYER_ROOT_DROP_ID) addFromPalette(id, null, dropCell(event, target));
        return;
      }
      if (target.startsWith('layer-item:')) {
        const destination = findLocation(documentRef.current, target.slice(layerItemDropId('').length));
        if (destination) addFromPalette(id, destination.groupId, { x: destination.item.x, y: destination.item.y });
      } else if (target.startsWith('layer-group:')) {
        addFromPalette(id, target.slice(layerGroupDropId('').length));
      } else if (target === CANVAS_DROP_ID || target.startsWith('dashboard-group:') || target === LAYER_ROOT_DROP_ID) {
        addFromPalette(id, target.startsWith('dashboard-group:') ? target.slice(groupDropId('').length) : null, dropCell(event, target));
      }
      return;
    }
    if (!active.startsWith('tile:')) return;
    const source = findLocation(documentRef.current, id);
    if (!source) return;
    if (source.item.kind === 'widget') {
      const layerTarget = target.startsWith('layer-item:')
        ? findLocation(documentRef.current, target.slice(layerItemDropId('').length)) : null;
      const destination = layerTarget?.groupId ?? (target.startsWith('layer-group:') ? target.slice(layerGroupDropId('').length)
        : target.startsWith('dashboard-group:') ? target.slice(groupDropId('').length) : null);
      const cell = layerTarget ? { x: layerTarget.item.x, y: layerTarget.item.y } : dropCell(event, target);
      if (destination !== source.groupId) {
        commit((current) => placeWidgetInContainer(current, source.item as DashboardWidget, destination, cell));
        return;
      }
      if (target.startsWith('layer-item:') && layerTarget?.item.id !== source.item.id) {
        commit((current) => placeWidgetInContainer(current, source.item as DashboardWidget, destination, cell));
        return;
      }
    }
    if (target.startsWith('layer-')) return;
    const { dx, dy } = cellDelta(event, id);
    commit((current) => moveLayoutItem(current, id, dx, dy));
  };

  const actions: TileActions = {
    editMode: layoutOpen,
    onEdit: (id) => { setSelectedId(id); setLayoutOpen(true); setEditorOpen(true); },
    onRemove: (id) => { commit((current) => removeLayoutItem(current, id)); if (selectedId === id) setSelectedId(null); },
    onResizeStart: (id) => { beginGesture(); setActiveResizeId(id); },
    onResizePreview: (id, dw, dh) => setPreview(resizeLayoutItem(documentRef.current, id, dw, dh)),
    onResizeEnd: () => { setPreview(null); setActiveResizeId(null); endGesture(); },
    onResize: (id, dw, dh) => commit((current) => resizeLayoutItem(current, id, dw, dh)),
    activeDragId, activeResizeId, selectedId,
  };
  const visible = preview ?? document;
  const activeLocation = activeDragId && !activeDragId.startsWith('palette:') ? findLocation(document, activeDragId) : null;
  const draggedWidget = activeLocation?.item.kind === 'widget' ? activeLocation.item : null;
  const selected = selectedId ? findLocation(document, selectedId)?.item : null;
  const usedIds = allWidgetIds(document);
  const draggedGroup = activeDragId?.startsWith('layer-group-drag:')
    ? findLocation(document, activeDragId.slice(layerGroupDragId('').length))?.item : null;

  if (loadError) return <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">Dashboard layout could not be loaded. <button type="button" onClick={() => void load()} className="underline">Retry</button></div>;
  if (!loaded) return <div className="rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-500">Loading dashboard layout…</div>;

  return <WorkspaceDashboardProvider projectId={projectId}>
    <DndContext sensors={sensors} collisionDetection={collisionDetection} onDragStart={onDragStart} onDragMove={onDragMove} onDragEnd={onDragEnd}
      onDragCancel={() => { setActiveDragId(null); setPreview(null); setDropIndicator(null); setGroupInsertion(null); setLayerDropTarget(null); endGesture(); }}>
      <div ref={builderRef} className="min-w-0">
        <div className="min-w-0">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-base font-semibold text-gray-900">Overview</h2>
            <div className="flex items-center gap-2">
              <span role={saveState === 'failed' ? 'alert' : 'status'} className={`text-xs ${saveState === 'failed' ? 'text-red-700' : 'text-gray-500'}`}>
                {saveState === 'failed' ? saveError : saveState === 'saving' ? 'Saving…' : 'Saved'}
              </span>
              {saveState === 'failed' && <button type="button" onClick={() => void flush()} className="flex items-center gap-1 rounded border border-red-200 px-2 py-1 text-xs text-red-700"><RotateCcw className="h-3 w-3" />Retry</button>}
              {!layoutOpen && <button type="button" aria-label="Edit dashboard layout" title="Edit dashboard layout"
                onClick={() => { setLayoutOpen(true); setEditorOpen(false); }}
                className="flex h-8 w-8 items-center justify-center rounded-md border border-gray-200 bg-white text-gray-700 hover:border-cyan-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500">
                <Pencil className="h-4 w-4" />
              </button>}
            </div>
          </div>
          <div style={{ minHeight: gestureGridMinHeight ?? undefined }}>
            <CanvasDropGrid gridRef={gridRef}>
              {visible.items.map((item: DashboardItem) => item.kind === 'group'
                ? <GroupTile key={item.id} group={item} context={context} actions={actions} draggedWidget={draggedWidget} dropIndicator={dropIndicator} />
                : <WidgetTile key={item.id} widget={draggedWidget?.id === item.id ? draggedWidget : item} context={context} columnStep={(gridWidth + GAP) / GRID_COLUMNS} actions={actions} />)}
              {dropIndicator?.groupId === null && <DropPreview indicator={dropIndicator} />}
              {visible.items.length === 0 && <div className="col-span-12 flex min-h-48 items-center justify-center rounded-xl border border-dashed border-gray-300 text-sm text-gray-500">Drag a widget or group here</div>}
            </CanvasDropGrid>
          </div>
        </div>
        {layoutOpen && <aside ref={panelRef} data-floating-widgets-panel="true"
          className="fixed z-50 overflow-hidden rounded-xl bg-white shadow-xl ring-1 ring-gray-200"
          style={{ left: panelPosition?.left ?? 0, top: panelPosition?.top ?? 0, width: 'min(320px, calc(100vw - 24px))',
            maxHeight: panelPosition ? `calc(100dvh - ${panelPosition.top + 12}px)` : undefined,
            visibility: panelPosition ? 'visible' : 'hidden' }}
          aria-label={editorOpen ? 'Dashboard item editor' : 'Dashboard widget palette'}>
          <div className="overflow-hidden rounded-xl bg-white">
            <div className="h-[3px] bg-gradient-to-r from-[#3CCED7] to-[#A6E661]" />
            <div className="px-4 pb-3 pt-4">
              <div className="flex items-center gap-1">
                <button type="button" aria-label="Move Widgets window" title="Move Widgets window"
                  onPointerDown={(event) => {
                    if (event.button !== 0 || !panelPosition) return;
                    event.stopPropagation();
                    panelDragRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY,
                      left: panelPosition.left, top: panelPosition.top };
                    event.currentTarget.setPointerCapture(event.pointerId);
                  }}
                  onPointerMove={(event) => {
                    const drag = panelDragRef.current;
                    if (drag?.pointerId === event.pointerId) setPanelPosition(clampPanelPosition(
                      drag.left + event.clientX - drag.x, drag.top + event.clientY - drag.y));
                  }}
                  onPointerUp={(event) => {
                    if (panelDragRef.current?.pointerId === event.pointerId) panelDragRef.current = null;
                  }}
                  onPointerCancel={() => { panelDragRef.current = null; }}
                  onKeyDown={(event) => {
                    const arrows: Record<string, [number, number]> = {
                      ArrowLeft: [-24, 0], ArrowRight: [24, 0], ArrowUp: [0, -24], ArrowDown: [0, 24],
                    };
                    const delta = arrows[event.key];
                    if (delta) {
                      event.preventDefault();
                      setPanelPosition((current) => current && clampPanelPosition(current.left + delta[0], current.top + delta[1]));
                    }
                  }}
                  className="flex h-7 w-6 shrink-0 touch-none cursor-move items-center justify-center rounded text-gray-400 hover:bg-gray-100 hover:text-gray-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500">
                  <GripVertical className="h-4 w-4" />
                </button>
                {editorOpen && selected ? <button type="button" onClick={() => setEditorOpen(false)} className="text-xs font-medium text-cyan-700 hover:underline">← Widgets</button>
                  : <h2 className="text-base font-semibold text-gray-900">Widgets</h2>}
                <div className="ml-auto flex items-center gap-1">
                  {(!editorOpen || !selected) && <NewGroupButton onAdd={() => addFromPalette('group')} />}
                  <button type="button" aria-label="Close layout panel" title="Close layout panel"
                    onClick={() => { setLayoutOpen(false); setEditorOpen(false); setAddingToGroupId(null); }}
                    className="rounded p-1 text-gray-500 hover:bg-gray-100 hover:text-gray-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500"><X className="h-4 w-4" /></button>
                </div>
              </div>
            </div>
            <div data-testid="widgets-panel-scroll" className="dashboard-widgets-scroll ml-1 mr-2 overflow-y-auto pb-4 pl-3 pr-2"
              style={{ maxHeight: panelPosition ? `calc(100dvh - ${panelPosition.top + 77}px)` : undefined }}>
              {(!editorOpen || !selected) && <>
              <section aria-label="Add widgets">
                <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-gray-500">Add widgets</h3>
                <div className="space-y-2">
                  {widgetRegistry.filter((definition) => !definition.legacy && !usedIds.has(definition.id)).map((definition) =>
                    <PaletteCard key={definition.id} id={definition.id} title={definition.title}
                      onAdd={() => addFromPalette(definition.id)} />)}
                  {widgetRegistry.every((definition) => definition.legacy || usedIds.has(definition.id)) &&
                    <p className="text-xs text-gray-400">All widgets are on the dashboard.</p>}
                </div>
              </section>
              <LayerRoot dropTarget={layerDropTarget === LAYER_ROOT_DROP_ID}>
                {document.items.map((item) => item.kind === 'group'
                  ? <LayerGroupRow key={item.id} group={item} collapsed={collapsedGroupIds.has(item.id)}
                    dropTarget={layerDropTarget === layerGroupDropId(item.id)}
                    insertionSide={groupInsertion?.targetId === item.id ? groupInsertion.side : null}
                    itemDropTarget={layerDropTarget?.startsWith('layer-item:') ? layerDropTarget.slice(layerItemDropId('').length) : null}
                    showAdd={addingToGroupId === item.id}
                    availableWidgets={widgetRegistry.filter((definition) => !definition.legacy && !usedIds.has(definition.id))}
                    onToggle={() => setCollapsedGroupIds((current) => {
                      const next = new Set(current);
                      if (next.has(item.id)) next.delete(item.id); else next.add(item.id);
                      return next;
                    })}
                    onEdit={() => actions.onEdit(item.id)}
                    onAdd={() => setAddingToGroupId((current) => current === item.id ? null : item.id)}
                    onAddWidget={(id) => {
                      addFromPalette(id, item.id);
                      setAddingToGroupId(null);
                      setCollapsedGroupIds((current) => { const next = new Set(current); next.delete(item.id); return next; });
                    }}
                    onRemoveWidget={(id) => actions.onRemove(id)} />
                  : <ul key={item.id} aria-label={`Ungrouped widget ${item.title ?? widgetById[item.id]?.title ?? item.id}`} className="rounded-lg border border-gray-200 bg-white px-2 py-1">
                    <LayerWidgetRow widget={item} dropTarget={layerDropTarget === layerItemDropId(item.id)} onRemove={() => actions.onRemove(item.id)} />
                  </ul>)}
                {document.items.length === 0 && <p className="text-xs text-gray-400">No widgets on the dashboard yet.</p>}
              </LayerRoot>
              </>}
              {editorOpen && selected?.kind === 'group' && <div aria-label="Selected item settings">
                <h3 className="text-sm font-semibold text-gray-900">Edit group</h3>
                <label className="mt-3 block text-xs font-medium text-gray-600">Title
                  <input key={selected.id} value={titleDraft} maxLength={80} onChange={(event) => setTitleDraft(event.target.value)}
                    onBlur={() => { if (titleDraft.trim()) commit((current) => updateLayoutItem(current, selected.id, { title: titleDraft.trim() })); }}
                    onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}
                    className="mt-1 w-full rounded-md border border-gray-200 px-2 py-1.5 text-sm text-gray-900 focus:border-cyan-400 focus:outline-none" />
                </label>
                <button type="button" onClick={() => actions.onRemove(selected.id)} className="mt-4 text-xs font-medium text-red-600 hover:underline">
                  Ungroup and keep cards
                </button>
              </div>}
            </div>
          </div>
        </aside>}
      </div>
      <DragOverlay dropAnimation={null}>
        {(activeDragId?.startsWith('palette:') || activeDragId?.startsWith('layer:') || activeDragId?.startsWith('layer-group-drag:')) && <div className="pointer-events-none flex items-center gap-1 rounded-md border border-cyan-300 bg-white px-3 py-2 text-xs font-medium text-gray-700 shadow-lg">
          <GripVertical className="h-3.5 w-3.5 text-cyan-600" />
          {activeDragId === paletteDragId('group') ? 'New titled group'
            : activeDragId.startsWith('layer-group-drag:') ? draggedGroup?.kind === 'group' ? draggedGroup.title : 'Group'
              : activeDragId.startsWith('layer:') ? widgetById[activeDragId.slice(6)]?.title : widgetById[activeDragId.slice(8)]?.title}
        </div>}
      </DragOverlay>
    </DndContext>
  </WorkspaceDashboardProvider>;
}
