'use client';

import { useState } from 'react';
import { FlaskConical, Send } from 'lucide-react';
import { ConversationThread } from '@/components/csm/conversations/ConversationThread';
import type { ConversationMessage } from '@/types/csmConversation';
import { BUILDER_CONTROL_CLASS } from '../constants';

interface Props {
  messages: ConversationMessage[];
  disabled: boolean;
  disabledReason?: string;
  onSend: (text: string) => void;
}

/**
 * Simulated customer chat. Deliberately not the workspace ConversationComposer,
 * which sends real messages: this composer only appends to local state.
 */
export default function SandboxChatSimulator({ messages, disabled, disabledReason, onSend }: Props) {
  const [draft, setDraft] = useState('');

  const submit = () => {
    if (disabled || !draft.trim()) return;
    onSend(draft);
    setDraft('');
  };

  return (
    <div className="flex h-full min-h-[420px] flex-col overflow-hidden rounded-xl border border-gray-200 bg-white">
      <div className="flex items-center gap-2 border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs font-medium text-amber-800">
        <FlaskConical className="h-4 w-4 shrink-0" aria-hidden />
        Sandbox: nothing is sent or saved. Messages stay in this browser tab.
      </div>

      <ConversationThread messages={messages} />

      <form
        className="flex items-end gap-2 border-t border-gray-100 p-3"
        onSubmit={(e) => { e.preventDefault(); submit(); }}
      >
        <label htmlFor="sb-message" className="sr-only">Customer message</label>
        <textarea
          id="sb-message"
          rows={2}
          value={draft}
          maxLength={5000}
          disabled={disabled}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={disabled ? disabledReason : 'Type as the customer… (Enter to send)'}
          className={`${BUILDER_CONTROL_CLASS} resize-none`}
        />
        <button
          type="submit"
          disabled={disabled || !draft.trim()}
          aria-label="Send test message"
          className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-[#3CCED7] to-[#A6E661] text-white disabled:opacity-50"
        >
          <Send className="h-4 w-4" aria-hidden />
        </button>
      </form>
    </div>
  );
}
