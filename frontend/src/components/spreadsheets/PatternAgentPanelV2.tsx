'use client';

import { useEffect, useMemo, useState } from 'react';
import { DndContext, closestCenter } from '@dnd-kit/core';
import { arrayMove, SortableContext, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { ChevronDown, ChevronLeft, ChevronRight, GripVertical, Trash2, PencilLine, Trash, X } from 'lucide-react';
import Modal from '@/components/ui/Modal';
import { columnIndexToLabel, parseA1 } from '@/lib/spreadsheets/a1';
import {
  PatternJobStatus,
  PatternStep,
  WorkflowPatternSummary,
  TimelineItem,
  OperationGroup,
  isOperationGroup,
} from '@/types/patterns';

const DEFAULT_GROUP_NAME = 'Grouped Operation';

interface PatternAgentPanelProps {
  loading?: boolean;
  items: TimelineItem[];
  patterns: WorkflowPatternSummary[];
  selectedPatternId: string | null;
  applySteps: Array<{
    id: string;
    seq: number;
    type: string;
    params: Record<string, any>;
    disabled: boolean;
    status: 'pending' | 'success' | 'error';
    errorMessage?: string;
  }>;
  applyError: string | null;
  applyFailedIndex: number | null;
  isApplying: boolean;
  exporting: boolean;
  applyJobStatus: PatternJobStatus | null;
  applyJobProgress: number;
  applyJobError: string | null;
  onReorder: (items: TimelineItem[]) => void;
  onUpdateStep: (
    id: string,
    updates: Partial<PatternStep> | Partial<Pick<OperationGroup, 'name' | 'collapsed' | 'items'>>
  ) => void;
  onDeleteStep: (id: string) => void;
  onHoverStep: (step: PatternStep) => void;
  onClearHover: () => void;
  onExportPattern: (name: string, selectedItems: TimelineItem[]) => Promise<boolean>;
  onSelectPattern: (patternId: string) => void;
  onDeletePattern: (patternId: string) => void;
  onApplyPattern: () => void;
  onRetryApply: () => void;
  /** When true, Apply Pattern is disabled (e.g. sheet still hydrating after import). */
  disableApplyPattern?: boolean;
  /** Move a step out of a group so it becomes a standalone timeline item. */
  onMoveStepOutOfGroup?: (groupId: string, step: PatternStep) => void;
  /** Initial collapsed state. */
  defaultCollapsed?: boolean;
  /** Called when collapse state changes. */
  onCollapseChange?: (collapsed: boolean) => void;
}

const formatTime = (iso: string) => {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString();
};

  const StepCard = ({
  step,
  isSelected,
  onSelect,
  onToggleDisabled,
  onDelete,
  onDoubleClick,
  onHover,
  onLeave,
}: {
  step: PatternStep;
  isSelected: boolean;
  onSelect: () => void;
  onToggleDisabled: () => void;
  onDelete: () => void;
  onDoubleClick: () => void;
  onHover: () => void;
  onLeave: () => void;
}) => {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: step.id,
  });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  let preview = '';
  if (step.type === 'APPLY_FORMULA') {
    preview = `${step.a1} = ${step.formula.startsWith('=') ? step.formula.slice(1) : step.formula}`;
  } else if (step.type === 'INSERT_ROW') {
    preview = `Insert row ${step.params.position} ${step.params.index}`;
  } else if (step.type === 'INSERT_COLUMN') {
    const label = columnIndexToLabel(step.params.index);
    preview = `Insert column ${step.params.position} of ${label}`;
  } else if (step.type === 'DELETE_COLUMN') {
    const label = columnIndexToLabel(step.params.index);
    preview = `Delete column ${label}`;
  } else if (step.type === 'FILL_SERIES') {
    const source = step.params.source;
    const range = step.params.range;
    const sourceLabel = `${columnIndexToLabel(source.col)}${source.row}`;
    const startLabel = `${columnIndexToLabel(range.start_col)}${range.start_row}`;
    const endLabel = `${columnIndexToLabel(range.end_col)}${range.end_row}`;
    preview = `Fill ${sourceLabel} → ${startLabel}:${endLabel}`;
  } else if (step.type === 'SET_COLUMN_NAME') {
    const columnIndex = step.params.column_ref?.index ?? 1;
    const label = columnIndexToLabel(columnIndex);
    const name = step.params.to_header ?? '';
    preview = `Rename column ${label} to ${name}`;
  } else if (step.type === 'APPLY_HIGHLIGHT') {
    const scope = step.params.scope?.toLowerCase?.() ?? 'range';
    const color = step.params.color ?? '';
    const header = step.params.target?.by_header;
    if (step.params.scope === 'COLUMN' && header) {
      preview = `Highlight column '${header}' (${color})`;
    } else {
      const fallback = step.params.target?.fallback;
      const startRow = fallback?.start_row ?? fallback?.row_index ?? 1;
      const endRow = fallback?.end_row ?? startRow;
      const startCol = fallback?.start_col ?? fallback?.col_index ?? 1;
      const endCol = fallback?.end_col ?? startCol;
      const startLabel = `${columnIndexToLabel(startCol)}${startRow}`;
      const endLabel = `${columnIndexToLabel(endCol)}${endRow}`;
      preview = `Highlight ${scope} ${startLabel}:${endLabel} (${color})`;
    }
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      onMouseEnter={onHover}
      onMouseLeave={onLeave}
      className={`group rounded-lg border border-gray-200 bg-white p-3 shadow-sm transition ${
        step.disabled ? 'opacity-60' : ''
      } ${isDragging ? 'shadow-lg' : 'hover:shadow-md hover:scale-[1.01]'}`}
    >
      <div className="flex items-start gap-2">
        <button
          type="button"
          className="mt-1 text-gray-400 hover:text-gray-600"
          {...attributes}
          {...listeners}
          aria-label="Drag to reorder"
        >
          <GripVertical className="h-4 w-4" />
        </button>
        <div className="flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="text-sm font-semibold text-gray-900">{preview}</div>
            <button
              type="button"
              onClick={onDelete}
              className="text-gray-400 hover:text-red-500"
              aria-label="Delete step"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
          <div className="mt-1 text-xs text-gray-500">{formatTime(step.createdAt)}</div>
          <div className="mt-2 flex items-center justify-between gap-2 text-xs text-gray-600">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={isSelected} onChange={onSelect} />
              Select
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={step.disabled} onChange={onToggleDisabled} />
              Disable
            </label>
          </div>
        </div>
        {step.type === 'APPLY_FORMULA' && (
          <button
            type="button"
            onClick={onDoubleClick}
            className="text-gray-400 hover:text-[#0E8A96]"
            aria-label="Edit step"
          >
            <PencilLine className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );
};

function stepPreview(step: PatternStep): string {
  if (step.type === 'APPLY_FORMULA') {
    return `${step.a1} = ${step.formula.startsWith('=') ? step.formula.slice(1) : step.formula}`;
  }
  if (step.type === 'INSERT_ROW') return `Insert row ${step.params.position} ${step.params.index}`;
  if (step.type === 'INSERT_COLUMN') {
    return `Insert column ${step.params.position} of ${columnIndexToLabel(step.params.index)}`;
  }
  if (step.type === 'DELETE_COLUMN') return `Delete column ${columnIndexToLabel(step.params.index)}`;
  if (step.type === 'FILL_SERIES') {
    const source = step.params.source;
    const range = step.params.range;
    const sourceLabel = `${columnIndexToLabel(source.col)}${source.row}`;
    const startLabel = `${columnIndexToLabel(range.start_col)}${range.start_row}`;
    const endLabel = `${columnIndexToLabel(range.end_col)}${range.end_row}`;
    return `Fill ${sourceLabel} → ${startLabel}:${endLabel}`;
  }
  if (step.type === 'SET_COLUMN_NAME') {
    const columnIndex = step.params.column_ref?.index ?? 1;
    const label = columnIndexToLabel(columnIndex);
    return `Rename column ${label} to ${step.params.to_header ?? ''}`;
  }
  if (step.type === 'APPLY_HIGHLIGHT') {
    const scope = step.params.scope?.toLowerCase?.() ?? 'range';
    const color = step.params.color ?? '';
    const header = step.params.target?.by_header;
    if (step.params.scope === 'COLUMN' && header) return `Highlight column '${header}' (${color})`;
    const fallback = step.params.target?.fallback;
    const startRow = fallback?.start_row ?? fallback?.row_index ?? 1;
    const endRow = fallback?.end_row ?? startRow;
    const startCol = fallback?.start_col ?? fallback?.col_index ?? 1;
    const endCol = fallback?.end_col ?? startCol;
    return `Highlight ${scope} ${columnIndexToLabel(startCol)}${startRow}:${columnIndexToLabel(endCol)}${endRow} (${color})`;
  }
  return String((step as { type: string }).type);
}

const GroupCard = ({
  group,
  isSelected,
  onSelect,
  onToggleCollapsed,
  onRename,
  onDelete,
  onStepHover,
  onStepLeave,
  onStepToggleDisabled,
  onStepDelete,
  onStepMoveOut,
  onStepDoubleClick,
}: {
  group: OperationGroup;
  isSelected: boolean;
  onSelect: () => void;
  onToggleCollapsed: () => void;
  onRename: (name: string) => void;
  onDelete: () => void;
  onStepHover: (step: PatternStep) => void;
  onStepLeave: () => void;
  onStepToggleDisabled: (step: PatternStep) => void;
  onStepDelete: (step: PatternStep) => void;
  onStepMoveOut?: (step: PatternStep) => void;
  onStepDoubleClick: (step: PatternStep) => void;
}) => {
  const [editingName, setEditingName] = useState(false);
  const [editNameValue, setEditNameValue] = useState(group.name);
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: group.id,
  });
  const style = { transform: CSS.Transform.toString(transform), transition };

  useEffect(() => {
    setEditNameValue(group.name);
  }, [group.name]);

  const saveRename = () => {
    const trimmed = editNameValue.trim();
    if (trimmed && trimmed !== group.name) onRename(trimmed);
    setEditingName(false);
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`rounded-lg border border-[#3CCED7]/30 bg-[#3CCED7]/5 transition ${
        isDragging ? 'shadow-lg' : 'hover:shadow-md'
      }`}
    >
      <div className="flex items-center gap-2 p-2">
        <button
          type="button"
          className="text-gray-400 hover:text-gray-600"
          {...attributes}
          {...listeners}
          aria-label="Drag to reorder"
        >
          <GripVertical className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={onToggleCollapsed}
          className="text-gray-500 hover:text-gray-700"
          aria-label={group.collapsed ? 'Expand' : 'Collapse'}
        >
          {group.collapsed ? (
            <ChevronRight className="h-4 w-4" />
          ) : (
            <ChevronDown className="h-4 w-4" />
          )}
        </button>
        <div className="flex-1 min-w-0">
          {editingName ? (
            <input
              value={editNameValue}
              onChange={(e) => setEditNameValue(e.target.value)}
              onBlur={saveRename}
              onKeyDown={(e) => {
                if (e.key === 'Enter') saveRename();
                if (e.key === 'Escape') {
                  setEditNameValue(group.name);
                  setEditingName(false);
                }
              }}
              className="w-full rounded border border-gray-300 px-2 py-0.5 text-sm font-semibold text-gray-900"
              autoFocus
            />
          ) : (
            <button
              type="button"
              onClick={() => setEditingName(true)}
              className="text-left text-sm font-semibold text-gray-900 hover:underline truncate block w-full"
            >
              {group.name}
            </button>
          )}
          <div className="text-xs text-gray-500">{group.items.length} operations</div>
        </div>
        <label className="flex items-center gap-1 text-xs text-gray-600 shrink-0">
          <input type="checkbox" checked={isSelected} onChange={onSelect} />
          Select
        </label>
        <button type="button" onClick={onDelete} className="text-gray-400 hover:text-red-500" aria-label="Delete group">
          <Trash2 className="h-4 w-4" />
        </button>
      </div>
      {!group.collapsed && (
        <div className="border-t border-[#3CCED7]/20 pl-6 pr-2 pb-2 space-y-2">
          {group.items.map((step) => (
            <div
              key={step.id}
              onMouseEnter={() => onStepHover(step)}
              onMouseLeave={onStepLeave}
              className={`rounded border border-gray-200 bg-white p-2 text-xs ${step.disabled ? 'opacity-60' : ''}`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-gray-900 truncate">{stepPreview(step)}</span>
                <div className="flex items-center gap-2 shrink-0">
                  <label className="flex items-center gap-1 text-gray-600">
                    <input
                      type="checkbox"
                      checked={step.disabled}
                      onChange={() => onStepToggleDisabled(step)}
                    />
                    Disable
                  </label>
                  {onStepMoveOut && (
                    <button
                      type="button"
                      onClick={() => onStepMoveOut(step)}
                      className="text-gray-400 hover:text-[#0E8A96]"
                      aria-label="Move out of group"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => onStepDelete(step)}
                    className="text-gray-400 hover:text-red-500"
                    aria-label="Remove from group"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                  {step.type === 'APPLY_FORMULA' && (
                    <button
                      type="button"
                      onClick={() => onStepDoubleClick(step)}
                      className="text-gray-400 hover:text-[#0E8A96]"
                      aria-label="Edit"
                    >
                      <PencilLine className="h-3 w-3" />
                    </button>
                  )}
                </div>
              </div>
              <div className="text-gray-500 mt-0.5">{formatTime(step.createdAt)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default function PatternAgentPanel({
  items,
  patterns,
  selectedPatternId,
  applySteps,
  applyError,
  applyFailedIndex,
  isApplying,
  exporting,
  applyJobStatus,
  applyJobProgress,
  applyJobError,
  onReorder,
  onUpdateStep,
  onDeleteStep,
  onHoverStep,
  onClearHover,
  onExportPattern,
  onSelectPattern,
  onDeletePattern,
  onApplyPattern,
  onRetryApply,
  disableApplyPattern = false,
  onMoveStepOutOfGroup,
  defaultCollapsed = false,
  onCollapseChange,
}: PatternAgentPanelProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const [activeTab, setActiveTab] = useState<'timeline' | 'patterns'>('timeline');
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [editingStepId, setEditingStepId] = useState<string | null>(null);
  const [editTarget, setEditTarget] = useState('');
  const [editFormula, setEditFormula] = useState('');
  const [editError, setEditError] = useState<string | null>(null);
  const [exportModalOpen, setExportModalOpen] = useState(false);
  const [exportName, setExportName] = useState('');
  const [exportError, setExportError] = useState<string | null>(null);
  const [groupToDelete, setGroupToDelete] = useState<OperationGroup | null>(null);

  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const selectedItems = useMemo(
    () => items.filter((item) => selectedIds.includes(item.id)),
    [items, selectedIds]
  );

  useEffect(() => {
    setSelectedIds((prev) => prev.filter((id) => items.some((item) => item.id === id)));
  }, [items]);

  const handleDragEnd = (event: { active: { id: string | number }; over: { id: string | number } | null }) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const activeId = String(active.id);
    const overId = String(over.id);
    const oldIndex = items.findIndex((item) => item.id === activeId);
    const newIndex = items.findIndex((item) => item.id === overId);
    if (oldIndex === -1 || newIndex === -1) return;
    onReorder(arrayMove(items, oldIndex, newIndex));
  };

  const handleMergeSelected = () => {
    if (selectedIds.length < 2) return;
    const indices = items
      .map((_, i) => i)
      .filter((i) => selectedIds.includes(items[i].id))
      .sort((a, b) => a - b);
    const stepsToMerge: PatternStep[] = [];
    for (const i of indices) {
      const item = items[i];
      if (isOperationGroup(item)) stepsToMerge.push(...item.items);
      else stepsToMerge.push(item as PatternStep);
    }
    const newGroup: OperationGroup = {
      id: typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `group_${Date.now()}`,
      type: 'GROUP',
      name: DEFAULT_GROUP_NAME,
      items: stepsToMerge,
      collapsed: false,
      createdAt: new Date().toISOString(),
    };
    const withoutSelected = items.filter((_, i) => !indices.includes(i));
    const insertAt = indices[0];
    withoutSelected.splice(insertAt, 0, newGroup);
    onReorder(withoutSelected);
    setSelectedIds([]);
  };

  const openEditModal = (step: PatternStep) => {
    if (step.type !== 'APPLY_FORMULA') return;
    setEditingStepId(step.id);
    setEditTarget(step.a1);
    setEditFormula(step.formula);
    setEditError(null);
  };

  const handleSaveEdit = () => {
    if (!editingStepId) return;
    const target = parseA1(editTarget);
    if (!target) {
      setEditError('Target must be a valid A1 cell reference');
      return;
    }
    if (!editFormula.trim().startsWith('=')) {
      setEditError('Formula must start with "="');
      return;
    }
    onUpdateStep(editingStepId, {
      target,
      a1: editTarget.toUpperCase(),
      formula: editFormula.trim(),
    });
    setEditingStepId(null);
  };

  const allSelected = items.length > 0 && selectedIds.length === items.length;
  const hasApplySteps = applySteps.length > 0;
  const hasJobStatus = applyJobStatus != null;

  const handleCollapseToggle = () => {
    const newCollapsed = !collapsed;
    setCollapsed(newCollapsed);
    onCollapseChange?.(newCollapsed);
  };

  return (
    <div
      className={`fixed bottom-0 right-0 top-12 z-30 flex flex-col border-l border-gray-200 bg-white shadow-2xl transition-all duration-300 ease-in-out sm:static sm:top-auto sm:z-auto sm:h-full sm:shadow-none ${
        collapsed ? 'w-14' : 'w-[min(360px,calc(100vw-3.5rem))] sm:w-80'
      }`}
    >
      <div
        className={`flex flex-shrink-0 items-center border-b border-gray-100 transition-all duration-300 ${
          collapsed ? 'flex-col justify-center py-3' : 'justify-between gap-2 p-3'
        }`}
      >
        {!collapsed && (
          <>
            <div className="text-sm font-semibold text-gray-900">AI Workflows</div>
            <div className="flex items-center gap-1.5 text-xs">
              <button
                type="button"
                onClick={() => setActiveTab('timeline')}
                className={`rounded px-2 py-1 ${
                  activeTab === 'timeline' ? 'bg-gradient-to-r from-[#3CCED7] to-[#A6E661] text-white' : 'bg-gray-100 text-gray-600'
                }`}
              >
                Timeline
              </button>
              <button
                type="button"
                onClick={() => setActiveTab('patterns')}
                className={`rounded px-2 py-1 ${
                  activeTab === 'patterns' ? 'bg-gradient-to-r from-[#3CCED7] to-[#A6E661] text-white' : 'bg-gray-100 text-gray-600'
                }`}
              >
                Patterns
              </button>
            </div>
          </>
        )}
        <button
          onClick={handleCollapseToggle}
          className="rounded-lg p-1.5 text-gray-600 transition-colors hover:bg-gray-100"
          aria-label={collapsed ? 'Expand panel' : 'Collapse panel'}
        >
          {collapsed ? (
            <ChevronLeft className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </button>
      </div>

      {!collapsed && (activeTab === 'timeline' ? (
        <div className="flex-1 overflow-y-auto p-4">
          {items.length === 0 ? (
            <div className="rounded-lg border border-dashed border-gray-200 bg-gray-50 p-4 text-center text-xs text-gray-500">
              No steps yet. Edits you make in the grid will be recorded here.
            </div>
          ) : (
            <>
              <div className="mb-3 flex items-center justify-between text-xs">
                <div className="text-gray-500">{selectedIds.length} selected</div>
                <div className="flex items-center gap-2 flex-wrap">
                  <button
                    type="button"
                    onClick={() => setSelectedIds(items.map((item) => item.id))}
                    className="rounded border border-gray-200 px-2 py-1 font-semibold text-gray-700 hover:bg-gray-50"
                    disabled={allSelected}
                  >
                    Select all
                  </button>
                  <button
                    type="button"
                    onClick={handleMergeSelected}
                    className="rounded border border-[#3CCED7]/40 bg-[#3CCED7]/10 px-2 py-1 font-semibold text-[#0E8A96] hover:bg-[#3CCED7]/20 disabled:opacity-50"
                    disabled={selectedIds.length < 2}
                    title="Merge selected into one group"
                  >
                    Merge
                  </button>
                  <button
                    type="button"
                    onClick={() => setSelectedIds([])}
                    className="rounded border border-gray-200 px-2 py-1 font-semibold text-gray-700 hover:bg-gray-50"
                    disabled={selectedIds.length === 0}
                  >
                    Clear
                  </button>
                </div>
              </div>
              <DndContext collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
                <SortableContext items={items.map((item) => item.id)} strategy={verticalListSortingStrategy}>
                  <div className="space-y-3">
                    {items.map((item) =>
                      isOperationGroup(item) ? (
                        <GroupCard
                          key={item.id}
                          group={item}
                          isSelected={selectedSet.has(item.id)}
                          onSelect={() => {
                            setSelectedIds((prev) =>
                              prev.includes(item.id) ? prev.filter((id) => id !== item.id) : [...prev, item.id]
                            );
                          }}
                          onToggleCollapsed={() =>
                            onUpdateStep(item.id, { collapsed: !item.collapsed })
                          }
                          onRename={(name) => onUpdateStep(item.id, { name })}
                          onDelete={() => setGroupToDelete(item)}
                          onStepHover={onHoverStep}
                          onStepLeave={onClearHover}
                          onStepToggleDisabled={(step) =>
                            onUpdateStep(step.id, { disabled: !step.disabled })
                          }
                          onStepDelete={(step) => {
                            const next = item.items.filter((s) => s.id !== step.id);
                            if (next.length === 0) {
                              onDeleteStep(item.id);
                            } else {
                              onUpdateStep(item.id, { items: next });
                            }
                          }}
                          onStepMoveOut={onMoveStepOutOfGroup ? (step) => onMoveStepOutOfGroup(item.id, step) : undefined}
                          onStepDoubleClick={(step) => openEditModal(step)}
                        />
                      ) : (
                        <StepCard
                          key={item.id}
                          step={item}
                          isSelected={selectedSet.has(item.id)}
                          onSelect={() => {
                            setSelectedIds((prev) =>
                              prev.includes(item.id) ? prev.filter((id) => id !== item.id) : [...prev, item.id]
                            );
                          }}
                          onToggleDisabled={() =>
                            onUpdateStep(item.id, { disabled: !item.disabled })
                          }
                          onDelete={() => onDeleteStep(item.id)}
                          onDoubleClick={() => openEditModal(item)}
                          onHover={() => onHoverStep(item)}
                          onLeave={onClearHover}
                        />
                      )
                    )}
                  </div>
                </SortableContext>
              </DndContext>
            </>
          )}
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto p-4">
          {patterns.length === 0 ? (
            <div className="rounded-lg border border-dashed border-gray-200 bg-gray-50 p-6 text-center text-xs text-gray-500">
              No saved patterns yet.
            </div>
          ) : (
            <div className="space-y-3">
              <div className="space-y-2">
                {patterns.map((pattern) => (
                  <div
                    key={pattern.id}
                    className={`flex w-full items-start justify-between rounded border px-3 py-2 text-left text-xs ${
                      selectedPatternId === pattern.id
                        ? 'border-[#3CCED7] bg-[#3CCED7]/10 text-[#0E8A96]'
                        : 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50'
                    }`}
                  >
                    <button type="button" onClick={() => onSelectPattern(pattern.id)} className="flex-1">
                      <div className="font-semibold text-gray-900">{pattern.name}</div>
                      <div className="text-gray-500">{formatTime(pattern.createdAt)}</div>
                    </button>
                    <button
                      type="button"
                      onClick={() => onDeletePattern(pattern.id)}
                      className="ml-2 text-gray-400 hover:text-red-500"
                      aria-label="Delete pattern"
                    >
                      <Trash className="h-4 w-4" />
                    </button>
                  </div>
                ))}
              </div>

              {hasApplySteps && (
                <div className="rounded-lg border border-gray-200 bg-white p-3">
                  <div className="mb-3 flex items-center gap-2">
                    <button
                      type="button"
                      onClick={onApplyPattern}
                      disabled={isApplying || disableApplyPattern}
                      title={disableApplyPattern ? 'Preparing sheet...' : undefined}
                      className="rounded bg-gradient-to-r from-[#3CCED7] to-[#A6E661] px-3 py-1 text-xs font-semibold text-white shadow-sm transition hover:opacity-95 disabled:opacity-60"
                    >
                      {isApplying ? 'Applying...' : 'Apply'}
                    </button>
                    {applyFailedIndex != null && (
                      <button
                        type="button"
                        onClick={onRetryApply}
                        disabled={isApplying || disableApplyPattern}
                        className="rounded border border-gray-200 px-3 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-60"
                      >
                        Retry
                      </button>
                    )}
                  </div>

                  {applyError && (
                    <div className="mb-3 rounded border border-red-200 bg-red-50 p-2 text-xs text-red-700">
                      Stopped on step {applyFailedIndex != null ? applyFailedIndex + 1 : ''}: {applyError}
                    </div>
                  )}

                  {hasJobStatus && (
                    <div className="mb-3 rounded border border-gray-200 bg-gray-50 p-2 text-xs text-gray-700">
                      <div className="flex items-center justify-between">
                        <span className="font-semibold">Status: {applyJobStatus}</span>
                        <span>{applyJobProgress}%</span>
                      </div>
                      <div className="mt-2 h-1.5 w-full overflow-hidden rounded bg-gray-200">
                        <div
                          className="h-full bg-gradient-to-r from-[#3CCED7] to-[#A6E661] transition-all"
                          style={{ width: `${applyJobProgress}%` }}
                        />
                      </div>
                      {applyJobError && (
                        <div className="mt-2 text-xs text-red-700">{applyJobError}</div>
                      )}
                    </div>
                  )}

                  <div className="space-y-2">
                    {applySteps.map((step, index) => {
                      let preview = '';
                      if (step.type === 'APPLY_FORMULA') {
                        const target = step.params?.target;
                        const label =
                          target && target.row != null && target.col != null
                            ? `${columnIndexToLabel(target.col)}${target.row}`
                            : 'Cell';
                        const formula = step.params?.formula ?? '';
                        preview = `${label} = ${formula.startsWith('=') ? formula.slice(1) : formula}`;
                      } else if (step.type === 'INSERT_ROW') {
                        preview = `Insert row ${step.params?.position} ${step.params?.index}`;
                      } else if (step.type === 'INSERT_COLUMN') {
                        const label = columnIndexToLabel(step.params?.index ?? 1);
                        preview = `Insert column ${step.params?.position} of ${label}`;
                      } else if (step.type === 'DELETE_COLUMN') {
                        const label = columnIndexToLabel(step.params?.index ?? 1);
                        preview = `Delete column ${label}`;
                      } else if (step.type === 'FILL_SERIES') {
                        const source = step.params?.source;
                        const range = step.params?.range;
                        const sourceLabel =
                          source && source.row != null && source.col != null
                            ? `${columnIndexToLabel(source.col)}${source.row}`
                            : 'Cell';
                        const startLabel =
                          range && range.start_row != null && range.start_col != null
                            ? `${columnIndexToLabel(range.start_col)}${range.start_row}`
                            : 'Start';
                        const endLabel =
                          range && range.end_row != null && range.end_col != null
                            ? `${columnIndexToLabel(range.end_col)}${range.end_row}`
                            : 'End';
                        preview = `Fill ${sourceLabel} → ${startLabel}:${endLabel}`;
                      } else if (step.type === 'SET_COLUMN_NAME') {
                        const columnIndex = step.params?.column_ref?.index ?? 1;
                        const label = columnIndexToLabel(columnIndex);
                        const name = step.params?.to_header ?? '';
                        preview = `Rename column ${label} to ${name}`;
                      } else if (step.type === 'APPLY_HIGHLIGHT') {
                        const color = step.params?.color ?? '';
                        const scope = step.params?.scope?.toLowerCase?.() ?? 'range';
                        const header = step.params?.target?.by_header;
                        if (step.params?.scope === 'COLUMN' && header) {
                          preview = `Highlight column '${header}' (${color})`;
                        } else {
                          const fallback = step.params?.target?.fallback;
                          const startRow = fallback?.start_row ?? fallback?.row_index ?? 1;
                          const endRow = fallback?.end_row ?? startRow;
                          const startCol = fallback?.start_col ?? fallback?.col_index ?? 1;
                          const endCol = fallback?.end_col ?? startCol;
                          const startLabel = `${columnIndexToLabel(startCol)}${startRow}`;
                          const endLabel = `${columnIndexToLabel(endCol)}${endRow}`;
                          preview = `Highlight ${scope} ${startLabel}:${endLabel} (${color})`;
                        }
                      } else if (step.type === 'GROUP') {
                        const name = step.params?.name ?? DEFAULT_GROUP_NAME;
                        const count = Array.isArray(step.params?.items) ? step.params.items.length : 0;
                        preview = `${name} (${count} operations)`;
                      } else {
                        preview = step.type;
                      }

                      const statusClass =
                        step.status === 'success'
                          ? 'border-green-200 bg-green-50'
                          : step.status === 'error'
                            ? 'border-red-200 bg-red-50'
                            : 'border-gray-200 bg-white';

                      return (
                        <div key={step.id ?? index} className={`rounded border p-2 text-xs ${statusClass}`}>
                          <div className="font-semibold text-gray-900">{preview}</div>
                          {step.status === 'error' && step.errorMessage && (
                            <div className="mt-1 text-xs text-red-700">{step.errorMessage}</div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      ))}

      {!collapsed && activeTab === 'timeline' && (
        <div className="border-t border-gray-100 p-4">
          <button
            type="button"
            disabled={selectedItems.length === 0 || exporting}
            onClick={() => {
              setExportName('');
              setExportError(null);
              setExportModalOpen(true);
            }}
            className="w-full rounded bg-gradient-to-r from-[#3CCED7] to-[#A6E661] px-3 py-2 text-xs font-semibold text-white shadow-sm transition hover:opacity-95 disabled:opacity-60"
          >
            Export Pattern
          </button>
        </div>
      )}

      {editingStepId && (
        <Modal isOpen={true} onClose={() => setEditingStepId(null)}>
          <div className="w-[min(420px,calc(100vw-2rem))]">
            <div className="rounded-2xl bg-white shadow-2xl ring-1 ring-gray-100">
              <div className="border-b border-gray-100 px-6 pb-4 pt-6">
                <h2 className="text-lg font-semibold text-gray-900">Edit Formula Step</h2>
                <p className="text-xs text-gray-500">Update the target cell and formula.</p>
              </div>
              <div className="space-y-4 p-6">
                <div>
                  <label className="text-xs font-semibold text-gray-700">Target cell</label>
                  <input
                    value={editTarget}
                    onChange={(e) => setEditTarget(e.target.value)}
                    className="mt-2 w-full rounded border border-gray-200 px-3 py-2 text-sm"
                    placeholder="A1"
                  />
                </div>
                <div>
                  <label className="text-xs font-semibold text-gray-700">Formula</label>
                  <input
                    value={editFormula}
                    onChange={(e) => setEditFormula(e.target.value)}
                    className="mt-2 w-full rounded border border-gray-200 px-3 py-2 text-sm"
                    placeholder="=SUM(A1:A10)"
                  />
                </div>
                {editError && <div className="text-xs text-red-600">{editError}</div>}
              </div>
              <div className="flex items-center justify-end gap-2 border-t border-gray-100 p-4">
                <button
                  type="button"
                  onClick={() => setEditingStepId(null)}
                  className="rounded border border-gray-200 px-3 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleSaveEdit}
                  className="rounded bg-gradient-to-r from-[#3CCED7] to-[#A6E661] px-3 py-1 text-xs font-semibold text-white shadow-sm transition hover:opacity-95"
                >
                  Save
                </button>
              </div>
            </div>
          </div>
        </Modal>
      )}

      {groupToDelete && (
        <Modal isOpen={true} onClose={() => setGroupToDelete(null)}>
          <div className="w-[min(420px,calc(100vw-2rem))]">
            <div className="rounded-2xl bg-white shadow-2xl ring-1 ring-gray-100">
              <div className="border-b border-gray-100 px-6 pb-4 pt-6">
                <h2 className="text-lg font-semibold text-gray-900">Delete group</h2>
                <p className="text-sm text-gray-600 mt-1">
                  Delete &quot;{groupToDelete.name}&quot; and all {groupToDelete.items.length} operations inside it?
                  This cannot be undone.
                </p>
              </div>
              <div className="flex items-center justify-end gap-2 border-t border-gray-100 p-4">
                <button
                  type="button"
                  onClick={() => setGroupToDelete(null)}
                  className="rounded border border-gray-200 px-3 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => {
                    onDeleteStep(groupToDelete.id);
                    setGroupToDelete(null);
                  }}
                  className="rounded bg-red-600 px-3 py-1 text-xs font-semibold text-white hover:bg-red-700"
                >
                  Delete
                </button>
              </div>
            </div>
          </div>
        </Modal>
      )}

      {exportModalOpen && (
        <Modal isOpen={true} onClose={() => setExportModalOpen(false)}>
          <div className="w-[min(420px,calc(100vw-2rem))]">
            <div className="rounded-2xl bg-white shadow-2xl ring-1 ring-gray-100">
              <div className="border-b border-gray-100 px-6 pb-4 pt-6">
                <h2 className="text-lg font-semibold text-gray-900">Export Pattern</h2>
                <p className="text-xs text-gray-500">Save selected steps as a reusable pattern.</p>
              </div>
              <div className="space-y-4 p-6">
                <div>
                  <label className="text-xs font-semibold text-gray-700">Pattern name</label>
                  <input
                    value={exportName}
                    onChange={(e) => setExportName(e.target.value)}
                    className="mt-2 w-full rounded border border-gray-200 px-3 py-2 text-sm"
                    placeholder="My Pattern"
                  />
                </div>
                {exportError && <div className="text-xs text-red-600">{exportError}</div>}
              </div>
              <div className="flex items-center justify-end gap-2 border-t border-gray-100 p-4">
                <button
                  type="button"
                  onClick={() => setExportModalOpen(false)}
                  className="rounded border border-gray-200 px-3 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-50"
                  disabled={exporting}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={async () => {
                    if (!exportName.trim()) {
                      setExportError('Pattern name is required');
                      return;
                    }
                    setExportError(null);
                    const success = await onExportPattern(exportName.trim(), selectedItems);
                    if (success) {
                      setExportModalOpen(false);
                    }
                  }}
                  className="rounded bg-gradient-to-r from-[#3CCED7] to-[#A6E661] px-3 py-1 text-xs font-semibold text-white shadow-sm transition hover:opacity-95 disabled:opacity-60"
                  disabled={exporting}
                >
                  {exporting ? 'Saving...' : 'Save'}
                </button>
              </div>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
