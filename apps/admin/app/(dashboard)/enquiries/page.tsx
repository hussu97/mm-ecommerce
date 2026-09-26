'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { customOrderEnquiriesApi } from '@/lib/api';
import type { CustomOrderEnquiry } from '@/lib/types';
import { Button, Pagination, LoadError, Spinner } from '@/components/ui';
import { DataTable } from '@/components/ui/DataTable';
import { useApiList } from '@/hooks/useApiList';
import { formatDate } from '@/lib/utils';

export default function EnquiriesPage() {
  const [selected, setSelected] = useState<CustomOrderEnquiry | null>(null);

  const fetchEnquiries = useCallback(
    (page: number, perPage: number) =>
      customOrderEnquiriesApi.list({ page, per_page: perPage }),
    [],
  );

  const {
    items: enquiries, total, pages, page, perPage, setPage, setPerPage,
    loading, loadError, refetch,
  } = useApiList<CustomOrderEnquiry>({ paginate: 'server', fetch: fetchEnquiries });

  return (
    <div>
      <LoadError message={loadError} onRetry={refetch} />

      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-display text-2xl text-gray-800">Cake Enquiries</h1>
          <p className="text-xs text-gray-400 font-body mt-0.5">
            {total} custom-order {total === 1 ? 'enquiry' : 'enquiries'} from the website — leads to follow up. Convert one into a custom order once it is agreed.
          </p>
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : (
        <DataTable<CustomOrderEnquiry>
          rows={enquiries}
          rowKey={e => e.id}
          empty={
            <p className="py-16 text-center text-sm text-gray-400 font-body">No enquiries yet.</p>
          }
          actions={e => (
            <div className="flex gap-2">
              <Button variant="ghost" size="sm" className="flex-1 sm:flex-none" onClick={() => setSelected(e)}>
                View
              </Button>
              <ConvertLink enquiryId={e.id} />
            </div>
          )}
          columns={[
            {
              header: 'Received',
              render: e => <span className="text-gray-400 whitespace-nowrap">{formatDate(e.created_at)}</span>,
            },
            {
              header: 'Name',
              priority: 'primary',
              render: e => (
                <span className="text-xs font-body font-medium text-gray-800">{e.customer_name}</span>
              ),
            },
            {
              header: 'Phone',
              priority: 'secondary',
              render: e => <a href={`tel:${e.customer_phone}`} className="text-primary" dir="ltr">{e.customer_phone}</a>,
            },
            {
              header: 'Wanted by',
              render: e => (e.delivery_by ? formatDate(e.delivery_by) : '—'),
            },
            {
              header: 'Size',
              className: 'text-center',
              render: e => (e.approx_kg ? `${e.approx_kg} kg` : '—'),
            },
            {
              header: 'Photos',
              className: 'text-center',
              render: e => e.reference_image_urls.length || '—',
            },
            {
              header: 'Request',
              render: e => (
                <span className="text-gray-600 line-clamp-2 max-w-md">{e.description}</span>
              ),
            },
          ]}
        />
      )}

      <Pagination
        page={page}
        pages={pages}
        total={total}
        perPage={perPage}
        onPageChange={setPage}
        onPerPageChange={setPerPage}
        label="enquiries"
      />

      {selected && <EnquiryDetail enquiry={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

function EnquiryDetail({ enquiry, onClose }: { enquiry: CustomOrderEnquiry; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg max-h-[85vh] overflow-y-auto bg-white rounded-md shadow-xl"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start justify-between border-b border-gray-100 px-6 py-4">
          <div>
            <h2 className="font-display text-lg text-gray-800">{enquiry.customer_name}</h2>
            <p className="text-xs text-gray-400 font-body mt-0.5">
              Received {formatDate(enquiry.created_at)}
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="text-gray-400 hover:text-gray-700 text-xl leading-none"
          >
            ×
          </button>
        </div>

        <div className="px-6 py-5 space-y-4 text-sm font-body">
          <Row label="Phone">
            <a href={`tel:${enquiry.customer_phone}`} className="text-primary" dir="ltr">{enquiry.customer_phone}</a>
          </Row>
          {enquiry.approx_kg != null && <Row label="Approx. size">{enquiry.approx_kg} kg</Row>}
          <Row label="Wanted by">
            {enquiry.delivery_by ? formatDate(enquiry.delivery_by) : '—'}
            <span className="block text-xs text-gray-400 mt-0.5">A preference — confirm the real date when you reply.</span>
          </Row>
          <div>
            <p className="text-xs uppercase tracking-wider text-gray-500 mb-1">Request</p>
            <p className="whitespace-pre-wrap text-gray-800">{enquiry.description}</p>
          </div>
          {enquiry.reference_image_urls.length > 0 && (
            <div>
              <p className="text-xs uppercase tracking-wider text-gray-500 mb-2">
                Inspiration photos ({enquiry.reference_image_urls.length})
              </p>
              <div className="flex flex-wrap gap-2">
                {enquiry.reference_image_urls.map((url, i) => (
                  <a key={url} href={url} target="_blank" rel="noopener noreferrer" className="block h-24 w-24 overflow-hidden rounded border border-gray-200">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={url} alt={`Inspiration ${i + 1}`} className="h-full w-full object-cover" />
                  </a>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="border-t border-gray-100 px-6 py-3 flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>Close</Button>
          <ConvertLink enquiryId={enquiry.id} />
        </div>
      </div>
    </div>
  );
}

/**
 * Turns the lead into a custom order: the new-order form opens prefilled with
 * the enquiry's name, phone, request and wanted-by date, and the order records
 * which enquiry it came from.
 */
function ConvertLink({ enquiryId }: { enquiryId: string }) {
  return (
    <Link
      href={`/custom-orders/new?enquiry=${encodeURIComponent(enquiryId)}`}
      className="inline-flex flex-1 sm:flex-none items-center justify-center gap-1.5 text-xs px-3 py-1.5 min-h-9 font-body font-medium uppercase tracking-wider bg-primary text-white hover:opacity-90"
    >
      <span className="material-icons text-[14px]">cake</span>
      Convert
    </Link>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3">
      <span className="w-28 shrink-0 text-xs uppercase tracking-wider text-gray-500 pt-0.5">{label}</span>
      <span className="text-gray-800">{children}</span>
    </div>
  );
}
