'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { ConversationDetail } from '@/types/csmConversation';
import { Queue } from '@/types/csm';
import { useCsmConversationStore } from '@/lib/csmConversationStore';
import { useCsmConversationSocket } from '@/hooks/useCsmConversationSocket';
import CsmConversationAPI from '@/lib/api/csmConversationApi';
import { ConversationList } from '@/components/csm/conversations/ConversationList';
import { ConversationThread } from '@/components/csm/conversations/ConversationThread';
import { ConversationComposer } from '@/components/csm/conversations/ConversationComposer';
import { CustomerProfilePanel } from '@/components/csm/conversations/CustomerProfilePanel';
import { MyTicketsPanel } from '@/components/csm/conversations/MyTicketsPanel';
import { ConversationActions } from '@/components/csm/conversations/ConversationActions';
import { CreateTicketModal } from '@/components/csm/conversations/CreateTicketModal';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import Link from 'next/link';
import { ClipboardCheck } from 'lucide-react';
import { useAuthStore } from '@/lib/authStore';
import { useBuildUrl } from '@/lib/buildUrl';

function ConversationsPageContent() {
  const buildUrl = useBuildUrl();
  const isSupervisor = useAuthStore((s) => Boolean(s.user?.is_csm_supervisor));
  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [leftTab, setLeftTab] = useState<'conversations' | 'mytickets'>('conversations');
  const [ticketRefreshKey, setTicketRefreshKey] = useState(0);
  const [showCreateTicket, setShowCreateTicket] = useState(false);

  // Queue selector state
  const [queues, setQueues] = useState<Queue[]>([]);
  const [selectedQueue, setSelectedQueue] = useState<number | null>(null);
  const [queueLoadError, setQueueLoadError] = useState(false);
  const setSelectedQueueId = useCsmConversationStore((s) => s.setSelectedQueueId);

  const conversations = useCsmConversationStore((s) => s.conversations);
  const setConversations = useCsmConversationStore((s) => s.setConversations);
  const activeId = useCsmConversationStore((s) => s.activeConversationId);
  const setMessages = useCsmConversationStore((s) => s.setMessages);
  const messagesByConversation = useCsmConversationStore((s) => s.messagesByConversation);
  const typingByConversation = useCsmConversationStore((s) => s.typingByConversation);

  const { sendTyping } = useCsmConversationSocket(activeId);

  // Load available queues once
  useEffect(() => {
    CsmConversationAPI.availableQueues().then((data) => {
      const list = Array.isArray(data) ? data : [];
      setQueues(list);
      setQueueLoadError(false);
    }).catch(() => setQueueLoadError(true));
  }, []);

  // Sync selectedQueue into the zustand store so the WS hook can use it for
  // client-side queue filtering of new_conversation events.
  useEffect(() => {
    setSelectedQueueId(selectedQueue);
  }, [selectedQueue, setSelectedQueueId]);

  // Load conversation list — re-fetches when selectedQueue changes
  const loadConversations = useCallback(() => {
    setLoading(true);
    CsmConversationAPI.list({ queue: selectedQueue })
      .then((data) => setConversations(data))
      .finally(() => setLoading(false));
  }, [selectedQueue, setConversations]);

  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  // Load conversation detail when active changes
  useEffect(() => {
    if (!activeId) {
      setDetail(null);
      return;
    }
    // Clear stale detail immediately so the UI doesn't mix old/new data.
    setDetail(null);
    let cancelled = false;
    CsmConversationAPI.get(activeId).then((data) => {
      if (cancelled) return;
      setDetail(data);
      setMessages(activeId, data.messages);
    });
    return () => { cancelled = true; };
  }, [activeId, setMessages]);

  const activeConversation = conversations.find((c) => c.id === activeId) ?? null;
  const selectedQueueName = selectedQueue
    ? queues.find((q) => q.id === selectedQueue)?.name ?? null
    : null;
  const messages = activeId ? (messagesByConversation[activeId] ?? []) : [];
  const typingUsers = activeId ? (typingByConversation[activeId] ?? []) : [];

  return (
    <div className="flex h-full min-h-0 overflow-hidden">
      {/* LEFT: Tab panel */}
      <div className="hidden h-full w-80 shrink-0 border-r border-gray-200 md:flex md:flex-col">
        {/* Tab header */}
        <div className="flex border-b border-gray-100 shrink-0">
          <button
            onClick={() => setLeftTab('conversations')}
            className={`flex-1 py-2.5 text-xs font-medium transition-colors ${
              leftTab === 'conversations'
                ? 'text-blue-600 border-b-2 border-blue-600'
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            Conversations
          </button>
          <button
            onClick={() => setLeftTab('mytickets')}
            className={`flex-1 py-2.5 text-xs font-medium transition-colors ${
              leftTab === 'mytickets'
                ? 'text-blue-600 border-b-2 border-blue-600'
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            My Tickets
          </button>
        </div>

        {/* Supervisors review closed conversations in the quality area. */}
        {isSupervisor && (
          <Link
            href={buildUrl('/csm/quality')}
            className="flex shrink-0 items-center gap-1.5 border-b border-gray-100 px-3 py-2 text-xs font-medium text-gray-500 transition-colors hover:bg-gray-50 hover:text-[#1a9ba3]"
          >
            <ClipboardCheck className="h-3.5 w-3.5" />
            Conversation quality
          </Link>
        )}

        {/* Queue selector — only shown on Conversations tab */}
        {leftTab === 'conversations' && (
          <div className="px-3 py-2 border-b border-gray-100 shrink-0">
            <div className="mb-1.5 flex items-center justify-between text-[11px] text-gray-400">
              <span>Assigned queues</span>
              <span>{queueLoadError ? 'Unavailable' : `${queues.length} available`}</span>
            </div>
            <select
              value={selectedQueue ?? ''}
              onChange={(e) => setSelectedQueue(e.target.value ? Number(e.target.value) : null)}
              className="w-full text-xs border border-gray-200 rounded-md px-2 py-1.5 bg-white outline-none focus:ring-1 focus:ring-blue-400 text-gray-700"
            >
              <option value="">All Queues</option>
              {queues.map((q) => (
                <option key={q.id} value={q.id}>{q.name}</option>
              ))}
            </select>
            {queueLoadError && (
              <p className="mt-1.5 text-[11px] leading-relaxed text-red-500">
                Queue access could not be loaded. Conversation visibility may be limited.
              </p>
            )}
            {!queueLoadError && queues.length === 0 && (
              <p className="mt-1.5 text-[11px] leading-relaxed text-orange-500">
                You are not assigned to any active queue. Ask an admin to assign one.
              </p>
            )}
          </div>
        )}

        <div className="flex-1 min-h-0 overflow-y-auto">
          {leftTab === 'conversations' ? (
            <ConversationList
              conversations={conversations}
              loading={loading}
              onClaimed={() => setTicketRefreshKey((k) => k + 1)}
              selectedQueueName={selectedQueueName}
              queueLoadError={queueLoadError}
              hasAvailableQueues={queues.length > 0}
            />
          ) : (
            <MyTicketsPanel refreshKey={ticketRefreshKey} />
          )}
        </div>
      </div>

      {/* MIDDLE: Conversation thread + composer */}
      <div className="flex flex-1 min-w-0 flex-col">
        {!activeConversation ? (
          <div className="flex flex-1 items-center justify-center px-6 text-center">
            <div>
              <p className="text-sm font-medium text-gray-700">Select a conversation</p>
              <p className="mt-1 text-xs text-gray-400">
                Open an assigned conversation to view the thread, reply, create a ticket, or update tags and ownership.
              </p>
            </div>
          </div>
        ) : (
          <>
            <ConversationActions
              conversation={activeConversation}
              onPriorityChanged={(newPriority) => {
                setTicketRefreshKey((k) => k + 1);
                setDetail((prev) => {
                  if (!prev) return prev;
                  return {
                    ...prev,
                    linked_tickets: prev.linked_tickets.map((t) =>
                      t.id === activeConversation.ticket?.id
                        ? { ...t, priority: newPriority }
                        : t
                    ),
                  };
                });
              }}
            />
            <ConversationThread messages={messages} typingUserIds={typingUsers} />
            {activeConversation.ticket ? (
              <ConversationComposer
                key={activeId}
                conversationId={activeId!}
                organisationId={activeConversation.queue_organisation_id}
                onTyping={sendTyping}
                onSent={() => setTicketRefreshKey((k) => k + 1)}
              />
            ) : (
              <div className="border-t border-gray-100 px-4 py-3 text-xs text-gray-400 text-center bg-gray-50">
                Claim this conversation to reply
              </div>
            )}
          </>
        )}
      </div>

      {/* RIGHT: Customer profile panel */}
      <div className="hidden h-full w-72 shrink-0 border-l border-gray-200 md:flex md:flex-col">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 shrink-0">
          <h2 className="font-semibold text-gray-900 text-sm">Customer Profile</h2>
          {detail && !activeConversation?.ticket && (
            <button
              onClick={() => setShowCreateTicket(true)}
              className="px-2.5 py-1 text-xs font-medium bg-blue-600 text-white rounded-md hover:bg-blue-700"
            >
              Create Ticket
            </button>
          )}
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto">
          {detail ? (
            <CustomerProfilePanel
              profile={detail.customer_profile}
              linkedTickets={detail.linked_tickets}
              conversationId={detail.id}
            />
          ) : (
            <div className="flex items-center justify-center h-full text-sm text-gray-400">
              {activeId ? 'Loading…' : 'No conversation selected'}
            </div>
          )}
        </div>
      </div>

      {/* Create Ticket modal (AC4): form pre-populated from the linked customer profile */}
      {showCreateTicket && detail && activeId && (
        <CreateTicketModal
          conversationId={activeId}
          customerProfile={detail.customer_profile}
          defaultQueueId={activeConversation?.queue ?? null}
          onClose={() => setShowCreateTicket(false)}
          onCreated={() => {
            setTicketRefreshKey((k) => k + 1);
            loadConversations();
            // Capture the current activeId so we don't overwrite detail if the
            // agent switched to a different conversation while the request was
            // in flight.
            const currentId = activeId;
            CsmConversationAPI.get(currentId).then((data) => {
              if (useCsmConversationStore.getState().activeConversationId !== currentId) return;
              setDetail(data);
              setMessages(currentId, data.messages);
            });
          }}
        />
      )}
    </div>
  );
}

export default function ConversationsPage() {
  return (
    <ProtectedRoute>
      <DashboardLayout hideRightPanel>
        <ConversationsPageContent />
      </DashboardLayout>
    </ProtectedRoute>
  );
}
