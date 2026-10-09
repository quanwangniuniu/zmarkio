'use client';

import { DndContext, DragOverlay, useDroppable } from '@dnd-kit/core';
import { GripVertical, Pencil, RotateCcw } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from 'react';
import { useDashboardLayout, useDashboardLayoutSave } from '@/hooks/useDashboardLayout';
import { useDashboardGestures } from '@/hooks/useDashboardGestures';
import type { DashboardLayoutResponse, DashboardWidgetPosition as Widget } from '@/types/dashboardLayout';
import { createLayoutReducer } from './layoutReducer';
import { createWidgetRegistry, type WidgetContext } from './widgetRegistry';
import { WidgetTile, PaletteWidget } from './DashboardWidget';
import { WorkspaceDashboardError, WorkspaceDashboardProvider, isWorkspaceWidgetId, SectionLabel } from '@/components/projects/WorkspaceDashboard';


const NATIVE_SECTIONS = [
  { label: 'Project Overview', ids: ['overall-progress', 'tasks-completed', 'task-completion-rate', 'overdue-tasks', 'needs-attention'] },
  { label: 'Module Summary', ids: ['decisions', 'tasks', 'operations'] },
];
const LABEL_HEIGHT = 24;

/** Restore original static labels by widget identity, without persisting title widgets. */
function nativeSections(widgets: Widget[], rowStep: number, widgetStyle: (widget: Widget) => CSSProperties) {
  const labels = NATIVE_SECTIONS.flatMap(({ label, ids }) => {
    const first = widgets.filter((widget) => ids.includes(widget.id)).sort((a, b) => a.y - b.y || a.x - b.x)[0];
    return first ? [{ label, first }] : [];
  });
  const rows = [...new Set(labels.map(({ first }) => first.y))].sort((a, b) => a - b);
  const offset = (y: number) => rows.filter((row) => row <= y).length * LABEL_HEIGHT;
  return {
    labels: labels.map(({ label, first }) => ({ label, style: { ...widgetStyle(first), top: first.y * rowStep + offset(first.y) - LABEL_HEIGHT, height: LABEL_HEIGHT } })),
    style: (widget: Widget) => ({ ...widgetStyle(widget), top: widget.y * rowStep + offset(widget.y) }),
    canvasToLayoutY: (pixelY: number) => pixelY - rows.filter((row, index) => pixelY >= row * rowStep + index * LABEL_HEIGHT).length * LABEL_HEIGHT,
  };
}

/** The editor mounts only after its server-owned configuration and layout are loaded. */
export default function DashboardBuilder(context: WidgetContext) {
  const { layout, error, retry } = useDashboardLayout(context.projectId);
  if (error) return (
    <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
      Dashboard layout could not be loaded.{' '}
      <button type="button" onClick={() => void retry()} className="underline">Retry</button>
    </div>
  );
  if (!layout) return <div className="rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-500">Loading dashboard layout…</div>;
  return <DashboardEditor key={String(layout.project_id)} {...context} initialLayout={layout} />;
}

function Canvas({
  gridRef,
  children,
  minHeight,
  gap,
}: {
  gridRef: RefObject<HTMLDivElement>;
  children: ReactNode;
  minHeight: number;
  gap: number;
}) {
  const { setNodeRef } = useDroppable({ id: 'dashboard-canvas' });
  return (
    <div
      ref={(node) => {
        setNodeRef(node);
        (gridRef as React.MutableRefObject<HTMLDivElement | null>).current = node;
      }}
      data-testid="dashboard-canvas"
      className="dashboard-builder-grid relative"
      style={{ height: minHeight, minHeight, '--dashboard-gap': `${gap}px` } as CSSProperties}
    >
      {children}
    </div>
  );
}

