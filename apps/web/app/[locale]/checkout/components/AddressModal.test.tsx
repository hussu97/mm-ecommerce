/**
 * The address sheet answers two different questions, and has to know which.
 *
 * "Change my address" wants the list of saved addresses. "Verify your mobile
 * number" wants the panel that sends the SMS — which is on the *form*, one
 * screen further in, behind an Edit button that a customer looking for a
 * verification field has no reason to press.
 *
 * Opening on the list for both is the dead click this file exists to prevent:
 * the customer taps the address they had already chosen, which re-selects it
 * and closes the sheet, and they are back on the checkout with the same warning
 * and nothing to show for three taps.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  verifiedWith: vi.fn(),
  firebaseOn: vi.fn(() => true),
}));

vi.mock('@/lib/api', () => ({
  addressesApi: {
    list: vi.fn().mockResolvedValue([]),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('@/lib/analytics', () => ({
  analytics: {
    addressSaved: vi.fn(),
    addressSaveFailed: vi.fn(),
    addressDeleted: vi.fn(),
    savedAddressSelected: vi.fn(),
  },
  failureReason: () => 'unknown',
}));

vi.mock('@/lib/geocode', () => ({ reverseGeocode: vi.fn().mockResolvedValue(null) }));

// A build with the Firebase vars in it. Without them there is no way to send a
// code, and the card that offers one must not be drawn — see `firebaseOff`.
vi.mock('@/lib/firebase', () => ({ isFirebaseConfigured: () => mocks.firebaseOn() }));

// The map pulls in the Google SDK and answers nothing this file asks.
vi.mock('@/components/ui/LocationPicker', () => ({
  LocationPicker: () => <div data-testid="map" />,
}));

// Stood in for: what matters is that it is on screen with the customer's
// number, and that confirming it hands the number up.
vi.mock('@/components/ui/PhoneVerify', () => ({
  PhoneVerify: ({ phone, onVerified }: { phone: string; onVerified: (p: string) => void }) => (
    <button type="button" onClick={() => { mocks.verifiedWith(phone); onVerified(phone); }}>
      send-code-to-{phone}
    </button>
  ),
}));

vi.mock('@/lib/i18n/TranslationProvider', () => ({
  useTranslation: () => ({ locale: 'en', t: (key: string) => key }),
}));

const { AddressModal, toDraft } = await import('./AddressModal');
import type { Address } from '@/lib/types';

const SAVED = {
  id: 'addr-1',
  label: 'Home',
  first_name: 'Aisha',
  last_name: 'Khan',
  phone: '+971501234567',
  address_line_1: 'Villa 12, Jumeirah 1',
  address_line_2: null,
  unit_number: null,
  latitude: 25.2,
  longitude: 55.27,
  is_default: true,
} as unknown as Address;

function renderSheet(over: Partial<React.ComponentProps<typeof AddressModal>> = {}) {
  const onClose = vi.fn();
  const onSave = vi.fn();
  const onVerified = vi.fn();
  render(
    <AddressModal
      isOpen
      onClose={onClose}
      onSave={onSave}
      isAuthenticated
      savedAddresses={[SAVED]}
      onSavedAddressesChange={vi.fn()}
      selectedAddressId="addr-1"
      initialDraft={toDraft(SAVED)}
      askToVerify
      verifiedPhone={null}
      onVerified={onVerified}
      {...over}
    />,
  );
  return { onClose, onSave, onVerified };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.firebaseOn.mockReturnValue(true);
  vi.useRealTimers();
  // jsdom has no layout, so nothing to scroll.
  Element.prototype.scrollIntoView = vi.fn();
});

describe('AddressModal — which question it opens on', () => {
  it('opens on the saved list when the errand is choosing an address', () => {
    renderSheet();
    expect(screen.getByText('Home')).toBeInTheDocument();
    expect(screen.queryByText(/send-code-to/)).not.toBeInTheDocument();
  });

  it('opens on the verification panel when the checkout asked for a number', () => {
    renderSheet({ intent: 'verifyPhone' });
    // Straight to the form for the address already on the checkout — no list,
    // no Edit button, no re-selecting what was already selected.
    expect(screen.getByText('send-code-to-+971501234567')).toBeInTheDocument();
  });

  it('still offers the list when there is no address to verify against', () => {
    renderSheet({ intent: 'verifyPhone', initialDraft: null });
    expect(screen.getByText('Home')).toBeInTheDocument();
  });

  it('pins the code panel to the footer, out of the scrolling body', () => {
    // The number is the last field on this form, so a panel *under* it sits
    // below the fold once the keyboard is up — while "Save address" stays
    // pinned in view and gets pressed instead. Sharing the footer with that
    // button is what makes it unmissable.
    renderSheet({ intent: 'verifyPhone' });

    const save = screen.getByRole('button', { name: 'address.save_and_continue' });
    const panel = screen.getByText('send-code-to-+971501234567');
    expect(save.parentElement).toContainElement(panel);
  });

  it('offers no code panel until the number is one that could receive a code', () => {
    renderSheet({
      intent: 'verifyPhone',
      initialDraft: { ...toDraft(SAVED), phone: '+9715085' },
    });
    expect(screen.queryByText(/send-code-to/)).not.toBeInTheDocument();
  });

  it('draws no card on a build that cannot send a code', () => {
    // `PhoneVerify` renders nothing without the Firebase vars. A heading and
    // "we'll text you a code" over the empty space where its button would have
    // been is a promise the build cannot keep.
    mocks.firebaseOn.mockReturnValue(false);
    renderSheet({ intent: 'verifyPhone' });
    expect(screen.queryByText('verify.title')).not.toBeInTheDocument();
  });

  it('scrolls the panel into view rather than opening on the map', async () => {
    renderSheet({ intent: 'verifyPhone' });
    // Deferred a frame, so the panel it is aiming at has been laid out.
    await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalled());
  });
});

describe('AddressModal — what happens once the number is proved', () => {
  it('hands the number up and closes itself, so the checkout is one press away', async () => {
    const { onVerified, onClose } = renderSheet({ intent: 'verifyPhone' });

    fireEvent.click(screen.getByText('send-code-to-+971501234567'));

    expect(onVerified).toHaveBeenCalledWith('+971501234567');
    // The address was never edited, so there is nothing to save — pressing
    // "Save and continue" would spend a round trip rewriting what is there.
    await waitFor(() => expect(onClose).toHaveBeenCalled(), { timeout: 2000 });
  });

  it('stays open when the address was edited, so the edit is not thrown away', async () => {
    const { onVerified, onClose } = renderSheet({ intent: 'verifyPhone' });

    fireEvent.change(screen.getByLabelText('address.unit_number'), {
      target: { value: 'Flat 4' },
    });
    fireEvent.click(screen.getByText('send-code-to-+971501234567'));

    expect(onVerified).toHaveBeenCalled();
    await new Promise((r) => setTimeout(r, 1200));
    expect(onClose).not.toHaveBeenCalled();
  });

  it('leaves an ordinary address edit alone — no self-closing', async () => {
    const { onClose } = renderSheet({ intent: 'select' });
    fireEvent.click(screen.getByText('common.edit'));

    fireEvent.click(screen.getByText('send-code-to-+971501234567'));
    await new Promise((r) => setTimeout(r, 1200));
    expect(onClose).not.toHaveBeenCalled();
  });
});
