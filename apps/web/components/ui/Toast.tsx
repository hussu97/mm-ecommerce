'use client';

import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/lib/utils';
import { generateId } from '@/lib/utils';

export type ToastType = 'success' | 'error' | 'info' | 'warning';

interface Toast {
  id: string;
  message: string;
  type: ToastType;
}

interface ToastContextType {
  addToast: (message: string, type?: ToastType) => void;
}

const ToastContext = createContext<ToastContextType | null>(null);

const typeConfig: Record<ToastType, { icon: string; classes: string }> = {
  success: { icon: 'check_circle', classes: 'bg-green-50 border-green-400 text-green-800' },
  error:   { icon: 'error',        classes: 'bg-red-50 border-red-400 text-red-800' },
  warning: { icon: 'warning',      classes: 'bg-yellow-50 border-yellow-400 text-yellow-800' },
  info:    { icon: 'info',         classes: 'bg-primary/5 border-primary text-primary' },
};

function ToastItem({ toast, onRemove }: { toast: Toast; onRemove: () => void }) {
  const { icon, classes } = typeConfig[toast.type];
  return (
    <div
      role="alert"
      className={cn(
        'flex items-start gap-3 px-4 py-3 border rounded-sm shadow-lg min-w-[280px] max-w-[360px]',
        'animate-in slide-in-from-right-4 duration-300',
        classes,
      )}
    >
      <span className="material-icons text-lg leading-none mt-0.5 shrink-0">{icon}</span>
      <p className="flex-1 text-sm font-body">{toast.message}</p>
      <button onClick={onRemove} className="opacity-60 hover:opacity-100 transition-opacity ml-1 shrink-0" aria-label="Dismiss">
        <span className="material-icons text-base leading-none">close</span>
      </button>
    </div>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const addToast = useCallback((message: string, type: ToastType = 'info') => {
    const id = generateId();
    setToasts(prev => [...prev, { id, message, type }]);
    setTimeout(() => removeToast(id), 4000);
  }, [removeToast]);

  return (
    <ToastContext.Provider value={{ addToast }}>
      {children}
      {mounted && createPortal(
        <div
          aria-live="polite"
          className="fixed bottom-5 right-5 z-[9999] flex flex-col gap-2 items-end"
        >
          {toasts.map(t => (
            <ToastItem key={t.id} toast={t} onRemove={() => removeToast(t.id)} />
          ))}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextType {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within ToastProvider');
  return ctx;
}