function DashboardEditor(context: WidgetContext & { initialLayout: DashboardLayoutResponse }) {
  const { projectId, initialLayout } = context;
  const configuration = initialLayout.configuration;
  const GRID_COLUMNS = configuration.columns;
  const GAP = configuration.gap;
  const ROW_STEP = configuration.row_height + GAP;
  const layout = useMemo(() => createLayoutReducer(configuration), [configuration]);
  const { widgetStyle, addWidget, removeWidget, resizeWidget } = layout;
  const registry = useMemo(() => createWidgetRegistry(configuration), [configuration]);
  const { widgets: widgetRegistry, getWidgetDefinition, createWidget } = registry;
  const { widgets, widgetsRef, saveState, update, flush } = useDashboardLayoutSave(projectId, initialLayout.widgets);
  const [editing, setEditing] = useState(false);
  const editingRef = useRef(editing);
  editingRef.current = editing;
  const [gridWidth, setGridWidth] = useState(1200);
  const gridRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!gridRef.current) return;
    const grid = gridRef.current;
    const measure = () => setGridWidth(grid.clientWidth);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(grid);
    return () => observer.disconnect();
  }, []);

  const columnStep = (gridWidth + GAP) / GRID_COLUMNS;
  const commit = (change: (current: Widget[]) => Widget[]) => {
    if (editingRef.current) update(change);
  };
  const sections = nativeSections(widgets, ROW_STEP, widgetStyle);
  const { dragPreview, resizePreview, activeDragId, activeResizeId, gestureGridMinHeight, paletteDrag, paletteRef,
    sensors, onDragStart, onDragMove, onDragEnd, startResize, endResize, previewResize, cancelGesture } =
    useDashboardGestures({ configuration, layout, registry, editingRef, widgetsRef, gridRef, columnStep, canvasToLayoutY: sections.canvasToLayoutY, commit });
  const displayedWidgets = resizePreview ?? dragPreview ?? widgets;
  const displayedSections = nativeSections(displayedWidgets, ROW_STEP, widgetStyle);
  const layoutHeight = Math.max(
    0,
    ...displayedWidgets.map((widget) => Number(displayedSections.style(widget).top) + Number(widgetStyle(widget).height)),
  );
  const toggleEditing = () => {
    if (editing) cancelGesture();
    // Block stale pointer/keyboard callbacks immediately when leaving editing.
    editingRef.current = !editing;
    setEditing(!editing);
  };

  return (
    <WorkspaceDashboardProvider projectId={projectId}>
      <div>
        <div className="mb-3 flex flex-wrap items-center justify-end gap-2">
          <h1 className="min-w-0 max-w-full break-words text-2xl font-semibold text-gray-900">
            {context.projectName?.trim() || 'Overview'}
          </h1>
          {editing && (
            <p role="note" className="min-w-[16rem] flex-1 text-xs text-gray-500">
              <strong className="font-medium text-gray-600">Tips:</strong> Drag any component onto the
              dashboard to customize your layout.
            </p>
          )}
          <div className="ml-auto flex shrink-0 items-center gap-2">
            <span
              role={saveState === 'failed' ? 'alert' : 'status'}
              className={`text-xs ${saveState === 'failed' ? 'text-red-700' : 'text-gray-500'}`}
            >
              {saveState === 'failed'
                ? 'Could not save layout'
                : saveState === 'saving'
                  ? 'Saving…'
                  : 'Saved'}
            </span>
            {saveState === 'failed' && (
              <button
                type="button"
                onClick={() => void flush()}
                className="flex items-center gap-1 rounded border border-red-200 px-2 py-1 text-xs text-red-700"
              >
                <RotateCcw className="h-3 w-3" />
                Retry
              </button>
            )}
            <button
              type="button"
              aria-expanded={editing}
              aria-pressed={editing}
              onClick={toggleEditing}
              className="flex items-center gap-1 rounded-md border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
            >
              <Pencil className="h-3.5 w-3.5" /> Customize
            </button>
          </div>
        </div>
        {widgets.some((widget) => isWorkspaceWidgetId(widget.id)) && <WorkspaceDashboardError />}
        <DndContext
          sensors={editing ? sensors : []}
          onDragStart={onDragStart}
          onDragMove={onDragMove}
          onDragEnd={onDragEnd}
          onDragCancel={cancelGesture}
        >
          {editing && (
            <div
              className="mb-3 flex flex-wrap gap-2 rounded-lg border border-gray-200 bg-gray-50 p-2"
              aria-label="Widget picker"
            >
              {widgetRegistry
                .filter((definition) => !widgets.some((widget) => widget.id === definition.id))
                .map((definition) => (
                  <PaletteWidget
                    key={definition.id}
                    id={definition.id}
                    title={definition.title}
                    onAdd={() => commit((current) => addWidget(current, createWidget(definition)))}
                  />
                ))}
              {widgetRegistry.every((definition) => widgets.some((widget) => widget.id === definition.id)) && (
                <p className="px-2 py-1.5 text-xs text-gray-500">All widgets are on your dashboard.</p>
              )}
            </div>
          )}
          <div style={{ minHeight: gestureGridMinHeight ?? undefined }}>
            <Canvas
              gridRef={gridRef}
              gap={GAP}
              minHeight={Math.max(
                layoutHeight,
                gestureGridMinHeight ?? 0,
                paletteDrag
                  ? (Math.max(0, ...widgets.map((widget) => widget.y + widget.h)) +
                      (paletteRef.current?.h ?? 4)) *
                      ROW_STEP
                  : ROW_STEP * 4,
              )}
            >
              {displayedSections.labels.map(({ label, style }) => (
                <div key={label} data-testid={`native-section-${label.toLowerCase().replaceAll(' ', '-')}`} style={style}>
                  <SectionLabel>{label}</SectionLabel>
                </div>
              ))}
              {displayedWidgets.map((widget) =>
                paletteDrag && widget.id === activeDragId ? (
                  <div
                    key={widget.id}
                    data-testid="widget-drop-preview"
                    aria-hidden="true"
                    className="pointer-events-none rounded-xl border-2 border-dashed border-cyan-400 bg-cyan-50/50"
                    style={displayedSections.style(widget)}
                  />
                ) : (
                  <WidgetTile
                    key={widget.id}
                    widget={widget}
                    style={displayedSections.style(widget)}
                    editing={editing}
                    context={context}
                    definition={getWidgetDefinition(widget.id)}
                    configuration={configuration}
                    layout={layout}
                    columnStep={columnStep}
                    dragging={activeDragId === widget.id}
                    resizing={activeResizeId === widget.id}
                    onResizeStart={startResize}
                    onResizePreview={previewResize}
                    onResizeEnd={endResize}
                    onResize={(id, dw, dh) => commit((current) => resizeWidget(current, id, dw, dh))}
                    onRemove={(id) => {
                      cancelGesture();
                      commit((current) => removeWidget(current, id));
                    }}
                  />
                ),
              )}
            </Canvas>
          </div>
          <DragOverlay dropAnimation={null}>
            {activeDragId && (
              <div className="pointer-events-none flex items-center gap-1 rounded-md border border-cyan-300 bg-white px-3 py-2 text-xs font-medium text-gray-700 shadow-lg">
                <GripVertical className="h-3.5 w-3.5 text-cyan-600" />
                {getWidgetDefinition(activeDragId)?.title ??
                  activeDragId}
              </div>
            )}
          </DragOverlay>
        </DndContext>
        {widgets.length === 0 && (
          <p className="rounded-lg border border-dashed border-gray-200 p-6 text-center text-sm text-gray-500">
            Your dashboard is empty. Add a widget to get started.
          </p>
        )}
      </div>
    </WorkspaceDashboardProvider>
  );
}
