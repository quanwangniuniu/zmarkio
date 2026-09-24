import type {
  RoutingCondition,
  RoutingConditionValue,
  RoutingVocabulary,
} from '@/types/routingRule';
import { CHANNEL_TYPE_LABELS, type ChannelType } from '@/types/supportChannel';

export interface SummaryLookups {
  vocabulary: RoutingVocabulary | null;
  channelNames: Map<number, string>;
  organisationNames: Map<number, string>;
}

function fieldLabel(vocabulary: RoutingVocabulary | null, field: string) {
  return vocabulary?.fields.find((f) => f.field === field)?.label ?? field;
}

function operatorLabel(vocabulary: RoutingVocabulary | null, field: string, operator: string) {
  return (
    vocabulary?.fields
      .find((f) => f.field === field)
      ?.operators.find((o) => o.operator === operator)?.label ?? operator
  );
}

/** Human-readable rendering of a condition value (ids become names). */
export function formatConditionValue(
  field: string,
  value: RoutingConditionValue,
  lookups: SummaryLookups,
): string {
  if (value === null || value === undefined) return '';
  const list = Array.isArray(value) ? value : [value];
  return list
    .map((item) => {
      if (field === 'support_channel' && typeof item === 'number') {
        return lookups.channelNames.get(item) ?? `#${item}`;
      }
      if (field === 'customer_organisation' && typeof item === 'number') {
        return lookups.organisationNames.get(item) ?? `#${item}`;
      }
      if (field === 'channel_type' && typeof item === 'string') {
        return CHANNEL_TYPE_LABELS[item as ChannelType] ?? item;
      }
      return typeof item === 'string' && Array.isArray(value) ? `“${item}”` : String(item);
    })
    .join(', ');
}

/** e.g. `Latest customer message contains any of “refund”, “invoice”` */
export function summariseCondition(condition: RoutingCondition, lookups: SummaryLookups): string {
  const { field, operator, value } = condition;
  const parts = [
    fieldLabel(lookups.vocabulary, field),
    operatorLabel(lookups.vocabulary, field, operator),
    formatConditionValue(field, value, lookups),
  ];
  return parts.filter(Boolean).join(' ');
}
