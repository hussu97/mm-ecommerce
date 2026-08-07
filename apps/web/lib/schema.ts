const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';

/**
 * One id for the business, shared by every page that describes it.
 *
 * The home page, the contact page and the about page each used to declare their
 * own unnamed business node. A crawler has no way to tell those three apart
 * from three different bakeries, so the opening hours on one page and the phone
 * number on another never joined up. Pointing all of them at this id makes them
 * statements about a single entity.
 */
export const BUSINESS_ID = `${SITE_URL}/#bakery`;
export const FOUNDER_ID = `${SITE_URL}/#fatema`;

/**
 * The share card, for pages that have no better picture of their own.
 *
 * `app/opengraph-image.tsx` renders this at 1200×630. The file convention
 * alone is not enough: any page whose `generateMetadata` returns an
 * `openGraph` object *replaces* the inherited one, images included, so a page
 * that sets a title and description and nothing else ends up sharing with no
 * image at all. Pages that have a real photograph — a product, a category, a
 * blog cover — should keep using it and ignore this.
 */
export const OG_IMAGE = {
  url: '/opengraph-image',
  width: 1200,
  height: 630,
  alt: 'Melting Moments Cakes — brownies, cookies and desserts delivered across the UAE',
};

export const BRAND = {
  '@type': 'Organization' as const,
  '@id': BUSINESS_ID,
  name: 'Melting Moments Cakes',
  url: SITE_URL,
  logo: `${SITE_URL}/images/logos/color_logo.jpeg`,
};

// For Product.brand — Google Merchant listings requires @type: Brand (not Organization)
export const PRODUCT_BRAND = {
  '@type': 'Brand' as const,
  name: 'Melting Moments Cakes',
};

/**
 * Where the delivery actually goes.
 *
 * This was the single string 'AE'. Every city below is a place someone types
 * into a search box next to the word "delivery", and none of them appeared
 * anywhere in the markup. Emirates and cities are both listed because people
 * search for the emirate ("dessert delivery Sharjah") and for the town inside
 * it ("Al Ain") interchangeably.
 */
export const SERVICE_AREA = [
  'Dubai',
  'Sharjah',
  'Ajman',
  'Abu Dhabi',
  'Al Ain',
  'Ras Al Khaimah',
  'Fujairah',
  'Umm Al Quwain',
  'United Arab Emirates',
].map(name => ({ '@type': 'AdministrativeArea' as const, name }));

/** What the bakery sells, in the words people search for. */
export const KNOWS_ABOUT = [
  'Brownie delivery',
  'Cookie delivery',
  'Cookie melts',
  'Dessert delivery',
  'Birthday cakes',
  'Eggless baking',
  'Dessert gift boxes',
  'Corporate gifting',
  'Eid and Ramadan sweets',
  'Halal baking',
];

export const CONTACT_POINTS = [
  {
    '@type': 'ContactPoint' as const,
    contactType: 'customer service',
    telephone: '+971503687757',
    email: 'fatema_f@hotmail.co.uk',
    areaServed: 'AE',
    availableLanguage: ['English', 'Arabic'],
  },
  {
    '@type': 'ContactPoint' as const,
    contactType: 'sales',
    url: 'https://wa.me/971503687757',
    contactOption: 'TollFree',
    areaServed: 'AE',
    availableLanguage: ['English', 'Arabic'],
  },
];

export const OPENING_HOURS = [
  {
    '@type': 'OpeningHoursSpecification' as const,
    dayOfWeek: ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'],
    opens: '08:00',
    closes: '23:30',
  },
  {
    '@type': 'OpeningHoursSpecification' as const,
    dayOfWeek: 'Sunday',
    opens: '15:00',
    closes: '23:30',
  },
];

export const FOUNDER = {
  '@type': 'Person' as const,
  '@id': FOUNDER_ID,
  name: 'Fatema Abbasi',
  jobTitle: 'Founder & Baker',
  sameAs: ['https://www.instagram.com/meltingmomentscakes'],
};

