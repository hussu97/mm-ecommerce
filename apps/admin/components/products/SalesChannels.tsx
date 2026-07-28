'use client';

/**
 * Where a product is sold, as one control.
 *
 * This used to be two checkboxes — "Show on website" and "Show on POS" — set
 * in different corners of the form, so nothing on screen said they were one
 * decision. Somebody could put an item on the register without ever seeing
 * that it was still on the website too, which is how the counter ended up
 * offering the nine-piece retail boxes alongside the singles it sells.
 *
 * One list, read left to right, and an empty one that says so out loud rather
 * than looking like an unset field.
 */

import { Badge } from '@/components/ui';
import {
  SALES_CHANNELS,
  SALES_CHANNEL_LABELS,
  type SalesChannel,
} from '@/lib/types';

export function SalesChannelPicker({
  value,
  onChange,
}: {
  value: SalesChannel[];
  onChange: (next: SalesChannel[]) => void;
}) {
  // Toggle through the canonical order rather than appending, so two products
  // sold in the same places always store the same list.
  function toggle(channel: SalesChannel) {
    const next = value.includes(channel)
      ? value.filter(c => c !== channel)
      : [...value, channel];
    onChange(SALES_CHANNELS.filter(c => next.includes(c)));
  }

  return (
    <div>
      <label className="block text-xs font-body uppercase tracking-widest text-gray-500 mb-1.5">
        Sales channels
      </label>
      <div className="flex flex-wrap gap-2">
        {SALES_CHANNELS.map(channel => {
          const on = value.includes(channel);
          return (
            <button
              key={channel}
              type="button"
              aria-pressed={on}
              onClick={() => toggle(channel)}
              className={`px-3 py-2 text-sm font-body border rounded-sm transition-colors cursor-pointer ${
                on
                  ? 'border-primary bg-primary/5 text-primary'
                  : 'border-gray-300 text-gray-500 hover:border-gray-400'
              }`}
            >
              <span className="material-icons text-[14px] align-middle mr-1">
                {on ? 'check_box' : 'check_box_outline_blank'}
              </span>
              {SALES_CHANNEL_LABELS[channel]}
            </button>
          );
        })}
      </div>
      <p className="text-[11px] font-body text-gray-400 mt-1.5">
        {value.length === 0
          ? 'Not sold anywhere — the product stays in the catalogue but no channel lists it.'
          : `Sold on ${value.map(c => SALES_CHANNEL_LABELS[c]).join(' and ')}.`}
      </p>
      {value.includes('pos') && (
        <p className="text-[11px] font-body text-gray-400 mt-1">
          The register also needs the product in an active menu group.
        </p>
      )}
    </div>
  );
}

/**
 * The same fact in a table row.
 *
 * Only worth a badge when it is not the default of both — a badge on every
 * row is a badge nobody reads.
 */
export function ChannelBadges({ channels }: { channels: SalesChannel[] }) {
  if (!channels || channels.length === 0) {
    return <Badge variant="danger">No channel</Badge>;
  }
  if (channels.length === SALES_CHANNELS.length) return null;
  return (
    <Badge variant="warning">{SALES_CHANNEL_LABELS[channels[0]]} only</Badge>
  );
}
