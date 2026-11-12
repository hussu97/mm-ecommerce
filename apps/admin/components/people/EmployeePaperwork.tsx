'use client';

import { useState } from 'react';
import type { EmployeeDocument, EmployeeDocumentTypeSpec, EmployeeFile } from '@/lib/people-api';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui';
import { daysUntil, formatDate } from '@/components/pos/LegalEntityPaperwork';
import { EXPIRY_WARNING_DAYS } from './ExpiringDocuments';

/**
 * The pieces of the employee page that show paperwork: a status strip with
 * one tile per document type (a missing type's tile is how it is added), a
 * card per document on file, and the list of other files.
 *
 * One status vocabulary throughout, the same thresholds as the expiry popup:
 * expired, due (inside the 15-day warning), soon (within 60 days), valid, or
 * missing.
 */

export type PaperTone = 'expired' | 'due' | 'soon' | 'ok' | 'missing';

export function paperStatus(expiry: string | null | undefined): {
  tone: PaperTone;
  label: string;
} {
  if (!expiry) return { tone: 'missing', label: 'Not on file' };
  const days = daysUntil(expiry);
  if (days < 0)
    return {
      tone: 'expired',
      label: `Expired ${-days === 1 ? 'yesterday' : `${-days} days ago`}`,
    };
  if (days <= EXPIRY_WARNING_DAYS) {
    return {
      tone: 'due',
      label: days === 0 ? 'Expires today' : `Expires in ${days} day${days === 1 ? '' : 's'}`,
    };
  }
  if (days <= 60) return { tone: 'soon', label: `Expires in ${days} days` };
  return { tone: 'ok', label: `Valid until ${formatDate(expiry)}` };
}

/** "in 9 days" / "12 days ago" / "today", for beside an expiry date. */
export function relativeDays(expiry: string): string {
  const days = daysUntil(expiry);
  if (days === 0) return 'today';
  if (days > 0) return `in ${days} day${days === 1 ? '' : 's'}`;
  return `${-days} day${days === -1 ? '' : 's'} ago`;
}

/** Whether a tone asks for the super-admin's attention. */
export const needsAttention = (tone: PaperTone) => tone === 'expired' || tone === 'due' || tone === 'missing';

const TONE: Record<PaperTone, { dot: string; pill: string; tile: string; word: string }> = {
  expired: {
    dot: 'bg-red-500',
    pill: 'bg-red-50 text-red-700 border-red-200',
    tile: 'border-red-200 bg-red-50/60',
    word: 'Expired',
  },
  due: {
    dot: 'bg-red-500',
    pill: 'bg-red-50 text-red-700 border-red-200',
    tile: 'border-red-200 bg-red-50/60',
    word: 'Renew now',
  },
  soon: {
    dot: 'bg-amber-500',
    pill: 'bg-amber-50 text-amber-800 border-amber-200',
    tile: 'border-amber-200 bg-amber-50/60',
    word: 'Due soon',
  },
  ok: {
    dot: 'bg-green-500',
    pill: 'bg-green-50 text-green-700 border-green-200',
    tile: 'border-gray-200 bg-white',
    word: 'Valid',
  },
  missing: {
    dot: 'bg-gray-300',
    pill: 'bg-gray-50 text-gray-500 border-gray-200',
    tile: 'border-dashed border-gray-300 bg-white',
    word: 'Missing',
  },
};

export function StatusPill({ tone, children }: { tone: PaperTone; children?: React.ReactNode }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 border px-2 py-0.5 text-[11px] font-body uppercase tracking-wide',
        TONE[tone].pill,
      )}
    >
      <span className={cn('h-1.5 w-1.5 rounded-full', TONE[tone].dot)} aria-hidden />
      {children ?? TONE[tone].word}
    </span>
  );
}

export function IconAction({
  icon,
  label,
  onClick,
  danger = false,
  disabled = false,
}: {
  icon: string;
  label: string;
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'inline-flex h-9 w-9 items-center justify-center rounded text-gray-500 transition-colors sm:h-8 sm:w-8',
        'disabled:cursor-not-allowed disabled:opacity-30',
        danger ? 'hover:bg-red-50 hover:text-red-600' : 'hover:bg-gray-100 hover:text-primary',
      )}
    >
      <span className="material-icons text-[18px]" aria-hidden>
        {icon}
      </span>
    </button>
  );
}

