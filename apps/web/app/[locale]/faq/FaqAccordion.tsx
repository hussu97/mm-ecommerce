'use client';

import Link from 'next/link';
import { useState } from 'react';
import { analytics } from '@/lib/analytics';
import { parseInlineLinks } from '@/lib/inline-links';
import { cn } from '@/lib/utils';
import { Icon } from '@/components/ui/Icon';

interface FaqItem {
  q: string;
  a: string;
}

/** An answer's `[label](/path)` links, rendered; everything else as text. */
function Answer({ text, locale }: { text: string; locale: string }) {
  return (
    <>
      {parseInlineLinks(text).map((segment, i) =>
        segment.kind === 'text' ? (
          segment.text
        ) : segment.internal ? (
          <Link
            key={i}
            href={`/${locale}${segment.href}`}
            className="text-primary underline underline-offset-2 hover:opacity-80"
          >
            {segment.text}
          </Link>
        ) : (
          <a
            key={i}
            href={segment.href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary underline underline-offset-2 hover:opacity-80"
          >
            {segment.text}
          </a>
        ),
      )}
    </>
  );
}

function AccordionItem({
  question,
  answer,
  index,
  locale,
}: {
  question: string;
  answer: string;
  index: number;
  locale: string;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="border-b border-gray-200 last:border-0">
      <button
        onClick={() => {
          // Opening only. A close is the same question asked once, and counting
          // both would make a question that was read carefully look twice as
          // popular as one that was glanced at. What the list is for is finding
          // the questions the site should have answered before it was asked —
          // the delivery ones on the product page, the allergen ones on the PDP.
          if (!open) analytics.faqOpened({ question, position: index });
          setOpen(o => !o);
        }}
        className="w-full flex items-center justify-between gap-4 py-5 text-left"
        aria-expanded={open}
      >
        <div className="flex items-start gap-4">
          <span className="font-display text-secondary text-lg leading-none shrink-0 mt-0.5">
            {String(index + 1).padStart(2, '0')}
          </span>
          <span className="font-body text-sm font-medium text-gray-800 leading-relaxed">
            {question}
          </span>
        </div>
        <Icon
          name="expand_more"
          className={cn('text-gray-400 shrink-0 transition-transform duration-200', open && 'rotate-180')}
        />
      </button>

      <div className={cn('overflow-hidden transition-all duration-300', open ? 'max-h-96 pb-5' : 'max-h-0')}>
        <p className="font-body text-sm text-gray-500 leading-relaxed pl-10">
          <Answer text={answer} locale={locale} />
        </p>
      </div>
    </div>
  );
}

export function FaqAccordion({ faqs, locale }: { faqs: FaqItem[]; locale: string }) {
  return (
    <div className="border border-gray-200 px-4 sm:px-8">
      {faqs.map((faq, i) => (
        <AccordionItem key={i} question={faq.q} answer={faq.a} index={i} locale={locale} />
      ))}
    </div>
  );
}
