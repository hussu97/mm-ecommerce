import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./api', () => ({
  ensureSessionId: vi.fn(() => 'sess_test'),
  authApi: {
    me: vi.fn(),
    guest: vi.fn(),
  },
}));

import { authApi, ensureSessionId } from './api';
import { accountEmailOf, ensureCheckoutAuth, isApplePayTestUser } from './checkout-auth';
import type { User } from './types';

describe('ensureCheckoutAuth', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('does not create a guest when a registered user exists in context', async () => {
    const user = { id: 'u1', is_guest: false } as User;

    await ensureCheckoutAuth(user);

    expect(ensureSessionId).toHaveBeenCalledOnce();
    expect(authApi.me).not.toHaveBeenCalled();
    expect(authApi.guest).not.toHaveBeenCalled();
  });

  it('reuses an existing guest cookie when auth/me succeeds', async () => {
    vi.mocked(authApi.me).mockResolvedValue({ id: 'guest', is_guest: true } as User);

    await ensureCheckoutAuth(null);

    expect(authApi.me).toHaveBeenCalledOnce();
    expect(authApi.guest).not.toHaveBeenCalled();
  });

  it('creates a guest only when no auth cookie can be reused', async () => {
    vi.mocked(authApi.me).mockRejectedValue(new Error('unauthorized'));

    await ensureCheckoutAuth(null);

    expect(authApi.me).toHaveBeenCalledOnce();
    expect(authApi.guest).toHaveBeenCalledOnce();
  });
});

describe('accountEmailOf', () => {
  const asUser = (u: Partial<User>) => u as User;

  it('gives back the address a signed-in customer will be written to', () => {
    expect(accountEmailOf(asUser({ email: 'Sara@example.com', is_guest: false })))
      .toBe('Sara@example.com');
  });

  it('has nothing to show when nobody is signed in', () => {
    expect(accountEmailOf(null)).toBeNull();
  });

  it('refuses a guest, whose address is minted and reaches nobody', () => {
    expect(accountEmailOf(asUser({ email: 'abc123@guest.local', is_guest: true }))).toBeNull();
  });

  it('refuses the synthetic domain even when the guest flag says otherwise', () => {
    // The flag is only as good as whatever set it; the domain is the fact.
    expect(accountEmailOf(asUser({ email: 'abc123@Guest.Local', is_guest: false }))).toBeNull();
  });

  it('treats a blank address as no address', () => {
    expect(accountEmailOf(asUser({ email: '   ', is_guest: false }))).toBeNull();
  });
});

describe('isApplePayTestUser', () => {
  const asUser = (u: Partial<User>) => u as User;

  it('admits the allowlisted account', () => {
    expect(isApplePayTestUser(asUser({ email: 'h_abbasi97@hotmail.com', is_guest: false }))).toBe(true);
  });

  it('matches regardless of case or surrounding space', () => {
    expect(isApplePayTestUser(asUser({ email: '  H_Abbasi97@Hotmail.com ', is_guest: false }))).toBe(true);
  });

  it('turns away every other signed-in customer', () => {
    expect(isApplePayTestUser(asUser({ email: 'someone@else.com', is_guest: false }))).toBe(false);
  });

  it('turns away a guest even if the address happens to match', () => {
    expect(isApplePayTestUser(asUser({ email: 'h_abbasi97@hotmail.com', is_guest: true }))).toBe(false);
  });

  it('turns away nobody-signed-in', () => {
    expect(isApplePayTestUser(null)).toBe(false);
  });
});
