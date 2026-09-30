import { describe, expect, it } from 'vitest';
import { humaniseTemplateKey, templateOptions } from './email-templates';

describe('email template labels', () => {
  it('makes an unnamed key readable rather than showing it raw', () => {
    expect(humaniseTemplateKey('inventory_report_submitted')).toBe('Inventory report submitted');
    expect(humaniseTemplateKey('report')).toBe('Report');
    expect(humaniseTemplateKey('')).toBe('');
  });

  it("keeps the backend's options and adds keys on screen it did not name", () => {
    const registered = [{ value: 'welcome', label: 'Welcome' }];
    expect(templateOptions(registered, ['welcome', 'zeta_email', 'alpha_email', 'zeta_email'])).toEqual([
      { value: 'welcome', label: 'Welcome' },
      { value: 'alpha_email', label: 'Alpha email' },
      { value: 'zeta_email', label: 'Zeta email' },
    ]);
  });
});
