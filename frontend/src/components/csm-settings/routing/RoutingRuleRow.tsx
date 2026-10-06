'use client';

import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { AlertTriangle, GripVertical, Pencil, Trash2 } from 'lucide-react';
import type { RoutingRule } from '@/types/routingRule';
import { summariseCondition, type SummaryLookups } from './conditionSummary';

interface Props {
  rule: RoutingRule;
  index: number;
  lookups: SummaryLookups;
  onEdit: (rule: RoutingRule) => void;
  onToggle: (rule: RoutingRule) => void;
  onDelete: (rule: RoutingRule) => void;
}

export default function RoutingRuleRow({ rule, index, lookups, onEdit, onToggle, onDelete }: Props) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: rule.id,
  });
  const joiner = rule.match_mode === 'any' ? 'or' : 'and';

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`flex items-start gap-3 rounded-lg border bg-white p-4 ${
        rule.can_route ? 'border-gray-200' : 'border-red-300'
      } ${
        isDragging ? 'opacity-60' : ''
      } ${rule.is_enabled ? '' : 'bg-gray-50'}`}
      data-testid="routing-rule-row"
    >
      <button
        type="button"
        className="cursor-grab p-1.5 text-gray-400 hover:text-gray-600 active:cursor-grabbing"
        aria-label={`Drag to reorder ${rule.name}`}
        {...attributes}
        {...listeners}
      >
        <GripVertical className="h-4 w-4" aria-hidden />
      </button>

      <span className="mt-1 w-6 shrink-0 text-xs font-semibold text-gray-400">{index + 1}</span>

      <div className="min-w-0 flex-1">
        <p className={`flex items-center gap-2 text-sm font-medium ${rule.is_enabled ? 'text-gray-900' : 'text-gray-500'}`}>
          {rule.name}
          {!rule.can_route && (
            <span className="rounded-full bg-red-100 px-2 py-0.5 text-[11px] font-medium text-red-700">
              Can&apos;t route
            </span>
          )}
        </p>
        <p className="mt-1 text-xs text-gray-600">
          {rule.conditions.length === 0
            ? 'Always matches'
            : rule.conditions.map((c) => summariseCondition(c, lookups)).join(` ${joiner} `)}
        </p>
        <p className="mt-1 text-xs text-gray-600">
          → Route to{' '}
          <span className="font-medium text-gray-800">{rule.target_queue_name ?? 'no queue'}</span>
          {rule.add_tags.length > 0 && <> · tag {rule.add_tags.join(', ')}</>}
        </p>
        {!rule.can_route && (
          <p className="mt-1 inline-flex items-center gap-1 text-xs text-red-700">
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
            {rule.target_queue === null ? 'Its queue was deleted' : 'Its queue is inactive'}, so this rule is
            skipped. Edit it and choose an active queue.
          </p>
        )}
      </div>

      <label className="mt-0.5 flex shrink-0 items-center gap-1.5 text-xs text-gray-600">
        <input
          type="checkbox"
          checked={rule.is_enabled}
          onChange={() => onToggle(rule)}
          aria-label={`${rule.is_enabled ? 'Disable' : 'Enable'} ${rule.name}`}
        />
        Enabled
      </label>

      <div className="flex shrink-0 items-center gap-1">
        <button
          type="button"
          onClick={() => onEdit(rule)}
          aria-label={`Edit ${rule.name}`}
          className="rounded-md p-1.5 text-gray-400 hover:bg-indigo-50 hover:text-indigo-600"
        >
          <Pencil className="h-4 w-4" aria-hidden />
        </button>
        <button
          type="button"
          onClick={() => onDelete(rule)}
          aria-label={`Delete ${rule.name}`}
          className="rounded-md p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600"
        >
          <Trash2 className="h-4 w-4" aria-hidden />
        </button>
      </div>
    </div>
  );
}
