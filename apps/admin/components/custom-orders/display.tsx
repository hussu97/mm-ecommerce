'use client';

/**
 * Words and badges for custom orders, shared by the list, calendar and detail.
 * Presentation only — which status an order may move to is the API's
 * `actions`, never derived here.
 */

import { Badge } from '@/components/ui';

type Variant = 'success' | 'warning' | 'danger' | 'info' | 'neutral';

const STATUS: Record<string, { label: string; variant: Variant }> = {
  created: { label: 'Taken', variant: 'warning' },
  confirmed: { label: 'Confirmed', variant: 'warning' },
  // The kitchen docket was printed at the register: the kitchen has it.
  arrived_at_pos: { label: 'In the kitchen', variant: 'info' },
  packed: { label: 'Packed', variant: 'info' },
  out_for_delivery: { label: 'On the way', variant: 'info' },
  delivered: { label: 'Delivered', variant: 'success' },
  undelivered: { label: 'Undelivered', variant: 'danger' },
  cancelled: { label: 'Cancelled', variant: 'danger' },
};

export function statusLabel(status: string): string {
  return STATUS[status]?.label ?? status.replaceAll('_', ' ');
}

export function CustomOrderStatusBadge({ status }: { status: string }) {
  return <Badge variant={STATUS[status]?.variant ?? 'neutral'}>{statusLabel(status)}</Badge>;
}

/** The calendar chip's colour, keyed like the badge. */
export function statusChipClass(status: string): string {
  switch (STATUS[status]?.variant) {
    case 'success':
      return 'border-green-200 bg-green-50 text-green-800';
    case 'danger':
      return 'border-red-200 bg-red-50 text-red-700 line-through';
    case 'info':
      return 'border-blue-200 bg-blue-50 text-blue-800';
    default:
      return 'border-amber-200 bg-amber-50 text-amber-900';
  }
}

export const PAYMENT_TYPE_LABEL: Record<string, string> = {
  bank_transfer: 'Bank transfer',
  card: 'Card',
  cash: 'Cash',
};

export const CARD_FEE_MODE_LABEL: Record<string, string> = {
  separate_line: 'Card fee added as a separate line',
  included: 'Card fee included in the prices',
};

export const PROVIDER_LABEL: Record<string, string> = {
  slider_car: 'Slider (car)',
  slider_bike: 'Slider (bike)',
  lalamove: 'Lalamove',
  third_party: 'Third-party courier',
  noon_send: 'noon Send',
};

export function providerLabel(provider: string | null | undefined): string {
  if (!provider) return '—';
  return PROVIDER_LABEL[provider] ?? provider.replaceAll('_', ' ');
}

/** "30 Sep 2026" or "30 Sep 2026, 16:00" — the promise as it was made. */
export function deliveryLabel(date: string | null, time: string | null): string {
  if (!date) return '—';
  const [y, m, d] = date.split('-').map(Number);
  const day = new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-AE', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  });
  return time ? `${day}, ${time.slice(0, 5)}` : day;
}
