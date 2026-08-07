'use client';

import { useState } from 'react';
import { analytics } from '@/lib/analytics';
import { cn } from '@/lib/utils';

interface FaqItem {
  q: string;
  a: string;
}

function AccordionItem({ question, answer, index }: { question: string; answer: string; index: number }) {
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
        <span className={cn('material-icons text-gray-400 shrink-0 transition-transform duration-200', open && 'rotate-180')}>
          expand_more
        </span>
      </button>

      <div className={cn('overflow-hidden transition-all duration-300', open ? 'max-h-96 pb-5' : 'max-h-0')}>
        <p className="font-body text-sm text-gray-500 leading-relaxed pl-10">{answer}</p>
      </div>
    </div>
  );
}

export function FaqAccordion({ faqs }: { faqs: FaqItem[] }) {
  return (
    <div className="border border-gray-200 px-4 sm:px-8">
      {faqs.map((faq, i) => (
        <AccordionItem key={i} question={faq.q} answer={faq.a} index={i} />
      ))}
    </div>
  );
}
