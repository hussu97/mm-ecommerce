import type { Metadata } from 'next';
import { ContactForm } from './ContactForm';

export const metadata: Metadata = {
  title: 'Contact',
  description:
    'Get in touch with Melting Moments Cakes. Order custom treats, ask about delivery, or just say hello — we\'re always happy to hear from you.',
  openGraph: {
    title: 'Contact Us | Melting Moments Cakes',
    description: 'Reach out for custom orders, delivery enquiries, or anything else.',
  },
};

const INFO_CARDS = [
  {
    icon: 'chat',
    title: 'WhatsApp',
    lines: ['+971 56 352 6578'],
    href: 'https://wa.me/971563526578',
    cta: 'Message us',
  },
  {
    icon: 'mail',
    title: 'Email',
    lines: ['hello@meltingmomentscakes.com'],
    href: 'mailto:hello@meltingmomentscakes.com',
    cta: 'Send email',
  },
  {
    icon: 'location_on',
    title: 'Location',
    lines: ['Dubai, UAE', 'Delivering across the UAE'],
    href: null,
    cta: null,
  },
  {
    icon: 'schedule',
    title: 'Hours',
    lines: ['Mon – Sat: 9 AM – 9 PM', 'Orders before 12 PM for same-day'],
    href: null,
    cta: null,
  },
];

export default function ContactPage() {
  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'LocalBusiness',
    name: 'Melting Moments Cakes',
    url: 'https://meltingmomentscakes.com',
    telephone: '+971501234567',
    email: 'hello@meltingmomentscakes.com',
    address: {
      '@type': 'PostalAddress',
      addressCountry: 'AE',
      addressRegion: 'Dubai',
    },
    openingHours: 'Mo-Sa 09:00-21:00',
    sameAs: [
      'https://www.instagram.com/meltingmomentscakes',
      'https://wa.me/971501234567',
    ],
    areaServed: 'AE',
    currenciesAccepted: 'AED',
  };

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      {/* Header */}
      <div className="bg-[#f9f5f0] border-b border-secondary/30">
        <div className="max-w-3xl mx-auto px-4 py-14 text-center">
          <p className="text-xs font-body uppercase tracking-widest text-secondary mb-3">Reach Out</p>
          <h1 className="font-display text-4xl sm:text-5xl text-primary mb-4">Get in Touch</h1>
          <p className="font-body text-sm text-gray-500 max-w-md mx-auto">
            Custom orders, delivery questions, event catering — we&apos;re always happy to help.
          </p>
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-4 py-14">

        {/* Info Cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-14">
          {INFO_CARDS.map(({ icon, title, lines, href, cta }) => (
            <div
              key={title}
              className="border border-gray-200 p-5 text-center hover:border-primary transition-colors group"
            >
              <span className="material-icons text-2xl text-secondary group-hover:text-primary mb-3 block transition-colors">
                {icon}
              </span>
              <h3 className="font-body text-xs font-medium uppercase tracking-widest text-gray-700 mb-2">
                {title}
              </h3>
              {lines.map(line => (
                <p key={line} className="font-body text-xs text-gray-500 leading-relaxed">
                  {line}
                </p>
              ))}
              {href && cta && (
                <a
                  href={href}
                  target={href.startsWith('http') ? '_blank' : undefined}
                  rel={href.startsWith('http') ? 'noopener noreferrer' : undefined}
                  className="inline-block mt-3 text-[11px] font-body uppercase tracking-widest text-primary hover:underline transition-colors"
                >
                  {cta} →
                </a>
              )}
            </div>
          ))}
        </div>

        <div className="grid lg:grid-cols-2 gap-12 lg:gap-16">

          {/* Contact Form */}
          <div>
            <h2 className="font-display text-2xl text-primary mb-6">Send a Message</h2>
            <ContactForm />
          </div>

          {/* Map + Social */}
          <div>
            <h2 className="font-display text-2xl text-primary mb-6">Find Us</h2>

            {/* Google Maps embed */}
            <div className="aspect-[4/3] w-full overflow-hidden border border-gray-200 mb-8">
              <iframe
                src="https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d462560.3010168743!2d54.89782582968765!3d25.07575470416587!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!3m3!1m2!1s0x3e5f43496ad9c645%3A0xbde66e5084295162!2sDubai%20-%20United%20Arab%20Emirates!5e0!3m2!1sen!2s!4v1700000000000!5m2!1sen!2s"
                width="100%"
                height="100%"
                style={{ border: 0 }}
                allowFullScreen
                loading="lazy"
                referrerPolicy="no-referrer-when-downgrade"
                title="Melting Moments Cakes — Dubai, UAE"
              />
            </div>

            {/* Social Links */}
            <div>
              <h3 className="text-xs font-body uppercase tracking-widest text-gray-500 mb-4">Follow Along</h3>
              <div className="flex flex-col sm:flex-row gap-3">
                <a
                  href="https://www.instagram.com/meltingmomentscakes"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-3 border border-gray-200 hover:border-primary px-4 py-3 transition-colors group"
                >
                  <svg
                    viewBox="0 0 24 24"
                    className="w-5 h-5 text-gray-500 group-hover:text-primary transition-colors"
                    fill="currentColor"
                  >
                    <path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838c-3.403 0-6.162 2.759-6.162 6.162s2.759 6.163 6.162 6.163 6.162-2.759 6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162c-2.209 0-4-1.79-4-4 0-2.209 1.791-4 4-4s4 1.791 4 4c0 2.21-1.791 4-4 4zm6.406-11.845c-.796 0-1.441.645-1.441 1.44s.645 1.44 1.441 1.44c.795 0 1.439-.645 1.439-1.44s-.644-1.44-1.439-1.44z"/>
                  </svg>
                  <div>
                    <p className="text-xs font-body font-medium text-gray-700 group-hover:text-primary transition-colors">
                      Instagram
                    </p>
                    <p className="text-[11px] font-body text-gray-400">@meltingmomentscakes</p>
                  </div>
                </a>
                <a
                  href="https://wa.me/971501234567"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-3 border border-gray-200 hover:border-primary px-4 py-3 transition-colors group"
                >
                  <svg
                    viewBox="0 0 24 24"
                    className="w-5 h-5 text-gray-500 group-hover:text-primary transition-colors"
                    fill="currentColor"
                  >
                    <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/>
                  </svg>
                  <div>
                    <p className="text-xs font-body font-medium text-gray-700 group-hover:text-primary transition-colors">
                      WhatsApp
                    </p>
                    <p className="text-[11px] font-body text-gray-400">+971 50 123 4567</p>
                  </div>
                </a>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
