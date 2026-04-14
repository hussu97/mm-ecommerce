const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';

export const BRAND = {
  '@type': 'Organization' as const,
  name: 'Melting Moments Cakes',
  url: SITE_URL,
  logo: `${SITE_URL}/images/logos/color_logo.jpeg`,
};

// For Product.brand — Google Merchant listings requires @type: Brand (not Organization)
export const PRODUCT_BRAND = {
  '@type': 'Brand' as const,
  name: 'Melting Moments Cakes',
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

export const RETURN_POLICY = {
  '@type': 'MerchantReturnPolicy' as const,
  applicableCountry: 'AE',
  returnPolicyCategory: 'https://schema.org/MerchantReturnNotPermitted',
};
