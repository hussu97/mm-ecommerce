/**
 * A coupon has to be judged on the same facts the order will be.
 *
 * The server refuses a new-customer code on an account, an email *or* a phone
 * that has ordered before, and `create_order` checks all three. Validating on
 * the code and the subtotal alone therefore answers a different question from
 * the one the pay button is judged on: the discount reads as applied all the
 * way down the form, and the order is refused at the last step, where a
 * customer has nothing left to do but leave.
 *
 * The second half of the same problem is the refusal that *is* fixable. "Verify
 * your phone number to use this code" is a dead end unless the thing that fixes
 * it is next to it — the only other place a number can be proved is the address
 * book, which is behind a sign-in, and a guest is precisely who this coupon is
 * for.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  validate: vi.fn(),
  addToast: vi.fn(),
  verifiedWith: vi.fn(),
}));

vi.mock('@/lib/api', () => ({
  promoApi: { validate: mocks.validate },
}));

vi.mock('@/components/ui/Toast', () => ({
  useToast: () => ({ addToast: mocks.addToast }),
}));

vi.mock('@/lib/analytics', () => ({
  analytics: { promoApplied: vi.fn(), promoFailed: vi.fn() },
}));

// A build with the Firebase vars in it. Without them the component renders
// nothing, which is the preview-deploy case the step falls back to copy for.
vi.mock('@/lib/firebase', () => ({ isFirebaseConfigured: () => true }));

// Stood in for, because the real one loads the Firebase SDK. What matters here
// is that it is rendered with the customer's number and that confirming it
// re-applies the code — not how the SMS gets sent.
vi.mock('@/components/ui/PhoneVerify', () => ({
  PhoneVerify: ({ phone, onVerified }: { phone: string; onVerified: (p: string) => void }) => (
    <button type="button" onClick={() => { mocks.verifiedWith(phone); onVerified(phone); }}>
      send-code-to-{phone}
    </button>
  ),
}));

const STRINGS: Record<string, string> = {
  'checkout.promo_placeholder': 'Promo code',
  'checkout.apply': 'Apply',
  'checkout.invalid_promo': 'Invalid code',
  'checkout.promo_error': 'Something went wrong',
  'checkout.promo_applied': 'Applied {code}',
  'checkout.remove_promo': 'Remove',
  'verify.subtitle': "We'll text you a 6-digit code.",
  'verify.enter_phone_first': 'Add your mobile number above, then apply the code again.',
};

vi.mock('@/lib/i18n/TranslationProvider', () => ({
  useTranslation: () => ({
    locale: 'en',
    t: (key: string, params?: Record<string, string | number>) => {
      let value = STRINGS[key] ?? key;
      for (const [k, v] of Object.entries(params ?? {})) value = value.replace(`{${k}}`, String(v));
      return value;
    },
  }),
}));

import { PromoCodeStep } from './PromoCodeStep';

function renderStep(over: Partial<React.ComponentProps<typeof PromoCodeStep>> = {}) {
  const onChange = vi.fn();
  render(
    <PromoCodeStep
      promoCode="WELCOME15"
      promoDiscount={0}
      promoMessage=""
      subtotal={100}
      identity={{ email: 'someone@example.com', phone: '+971501234567' }}
      onChange={onChange}
      {...over}
    />,
  );
  return { onChange };
}

describe('PromoCodeStep', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.validate.mockResolvedValue({ valid: true, discount_amount: 15, message: null });
  });

  it('validates against the same identity the order will be written under', async () => {
    renderStep();
    fireEvent.click(screen.getByText('Apply'));

    await waitFor(() => expect(mocks.validate).toHaveBeenCalled());
    expect(mocks.validate).toHaveBeenCalledWith('WELCOME15', 100, {
      email: 'someone@example.com',
      phone: '+971501234567',
    });
  });

  it('offers the OTP next to a refusal an OTP would fix', async () => {
    mocks.validate.mockResolvedValue({
      valid: false,
      discount_amount: 0,
      message: 'Verify your phone number to use this code',
      requires_phone_verification: true,
    });

    renderStep();
    fireEvent.click(screen.getByText('Apply'));

    await waitFor(() =>
      expect(screen.getByText('send-code-to-+971501234567')).toBeInTheDocument(),
    );

    // And confirming it retries the code, rather than leaving the customer to
    // work out that they should press Apply again.
    mocks.validate.mockResolvedValue({ valid: true, discount_amount: 15, message: null });
    fireEvent.click(screen.getByText('send-code-to-+971501234567'));
    await waitFor(() => expect(mocks.validate).toHaveBeenCalledTimes(2));
  });

  it('says which field to fill in when there is no number to verify yet', async () => {
    mocks.validate.mockResolvedValue({
      valid: false,
      discount_amount: 0,
      message: 'Verify your phone number to use this code',
      requires_phone_verification: true,
    });

    renderStep({ identity: { email: null, phone: null } });
    fireEvent.click(screen.getByText('Apply'));

    await waitFor(() =>
      expect(
        screen.getByText('Add your mobile number above, then apply the code again.'),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText(/send-code-to/)).not.toBeInTheDocument();
  });

  it('does not offer an OTP for a refusal an OTP cannot fix', async () => {
    mocks.validate.mockResolvedValue({
      valid: false,
      discount_amount: 0,
      message: 'This code is for new customers only',
    });

    renderStep();
    fireEvent.click(screen.getByText('Apply'));

    await waitFor(() =>
      expect(screen.getByText('This code is for new customers only')).toBeInTheDocument(),
    );
    expect(screen.queryByText(/send-code-to/)).not.toBeInTheDocument();
  });

  it('re-checks an applied code when the identity changes under it', async () => {
    // A customer applies the code, then types the phone number that turns out to
    // have ordered three times before. The discount on the summary has to stop
    // being real at that point, not at the pay button.
    const { rerender } = render(
      <PromoCodeStep
        promoCode="WELCOME15"
        promoDiscount={15}
        promoMessage=""
        subtotal={100}
        identity={{ email: null, phone: null }}
        onChange={vi.fn()}
      />,
    );

    mocks.validate.mockResolvedValue({
      valid: false,
      discount_amount: 0,
      message: 'This code is for new customers only',
    });

    rerender(
      <PromoCodeStep
        promoCode="WELCOME15"
        promoDiscount={15}
        promoMessage=""
        subtotal={100}
        identity={{ email: null, phone: '+971509999999' }}
        onChange={vi.fn()}
      />,
    );

    await waitFor(
      () =>
        expect(mocks.validate).toHaveBeenCalledWith('WELCOME15', 100, {
          email: null,
          phone: '+971509999999',
        }),
      { timeout: 2000 },
    );
  });

  it('never talks to the server when nothing is applied and nothing is typed', async () => {
    render(
      <PromoCodeStep
        promoCode=""
        promoDiscount={0}
        promoMessage=""
        subtotal={100}
        identity={{ email: 'a@b.com', phone: null }}
        onChange={vi.fn()}
      />,
    );
    await new Promise((resolve) => setTimeout(resolve, 700));
    expect(mocks.validate).not.toHaveBeenCalled();
  });
});
