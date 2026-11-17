'use client';

import React, { useState, useEffect } from 'react';
import toast from 'react-hot-toast';
import { Conversation, ConversationStatus, UpdateConversationPayload, AssignableAgent } from '@/types/csmConversation';
import { TicketPriority, PRIORITY_LABELS } from '@/types/csm';
import { CSM_PRIORITY } from '@/lib/csm/csmConfig';
import { visibleOptions } from '@/lib/orgCustomization/optionConfig';
import CsmConversationAPI from '@/lib/api/csmConversationApi';
import { TicketAPI } from '@/lib/api/csmConversationApi';
import { useCsmConversationStore } from '@/lib/csmConversationStore';

const STATUS_OPTIONS: { value: ConversationStatus; label: string }[] = [
  { value: 'active',   label: 'Active' },
  { value: 'pending',  label: 'Pending' },
  { value: 'resolved', label: 'Resolved' },
  { value: 'closed',   label: 'Closed' },
];

const STATUS_COLORS: Record<ConversationStatus, string> = {
  active:   'bg-green-100 text-green-700',
  pending:  'bg-yellow-100 text-yellow-700',
  resolved: 'bg-blue-100 text-blue-700',
  closed:   'bg-gray-100 text-gray-500',
};

const PRIORITY_SELECT_COLORS: Record<string, string> = {
  critical: 'bg-red-50 text-red-700',
  high:     'bg-orange-50 text-orange-700',
  medium:   'bg-blue-50 text-blue-700',
  low:      'bg-gray-100 text-gray-500',
};

const PRIORITY_DOT: Record<string, string> = {
  critical: 'bg-red-500',
  high:     'bg-orange-400',
  medium:   'bg-blue-400',
  low:      'bg-gray-400',
};

interface Queue {
  id: number;
  name: string;
}

interface ConversationActionsProps {
  conversation: Conversation;
  onPriorityChanged?: (newPriority: string) => void;
}

