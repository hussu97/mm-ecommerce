'use client';

/**
 * A file shown in a pop-up over the page — a scan, a PDF, a photo, a logo —
 * with a Download button, instead of a new tab the user has to find their way
 * back from.
 *
 * Private documents arrive as two signed links from the API: `url` to show
 * inline and `download_url`, signed with `Content-Disposition: attachment`, to
 * save. That second link is what makes Download work at all — the files live on
 * storage.googleapis.com, and a browser ignores `<a download>` on another
 * origin. A public image with no such link (a receipt logo) asks the API to
 * sign one when Download is pressed — the public bucket ignores the header on
 * an unsigned URL — and falls back to opening it if the API won't (a pasted
 * URL from somewhere else).
 */

import { useEffect, useId, useState } from 'react';
import { ApiError, uploadsApi } from '@/lib/api';
import { Button, Spinner } from '@/components/ui';

export interface PreviewFile {
  url: string;
  /** A link that saves rather than shows. Absent for a public file. */
  download_url?: string | null;
  filename?: string | null;
  content_type?: string | null;
}

/** What to preview: a file in hand, or a call that signs one on demand. */
export type PreviewSource = PreviewFile | (() => Promise<PreviewFile>);

interface PreviewRequest {
  title: string;
  source: PreviewSource;
}

/** The extension in a URL's path (a signed URL carries a long query string). */
function extensionOf(url: string): string {
  try {
    const path = new URL(url).pathname;
    const dot = path.lastIndexOf('.');
    return dot > path.lastIndexOf('/') ? path.slice(dot + 1).toLowerCase() : '';
  } catch {
    return '';
  }
}

function kindOf(file: PreviewFile): 'image' | 'pdf' | 'other' {
  const type = file.content_type ?? '';
  if (type.startsWith('image/')) return 'image';
  if (type === 'application/pdf') return 'pdf';
  const ext = extensionOf(file.url) || (file.filename?.split('.').pop() ?? '').toLowerCase();
  if (['jpg', 'jpeg', 'png', 'webp', 'gif', 'svg'].includes(ext)) return 'image';
  if (ext === 'pdf') return 'pdf';
  return 'other';
}

function filenameOf(file: PreviewFile, title: string): string {
  if (file.filename) return file.filename;
  try {
    const last = new URL(file.url).pathname.split('/').pop();
    if (last) return decodeURIComponent(last);
  } catch {
    /* fall through */
  }
  return title;
}

/** One preview. Mounted fresh per file (see `useFilePreview`), so its state
 *  starts clean without an effect resetting it. */
function FilePreview({ title, source, onClose }: PreviewRequest & { onClose: () => void }) {
  const titleId = useId();
  const [file, setFile] = useState<PreviewFile | null>(typeof source === 'function' ? null : source);
  const [error, setError] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (typeof source !== 'function') return;
    let live = true;
    source()
      .then((f) => live && setFile(f))
      .catch((err) => live && setError(err instanceof ApiError ? err.message : 'Could not open the file.'));
    return () => {
      live = false;
    };
  }, [source]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = overflow;
    };
  }, [onClose]);

  const kind = file ? kindOf(file) : null;
  const filename = file ? filenameOf(file, title) : '';

  async function download() {
    if (!file) return;
    let link = file.download_url;
    if (!link) {
      setSaving(true);
      link = await uploadsApi
        .imageDownloadLink(file.url, title)
        .then((r) => r.download_url)
        .catch(() => null);
      setSaving(false);
    }
    // Content-Disposition: attachment — the browser saves it and the page stays put.
    if (link) window.location.assign(link);
    else window.open(file.url, '_blank', 'noopener');
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-2 sm:p-6"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      data-testid="file-preview"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="flex h-full max-h-[calc(100dvh-1rem)] w-full max-w-5xl flex-col overflow-hidden rounded-lg bg-white shadow-xl sm:max-h-[calc(100dvh-3rem)]">
        <div className="flex items-center gap-3 border-b border-gray-100 px-4 py-3">
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="truncate font-display text-base text-primary">
              {title}
            </h2>
            {filename && filename !== title && (
              <p className="truncate text-xs font-body text-gray-400">{filename}</p>
            )}
          </div>
          <Button size="sm" variant="secondary" onClick={() => void download()} disabled={!file} loading={saving}>
            <span className="material-icons mr-1 align-[-3px] text-[16px]">download</span>
            Download
          </Button>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-700" aria-label="Close">
            <span className="material-icons text-[22px]">close</span>
          </button>
        </div>

        <div className="relative flex min-h-0 flex-1 items-center justify-center bg-gray-100">
          {error ? (
            <p className="px-6 text-center text-sm font-body text-red-600">{error}</p>
          ) : !file ? (
            <Spinner />
          ) : kind === 'pdf' ? (
            <iframe src={file.url} title={title} className="h-full w-full border-0 bg-white" />
          ) : kind === 'image' ? (
            <>
              {!loaded && (
                <div className="absolute inset-0 flex items-center justify-center">
                  <Spinner />
                </div>
              )}
              {/* A signed storage URL — next/image would proxy and cache it. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={file.url}
                alt={title}
                onLoad={() => setLoaded(true)}
                onError={() => setError('Could not load the image.')}
                className="max-h-full max-w-full object-contain p-2"
              />
            </>
          ) : (
            <div className="px-6 text-center font-body">
              <p className="text-sm text-gray-600">This file can’t be previewed here.</p>
              <p className="mt-1 text-xs text-gray-400">Download it to open it.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * One preview at a time: `preview(title, source)` opens it, `node` renders it
 * (null when closed) — put `node` once anywhere in the page; it is a fixed
 * overlay. `source` is a file in hand or a call that signs one when opened.
 */
export function useFilePreview() {
  const [request, setRequest] = useState<(PreviewRequest & { key: number }) | null>(null);
  return {
    preview: (title: string, source: PreviewSource) => setRequest({ title, source, key: Date.now() }),
    node: request ? (
      <FilePreview key={request.key} title={request.title} source={request.source} onClose={() => setRequest(null)} />
    ) : null,
  };
}
