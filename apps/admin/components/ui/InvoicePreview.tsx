'use client';

import { Modal } from '@/components/pos/ResourcePage';

/**
 * A lightweight in-page preview of a purchase-order invoice — an image inline,
 * or a PDF in an iframe — over a short-lived signed URL the caller has already
 * fetched. Used by the PO list (View action) and the PO detail page so the
 * invoice never has to open in a separate browser tab just to be glanced at.
 */
export function InvoicePreview({
  url,
  title,
  onClose,
}: {
  url: string;
  title: string;
  onClose: () => void;
}) {
  const isPdf = url.split('?')[0].toLowerCase().endsWith('.pdf');
  return (
    <Modal title={`Invoice — ${title}`} onClose={onClose} wide>
      <div className="space-y-3">
        {isPdf ? (
          <iframe src={url} title="Invoice" className="h-[70vh] w-full rounded border border-gray-200" />
        ) : (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={url} alt="Invoice" className="mx-auto max-h-[70vh] w-auto rounded border border-gray-200" />
        )}
        <div className="flex justify-end">
          <a href={url} target="_blank" rel="noreferrer" className="text-sm text-primary hover:underline">
            Open in new tab
          </a>
        </div>
      </div>
    </Modal>
  );
}
