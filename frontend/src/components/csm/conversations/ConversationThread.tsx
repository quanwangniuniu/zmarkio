'use client';

import React, { useEffect, useRef } from 'react';
import { ConversationMessage, MessageSenderType } from '@/types/csmConversation';
import { cn } from '@/lib/utils';
import { RichMessageBody } from './RichMessageBody';

const SENDER_LABELS: Record<MessageSenderType, string> = {
  agent: 'Agent',
  customer: 'Customer',
  system: 'System',
};

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatDateKey(iso: string): string {
  return new Date(iso).toDateString();
}

function formatDateDivider(iso: string): string {
  const date = new Date(iso);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);

  if (date.toDateString() === today.toDateString()) return 'Today';
  if (date.toDateString() === yesterday.toDateString()) return 'Yesterday';

  const options: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric' };
  if (date.getFullYear() !== today.getFullYear()) options.year = 'numeric';
  return date.toLocaleDateString([], options);
}

interface ConversationThreadProps {
  messages: ConversationMessage[];
  typingUserIds?: number[];
}

export function ConversationThread({ messages, typingUserIds = [] }: ConversationThreadProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length]);

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-gray-400">
        No messages yet. Start the conversation.
      </div>
    );
  }

  // Group consecutive messages from the same sender, but never across dates.
  type Group = { senderType: MessageSenderType; dateKey: string; messages: ConversationMessage[] };
  const groups: Group[] = [];
  for (const msg of messages) {
    const dateKey = formatDateKey(msg.created_at);
    if (msg.sender_type === 'system') {
      groups.push({ senderType: 'system', dateKey, messages: [msg] });
    } else {
      const last = groups[groups.length - 1];
      if (
        last &&
        last.senderType === msg.sender_type &&
        last.dateKey === dateKey &&
        (last.senderType as string) !== 'system'
      ) {
        last.messages.push(msg);
      } else {
        groups.push({ senderType: msg.sender_type, dateKey, messages: [msg] });
      }
    }
  }

  // Date divider tracking
  let lastDate = '';

  return (
    <div className="flex flex-col flex-1 overflow-y-auto px-4 py-4 gap-4">
      {groups.map((group, gi) => {
        const firstMessage = group.messages[0];
        const showDateDivider = group.dateKey !== lastDate;
        if (showDateDivider) lastDate = group.dateKey;

        if (group.senderType === 'system') {
          return (
            <React.Fragment key={firstMessage.id}>
              {showDateDivider && (
                <div className="flex items-center gap-3 my-1">
                  <div className="flex-1 h-px bg-gray-100" />
                  <span className="text-[11px] text-gray-500 font-medium px-2 shrink-0">
                    {formatDateDivider(firstMessage.created_at)}
                  </span>
                  <div className="flex-1 h-px bg-gray-100" />
                </div>
              )}
              <div className="flex items-center gap-3 my-1">
                <div className="flex-1 h-px bg-gray-100" />
                <span className="text-xs text-gray-400 px-2 shrink-0">
                  {firstMessage.content}
                  <span className="mx-1.5 text-gray-300">·</span>
                  <span className="text-[11px] text-gray-300">{formatTime(firstMessage.created_at)}</span>
                </span>
                <div className="flex-1 h-px bg-gray-100" />
              </div>
            </React.Fragment>
          );
        }

        const isAgent = group.senderType === 'agent';
        const senderLabel = isAgent
          ? (group.messages[0].sender_agent_name ?? SENDER_LABELS.agent)
          : SENDER_LABELS.customer;

        return (
          <React.Fragment key={gi}>
            {showDateDivider && (
              <div className="flex items-center gap-3 my-1">
                <div className="flex-1 h-px bg-gray-100" />
                <span className="text-[11px] text-gray-500 font-medium px-2 shrink-0">
                  {formatDateDivider(firstMessage.created_at)}
                </span>
                <div className="flex-1 h-px bg-gray-100" />
              </div>
            )}

            <div className={cn('flex flex-col gap-1', isAgent ? 'items-end' : 'items-start')}>
              {/* Sender label — shown once per group */}
              <span className="text-xs text-gray-400 px-1">{senderLabel}</span>

              {/* Bubbles */}
              {group.messages.map((msg, mi) => {
                const isLast = mi === group.messages.length - 1;
                const hasImage = Boolean(msg.image_url);
                return (
                  <div key={msg.id} className="max-w-[75%]">
                    <div
                      className={cn(
                        'rounded-2xl text-sm shadow-sm transition-shadow hover:shadow-md overflow-hidden',
                        hasImage
                          ? 'bg-white border border-gray-200 text-gray-900'
                          : isAgent
                          ? cn(
                              'bg-brand-agent text-white',
                              isLast ? 'rounded-br-sm' : 'rounded-br-md',
                            )
                          : cn(
                              'bg-white border border-gray-200 text-gray-900',
                              isLast ? 'rounded-bl-sm' : 'rounded-bl-md',
                            ),
                      )}
                    >
                      {msg.image_url ? (
                        <a href={msg.image_url} target="_blank" rel="noopener noreferrer">
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img
                            src={msg.image_url}
                            alt="image"
                            className="max-w-[260px] max-h-[200px] object-contain block bg-white"
                          />
                        </a>
                      ) : msg.rich_body ? (
                        <div className="px-4 py-2.5">
                          <RichMessageBody body={msg.rich_body} isAgent={isAgent} />
                        </div>
                      ) : (
                        <div className="px-4 py-2.5">
                          <span className="whitespace-pre-wrap leading-relaxed">{msg.content}</span>
                        </div>
                      )}
                    </div>
                    {/* Timestamp */}
                    <div
                      className={cn(
                        'mt-1 px-1 text-[11px] text-gray-400',
                        isAgent ? 'text-right' : 'text-left',
                      )}
                    >
                      {formatTime(msg.created_at)}
                    </div>
                  </div>
                );
              })}
            </div>
          </React.Fragment>
        );
      })}

      {typingUserIds.length > 0 && (
        <div className="self-start flex items-center gap-1.5 text-xs text-gray-400 px-2 py-1">
          <span className="inline-flex gap-1">
            <span className="w-1.5 h-1.5 bg-gray-300 rounded-full animate-bounce" style={{ animationDelay: '-0.3s' }} />
            <span className="w-1.5 h-1.5 bg-gray-300 rounded-full animate-bounce" style={{ animationDelay: '-0.15s' }} />
            <span className="w-1.5 h-1.5 bg-gray-300 rounded-full animate-bounce" />
          </span>
          <span>typing…</span>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
