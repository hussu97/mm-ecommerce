/**
 * Turn a dropped pin into an address.
 *
 * The customer has already said where they are by putting a marker on the map;
 * making them then type the street name back to us is asking for something we
 * hold. One line comes back — Google's own formatted address — rather than the
 * street, area and emirate split across three inputs the customer had to
 * reconcile. The only thing left to type is the part no map can know: the flat.
 *
 * Nothing is derived from the result beyond that line. The emirate used to be
 * guessed out of Google's components and sent along with the order; the fee is
 * priced off the coordinates against the zone map, so the guess was a second,
 * worse answer to a question already answered.
 */

export interface GeocodedAddress {
  /** Google's formatted address, trimmed of the country suffix. */
  address: string;
}

type Component = google.maps.GeocoderAddressComponent;

function pick(components: Component[], type: string): string {
  return components.find((c) => c.types.includes(type))?.long_name ?? '';
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

    return { address };
  } catch {
    return null;
  }
}
