/**
 * Turn a dropped pin into an address.
 *
 * The customer has already said where they are by putting a marker on the map;
 * making them then type the street name back to us is asking for something we
 * hold. One line comes back — Google's own formatted address — rather than the
 * street, area and emirate split across three inputs the customer had to
 * reconcile. The only thing left to type is the part no map can know: the flat.
 *
 * The emirate still comes back, but as data rather than a question. It rides
 * along on the order for reporting; the delivery fee is priced off the
 * coordinates against the zone map.
 */

export interface GeocodedAddress {
  /** Google's formatted address, trimmed of the country suffix. */
  address: string;
  region: string;
}

type Component = google.maps.GeocoderAddressComponent;

function pick(components: Component[], type: string): string {
  return components.find((c) => c.types.includes(type))?.long_name ?? '';
}

/**
 * Google names UAE emirates several ways ("Abu Dhabi Emirate", "Emirate of
 * Sharjah", "Ras al Khaimah"), so match loosely on the distinctive words rather
 * than expecting one exact string.
 */
function toRegionSlug(components: Component[]): string {
  const admin = pick(components, 'administrative_area_level_1').toLowerCase();
  const locality = pick(components, 'locality').toLowerCase();

  // Al Ain sits inside the emirate of Abu Dhabi but has always been its own
  // line in the fee table, so the city wins over the emirate here.
  if (locality.includes('al ain') || admin.includes('al ain')) return 'al_ain';

  const haystack = `${admin} ${locality}`;
  if (haystack.includes('dubai')) return 'dubai';
  if (haystack.includes('sharjah')) return 'sharjah';
  if (haystack.includes('ajman')) return 'ajman';
  if (haystack.includes('abu dhabi') || haystack.includes('abudhabi')) return 'abu_dhabi';
  if (haystack.includes('ras al khaimah') || haystack.includes('ras el khaimah')) return 'ras_al_khaimah';
  if (haystack.includes('fujairah')) return 'fujairah';
  if (haystack.includes('umm al quwain') || haystack.includes('umm al qaiwain')) return 'umm_al_quwain';
  return 'rest_of_uae';
}

/**
 * Google appends the country and often a plus-code prefix. Neither helps a
 * rider who is already in the UAE, and both make the line too long to read in
 * a single row on a phone.
 */
function tidy(formatted: string, components: Component[]): string {
  const country = pick(components, 'country');
  let out = formatted;

  if (country) {
    out = out.replace(new RegExp(`,?\\s*${country}\\s*$`, 'i'), '');
  }
  // A plus code ("7CQ2+3M Dubai") is a fallback Google emits when it has no
  // street. It is machine-readable, not human-readable.
  out = out.replace(/^[A-Z0-9]{4}\+[A-Z0-9]{2,3}\s*,?\s*/i, '');

  return out.trim().replace(/^,\s*/, '').replace(/,\s*$/, '');
}

/**
 * Reverse-geocode a pin. Resolves to null when Google has nothing useful, so
 * callers can leave whatever the customer already typed rather than blanking it.
 */
export async function reverseGeocode(lat: number, lng: number): Promise<GeocodedAddress | null> {
  if (typeof google === 'undefined' || !google.maps?.Geocoder) return null;

  try {
    const geocoder = new google.maps.Geocoder();
    const { results } = await geocoder.geocode({ location: { lat, lng } });
    if (!results?.length) return null;

    // Prefer a street address; a plaza or an emirate centroid tells the rider
    // nothing they cannot already see from the pin.
    const best =
      results.find((r) => r.types.includes('street_address')) ??
      results.find((r) => r.types.includes('premise')) ??
      results.find((r) => r.types.includes('route')) ??
      results[0];

    const address = tidy(best.formatted_address, best.address_components);
    if (!address) return null;

    return { address, region: toRegionSlug(best.address_components) };
  } catch {
    return null;
  }
}
