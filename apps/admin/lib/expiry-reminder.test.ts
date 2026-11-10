import { describe, expect, it } from 'vitest';
import { dismissExpiryReminder, isExpiryReminderDismissed, resetExpiryReminder } from './expiry-reminder';

describe('expiring-documents reminder', () => {
  it('stays dismissed for the login, and a fresh login shows it again', () => {
    resetExpiryReminder();
    expect(isExpiryReminderDismissed()).toBe(false);
    dismissExpiryReminder();
    expect(isExpiryReminderDismissed()).toBe(true);
    resetExpiryReminder(); // what sign-in and sign-out do
    expect(isExpiryReminderDismissed()).toBe(false);
  });
});
