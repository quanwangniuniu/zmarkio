'use client';

import { useEffect, useRef, useState } from 'react';
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from 'react';
import { DndContext, PointerSensor, useDraggable, useSensor, useSensors } from '@dnd-kit/core';
import type { DragEndEvent, DragMoveEvent } from '@dnd-kit/core';
import { CSS } from '@dnd-kit/utilities';
import { GripVertical, MoveDiagonal2 } from 'lucide-react';
import { DashboardAPI } from '@/lib/api/dashboardApi';
import type { OverviewMock } from '@/types/overview';
import type { DashboardLayoutResponse, DashboardWidgetDefinition, DashboardWidgetPosition } from '@/types/dashboardLayout';
import { placeWidget } from './layout';
import { widgetRegistry } from './widgetRegistry';
import styles from './DashboardBuilder.module.css';

const ROW_HEIGHT = 24;
const GRID_GAP = 8;

interface DashboardBuilderProps {
  data: OverviewMock;
  projectId: number | string;
  projectName?: string | null;
  projectSlug?: string | null;
  contentLoading: boolean;
}

function WidgetCard({
  widget, definition, context, onMoveKey, onResizeStart, onResizeKey,
}: {
  widget: DashboardWidgetPosition;
  definition: DashboardWidgetDefinition;
  context: DashboardBuilderProps;
  onMoveKey: (event: ReactKeyboardEvent<HTMLButtonElement>, widget: DashboardWidgetPosition) => void;
  onResizeStart: (event: ReactPointerEvent<HTMLButtonElement>, widget: DashboardWidgetPosition) => void;
  onResizeKey: (event: ReactKeyboardEvent<HTMLButtonElement>, widget: DashboardWidgetPosition) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({ id: widget.id });
  const renderer = widgetRegistry[widget.id];
  const position = {
    '--widget-column': `${widget.x + 1} / span ${widget.w}`,
    '--widget-row': `${widget.y + 1} / span ${widget.h}`,
    transform: CSS.Translate.toString(transform),
    zIndex: isDragging ? 10 : undefined,
  } as CSSProperties;

  return (
    <section ref={setNodeRef} className={styles.widget} style={position} data-widget-id={widget.id}>
      <div className={styles.widgetHeader}>
        <button
          type="button"
          className={styles.dragHandle}
          aria-label={`Move ${definition.label}`}
          {...attributes}
          {...listeners}
          onKeyDown={(event) => onMoveKey(event, widget)}
        >
          <GripVertical size={15} />
        </button>
        <span className="truncate text-xs font-medium text-gray-600">{definition.label}</span>
      </div>
      <div className={styles.widgetContent}>{renderer?.(context)}</div>
      <button
        type="button"
        className={styles.resizeHandle}
        aria-label={`Resize ${definition.label}`}
        onPointerDown={(event) => onResizeStart(event, widget)}
        onKeyDown={(event) => onResizeKey(event, widget)}
      >
        <MoveDiagonal2 size={16} />
      </button>
    </section>
  );
}

export default function DashboardBuilder(props: DashboardBuilderProps) {
  const { projectId, projectName, contentLoading } = props;
  const [layout, setLayout] = useState<DashboardLayoutResponse | null>(null);
  const [widgets, setWidgets] = useState<DashboardWidgetPosition[]>([]);
  const [resizePreview, setResizePreview] = useState<DashboardWidgetPosition[] | null>(null);
  const [dropPreview, setDropPreview] = useState<DashboardWidgetPosition | null>(null);
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [loadError, setLoadError] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const gridRef = useRef<HTMLDivElement>(null);
  const saveChain = useRef<Promise<void>>(Promise.resolve());
  const revision = useRef(0);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));

  useEffect(() => {
    let cancelled = false;
    setLayout(null);
    setLoadError(false);
    DashboardAPI.getLayout(projectId).then(({ data }) => {
      if (cancelled) return;
      setLayout(data);
      setWidgets(data.widgets);
      setSaveState('idle');
    }).catch(() => {
      if (!cancelled) setLoadError(true);
    });
    return () => { cancelled = true; };
  }, [projectId, reloadKey]);

  function persist(next: DashboardWidgetPosition[]) {
    setWidgets(next);
    setSaveState('saving');
    const currentRevision = ++revision.current;
    saveChain.current = saveChain.current.catch(() => undefined).then(async () => {
      await DashboardAPI.saveLayout(projectId, next);
      if (currentRevision === revision.current) setSaveState('saved');
    }).catch(() => {
      if (currentRevision === revision.current) setSaveState('error');
    });
  }

  function movedLayout(event: DragMoveEvent | DragEndEvent) {
    if (!layout) return null;
    const widget = widgets.find((item) => item.id === event.active.id);
    const grid = gridRef.current;
    if (!widget || !grid) return null;
    const columnStep = (grid.clientWidth + GRID_GAP) / layout.columns;
    const candidate = {
      ...widget,
      x: widget.x + Math.round(event.delta.x / columnStep),
      y: widget.y + Math.round(event.delta.y / (ROW_HEIGHT + GRID_GAP)),
    };
    return placeWidget(widgets, candidate, layout.columns, layout.max_rows);
  }

  function onDragMove(event: DragMoveEvent) {
    const next = movedLayout(event);
    setDropPreview(next?.find((widget) => widget.id === event.active.id) ?? null);
  }

  function onDragEnd(event: DragEndEvent) {
    const next = movedLayout(event);
    setDropPreview(null);
    if (!next) return;
    if (JSON.stringify(next) !== JSON.stringify(widgets)) persist(next);
  }

  function onMoveKey(event: ReactKeyboardEvent<HTMLButtonElement>, widget: DashboardWidgetPosition) {
    if (!layout) return;
    const change = {
      ArrowRight: { x: 1, y: 0 }, ArrowLeft: { x: -1, y: 0 },
      ArrowDown: { x: 0, y: 1 }, ArrowUp: { x: 0, y: -1 },
    }[event.key];
    if (!change) return;
    event.preventDefault();
    const next = placeWidget(widgets, {
      ...widget, x: widget.x + change.x, y: widget.y + change.y,
    }, layout.columns, layout.max_rows);
    if (JSON.stringify(next) !== JSON.stringify(widgets)) persist(next);
  }

  function onResizeStart(event: ReactPointerEvent<HTMLButtonElement>, widget: DashboardWidgetPosition) {
    if (!layout || !gridRef.current || event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const startX = event.clientX;
    const startY = event.clientY;
    const columnStep = (gridRef.current.clientWidth + GRID_GAP) / layout.columns;
    const resizedLayout = (clientX: number, clientY: number) => {
      const definition = layout.catalog.find((item) => item.id === widget.id);
      const candidate = {
        ...widget,
        w: Math.max(3, widget.w + Math.round((clientX - startX) / columnStep)),
        h: Math.max(definition?.min_h ?? 1, widget.h + Math.round((clientY - startY) / (ROW_HEIGHT + GRID_GAP))),
      };
      return placeWidget(widgets, candidate, layout.columns, layout.max_rows);
    };
    const onPointerMove = (moveEvent: PointerEvent) => {
      setResizePreview(resizedLayout(moveEvent.clientX, moveEvent.clientY));
    };
    const cleanup = () => {
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);
      window.removeEventListener('pointercancel', onPointerCancel);
      setResizePreview(null);
    };
    const onPointerUp = (endEvent: PointerEvent) => {
      const next = resizedLayout(endEvent.clientX, endEvent.clientY);
      cleanup();
      if (JSON.stringify(next) !== JSON.stringify(widgets)) persist(next);
    };
    const onPointerCancel = () => cleanup();
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
    window.addEventListener('pointercancel', onPointerCancel);
  }

  function onResizeKey(event: ReactKeyboardEvent<HTMLButtonElement>, widget: DashboardWidgetPosition) {
    if (!layout) return;
    const definition = layout.catalog.find((item) => item.id === widget.id);
    const change = {
      ArrowRight: { w: 1, h: 0 }, ArrowLeft: { w: -1, h: 0 },
      ArrowDown: { w: 0, h: 1 }, ArrowUp: { w: 0, h: -1 },
    }[event.key];
    if (!change) return;
    event.preventDefault();
    const next = placeWidget(widgets, {
      ...widget,
      w: Math.max(3, widget.w + change.w),
      h: Math.max(definition?.min_h ?? 1, widget.h + change.h),
    }, layout.columns, layout.max_rows);
    if (JSON.stringify(next) !== JSON.stringify(widgets)) persist(next);
  }

  if (loadError) {
    return <div role="alert" className="rounded-lg border bg-white p-4 text-sm">Could not load dashboard layout. <button className="underline" onClick={() => setReloadKey((key) => key + 1)}>Try again</button></div>;
  }
  if (!layout) return <div className="rounded-lg border bg-white p-4 text-sm text-gray-500">Loading dashboard layout…</div>;

  const definitions = new Map(layout.catalog.map((definition) => [definition.id, definition]));

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">{projectName ?? 'Project'} dashboard</h1>
          <p className="text-xs text-gray-500">Drag a card by its handle. Resize from the lower-right corner.</p>
        </div>
        <div className="flex items-center gap-3">
          <span role="status" className="text-xs text-gray-500">
            {saveState === 'saving' ? 'Saving…' : saveState === 'saved' ? 'Saved' : saveState === 'error' ? 'Could not save' : ''}
          </span>
          {saveState === 'error' && <button type="button" className="text-xs underline" onClick={() => persist(widgets)}>Retry</button>}
        </div>
      </div>
      {contentLoading && <p role="status" className="mb-3 text-xs text-gray-500">Loading card data…</p>}
      <DndContext sensors={sensors} onDragMove={onDragMove} onDragEnd={onDragEnd} onDragCancel={() => setDropPreview(null)}>
        <div ref={gridRef} className={styles.grid}>
          {dropPreview && (
            <div aria-hidden="true" data-testid="dashboard-drop-preview" className={styles.dropPreview} style={{
              '--widget-column': `${dropPreview.x + 1} / span ${dropPreview.w}`,
              '--widget-row': `${dropPreview.y + 1} / span ${dropPreview.h}`,
            } as CSSProperties} />
          )}
          {(resizePreview ?? widgets).map((widget) => {
            const definition = definitions.get(widget.id);
            if (!definition || !widgetRegistry[widget.id]) return null;
            return <WidgetCard key={widget.id} widget={widget} definition={definition} context={props} onMoveKey={onMoveKey} onResizeStart={onResizeStart} onResizeKey={onResizeKey} />;
          })}
        </div>
      </DndContext>
    </div>
  );
}
