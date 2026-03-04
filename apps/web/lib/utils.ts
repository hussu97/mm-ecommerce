export function cn(...classes: (string | undefined | null | false)[]): string {
  return classes.filter(Boolean).join(' ');
}

export function generateId(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

export function formatPrice(amount: number | string): string {
  return `${Number(amount).toFixed(2)} AED`;
}
