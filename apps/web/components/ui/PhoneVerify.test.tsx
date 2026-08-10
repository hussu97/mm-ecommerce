import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

/**
 * These tests are about money.
 *
 * Every assertion below is a claim about how many times `signInWithPhoneNumber`
 * is allowed to run, because each run is a billed SMS. The component shipped for
 * three days with an unthrottled "Resend code" and no check for an existing
 * proof, and cost $4.04 across ~45 messages without completing a single
 * verification. What is tested here is the absence of that.
 */

const signInWithPhoneNumber = vi.fn();
const phoneVerified = vi.fn();
const verifyPhone = vi.fn();

vi.mock('firebase/auth', () => ({
  signInWithPhoneNumber: (...args: unknown[]) => signInWithPhoneNumber(...args),
  RecaptchaVerifier: class {
    clear() {}
  },
}));

vi.mock('@/lib/firebase', () => ({
  isFirebaseConfigured: () => true,
  getFirebaseAuth: async () => ({}),
  getRecaptchaVerifier: async () => ({ clear: () => {} }),
}));

vi.mock('@/lib/api', () => ({
  authApi: {
    phoneVerified: (...args: unknown[]) => phoneVerified(...args),
    verifyPhone: (...args: unknown[]) => verifyPhone(...args),
  },
}));

// Turnstile off by default; the one test that cares turns it on.
const turnstileEnabled = { value: false };
vi.mock('@/components/ui/Turnstile', () => ({
  isTurnstileEnabled: () => turnstileEnabled.value,
  Turnstile: () => null,
}));

vi.mock('@/lib/i18n/TranslationProvider', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));
vi.mock('@/lib/i18n/fallback', () => ({
  withFallback: (_t: unknown, _k: string, english: string) => english,
}));
vi.mock('@/components/ui/Icon', () => ({ Icon: () => null }));

vi.mock('@/lib/analytics', () => ({
  analytics: {
    phoneVerifyStarted: vi.fn(),
    phoneVerifySent: vi.fn(),
    phoneVerifySendFailed: vi.fn(),
    phoneVerifyResent: vi.fn(),
    phoneVerifySucceeded: vi.fn(),
    phoneVerifyFailed: vi.fn(),
  },
}));

const { PhoneVerify } = await import('./PhoneVerify');

const PHONE = '+971501234567';

beforeEach(() => {
  vi.clearAllMocks();
  turnstileEnabled.value = false;
  phoneVerified.mockResolvedValue({ phone: PHONE, verified: false });
  signInWithPhoneNumber.mockResolvedValue({ confirm: vi.fn() });
});

afterEach(() => {
  vi.useRealTimers();
});

/** Click "Send code" and let the two awaits inside `send` settle. */
async function sendOnce() {
  fireEvent.click(screen.getByRole('button', { name: /send code/i }));
  await waitFor(() =>
    expect(screen.getByRole('button', { name: /confirm/i })).toBeInTheDocument(),
  );
}

describe('PhoneVerify — what it costs', () => {
  it('asks whether the number is already proved before buying an SMS', async () => {
    phoneVerified.mockResolvedValue({ phone: PHONE, verified: true });
    const onVerified = vi.fn();
    render(<PhoneVerify phone={PHONE} onVerified={onVerified} />);

    fireEvent.click(screen.getByRole('button', { name: /send code/i }));

    await waitFor(() => expect(onVerified).toHaveBeenCalledWith(PHONE));
    // The whole point: no message was sent.
    expect(signInWithPhoneNumber).not.toHaveBeenCalled();
    expect(screen.getByText('Verified')).toBeInTheDocument();
  });

  it('hands back the number the server returned, not the one typed', async () => {
    phoneVerified.mockResolvedValue({ phone: '+971509999999', verified: true });
    const onVerified = vi.fn();
    render(<PhoneVerify phone={PHONE} onVerified={onVerified} />);

    fireEvent.click(screen.getByRole('button', { name: /send code/i }));
    await waitFor(() => expect(onVerified).toHaveBeenCalledWith('+971509999999'));
  });

  it('still sends when the already-verified check fails', async () => {
    phoneVerified.mockRejectedValue(new Error('network'));
    render(<PhoneVerify phone={PHONE} onVerified={vi.fn()} />);

    await sendOnce();
    // Fails open — a check we could not make must not block the customer.
    expect(signInWithPhoneNumber).toHaveBeenCalledTimes(1);
  });

  it('refuses a second send until the cooldown expires', async () => {
    render(<PhoneVerify phone={PHONE} onVerified={vi.fn()} />);
    await sendOnce();

    const resend = screen.getByRole('button', { name: /resend in/i });
    expect(resend).toBeDisabled();

    // The old bug: this loop used to cost $0.45.
    for (let i = 0; i < 5; i++) fireEvent.click(resend);
    expect(signInWithPhoneNumber).toHaveBeenCalledTimes(1);
  });

  it('counts the cooldown down and re-enables the button at zero', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<PhoneVerify phone={PHONE} onVerified={vi.fn()} />);
    await sendOnce();

    expect(screen.getByRole('button', { name: /resend in 60s/i })).toBeDisabled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^resend code$/i })).toBeEnabled(),
    );
  });

  it('stops offering to send after the ceiling, however long the customer waits', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<PhoneVerify phone={PHONE} onVerified={vi.fn()} />);
    await sendOnce();

    for (let i = 1; i < 3; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000);
      });
      fireEvent.click(await screen.findByRole('button', { name: /^resend code$/i }));
      await waitFor(() => expect(signInWithPhoneNumber).toHaveBeenCalledTimes(i + 1));
    }

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });

    expect(
      screen.queryByRole('button', { name: /resend/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/all the codes we can send/i)).toBeInTheDocument();
    expect(signInWithPhoneNumber).toHaveBeenCalledTimes(3);
  });

  it('cools down after a failed send too, so the retry does not hammer Firebase', async () => {
    signInWithPhoneNumber.mockRejectedValue(new Error('auth/too-many-requests'));
    render(<PhoneVerify phone={PHONE} onVerified={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: /send code/i }));
    await screen.findByRole('alert');

    const button = screen.getByRole('button', { name: /resend in/i });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(signInWithPhoneNumber).toHaveBeenCalledTimes(1);
  });

  it('will not buy an SMS before Turnstile has answered', async () => {
    turnstileEnabled.value = true;
    render(<PhoneVerify phone={PHONE} onVerified={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: /send code/i }));

    await screen.findByRole('alert');
    expect(signInWithPhoneNumber).not.toHaveBeenCalled();
    // And nothing was spent on the lookup either.
    expect(phoneVerified).not.toHaveBeenCalled();
  });
});
