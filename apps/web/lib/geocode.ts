/**
 * Turn a dropped pin into the address fields a rider can read.
 *
 * The customer has already told us where they are by putting a marker on the
 * map; making them then type the street name back to us is asking for
 * information we hold. Everything here is a starting point — every field it
 * fills stays editable, because geocoders are confidently wrong often enough
 * that an uneditable result would be worse than an empty one.
 */

export interface GeocodedAddress {
  addressLine1: string;
  addressLine2: string;
  region: string;
}

type Component = google.maps.GeocoderAddressComponent;

function pick(components: Component[], type: string): string {
  return components.find((c) => c.types.includes(type))?.long_name ?? '';
}

/**
 * Google names UAE emirates in several ways ("Abu Dhabi Emirate", "Emirate of
 * Sharjah", "Ras al Khaimah"), so match loosely on the distinctive words
 * rather than expecting one exact string.
 */
function toRegionSlug(components: Component[]): string {
  const admin = pick(components, 'administrative_area_level_1').toLowerCase();
  const locality = pick(components, 'locality').toLowerCase();

  // Al Ain sits inside the emirate of Abu Dhabi but is its own delivery zone,
  // so the city has to win over the emirate here.
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

function buildLine1(result: google.maps.GeocoderResult): string {
  const c = result.address_components;
  const street = [pick(c, 'street_number'), pick(c, 'route')].filter(Boolean).join(' ');
  if (street) return street;

  const named = pick(c, 'premise') || pick(c, 'establishment') || pick(c, 'point_of_interest');
  if (named) return named;

  // Last resort: the head of the formatted address, minus the emirate/country
  // tail the other fields already capture.
  return result.formatted_address.split(',')[0]?.trim() ?? '';
}

function buildLine2(result: google.maps.GeocoderResult): string {
  const c = result.address_components;
  return (
    pick(c, 'neighborhood') ||
    pick(c, 'sublocality_level_1') ||
    pick(c, 'sublocality') ||
    ''
  );
}

/**
 * Reverse-geocode a pin. Resolves to null when Google has nothing useful —
 * callers keep whatever the customer already typed rather than blanking it.
 */
export async function reverseGeocode(lat: number, lng: number): Promise<GeocodedAddress | null> {
  if (typeof google === 'undefined' || !google.maps?.Geocoder) return null;

  try {
    const geocoder = new google.maps.Geocoder();
    const { results } = await geocoder.geocode({ location: { lat, lng } });
    if (!results?.length) return null;

    // Prefer a street address; a plaza or emirate centroid tells the rider
    // nothing they cannot already see from the pin.
    const best =
      results.find((r) => r.types.includes('street_address')) ??
      results.find((r) => r.types.includes('premise')) ??
      results[0];

    const line1 = buildLine1(best);
    const line2 = buildLine2(best);
    const region = toRegionSlug(best.address_components);

    if (!line1 && !line2 && region === 'rest_of_uae') return null;
    return { addressLine1: line1, addressLine2: line2, region };
  } catch {
    return null;
  }
}
