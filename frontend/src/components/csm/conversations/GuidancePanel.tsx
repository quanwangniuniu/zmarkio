'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import CsmGuidanceAPI from '@/lib/api/csmGuidanceApi';
import { useCsmConversationStore } from '@/lib/csmConversationStore';
import type { ConversationGuidance } from '@/types/csmGuidance';
import { GuidanceEntryCard } from './GuidanceEntryCard';

interface GuidancePanelProps {
  conversationId: number;
  /** False while the conversation is unclaimed: no composer is mounted then. */
  canInsert: boolean;
}

/**
 * Guidance for the open conversation's matched Experience Group, in the
 * admin-configured order. Refetches silently when the socket reports that the
 * group's guidance changed (reorder, edit, delete).
 */
export function GuidancePanel({ conversationId, canInsert }: GuidancePanelProps) {
  const [guidance, setGuidance] = useState<ConversationGuidance | null>(null);
  const [error, setError] = useState(false);
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const requestRef = useRef(0);
  const seenVersionRef = useRef(0);

  const groupId = guidance?.experience_group?.id ?? null;
  const groupVersion = useCsmConversationStore((s) =>
    groupId != null ? s.guidanceVersionByGroup[groupId] ?? 0 : 0
  );
  const requestComposerInsert = useCsmConversationStore((s) => s.requestComposerInsert);

  const load = useCallback((id: number) => {
    const requestId = ++requestRef.current;
    // Versions as of the request: an update landing mid-flight still triggers a refetch.
    const versionsAtRequest = useCsmConversationStore.getState().guidanceVersionByGroup;
    CsmGuidanceAPI.forConversation(id)
      .then((data) => {
        // Ignore responses for a conversation the agent already left.
        if (requestId !== requestRef.current) return;
        const egId = data.experience_group?.id;
        seenVersionRef.current = egId != null ? versionsAtRequest[egId] ?? 0 : 0;
        setGuidance(data);
        setError(false);
      })
      .catch(() => {
        if (requestId !== requestRef.current) return;
        setError(true);
      });
  }, []);

  useEffect(() => {
    setGuidance(null);
    setError(false);
    setExpandedIds(new Set());
    load(conversationId);
  }, [conversationId, load]);

  // Live update: the socket bumped this group's version.
  useEffect(() => {
    if (groupId == null || groupVersion === seenVersionRef.current) return;
    load(conversationId);
  }, [groupId, groupVersion, conversationId, load]);

  const toggle = (entryId: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(entryId)) next.delete(entryId);
      else next.add(entryId);
      return next;
    });
  };

  if (error && !guidance) {
    return (
      <div className="px-4 py-6 text-center text-xs text-red-500">
        Guidance could not be loaded.{' '}
        <button type="button" onClick={() => load(conversationId)} className="underline">
          Retry
        </button>
      </div>
    );
  }

  if (!guidance) {
    return <div className="px-4 py-6 text-center text-xs text-gray-400">Loading guidance…</div>;
  }

  if (!guidance.experience_group) {
    return (
      <div className="px-4 py-6 text-center text-xs text-gray-400">
        This customer is not in an Experience Group, so no guidance applies.
      </div>
    );
  }

  return (
    <div className="px-3 py-3">
      <p className="mb-2 px-1 text-[11px] text-gray-400">
        For <span className="font-medium text-gray-600">{guidance.experience_group.name}</span>
      </p>
      {guidance.entries.length === 0 ? (
        <p className="px-1 py-4 text-center text-xs text-gray-400">
          No guidance has been configured for this Experience Group.
        </p>
      ) : (
        <ul className="space-y-2" aria-label="Guidance entries">
          {guidance.entries.map((entry) => (
            <GuidanceEntryCard
              key={entry.id}
              entry={entry}
              expanded={expandedIds.has(entry.id)}
              canInsert={canInsert}
              onToggle={() => toggle(entry.id)}
              onInsert={() => requestComposerInsert(conversationId, entry.recommended_response)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
