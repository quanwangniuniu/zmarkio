'use client';

import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { GripVertical, Pencil, Trash2 } from 'lucide-react';
import type { GuidanceEntry } from '@/types/csmGuidance';

interface Props {
  row: GuidanceEntry;
  canManage: boolean;
  /** Drag handles only make sense when a single group's order is shown. */
  sortable: boolean;
  onEdit: (row: GuidanceEntry) => void;
  onDelete: (row: GuidanceEntry) => void;
}

export default function GuidanceSortableRow({ row, canManage, sortable, onEdit, onDelete }: Props) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: row.id,
    disabled: !sortable || !canManage,
  });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`flex items-start gap-3 rounded-lg border border-gray-200 bg-white p-4 max-sm:flex-col ${
        isDragging ? 'opacity-60' : ''
      }`}
    >
      {sortable && canManage && (
        <button
          type="button"
          className="cursor-grab p-2 text-gray-400 hover:text-gray-600 active:cursor-grabbing"
          aria-label="Drag to reorder"
          {...attributes}
          {...listeners}
        >
          <GripVertical className="h-4 w-4" aria-hidden />
        </button>
      )}

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-medium text-indigo-700">
            {row.guidance_type_display}
          </span>
          {row.experience_groups.map((g) => (
            <span key={g.id} className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600">
              {g.name}
            </span>
          ))}
        </div>
        <p className="mt-2 text-sm font-medium text-gray-900">{row.trigger_description}</p>
        <p className="mt-1 line-clamp-2 whitespace-pre-wrap text-sm text-gray-500">
          {row.recommended_response}
        </p>
      </div>

      {canManage && (
        <div className="flex items-center gap-2 max-sm:w-full max-sm:justify-end">
          <button
            type="button"
            onClick={() => onEdit(row)}
            title="Edit"
            aria-label="Edit guidance"
            className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-indigo-50 hover:text-indigo-600"
          >
            <Pencil className="h-4 w-4" aria-hidden />
          </button>
          <button
            type="button"
            onClick={() => onDelete(row)}
            title="Delete"
            aria-label="Delete guidance"
            className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-red-50 hover:text-red-600"
          >
            <Trash2 className="h-4 w-4" aria-hidden />
          </button>
        </div>
      )}
    </div>
  );
}
