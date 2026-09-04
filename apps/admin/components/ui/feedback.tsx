'use client';

import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import { cn } from '@/lib/utils';
import { Button } from './index';

/**
 * The console's two feedback surfaces: toasts and a styled confirm dialog.
 *
 * These replace the ~41 bare `alert()` / `window.confirm()` calls that used to
 * follow every mutation. The native dialogs block the whole tab, carry no
 * styling, and — for `confirm` — read identically whether the question is
 * "save?" or "delete the live delivery map?". Now:
 *
 * - `useToast()` for outcomes: `toast.error(message)` where an `alert(err)`
 *   used to be, `toast.success(...)` where an action deserves an
 *   acknowledgement.
 * - `useConfirm()` for decisions: `if (!(await confirm({ ... }))) return;`
 *   in place of `if (!confirm('...')) return;`. Destructive questions pass
 *   `danger: true` and get the red button.
 *
 * Both live under one `<FeedbackProvider>`, mounted once in
 * `app/providers.tsx`. The dialog styling matches `Modal` in
 * `components/pos/ResourcePage.tsx`; it is re-implemented here rather than
 * imported to keep `components/ui` free of an import cycle.
 */

// ─── Types ────────────────────────────────────────────────────────────────────

interface ToastItem {
  id: number;
  kind: 'success' | 'error';
  message: string;
}

export interface ConfirmOptions {
  /** Dialog heading. Defaults to "Are you sure?". */
  title?: string;
  /** The question, phrased with its consequence — same copy the old `confirm()` carried. */
  message: string;
  /** Confirm button label. Defaults to "Confirm". */
  confirmLabel?: string;
  /** Cancel button label. Defaults to "Cancel". */
  cancelLabel?: string;
  /** Destructive action: renders the confirm button in the danger variant. */
  danger?: boolean;
}

interface FeedbackContextValue {
  toastSuccess: (message: string) => void;
  toastError: (message: string) => void;
  confirm: (options: ConfirmOptions) => Promise<boolean>;
}

const FeedbackContext = createContext<FeedbackContextValue | null>(null);

// ─── Provider ─────────────────────────────────────────────────────────────────

interface PendingConfirm {
  options: ConfirmOptions;
  resolve: (answer: boolean) => void;
}

export function FeedbackProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const push = useCallback((kind: ToastItem['kind'], message: string) => {
    const id = nextId.current++;
    setToasts(prev => [...prev, { id, kind, message }]);
    // Errors linger longer: they are the ones somebody needs time to read.
    setTimeout(() => dismiss(id), kind === 'error' ? 6000 : 3500);
  }, [dismiss]);

  const toastSuccess = useCallback((message: string) => push('success', message), [push]);
  const toastError = useCallback((message: string) => push('error', message), [push]);

  const confirm = useCallback((options: ConfirmOptions) => {
    return new Promise<boolean>(resolve => {
      setPending(current => {
        // A second question while one is open answers the first with "no"
        // rather than silently dropping either of them.
        current?.resolve(false);
        return { options, resolve };
      });
    });
  }, []);

  function answer(value: boolean) {
    pending?.resolve(value);
    setPending(null);
  }

  return (
    <FeedbackContext.Provider value={{ toastSuccess, toastError, confirm }}>
      {children}

      {/* Toast stack */}
      {toasts.length > 0 && (
        <div className="fixed bottom-4 right-4 z-[60] flex flex-col gap-2 max-w-sm">
          {toasts.map(t => (
            <div
              key={t.id}
              role={t.kind === 'error' ? 'alert' : 'status'}
              className={cn(
                'flex items-start gap-2 border px-3 py-2.5 shadow-md bg-white text-sm font-body',
                t.kind === 'error'
                  ? 'border-red-200 bg-red-50 text-red-700'
                  : 'border-green-200 bg-green-50 text-green-700',
              )}
            >
              <span className="material-icons text-[16px] mt-0.5">
                {t.kind === 'error' ? 'error_outline' : 'check_circle'}
              </span>
              <span className="flex-1">{t.message}</span>
              <button
                onClick={() => dismiss(t.id)}
                className="text-current opacity-50 hover:opacity-100"
                aria-label="Dismiss"
              >
                <span className="material-icons text-[16px]">close</span>
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Confirm dialog */}
      {pending && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl">
            <h2 className="font-display text-base text-primary mb-3">
              {pending.options.title ?? 'Are you sure?'}
            </h2>
            <p className="text-sm font-body text-gray-600">{pending.options.message}</p>
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="secondary" onClick={() => answer(false)}>
                {pending.options.cancelLabel ?? 'Cancel'}
              </Button>
              <Button
                variant={pending.options.danger ? 'danger' : 'primary'}
                onClick={() => answer(true)}
              >
                {pending.options.confirmLabel ?? 'Confirm'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </FeedbackContext.Provider>
  );
}

// ─── Hooks ────────────────────────────────────────────────────────────────────

function useFeedback(): FeedbackContextValue {
  const ctx = useContext(FeedbackContext);
  if (!ctx) throw new Error('FeedbackProvider is missing from the tree (see app/providers.tsx).');
  return ctx;
}

/** Non-blocking outcome feedback. `toast.error(...)` replaces `alert(...)`. */
export function useToast() {
  const { toastSuccess, toastError } = useFeedback();
  // The wrapper object must be referentially stable: `toast` is a legitimate
  // dependency of `useCallback`/`useEffect` in consumers (e.g. a component that
  // loads on mount and toasts on failure). Returning a fresh `{...}` each render
  // turned those effects into infinite re-fire loops (the catalog-sync hours
  // editor hammered GET /catalog-sync/branches/{id}/hours forever). The inner
  // functions are already memoised by the provider, so this memo never churns.
  return useMemo(
    () => ({ success: toastSuccess, error: toastError }),
    [toastSuccess, toastError],
  );
}

/**
 * A promise-shaped confirm: `if (!(await confirm({ message, danger: true }))) return;`
 * Resolves `true` on confirm, `false` on cancel.
 */
export function useConfirm() {
  return useFeedback().confirm;
}
