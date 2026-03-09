import type { MetadataRoute } from 'next';

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'Melting Moments Cakes',
    short_name: 'Melting Moments',
    description:
      'Handcrafted brownies, cookies, cookie melts, and desserts delivered across the UAE.',
    start_url: '/en',
    display: 'standalone',
    theme_color: '#8a5a64',
    background_color: '#ffffff',
    icons: [
      {
        src: '/images/logos/favicon_logo.jpeg',
        sizes: '192x192',
        type: 'image/jpeg',
      },
      {
        src: '/images/logos/color_logo.jpeg',
        sizes: '512x512',
        type: 'image/jpeg',
      },
    ],
  };
}
