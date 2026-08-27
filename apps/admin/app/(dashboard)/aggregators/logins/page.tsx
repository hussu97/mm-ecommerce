'use client';

import { useCallback, useEffect, useState } from 'react';

import { aggregatorAccountsApi, ApiError } from '@/lib/api';
import type { Schemas } from '@mm/types';
import { Badge, Button, Input, LoadError, Select, Spinner } from '@/components/ui';
import { DataTable, type DataColumn } from '@/components/ui/DataTable';
import { Modal } from '@/components/pos/ResourcePage';
import { useToast } from '@/components/ui/feedback';
import { AggregatorTabs } from '../AggregatorTabs';

type AccountRow = Schemas['AggregatorAccountPublic'];
type AccountInput = Schemas['AggregatorAccountPush'];
type LoginMethod = NonNullable<AccountInput['login_method']>;

const CHANNELS = ['careem', 'deliveroo', 'talabat', 'noon', 'keeta'] as const;

const DEFAULT_METHOD: Record<(typeof CHANNELS)[number], LoginMethod> = {
  careem: 'manual',
  deliveroo: 'email_password',
  talabat: 'email_password_otp',
  noon: 'email_otp',
  keeta: 'email_password',
};

const METHOD_OPTIONS: { value: LoginMethod; label: string }[] = [
  { value: 'email_password', label: 'Email + password — no OTP' },
  { value: 'email_otp', label: 'Email + OTP (no portal password)' },
  { value: 'email_password_otp', label: 'Email + password + OTP' },
  { value: 'sso', label: 'SSO / identity redirect' },
  { value: 'manual', label: 'Manual (headed login only)' },
];

const METHOD_LABEL: Record<string, string> = Object.fromEntries(
  METHOD_OPTIONS.map(o => [o.value, o.label]),
);

function channelName(code: string): string {
  return code === 'noon' ? 'noon' : code.charAt(0).toUpperCase() + code.slice(1);
}

function methodNeedsOtp(method: string): boolean {
  return method === 'email_otp' || method === 'email_password_otp';
}

function methodNeedsPassword(method: string): boolean {
  return method === 'email_password' || method === 'email_password_otp';
}

interface FormState {
  channel: (typeof CHANNELS)[number];
  login_method: LoginMethod;
  email: string;
  password: string;
  org_id: string;
  mailbox_host: string;
  mailbox_port: string;
  mailbox_username: string;
  mailbox_password: string;
  mailbox_folder: string;
  mailbox_sender_filter: string;
  mailbox_subject_filter: string;
  clear_mailbox: boolean;
}

function emptyForm(channel: (typeof CHANNELS)[number], existing?: AccountRow): FormState {
  const mailbox = existing?.mailbox;
  return {
    channel,
    login_method: (existing?.login_method as LoginMethod) || DEFAULT_METHOD[channel],
    email: existing?.email ?? '',
    password: '',
    org_id: existing?.extras && typeof existing.extras.org_id === 'string' ? existing.extras.org_id : '',
    mailbox_host: mailbox?.host ?? '',
    mailbox_port: mailbox?.port != null ? String(mailbox.port) : '993',
    mailbox_username: mailbox?.username ?? '',
    mailbox_password: '',
    mailbox_folder: mailbox?.folder || 'INBOX',
    mailbox_sender_filter: mailbox?.sender_filter ?? '',
    mailbox_subject_filter: mailbox?.subject_filter ?? '',
    clear_mailbox: false,
  };
}