/**
 * The parts of the Bakery node that are true on every page that renders it.
 * Callers spread this and add whatever their page knows on top.
 */
export const BAKERY_BASE = {
  '@type': 'Bakery' as const,
  '@id': BUSINESS_ID,
  name: 'Melting Moments Cakes',
  alternateName: 'Melting Moments',
  url: SITE_URL,
  telephone: '+971503687757',
  image: `${SITE_URL}/images/logos/color_logo.jpeg`,
  logo: `${SITE_URL}/images/logos/color_logo.jpeg`,
  address: {
    '@type': 'PostalAddress' as const,
    addressLocality: 'Sharjah',
    addressRegion: 'Sharjah',
    addressCountry: 'AE',
  },
  geo: {
    '@type': 'GeoCoordinates' as const,
    latitude: 25.3304,
    longitude: 55.371,
  },
  currenciesAccepted: 'AED',
  priceRange: 'AED 15–200',
  servesCuisine: ['Brownies', 'Cookies', 'Cakes', 'Desserts'],
  paymentAccepted: 'Credit Card, Debit Card, Apple Pay, Cash on pickup',
  areaServed: SERVICE_AREA,
  knowsAbout: KNOWS_ABOUT,
  founder: FOUNDER,
  contactPoint: CONTACT_POINTS,
  openingHoursSpecification: OPENING_HOURS,
  sameAs: [
    'https://www.instagram.com/meltingmomentscakes',
    'https://wa.me/971503687757',
  ],
};

export const SHIPPING_DETAILS = {
  '@type': 'OfferShippingDetails' as const,
  shippingDestination: {
    '@type': 'DefinedRegion' as const,
    addressCountry: 'AE',
  },
  deliveryTime: {
    '@type': 'ShippingDeliveryTime' as const,
    handlingTime: {
      '@type': 'QuantitativeValue' as const,
      minValue: 0,
      maxValue: 1,
      unitCode: 'DAY',
    },
    transitTime: {
      '@type': 'QuantitativeValue' as const,
      minValue: 0,
      maxValue: 1,
      unitCode: 'DAY',
    },
  },
};

/**
 * What each part of the country actually pays, as structured data.
 *
 * One `shippingRate` cannot describe this shop. Sharjah is free at any basket,
 * Dubai is AED 20 and free over 75, and the far emirates are 80 and free over
 * 200 — a single figure is wrong for five of the seven emirates whichever one
 * you pick. `shippingDestination` accepts a list of regions, so each band gets
 * its own entry and a shopping surface reading this can tell a customer in
 * Ajman something true.
 *
 * The numbers here are the published zone fees, not courier costs, and they
 * have to move when the map does — `085_cost_banded_map` is where they come
 * from.
 */
