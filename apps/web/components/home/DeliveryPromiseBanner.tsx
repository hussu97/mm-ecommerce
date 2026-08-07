'use client';

import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { useLocation } from '@/lib/location/LocationProvider';

/**
 * The delivery promise, said in terms of where the customer actually is.
 *
 * Everything here turns on one rule: a promise is only ever made about a place
 * we can name. `GET /delivery/area` answers for a pin, and the pin is the shop
 * itself until somebody tells us otherwise — so the interesting case is not the
 * located customer, it is the unlocated one. For them the copy names the zone
 * out loud ("Free delivery in Sharjah Central") and offers to swap it for
 * theirs, rather than saying "free delivery" flat and letting somebody in Abu
 * Dhabi read it as being about their doorstep.
 *
 * When the lookup has not landed, or landed on nothing we can serve, the copy
 * drops to the site-wide line — which says "in selected areas" and promises
 * nobody anything in particular. A stale or borrowed promise is worse than a
 * vague one.
 */

/** 150 rather than "150.00"; 12.5 keeps its half. */
function amount(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

export function DeliveryPromiseBanner() {
  const { t } = useTranslation();
  const { area, isKnown, requestBrowserLocation } = useLocation();

  const zone = area?.zone_name ?? null;

  function message(): string | null {
    // No lookup yet, a failed one, or a pin nothing reaches. Nothing here is
    // known well enough to name — and the generic line this used to fall back
    // to ("free delivery over 150 AED in selected areas") was a national
    // average that is wrong in every zone: Sharjah is free at any basket and
    // the far emirates want 200. Saying nothing beats saying a number that
    // applies nowhere.
    if (!area || !area.serviceable) return null;

    // Free delivery, but only ever attached to the zone it belongs to. Without
    // a zone name there is no way to bound the claim, so we do not make it.
    if (area.free_delivery_available && zone) {
      if (area.free_threshold === 0) return t('usp.free_delivery_here', { area: zone });
      if (area.free_threshold != null && area.free_threshold > 0) {
        return t('usp.free_delivery_over', { amount: amount(area.free_threshold), area: zone });
      }
    }

    // Serviceable, no free delivery to promise. Naming the place is still worth
    // saying — but only when the place is theirs and not the shop's.
    if (isKnown && zone) return t('usp.delivering_to', { area: zone });

    return null;
  }

  // Nothing worth saying, so no strip at all. An empty bar with a lone
  // "Change" button in it is furniture, not a promise.
  const text = message();
  if (!text) return null;

  return (
    <section aria-label="Delivery" className="bg-[#f4ece4] text-primary">
      <div className="max-w-7xl mx-auto px-5 sm:px-6 lg:px-8 py-2 flex items-center justify-center gap-2">
        {/* `dir="auto"` because the zone names arrive from the API in English
            and have to keep reading left to right inside Arabic copy. */}
        <p
          dir="auto"
          aria-live="polite"
          className="font-body text-[10px] sm:text-[11px] uppercase tracking-[0.22em] text-center"
        >
          {text}
        </p>

        {/* Offered only while we are standing in for a location we do not have.
            Once there is a real one, re-asking the browser buys a permission
            prompt and nothing else. */}
        {!isKnown && (
          <button
            type="button"
            onClick={() => void requestBrowserLocation()}
            className="ms-1 inline-flex items-center gap-1 shrink-0 font-body text-[10px] sm:text-[11px] uppercase tracking-[0.22em] underline underline-offset-4 hover:opacity-70 transition-opacity"
          >
            <span className="material-icons text-[14px]" aria-hidden="true">
              my_location
            </span>
            {t('usp.change_area')}
          </button>
        )}
      </div>
    </section>
  );
}
