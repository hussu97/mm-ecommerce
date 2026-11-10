/**
 * Whether the super-admin has put off the expiring-documents popup for this
 * login. Kept in sessionStorage so it lasts until the tab closes, and reset on
 * every sign-in and sign-out (see `auth-context.tsx`) so a fresh login always
 * shows the popup once — a dismissal must not outlive the login it was made in.
 */
const KEY = 'mm.expiringDocuments.dismissed';

export function isExpiryReminderDismissed(): boolean {
  try {
    return window.sessionStorage.getItem(KEY) === '1';
  } catch {
    return false;
  }
}

export function dismissExpiryReminder(): void {
  try {
    window.sessionStorage.setItem(KEY, '1');
  } catch {
    // Storage blocked (private mode): the popup simply shows again next load.
  }
}

export function resetExpiryReminder(): void {
  try {
    window.sessionStorage.removeItem(KEY);
  } catch {
    // Nothing stored, nothing to reset.
  }
}
