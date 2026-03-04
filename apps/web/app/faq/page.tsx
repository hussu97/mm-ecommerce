import type { Metadata } from 'next';
import Link from 'next/link';
import { FaqAccordion } from './FaqAccordion';

export const metadata: Metadata = {
  title: 'FAQ',
  description:
    'Frequently asked questions about ordering, delivery, payments, and products at Melting Moments Cakes in the UAE.',
  openGraph: {
    title: 'FAQ | Melting Moments Cakes',
    description: 'Answers to your questions about ordering, delivery, and our handcrafted products.',
  },
};

const FAQS = [
  {
    q: 'Where do you deliver?',
    a: 'We deliver across the UAE — including Dubai, Sharjah, Ajman, Abu Dhabi, Ras Al Khaimah, Fujairah, and Umm Al Quwain. Delivery to Dubai, Sharjah, and Ajman is AED 35. All other emirates are AED 50. Orders above AED 200 qualify for free delivery.',
  },
  {
    q: 'How far in advance do I need to order?',
    a: "We recommend placing orders at least 24–48 hours in advance to ensure freshness and availability. For large or custom orders (events, corporate gifting, weddings), please reach out at least 5–7 days ahead so we can plan accordingly.",
  },
  {
    q: 'Can I pick up my order?',
    a: "Yes! Store pickup is available and completely free of charge. Once your order is placed, we'll confirm the pickup time and location via WhatsApp. Orders placed before 12 PM are typically ready for same-day pickup.",
  },
  {
    q: 'Do you take custom orders?',
    a: "Absolutely. We love creating bespoke treats for birthdays, Eid, weddings, corporate events, and more. Get in touch via WhatsApp or the contact form with your requirements and we'll put together something special just for you.",
  },
  {
    q: 'What payment methods do you accept?',
    a: 'We accept card payments online (Visa, Mastercard, and Apple Pay via Stripe). Buy-now-pay-later options through Tabby and Tamara are coming soon. Cash on delivery is not currently available.',
  },
  {
    q: 'Are your products halal?',
    a: 'Yes, all our products are 100% halal. We use halal-certified ingredients and maintain strict hygiene standards throughout our baking process. We do not use any alcohol-based flavourings.',
  },
  {
    q: 'How long do the products stay fresh?',
    a: "Our brownies and cookies stay fresh for 3–5 days when stored in a cool, dry place. For best results, keep them in an airtight container. Cookie melts are best enjoyed within 2–3 days. We don't use preservatives, so freshness is best enjoyed soon after delivery!",
  },
  {
    q: 'What if I have an allergy or dietary requirement?',
    a: 'Our products are made in a home kitchen that handles nuts, dairy, eggs, gluten, and other common allergens. We cannot guarantee an allergen-free environment. If you have a specific requirement, please contact us before ordering so we can advise you appropriately.',
  },
];

export default function FaqPage() {
  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: FAQS.map(({ q, a }) => ({
      '@type': 'Question',
      name: q,
      acceptedAnswer: { '@type': 'Answer', text: a },
    })),
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
          <p className="text-xs font-body uppercase tracking-widest text-secondary mb-3">Help Centre</p>
          <h1 className="font-display text-4xl sm:text-5xl text-primary mb-4">
            Frequently Asked Questions
          </h1>
          <p className="font-body text-sm text-gray-500 max-w-md mx-auto">
            Everything you need to know about ordering, delivery, and our products.
          </p>
        </div>
      </div>

      {/* FAQ List */}
      <div className="max-w-3xl mx-auto px-4 py-14">
        <FaqAccordion faqs={FAQS} />

        {/* CTA */}
        <div className="mt-12 text-center border border-secondary/40 py-10 px-6 bg-[#f9f5f0]">
          <p className="text-xs font-body uppercase tracking-widest text-secondary mb-3">Still have questions?</p>
          <h2 className="font-display text-2xl text-primary mb-3">We&apos;re here to help</h2>
          <p className="font-body text-sm text-gray-500 mb-6 max-w-sm mx-auto">
            Can&apos;t find the answer you&apos;re looking for? Reach out and we&apos;ll get back to you as soon as possible.
          </p>
          <div className="flex flex-col sm:flex-row gap-3 justify-center">
            <a
              href="https://wa.me/971501234567"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center gap-2 px-6 py-3 bg-primary text-white text-xs font-body uppercase tracking-widest hover:opacity-90 transition-opacity"
            >
              <span className="material-icons text-[16px]">chat</span>
              WhatsApp Us
            </a>
            <Link
              href="/contact"
              className="inline-flex items-center justify-center gap-2 px-6 py-3 border border-primary text-primary text-xs font-body uppercase tracking-widest hover:bg-primary hover:text-white transition-all duration-200"
            >
              <span className="material-icons text-[16px]">mail</span>
              Contact Form
            </Link>
          </div>
        </div>
      </div>
    </>
  );
}