export default function LoginsPage() {
  const toast = useToast();
  const [accounts, setAccounts] = useState<AccountRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [form, setForm] = useState<FormState | null>(null);
  const [saving, setSaving] = useState(false);

  const byChannel = new Map(accounts.map(a => [a.channel, a]));
  const rows: AccountRow[] = CHANNELS.map(channel => {
    const stored = byChannel.get(channel);
    if (stored) return stored;
    return {
      channel,
      account_ref: '',
      login_method: DEFAULT_METHOD[channel],
      otp_required: methodNeedsOtp(DEFAULT_METHOD[channel]),
      email: '',
      has_password: false,
      has_mailbox: false,
      mailbox: null,
      extras: {},
      updated_at: null,
    };
  });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await aggregatorAccountsApi.list();
      setAccounts(data);
      setLoadError('');
    } catch (e) {
      setAccounts([]);
      setLoadError(e instanceof Error ? e.message : 'Could not load login recipes');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function openEdit(channel: (typeof CHANNELS)[number]) {
    setForm(emptyForm(channel, byChannel.get(channel)));
  }

  async function save() {
    if (!form) return;
    if (methodNeedsPassword(form.login_method) && !form.email.trim()) {
      toast.error('This login method needs the portal email.');
      return;
    }
    const existing = byChannel.get(form.channel);
    if (methodNeedsPassword(form.login_method) && !form.password && !existing?.has_password) {
      toast.error('This login method needs a portal password (first save).');
      return;
    }
    setSaving(true);
    try {
      const extras: Record<string, string> = {};
      if (form.org_id.trim()) extras.org_id = form.org_id.trim();
      const body: AccountInput = {
        channel: form.channel,
        account_ref: '',
        login_method: form.login_method,
        email: form.email.trim() || null,
        extras,
        clear_mailbox: form.clear_mailbox,
      };
      if (form.password) body.password = form.password;
      if (!form.clear_mailbox && methodNeedsOtp(form.login_method)) {
        const port = Number.parseInt(form.mailbox_port, 10);
        body.mailbox = {
          host: form.mailbox_host.trim() || null,
          port: Number.isFinite(port) ? port : 993,
          username: form.mailbox_username.trim() || null,
          folder: form.mailbox_folder.trim() || 'INBOX',
          sender_filter: form.mailbox_sender_filter.trim() || null,
          subject_filter: form.mailbox_subject_filter.trim() || null,
        };
        if (form.mailbox_password) body.mailbox.password = form.mailbox_password;
      }
      await aggregatorAccountsApi.upsert(body);
      toast.success(`${channelName(form.channel)} login saved.`);
      setForm(null);
      await load();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Could not save the login recipe.');
    } finally {
      setSaving(false);
    }
  }

  const columns: DataColumn<AccountRow>[] = [
    {
      header: 'Channel',
      priority: 'primary',
      render: r => <span className="font-medium text-gray-800">{channelName(r.channel)}</span>,
    },
    {
      header: 'Login method',
      render: r => <span className="text-sm text-gray-700">{METHOD_LABEL[r.login_method] ?? r.login_method}</span>,
    },
    {
      header: 'OTP',
      render: r =>
        r.otp_required ? (
          <Badge variant="warning">OTP</Badge>
        ) : (
          <Badge variant="neutral">No OTP</Badge>
        ),
    },
    {
      header: 'Portal email',
      render: r =>
        r.email ? (
          <span className="text-sm text-gray-700">{r.email}</span>
        ) : (
          <span className="text-gray-300">Not set</span>
        ),
    },
    {
      header: 'Password',
      render: r =>
        r.has_password ? (
          <Badge variant="success">Stored</Badge>
        ) : (
          <span className="text-gray-300">—</span>
        ),
    },
    {
      header: 'OTP mailbox',
      render: r =>
        r.has_mailbox ? (
          <span className="text-xs text-gray-600">
            {r.mailbox?.username || r.mailbox?.host}
          </span>
        ) : r.otp_required ? (
          <Badge variant="warning">Needed</Badge>
        ) : (
          <span className="text-gray-300">—</span>
        ),
    },
    {
      header: 'Actions',
      className: 'text-right',
      render: r => (
        <Button variant="ghost" size="sm" onClick={() => openEdit(r.channel as (typeof CHANNELS)[number])}>
          {r.email || r.has_password ? 'Edit' : 'Add'}
        </Button>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <AggregatorTabs />

      <div>
        <h1 className="font-display text-2xl text-gray-800">Logins</h1>
        <p className="text-xs text-gray-400 font-body mt-0.5">
          How the worker signs in to each marketplace. Passwords are stored sealed and never shown again.
        </p>
      </div>

      {loadError && <LoadError message={loadError} onRetry={load} />}

      {loading ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : (
        <DataTable<AccountRow>
          columns={columns}
          rows={rows}
          rowKey={r => r.channel}
          empty={
            <p className="py-16 text-center text-sm text-gray-400 font-body">
              No aggregator channels.
            </p>
          }
        />
      )}

      {form && (
        <Modal title={`${channelName(form.channel)} login`} onClose={() => setForm(null)}>
          <div className="space-y-4">
            <Select
              label="Login method"
              value={form.login_method}
              onChange={e =>
                setForm({ ...form, login_method: e.target.value as LoginMethod })
              }
              options={METHOD_OPTIONS}
            />
            <Input
              label="Portal email"
              type="email"
              autoComplete="off"
              value={form.email}
              onChange={e => setForm({ ...form, email: e.target.value })}
              placeholder="The address the marketplace login form uses"
            />
            {methodNeedsPassword(form.login_method) && (
              <Input
                label="Portal password"
                type="password"
                autoComplete="new-password"
                value={form.password}
                onChange={e => setForm({ ...form, password: e.target.value })}
                placeholder={
                  byChannel.get(form.channel)?.has_password
                    ? 'Leave blank to keep the stored password'
                    : 'Required on first save'
                }
                helper={
                  byChannel.get(form.channel)?.has_password
                    ? 'A password is already stored. Type a new one only to replace it.'
                    : undefined
                }
              />
            )}
            <Input
              label="Portal org / restaurant id"
              value={form.org_id}
              onChange={e => setForm({ ...form, org_id: e.target.value })}
              placeholder="Optional — Deliveroo orgId, Noon restaurant code…"
            />

            {methodNeedsOtp(form.login_method) && (
              <div className="space-y-3 border-t border-gray-100 pt-4">
                <p className="text-xs font-medium uppercase tracking-wider text-gray-600">
                  OTP mailbox
                </p>
                <p className="text-xs text-gray-400 font-body">
                  IMAP details for the inbox that receives the one-time code. Hotmail /
                  Outlook: <code>imap-mail.outlook.com</code>, port 993. The worker
                  reads the OTP from here so a headed login is not required every time.
                </p>
                <Input
                  label="IMAP host"
                  value={form.mailbox_host}
                  onChange={e => setForm({ ...form, mailbox_host: e.target.value })}
                  placeholder="imap-mail.outlook.com"
                />
                <Input
                  label="Port"
                  inputMode="numeric"
                  value={form.mailbox_port}
                  onChange={e => setForm({ ...form, mailbox_port: e.target.value })}
                />
                <Input
                  label="Mailbox username"
                  autoComplete="off"
                  value={form.mailbox_username}
                  onChange={e => setForm({ ...form, mailbox_username: e.target.value })}
                  placeholder="Usually the same as the portal email"
                />
                <Input
                  label="Mailbox password"
                  type="password"
                  autoComplete="new-password"
                  value={form.mailbox_password}
                  onChange={e => setForm({ ...form, mailbox_password: e.target.value })}
                  placeholder={
                    form.clear_mailbox
                      ? 'Cleared on save'
                      : byChannel.get(form.channel)?.mailbox?.has_password
                        ? 'Leave blank to keep the stored password'
                        : 'App password, not the Hotmail login if 2FA is on'
                  }
                />
                <Input
                  label="Folder"
                  value={form.mailbox_folder}
                  onChange={e => setForm({ ...form, mailbox_folder: e.target.value })}
                  placeholder="INBOX"
                />
                <Input
                  label="Sender filter"
                  value={form.mailbox_sender_filter}
                  onChange={e => setForm({ ...form, mailbox_sender_filter: e.target.value })}
                  placeholder="Optional — e.g. noon, no reply"
                  helper="Only mail whose From contains this text is treated as the OTP."
                />
                <Input
                  label="Subject filter"
                  value={form.mailbox_subject_filter}
                  onChange={e => setForm({ ...form, mailbox_subject_filter: e.target.value })}
                  placeholder="Optional — e.g. verify, partner portal"
                />
                <label className="flex items-center gap-2 text-sm font-body text-gray-700">
                  <input
                    type="checkbox"
                    checked={form.clear_mailbox}
                    onChange={e => setForm({ ...form, clear_mailbox: e.target.checked })}
                    className="h-4 w-4 accent-primary"
                  />
                  <span>Remove linked mailbox</span>
                </label>
              </div>
            )}

            <div className="flex justify-end gap-2 pt-1">
              <Button variant="secondary" onClick={() => setForm(null)} disabled={saving}>
                Cancel
              </Button>
              <Button onClick={() => void save()} loading={saving}>
                Save login
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
