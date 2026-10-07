import toast from 'react-hot-toast';
import type { WebhookDeliveryStatus } from '@/types/csmIntegrations';

const RESOURCE_LABELS: Record<string, string> = {
  customers: 'Customers',
  organisations: 'Organisations',
  tickets: 'Tickets',
  conversations: 'Conversations',
  templates: 'Quick reply templates',
  routing_rules: 'Routing rules',
  queues: 'Queues',
  agents: 'Agents',
};

const EVENT_LABELS: Record<string, string> = {
  'ticket.created': 'Ticket created',
  'ticket.status_changed': 'Ticket status changed',
  'sla.breached': 'SLA breached',
  ping: 'Test (ping)',
};

export const DELIVERY_STATUS_LABELS: Record<WebhookDeliveryStatus, string> = {
  pending: 'Pending',
  succeeded: 'Succeeded',
  retrying: 'Retry scheduled',
  failed: 'Failed',
};

export const DELIVERY_STATUS_CLASS: Record<WebhookDeliveryStatus, string> = {
  pending: 'bg-gray-100 text-gray-700',
  succeeded: 'bg-green-100 text-green-800',
  retrying: 'bg-amber-100 text-amber-800',
  failed: 'bg-red-100 text-red-700',
};

export function resourceLabel(resource: string): string {
  return RESOURCE_LABELS[resource] ?? resource;
}

export function eventLabel(event: string): string {
  return EVENT_LABELS[event] ?? event;
}

/** "Tickets (write), Queues (read)", grouping a resource's read+write as write. */
export function summarizeScopes(scopes: string[]): string {
  const byResource = new Map<string, string>();
  scopes.forEach((scope) => {
    const [resource, access] = scope.split(':');
    if (access === 'write' || !byResource.has(resource)) byResource.set(resource, access);
  });
  return Array.from(byResource, ([resource, access]) => `${resourceLabel(resource)} (${access})`).join(', ');
}

export function formatDateTime(value: string | null): string {
  if (!value) return '—';
  return new Date(value).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export async function copyToClipboard(text: string, what: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(`${what} copied.`);
  } catch {
    toast.error('Could not copy. Select the text and copy it manually.');
  }
}
