'use client';

import { useMemo } from 'react';
import { Trash2 } from 'lucide-react';
import PortalSelect from '@/components/ticket-form/portal/PortalSelect';
import PortalMultiSelect from '@/components/ticket-form/portal/PortalMultiSelect';
import type {
  RoutingCondition,
  RoutingConditionField,
  RoutingConditionValue,
  RoutingValueKind,
  RoutingVocabulary,
} from '@/types/routingRule';
import { CHANNEL_TYPE_LABELS, type ChannelType } from '@/types/supportChannel';
import { BUILDER_CONTROL_CLASS } from '../constants';
import KeywordChipsInput from './KeywordChipsInput';

export function defaultValueFor(kind: RoutingValueKind | undefined): RoutingConditionValue {
  switch (kind) {
    case 'keywords':
    case 'channel_ids':
    case 'channel_types':
    case 'organisation_ids':
      return [];
    case 'text':
      return '';
    case 'channel_status':
      return 'offline';
    case 'integer':
      return 1;
    default:
      return null;
  }
}

/** A blank condition for the first vocabulary field. */
export function newCondition(vocabulary: RoutingVocabulary): RoutingCondition {
  const field = vocabulary.fields[0];
  const op = field.operators[0];
  return { field: field.field, operator: op.operator, value: defaultValueFor(op.value_kind) };
}

interface Props {
  index: number;
  condition: RoutingCondition;
  vocabulary: RoutingVocabulary;
  channels: { id: number; display_name: string }[];
  organisations: { id: number; name: string }[];
  disabled?: boolean;
  onChange: (condition: RoutingCondition) => void;
  onRemove: () => void;
}

export default function ConditionRowEditor({
  index,
  condition,
  vocabulary,
  channels,
  organisations,
  disabled,
  onChange,
  onRemove,
}: Props) {
  const fieldDef = vocabulary.fields.find((f) => f.field === condition.field);
  const kind = fieldDef?.operators.find((o) => o.operator === condition.operator)?.value_kind;

  const fieldOptions = useMemo(
    () => vocabulary.fields.map((f) => ({ value: f.field, label: f.label })),
    [vocabulary],
  );
  const operatorOptions = (fieldDef?.operators ?? []).map((o) => ({ value: o.operator, label: o.label }));

  const changeField = (field: string) => {
    const def = vocabulary.fields.find((f) => f.field === field);
    const op = def?.operators[0];
    onChange({
      field: field as RoutingConditionField,
      operator: op?.operator ?? '',
      value: defaultValueFor(op?.value_kind),
    });
  };

  const changeOperator = (operator: string) => {
    const nextKind = fieldDef?.operators.find((o) => o.operator === operator)?.value_kind;
    const value = nextKind === kind ? condition.value : defaultValueFor(nextKind);
    onChange({ ...condition, operator, value });
  };

  const setValue = (value: RoutingConditionValue) => onChange({ ...condition, value });
  const idPrefix = `rr-cond-${index}`;

  const valueEditor = (() => {
    switch (kind) {
      case 'keywords':
        return (
          <KeywordChipsInput
            id={`${idPrefix}-value`}
            values={(condition.value as string[]) ?? []}
            onChange={setValue}
            max={vocabulary.limits.keywords}
            placeholder="Type a keyword and press Enter"
            disabled={disabled}
          />
        );
      case 'text':
        return (
          <input
            id={`${idPrefix}-value`}
            value={(condition.value as string) ?? ''}
            onChange={(e) => setValue(e.target.value)}
            disabled={disabled}
            className={BUILDER_CONTROL_CLASS}
          />
        );
      case 'integer':
        return (
          <input
            id={`${idPrefix}-value`}
            type="number"
            min={0}
            max={1000}
            value={condition.value as number}
            onChange={(e) => setValue(e.target.value === '' ? 0 : Number(e.target.value))}
            disabled={disabled}
            className={BUILDER_CONTROL_CLASS}
          />
        );
      case 'channel_status':
        return (
          <PortalSelect
            id={`${idPrefix}-value`}
            value={(condition.value as string) ?? 'offline'}
            options={vocabulary.channel_statuses.map((s) => ({
              value: s,
              label: s === 'online' ? 'Online' : 'Offline',
            }))}
            disabled={disabled}
            onChange={setValue}
          />
        );
      case 'channel_types':
        return (
          <PortalMultiSelect
            id={`${idPrefix}-value`}
            values={(condition.value as string[]) ?? []}
            options={vocabulary.channel_types.map((t) => ({
              value: t,
              label: CHANNEL_TYPE_LABELS[t as ChannelType] ?? t,
            }))}
            placeholder="Select channel types…"
            disabled={disabled}
            onChange={setValue}
          />
        );
      case 'channel_ids':
        return (
          <PortalMultiSelect
            id={`${idPrefix}-value`}
            values={((condition.value as number[]) ?? []).map(String)}
            options={channels.map((c) => ({ value: String(c.id), label: c.display_name }))}
            placeholder="Select channels…"
            disabled={disabled}
            onChange={(vals) => setValue(vals.map(Number))}
          />
        );
      case 'organisation_ids':
        return (
          <PortalMultiSelect
            id={`${idPrefix}-value`}
            values={((condition.value as number[]) ?? []).map(String)}
            options={organisations.map((o) => ({ value: String(o.id), label: o.name }))}
            placeholder="Select organisations…"
            disabled={disabled}
            onChange={(vals) => setValue(vals.map(Number))}
          />
        );
      default:
        return null;
    }
  })();

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-gray-200 bg-gray-50/60 p-3" data-testid="condition-row">
      <div className="flex items-start gap-2">
        <div className="grid flex-1 grid-cols-1 gap-2 sm:grid-cols-2">
          <PortalSelect
            id={`${idPrefix}-field`}
            value={condition.field}
            options={fieldOptions}
            disabled={disabled}
            onChange={changeField}
          />
          <PortalSelect
            id={`${idPrefix}-operator`}
            value={condition.operator}
            options={operatorOptions}
            disabled={disabled}
            onChange={changeOperator}
          />
        </div>
        <button
          type="button"
          onClick={onRemove}
          disabled={disabled}
          aria-label={`Remove condition ${index + 1}`}
          className="mt-1 rounded-md p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600"
        >
          <Trash2 className="h-4 w-4" aria-hidden />
        </button>
      </div>
      {valueEditor}
    </div>
  );
}
