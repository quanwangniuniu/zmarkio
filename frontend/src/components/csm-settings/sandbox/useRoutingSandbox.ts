'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { RoutingSandboxAPI } from '@/lib/api/routingRuleApi';
import type { ConversationMessage, QuickReplyTemplate } from '@/types/csmConversation';
import type { RoutingSandboxResult, RoutingTrace } from '@/types/routingRule';

export interface SandboxConfig {
  experienceGroupId: number | null;
  supportChannelId: number | null;
  customerOrganisationId: number | null;
  subject: string;
  /** `datetime-local` value; empty means "now". */
  simulatedAt: string;
}

export const EMPTY_CONFIG: SandboxConfig = {
  experienceGroupId: null,
  supportChannelId: null,
  customerOrganisationId: null,
  subject: '',
  simulatedAt: '',
};

export const PREVIEW_AGENT_NAME = 'Template preview · not sent';

let localId = 0;
const nextLocalId = () => { localId -= 1; return localId; };

function localMessage(partial: Pick<ConversationMessage, 'sender_type' | 'content'> & Partial<ConversationMessage>): ConversationMessage {
  return {
    id: nextLocalId(),
    conversation: 0,
    sender_agent: null,
    sender_agent_name: null,
    sender_agent_email: null,
    rich_body: null,
    image_url: null,
    created_at: new Date().toISOString(),
    ...partial,
  };
}

/**
 * Sandbox session state. Lives only in React state: the backend call is a
 * stateless dry run, so leaving the page leaves nothing behind.
 */
export function useRoutingSandbox(projectId: number) {
  const [config, setConfig] = useState<SandboxConfig>(EMPTY_CONFIG);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [traces, setTraces] = useState<RoutingTrace[]>([]);
  const [meta, setMeta] = useState<Omit<RoutingSandboxResult, 'traces'> | null>(null);
  const [evaluating, setEvaluating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestSeq = useRef(0);

  const customerTexts = messages.filter((m) => m.sender_type === 'customer').map((m) => m.content);

  const evaluate = useCallback(async (texts: string[], cfg: SandboxConfig) => {
    if (cfg.experienceGroupId === null || texts.length === 0) return;
    const seq = ++requestSeq.current;
    setEvaluating(true);
    setError(null);
    try {
      const result = await RoutingSandboxAPI.evaluate(projectId, {
        experience_group: cfg.experienceGroupId,
        messages: texts,
        subject: cfg.subject,
        support_channel: cfg.supportChannelId,
        customer_organisation: cfg.customerOrganisationId,
        simulated_at: cfg.simulatedAt ? new Date(cfg.simulatedAt).toISOString() : null,
        evaluate_each_prefix: true,
      });
      if (seq !== requestSeq.current) return;
      const { traces: nextTraces, ...rest } = result;
      setTraces(nextTraces);
      setMeta(rest);
    } catch {
      if (seq === requestSeq.current) setError('Could not evaluate routing rules.');
    } finally {
      if (seq === requestSeq.current) setEvaluating(false);
    }
  }, [projectId]);

  // Re-run when the scenario changes so the trace always matches the config.
  const textsKey = JSON.stringify(customerTexts);
  useEffect(() => {
    evaluate(JSON.parse(textsKey), config);
  }, [config, textsKey, evaluate]);

  const sendCustomerMessage = useCallback((text: string) => {
    const content = text.trim();
    if (!content) return;
    setMessages((prev) => [...prev, localMessage({ sender_type: 'customer', content })]);
  }, []);

  const insertTemplate = useCallback((template: QuickReplyTemplate) => {
    setMessages((prev) => [
      ...prev,
      localMessage({
        sender_type: 'agent',
        sender_agent_name: PREVIEW_AGENT_NAME,
        content: template.content,
        rich_body: template.rich_body,
      }),
    ]);
  }, []);

  const rerun = useCallback(
    () => evaluate(JSON.parse(textsKey), config),
    [evaluate, textsKey, config],
  );

  // Rules may have been edited in another tab; refresh when the admin comes back.
  useEffect(() => {
    window.addEventListener('focus', rerun);
    return () => window.removeEventListener('focus', rerun);
  }, [rerun]);

  const reset = useCallback(() => {
    requestSeq.current += 1;
    setMessages([]);
    setTraces([]);
    setMeta(null);
    setError(null);
    setEvaluating(false);
  }, []);

  return {
    config,
    setConfig,
    messages,
    traces,
    meta,
    evaluating,
    error,
    customerMessageCount: customerTexts.length,
    sendCustomerMessage,
    insertTemplate,
    rerun,
    reset,
  };
}