export const SHIPPING_BY_REGION = [
  {
    '@type': 'OfferShippingDetails' as const,
    name: 'Sharjah city',
    shippingDestination: {
      '@type': 'DefinedRegion' as const,
      addressCountry: 'AE',
      addressRegion: 'Sharjah',
    },
    shippingRate: {
      '@type': 'MonetaryAmount' as const,
      value: '0.00',
      currency: 'AED',
    },
    deliveryTime: {
      '@type': 'ShippingDeliveryTime' as const,
      handlingTime: { '@type': 'QuantitativeValue' as const, minValue: 0, maxValue: 0, unitCode: 'DAY' },
      // An hour, expressed in the only unit the vocabulary has for it.
      transitTime: { '@type': 'QuantitativeValue' as const, minValue: 0, maxValue: 0, unitCode: 'DAY' },
    },
  },
  {
    '@type': 'OfferShippingDetails' as const,
    name: 'Dubai and Ajman',
    shippingDestination: [
      { '@type': 'DefinedRegion' as const, addressCountry: 'AE', addressRegion: 'Dubai' },
      { '@type': 'DefinedRegion' as const, addressCountry: 'AE', addressRegion: 'Ajman' },
    ],
    shippingRate: {
      '@type': 'MonetaryAmount' as const,
      value: '20.00',
      currency: 'AED',
      // Free above this, which is the part a shopper actually acts on.
      eligibleTransactionVolume: {
        '@type': 'PriceSpecification' as const,
        minPrice: '75.00',
        priceCurrency: 'AED',
      },
    },
    deliveryTime: {
      '@type': 'ShippingDeliveryTime' as const,
      handlingTime: { '@type': 'QuantitativeValue' as const, minValue: 0, maxValue: 0, unitCode: 'DAY' },
      transitTime: { '@type': 'QuantitativeValue' as const, minValue: 0, maxValue: 1, unitCode: 'DAY' },
    },
  },
  {
    '@type': 'OfferShippingDetails' as const,
    name: 'Rest of the UAE',
    shippingDestination: [
      { '@type': 'DefinedRegion' as const, addressCountry: 'AE', addressRegion: 'Abu Dhabi' },
      { '@type': 'DefinedRegion' as const, addressCountry: 'AE', addressRegion: 'Ras al-Khaimah' },
      { '@type': 'DefinedRegion' as const, addressCountry: 'AE', addressRegion: 'Umm al-Quwain' },
      { '@type': 'DefinedRegion' as const, addressCountry: 'AE', addressRegion: 'Fujairah' },
    ],
    shippingRate: {
      '@type': 'MonetaryAmount' as const,
      value: '80.00',
      currency: 'AED',
      eligibleTransactionVolume: {
        '@type': 'PriceSpecification' as const,
        minPrice: '200.00',
        priceCurrency: 'AED',
      },
    },
    deliveryTime: {
      '@type': 'ShippingDeliveryTime' as const,
      handlingTime: { '@type': 'QuantitativeValue' as const, minValue: 0, maxValue: 1, unitCode: 'DAY' },
      transitTime: { '@type': 'QuantitativeValue' as const, minValue: 1, maxValue: 2, unitCode: 'DAY' },
    },
  },
];

/**
 * The small-basket fee, as its own charge rather than folded into shipping.
 *
 * It is not a delivery charge — it does not vary with distance and it is not
 * waived by free delivery — so describing it as one would misstate both. A
 * separate `DeliveryChargeSpecification` with its own `eligibleTransactionVolume`
 * says exactly what is true: this applies below AED 35 and not above it.
 */
export const LOW_ORDER_FEE_SPEC = {
  '@type': 'DeliveryChargeSpecification' as const,
  name: 'Small order fee',
  price: '15.00',
  priceCurrency: 'AED',
  eligibleTransactionVolume: {
    '@type': 'PriceSpecification' as const,
    maxPrice: '35.00',
    priceCurrency: 'AED',
  },
  eligibleRegion: {
    '@type': 'DefinedRegion' as const,
    addressCountry: 'AE',
  },
};

/**
 * Search Console flags `shippingRate` as a missing recommended field on every
 * merchant listing, so the fee has to be in the markup. It is zone-based at
 * checkout, so the honest single number to publish is the default fee — what
 * an address we have not drawn a zone around actually pays. Callers read it
 * from /delivery/rates rather than repeating the figure here, so the markup
 * cannot drift from what the API charges.
 */
export function buildShippingDetails(deliveryFee: number) {
  return {
    ...SHIPPING_DETAILS,
    shippingRate: {
      '@type': 'MonetaryAmount' as const,
      value: deliveryFee.toFixed(2),
      currency: 'AED',
    },
  };
}

export const RETURN_POLICY = {
  '@type': 'MerchantReturnPolicy' as const,
  applicableCountry: 'AE',
  returnPolicyCategory: 'https://schema.org/MerchantReturnNotPermitted',
};
