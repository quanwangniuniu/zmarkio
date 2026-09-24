'use client';

import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import { SortableContext, arrayMove, verticalListSortingStrategy } from '@dnd-kit/sortable';
import type { RoutingRule } from '@/types/routingRule';
import type { SummaryLookups } from './conditionSummary';
import RoutingRuleRow from './RoutingRuleRow';

interface Props {
  rules: RoutingRule[];
  lookups: SummaryLookups;
  onReorder: (rules: RoutingRule[]) => void;
  onEdit: (rule: RoutingRule) => void;
  onToggle: (rule: RoutingRule) => void;
  onDelete: (rule: RoutingRule) => void;
}

export default function RoutingRulesList({ rules, lookups, onReorder, onEdit, onToggle, onDelete }: Props) {
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }));

  const handleDragEnd = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id) return;
    const oldIndex = rules.findIndex((r) => r.id === active.id);
    const newIndex = rules.findIndex((r) => r.id === over.id);
    if (oldIndex < 0 || newIndex < 0) return;
    onReorder(arrayMove(rules, oldIndex, newIndex));
  };

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <SortableContext items={rules.map((r) => r.id)} strategy={verticalListSortingStrategy}>
        <div className="flex flex-col gap-2">
          {rules.map((rule, index) => (
            <RoutingRuleRow
              key={rule.id}
              rule={rule}
              index={index}
              lookups={lookups}
              onEdit={onEdit}
              onToggle={onToggle}
              onDelete={onDelete}
            />
          ))}
        </div>
      </SortableContext>
    </DndContext>
  );
}
