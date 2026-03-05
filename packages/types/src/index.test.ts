import { describe, it, expectTypeOf } from 'vitest';
import type {
  Emirate,
  OrderStatus,
  DeliveryMethod,
  PaymentProvider,
  DiscountType,
  ApiResponse,
  PaginatedResponse,
} from './index';

describe('Type definitions', () => {
  it('Emirate accepts valid values', () => {
    const e: Emirate = 'Dubai';
    expectTypeOf(e).toEqualTypeOf<Emirate>();
  });

  it('OrderStatus accepts all valid values', () => {
    const statuses: OrderStatus[] = ['created', 'confirmed', 'packed', 'cancelled'];
    expectTypeOf(statuses).toEqualTypeOf<OrderStatus[]>();
  });

  it('DeliveryMethod accepts valid values', () => {
    const m: DeliveryMethod = 'delivery';
    expectTypeOf(m).toEqualTypeOf<DeliveryMethod>();
  });

  it('PaymentProvider accepts valid values', () => {
    const p: PaymentProvider = 'stripe';
    expectTypeOf(p).toEqualTypeOf<PaymentProvider>();
  });

  it('DiscountType accepts valid values', () => {
    const d: DiscountType = 'percentage';
    expectTypeOf(d).toEqualTypeOf<DiscountType>();
  });

  it('ApiResponse is generic over data type', () => {
    const res: ApiResponse<string> = { data: 'hello' };
    expectTypeOf(res.data).toEqualTypeOf<string>();
  });

  it('PaginatedResponse has required fields', () => {
    const paged: PaginatedResponse<number> = {
      items: [1, 2, 3],
      total: 3,
      page: 1,
      page_size: 10,
      total_pages: 1,
    };
    expectTypeOf(paged.items).toEqualTypeOf<number[]>();
  });
});
