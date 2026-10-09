'use client';

import { KeyboardSensor, PointerSensor, useSensor, useSensors, type DragStartEvent, type DragMoveEvent, type DragEndEvent } from '@dnd-kit/core';
import { useEffect, useRef, useState, type MutableRefObject, type RefObject } from 'react';
import type { DashboardLayoutConfiguration, DashboardWidgetPosition as Widget } from '@/types/dashboardLayout';
import type { DashboardLayoutReducer } from '@/components/dashboard/builder/layoutReducer';
import type { createWidgetRegistry } from '@/components/dashboard/builder/widgetRegistry';

/** Manage transient drag/resize previews. Saving remains the caller's responsibility. */
export function useDashboardGestures({ configuration, layout, registry, editingRef, widgetsRef, gridRef, columnStep, canvasToLayoutY, commit }: {
  configuration: DashboardLayoutConfiguration;
  layout: DashboardLayoutReducer;
  registry: ReturnType<typeof createWidgetRegistry>;
  editingRef: MutableRefObject<boolean>;
  widgetsRef: MutableRefObject<Widget[]>;
  gridRef: RefObject<HTMLDivElement>;
  columnStep: number;
  canvasToLayoutY: (y: number) => number;
  commit: (update: (current: Widget[]) => Widget[]) => void;
}) {
  const GRID_COLUMNS = configuration.columns;
  const ROW_STEP = configuration.row_height + configuration.gap;
  const RESIZE_STEP = configuration.resize_step;
  const { resizeDelta, moveWidget, dropWidget, resizeWidget } = layout;
  const { createWidget, getWidgetDefinition } = registry;
  const [dragPreview, setDragPreview] = useState<Widget[] | null>(null);
  const [activeDragId, setActiveDragId] = useState<string | null>(null);
  const [resizePreview, setResizePreview] = useState<Widget[] | null>(null);
  const [activeResizeId, setActiveResizeId] = useState<string | null>(null);
  const [gestureGridMinHeight, setGestureGridMinHeight] = useState<number | null>(null);
  const gestureScrollRef = useRef<{ element: HTMLElement; overflowAnchor: string } | null>(null);
  const dragCellRef = useRef('0:0');
  const dragActiveRef = useRef(false);
  const paletteRef = useRef<Widget | null>(null);
  const pointerRef = useRef<{ x: number; y: number } | null>(null);
  const [paletteDrag, setPaletteDrag] = useState(false);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor),
  );

  useEffect(() => {
    const rememberPointer = (event: globalThis.PointerEvent) => {
      pointerRef.current = { x: event.clientX, y: event.clientY };
    };
    // Keep viewport coordinates exact when the dashboard scrolls during a palette drag.
    window.addEventListener('pointermove', rememberPointer, true);
    window.addEventListener('pointerup', rememberPointer, true);
    return () => {
      window.removeEventListener('pointermove', rememberPointer, true);
      window.removeEventListener('pointerup', rememberPointer, true);
    };
  }, []);
  useEffect(
    () => () => {
      if (gestureScrollRef.current) {
        gestureScrollRef.current.element.style.overflowAnchor = gestureScrollRef.current.overflowAnchor;
        gestureScrollRef.current = null;
      }
    },
    [],
  );

  const snappedDelta = (event: DragMoveEvent | DragEndEvent) => {
    const { dw, dh } = resizeDelta(event.delta.x, event.delta.y, columnStep);
    const widget = widgetsRef.current.find((item) => item.id === String(event.active.id));
    let dx = dw;
    let dy = dh;
    // Snap to the canvas edges when a rounded configured step lands just beside them.
    if (widget) {
      if ((widget.x + dx) * columnStep < RESIZE_STEP / 2) dx = -widget.x;
      else if ((GRID_COLUMNS - widget.w - widget.x - dx) * columnStep < RESIZE_STEP / 2)
        dx = GRID_COLUMNS - widget.w - widget.x;
      if ((widget.y + dy) * ROW_STEP < RESIZE_STEP / 2) dy = -widget.y;
    }
    return { dx, dy };
  };
  const beginGesture = () => {
    const grid = gridRef.current;
    setGestureGridMinHeight(grid?.getBoundingClientRect().height ?? null);
    const scrollContainer = grid?.closest('main');
    if (scrollContainer) {
      gestureScrollRef.current = {
        element: scrollContainer,
        overflowAnchor: scrollContainer.style.overflowAnchor,
      };
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
    const x = keyboard
      ? (translated?.left ?? rect.left)
      : (pointerRef.current?.x ?? pointer.clientX + event.delta.x);
    const y = keyboard
      ? (translated?.top ?? rect.top)
      : (pointerRef.current?.y ?? pointer.clientY + event.delta.y);
    if (x < rect.left || x > rect.right || y < rect.top || y > rect.bottom) return null;
    return {
      ...incoming,
      x:
        Math.max(0, Math.min(GRID_COLUMNS - incoming.w, Math.floor((x - rect.left) / columnStep))),
      y: Math.max(0, Math.min(configuration.max_y, Math.floor(canvasToLayoutY(y - rect.top) / ROW_STEP))),
    };
  };
  const onDragStart = (event: DragStartEvent) => {
    if (!editingRef.current) return;
    dragActiveRef.current = true;
    beginGesture();
    const dragId = String(event.active.id);
    const fromPalette = dragId.startsWith('palette:');
    const id = fromPalette ? dragId.slice(8) : dragId;
    paletteRef.current = fromPalette ? createWidget(getWidgetDefinition(id)!) : null;
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
      setDragPreview(
        target ? dropWidget(widgetsRef.current, target, target.x, target.y) : widgetsRef.current,
      );
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
  const previewResize = (id: string, dw: number, dh: number) => {
    if (editingRef.current) setResizePreview(resizeWidget(widgetsRef.current, id, dw, dh));
  };
  return { dragPreview, resizePreview, activeDragId, activeResizeId, gestureGridMinHeight, paletteDrag, paletteRef,
    sensors, onDragStart, onDragMove, onDragEnd, startResize, endResize, previewResize, cancelGesture };
}