export function ConversationActions({ conversation, onPriorityChanged }: ConversationActionsProps) {
  const [tagInput, setTagInput] = useState('');
  const [queues, setQueues] = useState<Queue[]>([]);
  const [queuesLoading, setQueuesLoading] = useState(true);
  const [queueLoadError, setQueueLoadError] = useState(false);
  const [agents, setAgents] = useState<AssignableAgent[]>([]);
  const [agentsLoadError, setAgentsLoadError] = useState(false);
  const [ticketPriority, setTicketPriority] = useState<string>(
    conversation.ticket?.priority ?? 'medium'
  );
  const [updatingPriority, setUpdatingPriority] = useState(false);

  const updateConversation = useCsmConversationStore((s) => s.updateConversation);

  // Sync priority if the active conversation changes
  useEffect(() => {
    if (conversation.ticket?.priority) {
      setTicketPriority(conversation.ticket.priority);
    }
  }, [conversation.ticket?.priority, conversation.id]);

  // Fetch available queues once per organisation
  useEffect(() => {
    setQueuesLoading(true);
    CsmConversationAPI.availableQueues(
      conversation.queue_organisation_id ? { organisation: conversation.queue_organisation_id } : undefined
    )
      .then((data) => {
        setQueues(data);
        setQueueLoadError(false);
      })
      .catch(() => {
        setQueues([]);
        setQueueLoadError(true);
      })
      .finally(() => setQueuesLoading(false));
  }, [conversation.queue_organisation_id]);

  // Fetch agents this conversation can be reassigned to (its queue's organisation)
  useEffect(() => {
    CsmConversationAPI.assignableAgents(conversation.id)
      .then((data) => {
        setAgents(data);
        setAgentsLoadError(false);
      })
      .catch(() => {
        setAgents([]);
        setAgentsLoadError(true);
      });
  }, [conversation.id]);

  const patch = async (data: UpdateConversationPayload) => {
    try {
      const updated = await CsmConversationAPI.update(conversation.id, data);
      updateConversation(updated);
    } catch {
      toast.error('Failed to update conversation.');
    }
  };

  const handleStatusChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    patch({ status: e.target.value as ConversationStatus });
  };

  const handleQueueChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const val = e.target.value;
    patch({ queue: val ? Number(val) : null });
  };

  const handleAssignChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const val = e.target.value;
    patch({ assigned_to: val ? Number(val) : null });
  };

  const handlePriorityChange = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    const newPriority = e.target.value as TicketPriority;
    if (!conversation.ticket || newPriority === ticketPriority) return;

    setUpdatingPriority(true);
    const prev = ticketPriority;
    setTicketPriority(newPriority);
    try {
      await TicketAPI.update(conversation.ticket.id, { priority: newPriority });
      // Reflect new priority in the store so the conversation list & right panel update
      updateConversation({
        ...conversation,
        ticket: { ...conversation.ticket, priority: newPriority },
      });
      toast.success(`Priority set to ${PRIORITY_LABELS[newPriority]}`);
      onPriorityChanged?.(newPriority);
    } catch {
      setTicketPriority(prev);
      toast.error('Failed to update priority.');
    } finally {
      setUpdatingPriority(false);
    }
  };

  const priorityColor = CSM_PRIORITY.colors[ticketPriority];

  const handleAddTag = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && tagInput.trim()) {
      patch({ tags: [...(conversation.tags ?? []), tagInput.trim()] });
      setTagInput('');
    }
  };

  const handleRemoveTag = (tag: string) => {
    patch({ tags: conversation.tags.filter((t) => t !== tag) });
  };

  return (
    <div className="flex items-center gap-2 px-4 py-2 border-b border-gray-100 bg-white flex-wrap">
      {/* Customer name */}
      <span className="font-semibold text-sm text-gray-900 truncate max-w-[160px]">
        {conversation.customer_name ?? conversation.customer_email ?? 'Unknown'}
      </span>

      {/* Ticket title */}
      {conversation.ticket && (
        <span className="text-xs text-gray-400 truncate max-w-[180px]">
          #{conversation.ticket.id} {conversation.ticket.title}
        </span>
      )}

      {/* Divider */}
      <span className="h-4 w-px bg-gray-200 shrink-0" />

      {/* Conversation status selector */}
      <select
        value={conversation.status}
        onChange={handleStatusChange}
        className={`text-xs px-2.5 py-1 rounded-md border-0 font-medium cursor-pointer outline-none ${STATUS_COLORS[conversation.status]}`}
      >
        {STATUS_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ))}
      </select>

      {/* Ticket priority selector */}
      {conversation.ticket && (
        <div className="relative flex items-center">
          <span
            className={`absolute left-2 w-1.5 h-1.5 rounded-full pointer-events-none z-10 ${
              priorityColor ? '' : PRIORITY_DOT[ticketPriority] ?? 'bg-gray-400'
            }`}
            style={priorityColor ? { backgroundColor: priorityColor } : undefined}
          />
          <select
            value={ticketPriority}
            onChange={handlePriorityChange}
            disabled={updatingPriority}
            className={`text-xs pl-5 pr-2.5 py-1 rounded-md border-0 font-medium cursor-pointer outline-none appearance-none disabled:opacity-60 disabled:cursor-wait ${
              PRIORITY_SELECT_COLORS[ticketPriority] ?? 'bg-gray-100 text-gray-500'
            }`}
          >
            {visibleOptions(CSM_PRIORITY, ticketPriority).map((value) => (
              <option key={value} value={value}>{PRIORITY_LABELS[value as TicketPriority]}</option>
            ))}
          </select>
        </div>
      )}

      {/* Queue selector */}
      <div className="flex items-center gap-1.5">
        <select
          value={conversation.queue ?? ''}
          onChange={handleQueueChange}
          disabled={queueLoadError || queuesLoading}
          title={
            queueLoadError
              ? 'Queue list could not be loaded'
              : queues.length > 0
                ? 'Change queue assignment'
                : 'No assigned queues available'
          }
          className="text-xs text-gray-600 bg-gray-100 rounded-md px-2.5 py-1 border-0 outline-none cursor-pointer hover:bg-gray-200 transition-colors disabled:cursor-not-allowed disabled:opacity-60"
        >
          <option value="">
            {queuesLoading ? 'Loading queues' : 'No queue'}
          </option>
          {queues.map((q) => (
            <option key={q.id} value={q.id}>{q.name}</option>
          ))}
        </select>
        <span className="text-[11px] text-gray-400">
          {queueLoadError
            ? 'Queues unavailable'
            : queues.length > 0
              ? `${queues.length} queue${queues.length === 1 ? '' : 's'}`
              : 'No assigned queues'}
        </span>
      </div>

      {/* Assignee selector — reassign the conversation to another agent */}
      <select
        value={conversation.assigned_to ?? ''}
        onChange={handleAssignChange}
        disabled={agentsLoadError}
        title={agentsLoadError ? 'Assignable agents could not be loaded' : 'Assign to agent'}
        className="text-xs text-gray-600 bg-gray-100 rounded-md px-2.5 py-1 border-0 outline-none cursor-pointer hover:bg-gray-200 transition-colors max-w-[140px] disabled:cursor-not-allowed disabled:opacity-60"
      >
        <option value="">{agentsLoadError ? 'Agents unavailable' : 'Unassigned'}</option>
        {/* Keep the current assignee selectable even if not in the fetched list */}
        {conversation.assigned_to != null &&
          !agents.some((a) => a.id === conversation.assigned_to) && (
          <option value={conversation.assigned_to}>
            {conversation.assigned_to_name ?? 'Current agent'}
          </option>
        )}
        {agents.map((a) => (
          <option key={a.id} value={a.id}>{a.name}</option>
        ))}
      </select>

      {/* Tags */}
      <div className="flex items-center gap-1 flex-wrap">
        {(conversation.tags ?? []).map((tag) => (
          <span
            key={tag}
            className="flex items-center gap-1 text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full"
          >
            {tag}
            <button
              onClick={() => handleRemoveTag(tag)}
              className="text-gray-400 hover:text-gray-700 leading-none"
            >
              ×
            </button>
          </span>
        ))}
        <input
          value={tagInput}
          onChange={(e) => setTagInput(e.target.value)}
          onKeyDown={handleAddTag}
          placeholder="Add tag…"
          className="text-xs text-gray-600 placeholder-gray-300 outline-none w-20 bg-transparent"
        />
      </div>
    </div>
  );
}
