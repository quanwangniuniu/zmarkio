'use client';

import { useDraggable } from '@dnd-kit/core';
import { GripVertical, Plus, Trash2 } from 'lucide-react';
import { useEffect, useRef, type CSSProperties, type PointerEvent } from 'react';
import type { DashboardLayoutConfiguration, DashboardWidgetPosition as Widget } from '@/types/dashboardLayout';
import type { DashboardLayoutReducer } from './layoutReducer';
import type { WidgetContext, WidgetDefinition } from './widgetRegistry';
import { DashboardTileControlsProvider } from './DashboardTileControls';

export function WidgetTile({
  widget,
  editing,
  context,
  onResize,
  onResizeStart,
  onResizePreview,
  onResizeEnd,
  onRemove,
  columnStep,
  dragging,
  resizing,
  definition,
  configuration,
  layout,
  style,
}: {
  style: CSSProperties;
  definition: WidgetDefinition | undefined;
  configuration: DashboardLayoutConfiguration;
  layout: DashboardLayoutReducer;
  widget: Widget;
  editing: boolean;
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
  const { resizeDelta } = layout;
  const RESIZE_STEP = configuration.resize_step;
  const { attributes, listeners, setNodeRef, setActivatorNodeRef } = useDraggable({
    id: widget.id,
    disabled: !editing,
  });
  const resizeCleanup = useRef<(() => void) | null>(null);
  const onResizeEndRef = useRef(onResizeEnd);
  onResizeEndRef.current = onResizeEnd;
  const resizeCellRef = useRef('0:0');
  const title = definition?.title ?? widget.id;
  const dragHandle = editing ? (
    <button
      ref={setActivatorNodeRef}
      type="button"
      aria-label={`Move ${title}`}
      title={`Move ${title}`}
      className="flex h-7 w-7 shrink-0 touch-none cursor-grab items-center justify-center rounded bg-white text-gray-500 opacity-0 transition-colors group-hover:opacity-100 group-focus-within:opacity-100 hover:text-gray-700 active:cursor-grabbing focus-visible:opacity-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500"
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
      className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-gray-400 opacity-0 transition-colors group-hover:opacity-100 group-focus-within:opacity-100 hover:text-red-600 focus-visible:opacity-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500"
    >
      <Trash2 className="h-4 w-4" />
    </button>
  ) : null;

  useEffect(
    () => () => {
      if (resizeCleanup.current) {
        resizeCleanup.current();
        onResizeEndRef.current();
      }
    },
    [editing],
  );

  const beginResize = (event: PointerEvent<HTMLButtonElement>) => {
    if (!editing || event.button !== 0 || resizeCleanup.current) return;
    event.preventDefault();
    event.stopPropagation();
    const { pointerId, clientX: startX, clientY: startY } = event;
    const grid = event.currentTarget.closest<HTMLElement>('.dashboard-builder-grid');
    grid?.setPointerCapture(pointerId);
    resizeCellRef.current = '0:0';
    onResizeStart(widget.id);

    const delta = (pointer: globalThis.PointerEvent) =>
      resizeDelta(pointer.clientX - startX, pointer.clientY - startY, columnStep);
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
      style={style}
    >
      <div className="h-full min-h-0 overflow-auto p-px" id={widget.id}>
        <DashboardTileControlsProvider
          value={editing ? { widgetId: widget.id, dragHandle, removeButton } : null}
        >
          {definition ? (
            definition.render(context)
          ) : (
            <p className="p-3 text-sm text-gray-500">This widget is unavailable.</p>
          )}
        </DashboardTileControlsProvider>
      </div>
      {editing && (
        <button
          type="button"
          aria-label={`Resize ${title}`}
          className={`absolute bottom-0 right-0 z-10 flex h-10 w-10 p-2 cursor-nwse-resize touch-none items-end justify-end rounded-tl-md text-gray-500 opacity-0 group-hover:opacity-100 hover:text-cyan-700 focus-visible:text-cyan-700 focus-visible:opacity-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-500`}
          onPointerDown={beginResize}
          onKeyDown={(event) => {
            const keys: Record<string, [number, number]> = {
              ArrowRight: [1, 0],
              ArrowLeft: [-1, 0],
              ArrowDown: [0, 1],
              ArrowUp: [0, -1],
            };
            const delta = keys[event.key];
            if (delta) {
              event.preventDefault();
              const { dw, dh } = resizeDelta(delta[0] * RESIZE_STEP, delta[1] * RESIZE_STEP, columnStep);
              onResize(widget.id, dw, dh);
            }
          }}
        >
          <svg
            viewBox="0 0 20 20"
            className="h-3 w-3"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            aria-hidden="true"
          >
            <path d="M5 15 15 5M10 15l5-5" />
          </svg>
        </button>
      )}
    </section>
  );
}

export function PaletteWidget({
  id,
  title,
  onAdd,
  disabledReason,
}: {
  id: string;
  title: string;
  onAdd: () => void;
  /** Set when no more of this widget can be added; explains why. */
  disabledReason?: string;
}) {
  const disabled = Boolean(disabledReason);
  const { setNodeRef, attributes, listeners, isDragging } = useDraggable({ id: `palette:${id}`, disabled });
  return (
    <div
      ref={setNodeRef}
      title={disabledReason}
      className={`flex items-center rounded border border-gray-200 bg-white text-xs text-gray-700 ${disabled ? 'opacity-50' : isDragging ? 'opacity-40' : 'hover:border-cyan-400'}`}
    >
      <button
        type="button"
        aria-label={`Drag ${title} onto dashboard`}
        onClick={onAdd}
        {...attributes}
        {...listeners}
        disabled={disabled}
        className="flex touch-none cursor-grab items-center gap-1.5 rounded px-2 py-1.5 active:cursor-grabbing focus-visible:outline focus-visible:outline-cyan-500 disabled:cursor-not-allowed"
      >
        <GripVertical className="h-3.5 w-3.5 text-gray-400" />
        {title}
        {disabledReason && <span className="text-gray-500">({disabledReason})</span>}
      </button>
      <button
        type="button"
        aria-label={`Add ${title}`}
        onClick={onAdd}
        disabled={disabled}
        className="rounded p-1.5 text-gray-500 hover:bg-cyan-50 hover:text-cyan-700 disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:hover:text-gray-500"
      >
        <Plus className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
