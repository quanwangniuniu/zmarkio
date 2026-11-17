'use client';

import React, { useState, useEffect } from 'react';
import { CustomerProfile } from '@/types/csmConversation';
import { PRIORITY_LABELS, TicketPriority } from '@/types/csm';
import { CSM_PRIORITY } from '@/lib/csm/csmConfig';
import CsmConversationAPI from '@/lib/api/csmConversationApi';
import { useCsmConversationStore } from '@/lib/csmConversationStore';
import { AlertCircle, ExternalLink } from 'lucide-react';
import { useBuildUrl } from '@/lib/buildUrl';

interface Queue {
  id: number;
  name: string;
}

interface CreateTicketModalProps {
  conversationId: number;
  customerProfile: CustomerProfile | null;
  defaultQueueId: number | null;
  onClose: () => void;
  onCreated?: () => void;
}

/** Build the initial description from the linked customer's profile (AC4: form pre-populated). */
function buildInitialDescription(profile: CustomerProfile | null): string {
  if (!profile) return '';
  const lines: string[] = [];
  if (profile.full_name) lines.push(`Customer: ${profile.full_name}`);
  if (profile.email) lines.push(`Email: ${profile.email}`);
  if (profile.company) lines.push(`Company: ${profile.company}`);
  if (profile.phone) lines.push(`Phone: ${profile.phone}`);
  if (profile.organisation_name) lines.push(`Organisation: ${profile.organisation_name}`);
  if (profile.region_name) lines.push(`Region: ${profile.region_name}`);
  return lines.join('\n');
}

export function CreateTicketModal({
  conversationId,
  customerProfile,
  defaultQueueId,
  onClose,
  onCreated,
}: CreateTicketModalProps) {
  const buildUrl = useBuildUrl();
  const customerName = customerProfile?.full_name || 'customer';
  const [title, setTitle] = useState(`Support request from ${customerName}`);
  const [description, setDescription] = useState(() => buildInitialDescription(customerProfile));
  const [priority, setPriority] = useState<'critical' | 'high' | 'medium' | 'low'>('medium');
  const [selectedQueueId, setSelectedQueueId] = useState<number | null>(defaultQueueId);
  const [queues, setQueues] = useState<Queue[]>([]);
  const [queueLoadError, setQueueLoadError] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const addMessage = useCsmConversationStore((s) => s.addMessage);

  // Fetch available queues
  useEffect(() => {
    CsmConversationAPI.availableQueues()
      .then((data: Queue[]) => {
        setQueues(data);
        setQueueLoadError(false);
        // Auto-select first queue if no default
        if (!selectedQueueId && data.length > 0) {
          setSelectedQueueId(data[0].id);
        }
      })
      .catch(() => setQueueLoadError(true));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedQueueId) {
      setError('Please select a queue.');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const result = await CsmConversationAPI.createTicket(conversationId, {
        title,
        description,
        priority,
        queue: selectedQueueId,
      });
      addMessage(conversationId, result.system_message);
      onCreated?.();
      onClose();
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail || 'Failed to create ticket. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg mx-4">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <div>
            <h2 className="font-semibold text-gray-900">Create Ticket</h2>
            <p className="mt-0.5 text-xs text-gray-400">Conversation #{conversationId}</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
        </div>

        <form onSubmit={handleSubmit} className="px-5 py-4 flex flex-col gap-4">
          <div className="rounded-lg border border-blue-100 bg-blue-50/70 px-3 py-2.5">
            <p className="text-xs font-medium uppercase tracking-wide text-blue-700">
              Pre-populated customer profile
            </p>
            <div className="mt-2 grid grid-cols-1 gap-1.5 text-xs text-gray-600 sm:grid-cols-2">
              <div>
                <span className="text-gray-400">Name</span>
                <p className="truncate font-medium text-gray-800">{customerProfile?.full_name || 'Unknown'}</p>
              </div>
              <div>
                <span className="text-gray-400">Email</span>
                <p className="truncate font-medium text-gray-800">{customerProfile?.email || 'Unknown'}</p>
              </div>
              <div>
                <span className="text-gray-400">Company</span>
                <p className="truncate font-medium text-gray-800">{customerProfile?.company || 'Not set'}</p>
              </div>
              <div>
                <span className="text-gray-400">Phone</span>
                <p className="truncate font-medium text-gray-800">{customerProfile?.phone || 'Not set'}</p>
              </div>
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Title</label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
              className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-400"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={5}
              className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-400 resize-none"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Queue *</label>
            {queueLoadError ? (
              <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
                <div className="flex items-start gap-2">
                  <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  <span>Queues could not be loaded. Refresh the page or check your queue access.</span>
                </div>
              </div>
            ) : queues.length === 0 ? (
              <div className="rounded-lg border border-orange-200 bg-orange-50 px-3 py-2 text-xs text-orange-700">
                No active queue is available for this workspace.
                <a href={buildUrl('/csm/queues')} className="ml-1 inline-flex items-center gap-1 font-medium text-orange-800 underline">
                  Manage queues <ExternalLink size={11} />
                </a>
              </div>
            ) : (
              <select
                value={selectedQueueId ?? ''}
                onChange={(e) => setSelectedQueueId(Number(e.target.value))}
                required
                className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-400"
              >
                <option value="" disabled>Select a queue…</option>
                {queues.map((q) => (
                  <option key={q.id} value={q.id}>{q.name}</option>
                ))}
              </select>
            )}
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Priority</label>
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value as typeof priority)}
              className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-400"
            >
              {CSM_PRIORITY.values.map((value) => (
                <option key={value} value={value}>{PRIORITY_LABELS[value as TicketPriority]}</option>
              ))}
            </select>
          </div>

          {error && (
            <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900">
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || !title.trim() || queues.length === 0 || queueLoadError}
              className="px-4 py-2 text-sm font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {submitting ? 'Creating…' : 'Create Ticket'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
