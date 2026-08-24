'use client';

/**
 * The custom-cake diary.
 *
 * A month grid rather than a list, because the question this page exists to
 * answer is "can we take a cake for the 14th", and a list makes you count.
 * A full day is red, a free day is not, and that has to be readable at arm's
 * length while somebody is on the phone.
 *
 * Adding an order from here is the point. Most of this bakery's custom work
 * arrives as an Instagram or WhatsApp message, and until it is written down
 * the website will happily sell the same Saturday to someone else.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  customOrdersApi,
  ApiError,
  type CalendarDay,
  type CustomOrder,
} from '@/lib/api';
import { Badge, Button, Input, LoadError, Select, Spinner, Textarea } from '@/components/ui';

const SOURCE_OPTIONS = [
  { value: 'instagram', label: 'Instagram' },
  { value: 'whatsapp', label: 'WhatsApp' },
  { value: 'phone', label: 'Phone' },
  { value: 'walk_in', label: 'Walk-in' },
  { value: 'website', label: 'Website' },
  { value: 'other', label: 'Other' },
];

const STATUS_OPTIONS = [
  { value: 'enquiry', label: 'Enquiry' },
  { value: 'confirmed', label: 'Confirmed' },
  { value: 'in_production', label: 'In production' },
  { value: 'ready', label: 'Ready' },
  { value: 'completed', label: 'Completed' },
  { value: 'cancelled', label: 'Cancelled' },
];

const STATUS_VARIANT: Record<string, 'neutral' | 'warning' | 'success' | 'danger'> = {
  enquiry: 'warning',
  confirmed: 'success',
  in_production: 'success',
  ready: 'success',
  completed: 'neutral',
  cancelled: 'danger',
};

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function iso(d: Date): string {
  // Local parts, not toISOString(): the latter converts to UTC first, which in
  // Dubai (UTC+4) turns any date before 04:00 into the day before.
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate(),
  ).padStart(2, '0')}`;
}

function monthBounds(cursor: Date): { start: string; end: string } {
  const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
  const last = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0);
  return { start: iso(first), end: iso(last) };
}

/** Monday-first offset for the 1st of the month. */
function leadingBlanks(cursor: Date): number {
  const weekday = new Date(cursor.getFullYear(), cursor.getMonth(), 1).getDay();
  return (weekday + 6) % 7;
}

interface FormState {
  due_date: string;
  customer_name: string;
  customer_phone: string;
  description: string;
  cake_message: string;
  flavour: string;
  size_label: string;
  servings: string;
  quoted_total: string;
  deposit_amount: string;
  source: string;
  status: string;
  admin_notes: string;
}

const EMPTY_FORM: FormState = {
  due_date: '',
  customer_name: '',
  customer_phone: '',
  description: '',
  cake_message: '',
  flavour: '',
  size_label: '',
  servings: '',
  quoted_total: '',
  deposit_amount: '',
  source: 'instagram',
  status: 'enquiry',
  admin_notes: '',
};

