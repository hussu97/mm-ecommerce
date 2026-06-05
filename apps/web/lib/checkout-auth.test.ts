import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./api', () => ({
  ensureSessionId: vi.fn(() => 'sess_test'),
  authApi: {
    me: vi.fn(),
    guest: vi.fn(),
  },
}));

import { authApi, ensureSessionId } from './api';
import { ensureCheckoutAuth } from './checkout-auth';
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
