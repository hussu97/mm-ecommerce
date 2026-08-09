/**
 * What the homepage tells a crawler about the coupon.
 *
 * This node is the one part of the offer that outlives the page view: a search
 * engine caches it and an answer engine quotes it back to somebody who never
 * visited. That makes a wrong claim here more expensive than a wrong pixel — a
 * stale code arrives at the checkout and is refused, and the shop looks like it
 * reneged. So the rules are narrow: state only what the row says, state nothing
 * at all when there is no row, and never invent an end date.
 */

import { describe, expect, it } from 'vitest';

import { isAdvertisable, offerSentence } from '@/lib/offer';
import { buildOffer } from './page';

const COUPON = {
  code: 'NEW',
  code_ar: 'جديد',
  discount_type: 'percentage' as const,
  discount_value: 20,
  max_discount_amount: 30,
  first_orders_limit: 3,
  requires_phone_verification: true,
  valid_until: '2026-12-31T20:00:00Z',
};

describe('the homepage offer node', () => {
  it('states the coupon the checkout will actually honour', () => {
    const offer = buildOffer(COUPON)!;

    expect(offer['@type']).toBe('Offer');
    expect(offer.discountCode).toBe('NEW');
    expect(offer.discount).toBe(20);
    expect(offer.priceCurrency).toBe('AED');
    expect(offer.name).toContain('20%');
    expect(offer.name).toContain('3 orders');
    expect(offer.description).toContain('AED 30');
  });

  it('names the code, in every surface that states the offer', () => {
    // Every hand-written version of this sentence forgot it. An answer engine
    // telling somebody about a discount they then cannot find is worse than not
    // mentioning the discount at all.
    const sentence = offerSentence(COUPON)!;
    expect(sentence).toContain('NEW');
    expect(sentence).toContain('20%');
    expect(sentence).toContain('first 3 orders');
    // And the phone gate is described as the delivery-only rule it is.
    expect(sentence).toContain('delivery orders need a verified mobile number');
    // The JSON-LD quotes the same sentence, so the two cannot drift.
    expect(buildOffer(COUPON)!.description).toBe(sentence);
  });

  it('does not claim a verified number is needed when the coupon does not ask', () => {
    const sentence = offerSentence({ ...COUPON, requires_phone_verification: false })!;
    expect(sentence).not.toContain('verified mobile');
  });

  it('says nothing about an offer that is not running', () => {
    expect(offerSentence(null)).toBeNull();
  });

  it('says when the campaign closes', () => {
    // Without it the offer reads as perpetual, and a crawler keeps quoting a
    // campaign that ended months ago.
    expect(buildOffer(COUPON)!.validThrough).toBe('2026-12-31T20:00:00Z');
  });

  it('does not invent an end date for an open-ended campaign', () => {
    const offer = buildOffer({ ...COUPON, valid_until: null })!;
    expect(offer).not.toHaveProperty('validThrough');
  });

  it('publishes nothing at all when no campaign is running', () => {
    // Not an empty node, not a node with blanks in it. The absence of the
    // campaign has to be the absence of the claim.
    expect(buildOffer(null)).toBeNull();
    expect(isAdvertisable(null)).toBe(false);
  });

  it('refuses to describe a coupon it has no honest wording for', () => {
    // The copy is percentage-shaped, so a fixed 20-dirham coupon would be
    // published as "20% off" — and unlike a mistake on screen, this one gets
    // repeated by machines to people who never saw the page.
    expect(buildOffer({ ...COUPON, discount_type: 'fixed', discount_value: 20 })).toBeNull();
    // Same for a coupon that is not an acquisition offer at all.
    expect(buildOffer({ ...COUPON, first_orders_limit: null })).toBeNull();
  });
});