function Fact({ label, children, wide = false }: { label: string; children: React.ReactNode; wide?: boolean }) {
  return (
    <div className={cn('min-w-0', wide && 'col-span-2')}>
      <dt className="text-[10px] uppercase tracking-widest text-gray-400">{label}</dt>
      <dd className="mt-0.5 break-words text-sm text-gray-800">
        {children || <span className="text-gray-300">—</span>}
      </dd>
    </div>
  );
}

const isIsoDate = (v: string) => /^\d{4}-\d{2}-\d{2}$/.test(v);

// ─── Status strip ────────────────────────────────────────────────────────────

export type TypeState = {
  spec: EmployeeDocumentTypeSpec;
  current?: EmployeeDocument;
};

/** One tile per document type: on file → jumps to its card; missing → adds it. */
export function PaperworkSummary({
  states,
  onOpen,
  onAdd,
}: {
  states: TypeState[];
  onOpen: (docType: string) => void;
  onAdd: (docType: string) => void;
}) {
  const flagged = states.filter((s) => needsAttention(paperStatus(s.current?.expiry_date).tone)).length;
  const onFile = states.filter((s) => s.current).length;
  return (
    <section className="border border-gray-200 bg-white p-4 font-body" aria-labelledby="paperwork-heading">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="paperwork-heading" className="font-display text-sm tracking-wide text-primary">
          Paperwork
        </h2>
        <p className={cn('text-xs', onFile === 0 ? 'text-gray-500' : flagged ? 'text-red-700' : 'text-green-700')}>
          {onFile === 0
            ? 'Nothing on file yet — start with the passport or visa'
            : flagged
              ? `${flagged} need${flagged === 1 ? 's' : ''} attention`
              : 'Everything is on file and valid'}
        </p>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-4">
        {states.map(({ spec, current }) => {
          const status = paperStatus(current?.expiry_date);
          return (
            <button
              key={spec.doc_type}
              type="button"
              data-testid={`status-${spec.doc_type}`}
              onClick={() => (current ? onOpen(spec.doc_type) : onAdd(spec.doc_type))}
              className={cn(
                'group flex min-h-[60px] flex-col items-start justify-center gap-1 border px-3 py-2 text-left transition-colors hover:border-primary/50',
                TONE[status.tone].tile,
              )}
            >
              <span className="flex w-full items-start gap-2">
                <span className={cn('mt-1.5 h-2 w-2 shrink-0 rounded-full', TONE[status.tone].dot)} aria-hidden />
                <span className="text-sm leading-tight text-gray-800">{spec.label}</span>
              </span>
              <span
                className={cn(
                  'pl-4 text-xs',
                  status.tone === 'expired' || status.tone === 'due'
                    ? 'text-red-700'
                    : status.tone === 'soon'
                      ? 'text-amber-800'
                      : status.tone === 'missing'
                        ? 'text-primary group-hover:underline'
                        : 'text-gray-500',
                )}
              >
                {status.tone === 'missing'
                  ? '+ Add'
                  : status.tone === 'ok'
                    ? `Until ${formatDate(current!.expiry_date)}`
                    : status.label}
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

// ─── A document on file ──────────────────────────────────────────────────────

export function DocumentCard({
  spec,
  current,
  history,
  highlighted,
  onView,
  onEdit,
  onDelete,
  onRenew,
  onCopy,
}: {
  spec: EmployeeDocumentTypeSpec;
  current: EmployeeDocument;
  history: EmployeeDocument[];
  highlighted: boolean;
  onView: (doc: EmployeeDocument) => void;
  onEdit: (doc: EmployeeDocument) => void;
  onDelete: (doc: EmployeeDocument) => void;
  onRenew: () => void;
  onCopy: (text: string) => void;
}) {
  const [showDetails, setShowDetails] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const status = paperStatus(current.expiry_date);
  const details = Object.entries(spec.details).filter(([key]) => current.details[key]);
  const extra = details.length + (current.notes ? 1 : 0);
  const urgent = status.tone === 'expired' || status.tone === 'due';
  // Renewal is the card's call to action only when it is coming up; otherwise
  // it is one of the header's icons and the card has no footer button.
  const prompting = urgent || status.tone === 'soon';

  return (
    <article
      id={`doc-${spec.doc_type}`}
      data-testid={`doc-${spec.doc_type}`}
      className={cn(
        'flex scroll-mt-24 flex-col border bg-white font-body transition-shadow',
        urgent ? 'border-red-200' : 'border-gray-200',
        highlighted && 'ring-2 ring-primary/40',
      )}
    >
      <header className="flex items-start justify-between gap-2 border-b border-gray-100 px-4 py-3">
        <div className="min-w-0">
          <h3 className="font-display text-sm tracking-wide text-primary">{spec.label}</h3>
          <div className="mt-1">
            <StatusPill tone={status.tone} />
          </div>
        </div>
        <div className="-mr-2 flex shrink-0 items-center">
          <IconAction
            icon="visibility"
            label={current.has_file ? 'View scan' : 'No scan on file'}
            disabled={!current.has_file}
            onClick={() => onView(current)}
          />
          <IconAction icon="edit" label="Edit" onClick={() => onEdit(current)} />
          {!prompting && <IconAction icon="autorenew" label="Add renewal" onClick={onRenew} />}
          <IconAction icon="delete_outline" label="Delete this record" danger onClick={() => onDelete(current)} />
        </div>
      </header>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 px-4 py-3">
        <Fact label={spec.number_label} wide>
          {current.document_number && (
            <span className="inline-flex max-w-full items-center gap-1">
              <span className="truncate font-mono text-[15px] tracking-tight">{current.document_number}</span>
              <button
                type="button"
                title="Copy"
                aria-label={`Copy ${spec.number_label.toLowerCase()}`}
                className="shrink-0 text-gray-300 hover:text-primary"
                onClick={() => onCopy(current.document_number!)}
              >
                <span className="material-icons text-[16px]" aria-hidden>content_copy</span>
              </button>
            </span>
          )}
        </Fact>
        <Fact label="Expires">
          {formatDate(current.expiry_date)}
          {status.tone !== 'ok' && (
            <span className={cn('block text-xs', urgent ? 'text-red-700' : 'text-amber-800')}>
              {relativeDays(current.expiry_date)}
            </span>
          )}
        </Fact>
        <Fact label="Issued">{current.issue_date && formatDate(current.issue_date)}</Fact>
        <Fact label={spec.authority_label} wide>
          {current.issuing_authority}
        </Fact>
      </dl>

      {extra > 0 && (
        <div className="px-4 pb-2">
          <button
            type="button"
            className="inline-flex items-center gap-1 text-xs text-gray-500 hover:text-primary"
            aria-expanded={showDetails}
            onClick={() => setShowDetails((v) => !v)}
          >
            <span className="material-icons text-[16px]" aria-hidden>{showDetails ? 'expand_less' : 'expand_more'}</span>
            {showDetails ? 'Hide details' : `Details (${extra})`}
          </button>
          {showDetails && (
            <div className="mt-2 border-t border-gray-100 pt-3">
              <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
                {details.map(([key, label]) => (
                  <Fact key={key} label={label}>
                    {isIsoDate(current.details[key]) ? formatDate(current.details[key]) : current.details[key]}
                  </Fact>
                ))}
              </dl>
              {current.notes && (
                <p className="mt-3 whitespace-pre-line bg-gray-50 px-3 py-2 text-xs text-gray-600">{current.notes}</p>
              )}
            </div>
          )}
        </div>
      )}

      {(prompting || history.length > 0) && (
        <footer className="mt-auto flex flex-wrap items-center justify-between gap-2 border-t border-gray-100 px-4 py-2">
          {history.length > 0 ? (
            <button
              type="button"
              className="inline-flex items-center gap-1 text-xs text-gray-500 hover:text-primary"
              aria-expanded={showHistory}
              onClick={() => setShowHistory((v) => !v)}
            >
              <span className="material-icons text-[16px]" aria-hidden>history</span>
              {history.length} previous
            </button>
          ) : (
            <span />
          )}
          {prompting && (
            <Button size="sm" onClick={onRenew}>
              <span className="material-icons text-[16px]" aria-hidden>autorenew</span>
              {urgent ? 'Renew now' : 'Add renewal'}
            </Button>
          )}
          {showHistory && (
            <ul className="w-full divide-y divide-gray-100 border-t border-gray-100">
              {history.map((doc) => (
                <li key={doc.id} className="flex items-center justify-between gap-2 py-1.5">
                  <div className="min-w-0 text-xs text-gray-600">
                    <span className="font-mono text-gray-800">{doc.document_number || '—'}</span>
                    <span className="block text-gray-400 sm:inline sm:pl-2">
                      {doc.issue_date ? `${formatDate(doc.issue_date)} → ` : 'until '}
                      {formatDate(doc.expiry_date)}
                    </span>
                  </div>
                  <div className="flex shrink-0">
                    <IconAction
                      icon="visibility"
                      label={doc.has_file ? 'View scan' : 'No scan on file'}
                      disabled={!doc.has_file}
                      onClick={() => onView(doc)}
                    />
                    <IconAction icon="edit" label="Edit" onClick={() => onEdit(doc)} />
                    <IconAction icon="delete_outline" label="Delete this record" danger onClick={() => onDelete(doc)} />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </footer>
      )}
    </article>
  );
}

// ─── Other files ─────────────────────────────────────────────────────────────

function fileIcon(contentType: string | null | undefined): string {
  if (contentType === 'application/pdf') return 'picture_as_pdf';
  if (contentType?.startsWith('image/')) return 'image';
  return 'description';
}

/** Show a type only when it adds something the name does not already say. */
function categoryWorthShowing(file: EmployeeFile): boolean {
  if (!file.category) return false;
  const norm = (s: string) =>
    s
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, ' ')
      .trim();
  return !norm(file.title).includes(norm(file.category));
}

export function OtherFiles({
  files,
  onAdd,
  onView,
  onEdit,
  onDelete,
}: {
  files: EmployeeFile[];
  onAdd: () => void;
  onView: (file: EmployeeFile) => void;
  onEdit: (file: EmployeeFile) => void;
  onDelete: (file: EmployeeFile) => void;
}) {
  const [query, setQuery] = useState('');
  const q = query.trim().toLowerCase();
  const shown = q
    ? files.filter((f) => [f.title, f.category, f.notes, f.original_filename].some((v) => v?.toLowerCase().includes(q)))
    : files;

  return (
    <section className="border border-gray-200 bg-white font-body" data-testid="employee-files">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 px-4 py-3">
        <h2 className="font-display text-sm tracking-wide text-primary">
          Other documents
          {files.length > 0 && <span className="ml-1.5 text-gray-400">({files.length})</span>}
        </h2>
        <div className="flex flex-1 items-center justify-end gap-2">
          {files.length > 5 && (
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search files…"
              aria-label="Search other documents"
              className="w-full max-w-[200px] border border-gray-200 px-2 py-1.5 text-xs focus:border-primary focus:outline-none"
            />
          )}
          <Button size="sm" variant="ghost" onClick={onAdd}>
            <span className="material-icons text-[16px]" aria-hidden>upload_file</span>
            Add file
          </Button>
        </div>
      </div>
      {files.length === 0 ? (
        <button
          type="button"
          onClick={onAdd}
          className="block w-full px-4 py-6 text-center text-xs text-gray-400 hover:text-primary"
        >
          Contracts, certificates, letters — anything else kept for this employee. Click to add the first.
        </button>
      ) : shown.length === 0 ? (
        <p className="px-4 py-6 text-center text-xs text-gray-400">Nothing matches “{query}”.</p>
      ) : (
        <ul className="divide-y divide-gray-100">
          {shown.map((file) => (
            <li key={file.id} className="flex items-start gap-3 px-4 py-2.5">
              <button
                type="button"
                className="mt-0.5 shrink-0 text-gray-400 hover:text-primary disabled:cursor-default disabled:hover:text-gray-400"
                disabled={!file.has_file}
                onClick={() => onView(file)}
                aria-label={`View ${file.title}`}
              >
                <span className="material-icons text-[22px]" aria-hidden>{fileIcon(file.file_content_type)}</span>
              </button>
              <div className="min-w-0 flex-1">
                <button
                  type="button"
                  className="max-w-full break-words text-left text-sm text-gray-800 hover:text-primary hover:underline disabled:no-underline disabled:hover:text-gray-800"
                  disabled={!file.has_file}
                  onClick={() => onView(file)}
                >
                  {file.title}
                </button>
                <p className="text-xs text-gray-400">
                  {categoryWorthShowing(file) && <span className="text-gray-600">{file.category} · </span>}
                  {formatDate(file.created_at.slice(0, 10))}
                  {!file.has_file && <span className="text-amber-700"> · file not uploaded</span>}
                </p>
                {file.notes && <p className="mt-1 whitespace-pre-line text-xs text-gray-600">{file.notes}</p>}
              </div>
              <div className="-mr-2 flex shrink-0">
                <IconAction icon="edit" label="Edit" onClick={() => onEdit(file)} />
                <IconAction icon="delete_outline" label="Delete" danger onClick={() => onDelete(file)} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
