'use client';

import { useState } from 'react';
import { posReportsApi, type DailySalesEmailResult } from '@/lib/pos-api';
import { Button, Textarea, Badge } from '@/components/ui';
import { useConfirm } from '@/components/ui/feedback';
import type { Window } from '../report-window';

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * Email the daily sales spreadsheet on demand.
 *
 * The same report the nightly job sends after the last branch closes — the five
 * stacked sections, one row per branch, one column per channel. This screen only
 * chooses when and to whom: it reuses the From/To pickers above (a single day for
 * the daily figure, a wider window for a range) and takes a comma-separated list
 * of recipients. Delivered trade only.
 */
export function EmailTab({ window }: { window: Window }) {
  // No default recipient: this used to ship one person's personal address in
  // source, so a hurried send mailed the figures there by default (F-ADM-3).
  const [emails, setEmails] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();
  const [result, setResult] = useState<DailySalesEmailResult>();
  const confirm = useConfirm();

  const send = async () => {
    const recipients = emails
      .split(',')
      .map((e) => e.trim())
      .filter(Boolean);

    if (!window.date_from || !window.date_to) {
      setError('Pick a date range above first.');
      return;
    }
    if (recipients.length === 0) {
      setError('Enter at least one recipient email.');
      return;
    }
    const invalid = recipients.filter((e) => !EMAIL_RE.test(e));
    if (invalid.length > 0) {
      setError(`Not a valid email: ${invalid.join(', ')}`);
      return;
    }

    // Mailing real sales figures to real inboxes is a side-effect worth a
    // second look — name the recipients and the range before it goes.
    const span =
      window.date_from === window.date_to
        ? window.date_from
        : `${window.date_from} to ${window.date_to}`;
    if (
      !(await confirm({
        title: 'Send the sales report',
        message: `Email the ${span} sales report to ${recipients.length} recipient${recipients.length === 1 ? '' : 's'}: ${recipients.join(', ')}?`,
        confirmLabel: 'Send',
      }))
    )
      return;

    setSending(true);
    setError(undefined);
    setResult(undefined);
    try {
      const res = await posReportsApi.sendDailyEmail({
        date_from: window.date_from,
        date_to: window.date_to,
        recipients,
      });
      setResult(res);
    } catch (e) {
      setError((e as Error)?.message ?? 'Could not send the report.');
    } finally {
      setSending(false);
    }
  };

  const rangeLabel =
    window.date_from === window.date_to
      ? window.date_from
      : `${window.date_from} to ${window.date_to}`;

  return (
    <div className="max-w-xl space-y-4">
      <div className="rounded-lg border border-gray-200 p-4">
        <p className="mb-1 text-sm text-gray-700 font-body">
          Send the sales spreadsheet for <strong>{rangeLabel ?? 'the selected window'}</strong> as
          an email attachment. Delivered orders only, split by branch and channel.
        </p>
        <p className="mb-3 text-xs text-gray-500 font-body">
          The nightly job sends this automatically after the last branch closes. This button is for
          a one-off or a wider date range.
        </p>

        <Textarea
          label="Recipients (comma-separated)"
          value={emails}
          onChange={(e) => setEmails(e.target.value)}
          rows={2}
          placeholder="owner@example.com, manager@example.com"
        />

        <div className="mt-3 flex items-center gap-3">
          <Button onClick={send} loading={sending} disabled={sending}>
            Send report
          </Button>
          {error && <span className="text-sm text-red-600 font-body">{error}</span>}
        </div>
      </div>

      {result && (
        <div className="rounded-lg border border-gray-200 p-4">
          <p className="mb-2 text-sm text-gray-700 font-body">
            <strong>{result.subject}</strong> — {result.rows} branch-day rows.
          </p>
          <ul className="space-y-1">
            {result.sent.map((s) => (
              <li key={s.recipient} className="flex items-center gap-2 text-sm font-body">
                <Badge variant={s.status === 'sent' ? 'success' : 'danger'}>{s.status}</Badge>
                <span className="text-gray-700">{s.recipient}</span>
                {s.error && <span className="text-xs text-red-600">{s.error}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
