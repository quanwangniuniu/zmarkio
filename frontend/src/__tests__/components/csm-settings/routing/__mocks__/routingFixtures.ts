import type { RoutingTrace, RoutingVocabulary } from '@/types/routingRule';

export const VOCABULARY: RoutingVocabulary = {
  fields: [
    {
      field: 'latest_message',
      label: 'Latest customer message',
      operators: [
        { operator: 'contains_any', label: 'contains any of', value_kind: 'keywords' },
        { operator: 'contains_all', label: 'contains all of', value_kind: 'keywords' },
      ],
    },
    {
      field: 'message_count',
      label: 'Customer message count',
      operators: [{ operator: 'gte', label: 'is at least', value_kind: 'integer' }],
    },
  ],
  channel_types: ['live_chat', 'contact_form', 'email'],
  channel_statuses: ['online', 'offline'],
  limits: { conditions: 10, keywords: 20, tags: 10 },
};

export function makeTrace(overrides: Partial<RoutingTrace> = {}): RoutingTrace {
  return {
    evaluated_at: '2026-09-25T00:00:00Z',
    message_count: 1,
    steps: [
      {
        rule_id: 1,
        rule_name: 'Refunds',
        position: 0,
        status: 'matched',
        match_mode: 'all',
        conditions: [{
          index: 0,
          field: 'latest_message',
          operator: 'contains_any',
          expected: ['refund'],
          actual: 'I want a refund',
          passed: true,
          detail: 'Matched: refund',
        }],
        action: { type: 'route_to_queue', queue_id: 10, queue_name: 'Billing', add_tags: ['money'] },
        note: '',
      },
      {
        rule_id: 2,
        rule_name: 'Catch all',
        position: 1,
        status: 'not_reached',
        match_mode: 'all',
        conditions: [],
        action: null,
        note: "Not evaluated; 'Refunds' already matched.",
      },
    ],
    fallback: null,
    outcome: {
      decided_by: 'rule', rule_id: 1, rule_name: 'Refunds',
      queue_id: 10, queue_name: 'Billing', tags: ['money'],
    },
    warnings: [],
    ...overrides,
  };
}

export const FALLBACK_TRACE = makeTrace({
  steps: [{
    rule_id: 1,
    rule_name: 'Refunds',
    position: 0,
    status: 'not_matched',
    match_mode: 'all',
    conditions: [{
      index: 0,
      field: 'latest_message',
      operator: 'contains_any',
      expected: ['refund'],
      actual: 'Hello',
      passed: false,
      detail: 'No keywords matched',
    }],
    action: null,
    note: '',
  }],
  fallback: {
    source: 'channel_default_queue', queue_id: 3, queue_name: 'Frontline',
    reason: "No rule matched, so the channel's default queue is used.",
  },
  outcome: {
    decided_by: 'fallback', rule_id: null, rule_name: null,
    queue_id: 3, queue_name: 'Frontline', tags: [],
  },
});
