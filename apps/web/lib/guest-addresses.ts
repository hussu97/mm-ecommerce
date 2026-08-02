'use client';

import type { Address, RegionCode } from './types';

/**
 * Addresses for people who have not made an account.
 *
 * A guest who ordered last week still lives in the same flat, and asking them
 * to re-pin it is the kind of small tax that ends a second order. These live in
 * localStorage under the same `Address` shape the API returns, so the checkout
 * and the address modal do not care which kind of customer they are serving.
 *
 * Nothing here is authoritative — the address is snapshotted onto the order at
 * purchase time either way. Clearing site data loses them, which is the right
 * trade for not putting a stranger's home address on our server unasked.
 */

const KEY = 'mm_guest_addresses';

function read(): Address[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function write(list: Address[]) {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    /* private mode / quota — the order still goes through without a saved copy */
  }
}

function newId(): string {
  return `guest-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
}

export interface GuestAddressInput {
  label?: string;
  first_name: string;
  last_name: string;
  phone: string;
  address_line_1: string;
  address_line_2?: string;
  unit_number?: string;
  region: RegionCode;
  latitude?: number | null;
  longitude?: number | null;
  is_default?: boolean;
}

function toAddress(input: GuestAddressInput, id: string): Address {
  return {
    id,
    user_id: '',
    label: input.label?.trim() || 'Home',
    first_name: input.first_name,
    last_name: input.last_name,
    phone: input.phone,
    address_line_1: input.address_line_1,
    address_line_2: input.address_line_2 ?? null,
    unit_number: input.unit_number ?? null,
    region: input.region,
    country: 'AE',
    is_default: input.is_default ?? false,
    latitude: input.latitude ?? null,
    longitude: input.longitude ?? null,
    created_at: new Date().toISOString(),
  };
}

export const guestAddresses = {
  list(): Address[] {
    return read();
  },

  create(input: GuestAddressInput): Address {
    const list = read();
    const address = toAddress(input, newId());
    // First address saved is the default; there is nothing to compare it to.
    if (list.length === 0) address.is_default = true;
    if (address.is_default) list.forEach((a) => { a.is_default = false; });
    list.push(address);
    write(list);
    return address;
  },

  update(id: string, input: GuestAddressInput): Address | null {
    const list = read();
    const index = list.findIndex((a) => a.id === id);
    if (index === -1) return null;
    const updated = { ...toAddress(input, id), created_at: list[index].created_at };
    if (updated.is_default) list.forEach((a) => { a.is_default = false; });
    list[index] = updated;
    write(list);
    return updated;
  },

  remove(id: string): void {
    const list = read().filter((a) => a.id !== id);
    // Never leave the book without a default, or the next checkout opens blank.
    if (list.length > 0 && !list.some((a) => a.is_default)) list[0].is_default = true;
    write(list);
  },
};