export default function CustomOrdersPage() {
  const [cursor, setCursor] = useState(() => new Date());
  const [days, setDays] = useState<CalendarDay[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');
  const [allowPast, setAllowPast] = useState(false);

  const { start, end } = useMemo(() => monthBounds(cursor), [cursor]);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      setDays(await customOrdersApi.calendar(start, end));
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Could not load the calendar.');
    } finally {
      setLoading(false);
    }
  }, [start, end]);

  useEffect(() => {
    load();
  }, [load]);

  const byDate = useMemo(() => {
    const map = new Map<string, CalendarDay>();
    for (const day of days) map.set(day.date, day);
    return map;
  }, [days]);

  const selectedDay = selected ? byDate.get(selected) : undefined;
  const monthLabel = cursor.toLocaleDateString('en-GB', {
    month: 'long',
    year: 'numeric',
  });

  function openFormFor(date: string) {
    setForm({ ...EMPTY_FORM, due_date: date });
    setAllowPast(date < iso(new Date()));
    setFormError('');
    setShowForm(true);
  }

  async function save() {
    setSaving(true);
    setFormError('');
    try {
      await customOrdersApi.create({
        due_date: form.due_date,
        customer_name: form.customer_name,
        description: form.description,
        source: form.source,
        status: form.status,
        customer_phone: form.customer_phone || null,
        cake_message: form.cake_message || null,
        flavour: form.flavour || null,
        size_label: form.size_label || null,
        servings: form.servings ? Number(form.servings) : null,
        quoted_total: form.quoted_total ? form.quoted_total : null,
        deposit_amount: form.deposit_amount || '0',
        admin_notes: form.admin_notes || null,
        allow_past: allowPast,
      });
      setShowForm(false);
      await load();
    } catch (err) {
      // The server owns capacity, so its refusal is the message worth showing —
      // it says which date is full and why.
      setFormError(err instanceof ApiError ? err.message : 'Could not save.');
    } finally {
      setSaving(false);
    }
  }

  async function changeStatus(order: CustomOrder, status: string) {
    try {
      await customOrdersApi.setStatus(order.id, status);
      await load();
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Could not update status.');
    }
  }

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Custom Orders</h1>
          <p className="text-sm text-gray-500">
            Every custom cake, whichever channel asked for it. Orders added here
            block the date on the website.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))}
          >
            ←
          </Button>
          <span className="min-w-[10rem] text-center font-medium">{monthLabel}</span>
          <Button
            variant="secondary"
            onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))}
          >
            →
          </Button>
          <Button variant="secondary" onClick={() => setCursor(new Date())}>
            Today
          </Button>
        </div>
      </div>

      {loadError && <LoadError message={loadError} onRetry={load} />}

      {loading ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : (
        <div className="grid grid-cols-7 gap-px bg-gray-200 border border-gray-200 rounded-lg overflow-hidden">
          {WEEKDAYS.map((label) => (
            <div key={label} className="bg-gray-50 px-2 py-1 text-xs font-medium text-gray-500 text-center">
              {label}
            </div>
          ))}
          {Array.from({ length: leadingBlanks(cursor) }).map((_, i) => (
            <div key={`blank-${i}`} className="bg-white min-h-[6rem]" />
          ))}
          {days.map((day) => {
            const isFull = day.remaining === 0;
            const dayNumber = Number(day.date.slice(-2));
            return (
              <button
                key={day.date}
                onClick={() => setSelected(day.date)}
                className={[
                  'bg-white min-h-[6rem] p-2 text-left align-top transition-colors',
                  'hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-gray-400',
                  selected === day.date ? 'ring-2 ring-inset ring-gray-900' : '',
                ].join(' ')}
              >
                <div className="flex items-start justify-between">
                  <span className="text-sm font-medium">{dayNumber}</span>
                  {day.is_blackout ? (
                    <Badge variant="neutral">Closed</Badge>
                  ) : isFull ? (
                    <Badge variant="danger">Full</Badge>
                  ) : (
                    <span className="text-xs text-gray-400">{day.remaining} free</span>
                  )}
                </div>
                <div className="mt-1 space-y-1">
                  {day.orders.slice(0, 3).map((order) => (
                    <div key={order.id} className="truncate text-xs text-gray-700">
                      {/* Name and number on one line — the number goes wherever
                          the name does; truncation keeps the calendar dense. */}
                      {order.customer_name}
                      {order.customer_phone && (
                        <span className="text-gray-400"> · {order.customer_phone}</span>
                      )}
                    </div>
                  ))}
                  {day.orders.length > 3 && (
                    <div className="text-xs text-gray-400">
                      +{day.orders.length - 3} more
                    </div>
                  )}
                </div>
              </button>
            );
          })}
        </div>
      )}

      {selectedDay && (
        <div className="border border-gray-200 rounded-lg p-4 space-y-3">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <h2 className="font-medium">
              {selectedDay.date} — {selectedDay.booked} of {selectedDay.capacity} booked
              {selectedDay.is_blackout && ` (closed: ${selectedDay.blackout_reason ?? 'no reason given'})`}
            </h2>
            <Button
              onClick={() => openFormFor(selectedDay.date)}
              disabled={selectedDay.remaining === 0 || selectedDay.is_blackout}
            >
              Add order
            </Button>
          </div>

          {selectedDay.orders.length === 0 ? (
            <p className="text-sm text-gray-500">Nothing booked for this date.</p>
          ) : (
            <ul className="divide-y divide-gray-100">
              {selectedDay.orders.map((order) => (
                <li key={order.id} className="py-3 flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{order.customer_name}</span>
                      <Badge variant={STATUS_VARIANT[order.status] ?? 'neutral'}>
                        {order.status.replace('_', ' ')}
                      </Badge>
                      <span className="text-xs text-gray-400">via {order.source}</span>
                    </div>
                    <p className="text-sm text-gray-600 mt-1">{order.description}</p>
                    {order.cake_message && (
                      <p className="text-sm text-gray-500 mt-1">
                        Message on cake: “{order.cake_message}”
                      </p>
                    )}
                    {order.customer_phone && (
                      <p className="text-xs text-gray-400 mt-1">{order.customer_phone}</p>
                    )}
                  </div>
                  <Select
                    options={STATUS_OPTIONS}
                    value={order.status}
                    onChange={(e) => changeStatus(order, e.target.value)}
                    className="w-40 shrink-0"
                  />
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {showForm && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-lg w-full max-w-2xl max-h-[90vh] overflow-y-auto p-6 space-y-4">
            <h2 className="text-lg font-semibold">Add custom order — {form.due_date}</h2>
            {allowPast && (
              <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">
                This date has passed. It will still be recorded, and it still
                counts against that day&apos;s capacity.
              </p>
            )}
            {formError && (
              <p className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2">
                {formError}
              </p>
            )}

            <div className="grid grid-cols-2 gap-3">
              <Input
                label="Customer name"
                value={form.customer_name}
                onChange={(e) => setForm({ ...form, customer_name: e.target.value })}
              />
              <Input
                label="Phone"
                value={form.customer_phone}
                onChange={(e) => setForm({ ...form, customer_phone: e.target.value })}
              />
              <Select
                label="Came from"
                options={SOURCE_OPTIONS}
                value={form.source}
                onChange={(e) => setForm({ ...form, source: e.target.value })}
              />
              <Select
                label="Status"
                options={STATUS_OPTIONS}
                value={form.status}
                onChange={(e) => setForm({ ...form, status: e.target.value })}
              />
            </div>

            <Textarea
              label="What they asked for"
              rows={3}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />

            <div className="grid grid-cols-2 gap-3">
              <Input
                label="Message on cake"
                helper="Reproduced exactly, spelling included"
                value={form.cake_message}
                onChange={(e) => setForm({ ...form, cake_message: e.target.value })}
              />
              <Input
                label="Flavour"
                value={form.flavour}
                onChange={(e) => setForm({ ...form, flavour: e.target.value })}
              />
              <Input
                label="Size"
                value={form.size_label}
                onChange={(e) => setForm({ ...form, size_label: e.target.value })}
              />
              <Input
                label="Servings"
                type="number"
                value={form.servings}
                onChange={(e) => setForm({ ...form, servings: e.target.value })}
              />
              <Input
                label="Quoted total (AED)"
                type="number"
                step="0.01"
                value={form.quoted_total}
                onChange={(e) => setForm({ ...form, quoted_total: e.target.value })}
              />
              <Input
                label="Deposit taken (AED)"
                type="number"
                step="0.01"
                value={form.deposit_amount}
                onChange={(e) => setForm({ ...form, deposit_amount: e.target.value })}
              />
            </div>

            <Textarea
              label="Internal notes"
              rows={2}
              value={form.admin_notes}
              onChange={(e) => setForm({ ...form, admin_notes: e.target.value })}
            />

            <div className="flex justify-end gap-2 pt-2">
              <Button variant="secondary" onClick={() => setShowForm(false)}>
                Cancel
              </Button>
              <Button
                onClick={save}
                loading={saving}
                disabled={!form.customer_name.trim() || !form.description.trim()}
              >
                Save
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
