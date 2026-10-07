'use client';

import { useState } from 'react';
import toast from 'react-hot-toast';
import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  SortableContext,
  arrayMove,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import CsmGuidanceAPI, { isGuidanceConflict } from '@/lib/api/csmGuidanceApi';
import type { GuidanceEntry } from '@/types/csmGuidance';
import GuidanceSortableRow from './GuidanceSortableRow';

interface Props {
  projectId: number;
  /** The group whose order is shown; null lists entries without a reorderable order. */
  experienceGroupId: number | null;
  canManage: boolean;
  items: GuidanceEntry[];
  onChange: (items: GuidanceEntry[]) => void;
  /** Called when the server rejects an order (e.g. the list was stale). */
  onReorderFailed: () => void;
  onEdit: (row: GuidanceEntry) => void;
  onDelete: (row: GuidanceEntry) => void;
}

export default function GuidanceSortableList({
  projectId,
  experienceGroupId,
  canManage,
  items,
  onChange,
  onReorderFailed,
  onEdit,
  onDelete,
}: Props) {
  const [savingOrder, setSavingOrder] = useState(false);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }));
  const sortable = experienceGroupId != null;

  const handleDragEnd = async (event: DragEndEvent) => {
    const { active, over } = event;
    if (!sortable || !over || active.id === over.id || savingOrder) return;

    const oldIndex = items.findIndex((i) => i.id === active.id);
    const newIndex = items.findIndex((i) => i.id === over.id);
    if (oldIndex < 0 || newIndex < 0) return;

    const previous = items;
    const reordered = arrayMove(items, oldIndex, newIndex);
    onChange(reordered);

    setSavingOrder(true);
    try {
      await CsmGuidanceAPI.reorder(
        projectId,
        experienceGroupId!,
        reordered.map((r) => r.id),
        previous.map((r) => r.id),
      );
      toast.success('Guidance order saved.');
    } catch (err) {
      onChange(previous);
      toast.error(
        isGuidanceConflict(err)
          ? 'Someone else changed this list. It has been refreshed; please try again.'
          : 'Could not save order. The list has been refreshed.',
      );
      onReorderFailed();
    } finally {
      setSavingOrder(false);
    }
  };

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <SortableContext items={items.map((i) => i.id)} strategy={verticalListSortingStrategy}>
        <div className="flex flex-col gap-2">
          {items.map((row) => (
            <GuidanceSortableRow
              key={row.id}
              row={row}
              canManage={canManage}
              sortable={sortable}
              onEdit={onEdit}
              onDelete={onDelete}
            />
          ))}
        </div>
      </SortableContext>
    </DndContext>
  );
}
