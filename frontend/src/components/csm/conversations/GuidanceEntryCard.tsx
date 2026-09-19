'use client';

import React from 'react';
import { ChevronDown, ChevronRight, CornerDownLeft } from 'lucide-react';
import type { GuidanceType, WorkspaceGuidanceEntry } from '@/types/csmGuidance';

const TYPE_BADGE_CLASS: Record<GuidanceType, string> = {
  handoff: 'bg-purple-50 text-purple-700',
  suggested_reply: 'bg-blue-50 text-blue-700',
  escalation_procedure: 'bg-red-50 text-red-700',
  process_note: 'bg-gray-100 text-gray-700',
};

interface GuidanceEntryCardProps {
  entry: WorkspaceGuidanceEntry;
  expanded: boolean;
  canInsert: boolean;
  onToggle: () => void;
  onInsert: () => void;
}

export function GuidanceEntryCard({
  entry,
  expanded,
  canInsert,
  onToggle,
  onInsert,
}: GuidanceEntryCardProps) {
  const panelId = `guidance-entry-${entry.id}`;
  return (
    <li className="rounded-md border border-gray-200 bg-white">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        aria-controls={panelId}
        className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-gray-50"
      >
        {expanded ? (
          <ChevronDown className="mt-0.5 h-3.5 w-3.5 shrink-0 text-gray-400" />
        ) : (
          <ChevronRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-gray-400" />
        )}
        <span className="min-w-0 flex-1">
          <span
            className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium ${TYPE_BADGE_CLASS[entry.guidance_type]}`}
          >
            {entry.guidance_type_display}
          </span>
          <span className={`mt-1 block text-xs text-gray-700 ${expanded ? '' : 'line-clamp-2'}`}>
            {entry.trigger_description}
          </span>
        </span>
      </button>
      {expanded && (
        <div id={panelId} className="border-t border-gray-100 px-3 py-2">
          <p className="text-[11px] font-medium uppercase tracking-wide text-gray-400">
            Recommended response
          </p>
          <p className="mt-1 whitespace-pre-wrap break-words text-xs text-gray-800">
            {entry.recommended_response}
          </p>
          <button
            type="button"
            onClick={onInsert}
            disabled={!canInsert}
            title={canInsert ? undefined : 'Claim this conversation to reply'}
            className="mt-2 inline-flex items-center gap-1 rounded-md bg-blue-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-300"
          >
            <CornerDownLeft className="h-3 w-3" />
            Insert into reply
          </button>
          {!canInsert && (
            <p className="mt-1 text-[11px] text-gray-400">Claim this conversation to reply.</p>
          )}
        </div>
      )}
    </li>
  );
}
