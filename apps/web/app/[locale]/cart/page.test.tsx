/**
 * F-WEB-7: the cart page never re-read `Cart.promo_code`.
 *
 * A code applied on this page is saved server-side (`cartApi.setPromo`) so the
 * checkout can find it without `sessionStorage` — see `useCartPromoRecovery`.
 * But the cart page itself never called that hook, so a reload, a second tab,
 * or landing here from a link only ever refetched `cart`, never re-ran the
 * validation that turns `Cart.promo_code` back into a discount. The basket
 * showed the full subtotal here and then, at checkout, watched the same code
 * take a slice off — one basket, two prices. This pins the fix: a loaded cart
 * carrying a `promo_code` recovers it, and the discounted total is what
 * renders.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Cart } from '@/lib/types';

const mocks = vi.hoisted(() => ({
  validate: vi.fn(),
  featured: vi.fn(),
  cartAddons: vi.fn(),
  setPromo: vi.fn(),
  addToast: vi.fn(),
}));

vi.mock('@/lib/cart-context', () => ({
  useCart: () => mockCart(),
}));

vi.mock('@/lib/auth-context', () => ({
  useAuth: () => ({ user: null }),
}));

vi.mock('@/components/ui/Toast', () => ({
  useToast: () => ({ addToast: mocks.addToast }),
}));

vi.mock('@/lib/i18n/TranslationProvider', () => ({
  useTranslation: () => ({ t: (key: string) => key, locale: 'en' }),
}));

// `productsApi.cartAddons` and `promoApi.featured` are the add-on tray's and
// the new-customer tray's own mount-time fetches — nothing this fix touches,
// but both would otherwise throw against an undefined function and take the
// whole render down with them.
vi.mock('@/lib/api', () => ({
  cartApi: { setPromo: mocks.setPromo },
  ensureSessionId: vi.fn(),
  promoApi: { validate: mocks.validate, featured: mocks.featured },
  productsApi: { cartAddons: mocks.cartAddons },
}));

// The basket has no zone pinned in this test, and `FreeDeliveryNudge` reads
// one from `LocationProvider` — a context this render does not set up. What it
// renders is not part of F-WEB-7.
vi.mock('@/components/cart/FreeDeliveryNudge', () => ({
  FreeDeliveryNudge: () => null,
}));

let currentCart: Cart | null = null;

function mockCart() {
  return {
    cart: currentCart,
    itemCount: currentCart?.item_count ?? 0,
    isLoading: false,
    cartLoaded: true,
    cartError: false,
    addItem: vi.fn(),
    updateItem: vi.fn(),
    updateNote: vi.fn(),
    removeItem: vi.fn(),
    mergeCart: vi.fn(),
  };
}

function cartWithPromo(promo_code: string | null): Cart {
  return {
    id: 'cart-1',
    user_id: null,
    session_id: 'sess-1',
    item_count: 1,
    // Deliberately distinct from the line total below, so a query for
    // "100.00 AED" can only ever mean the subtotal/total line, never the
    // item's own line — the two would otherwise collide and every exact-text
    // assertion below would match more than one node.
    subtotal: 100,
    promo_code,
    items: [
      {
        id: 'item-1',
        cart_id: 'cart-1',
        product_id: 'prod-1',
        quantity: 1,
        selected_options: [],
        created_at: '2026-09-01T00:00:00Z',
        product_name: 'Red Velvet Cake',
        product_image: null,
        unit_price: 60,
        line_total: 60,
      },
    ],
  };
}

import CartPage from './page';

describe('CartPage promo recovery (F-WEB-7)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    currentCart = null;
    mocks.featured.mockResolvedValue(null);
    mocks.cartAddons.mockResolvedValue([]);
  });

  it('recovers a promo_code the basket already carries and shows the discounted total', async () => {
    currentCart = cartWithPromo('WELCOME15');
    mocks.validate.mockResolvedValue({
      valid: true,
      discount_amount: 15,
      message: 'AED 15 off',
    });

    render(<CartPage />);

    await waitFor(() => expect(mocks.validate).toHaveBeenCalledWith('WELCOME15', 100, { email: null }));

    // The discounted total, matching what checkout will charge — not the
    // subtotal repeated as the total.
    expect(await screen.findByText('85.00 AED')).toBeInTheDocument();
    expect(screen.getByText('WELCOME15')).toBeInTheDocument();
    // The subtotal line is untouched; only the total moves.
    expect(screen.getByText('100.00 AED')).toBeInTheDocument();
  });

  it('shows the full price and never calls validate when the basket has no promo_code', async () => {
    currentCart = cartWithPromo(null);

    render(<CartPage />);

    // No discount line, and the subtotal and the total agree — both read
    // "100.00 AED" because nothing has been taken off.
    expect(await screen.findAllByText('100.00 AED')).toHaveLength(2);
    await new Promise((r) => setTimeout(r, 20));
    expect(mocks.validate).not.toHaveBeenCalled();
    expect(screen.queryByText('WELCOME15')).not.toBeInTheDocument();
  });
});
