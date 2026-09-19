import { Reveal } from './Reveal';
import { Icon } from '@/components/ui/Icon';
import { CaterGallery } from './CaterGallery';
import { CaterEnquiryForm, type CaterFormCopy } from './CaterEnquiryForm';

export interface CaterGalleryItem {
  /** Public image URL (a GCS object under the `cater/` folder). Named `image`
   *  to match the baker gallery's item shape and the admin's `GalleryItem`. */
  image?: string;
  /** Alt text / caption, e.g. the occasion this cake was made for. */
  alt?: string;
}

export interface CaterContent {
  title?: string;
  subtitle?: string;
  /** Real custom cakes to show off, per occasion. Rendered as an auto-scrolling,
   *  click-to-zoom gallery. Editable in the admin Content tab. */
  gallery?: CaterGalleryItem[];
  /** Copy for the inline enquiry form. Bilingual via the CMS; every field falls
   *  back to a sensible English default so the form works before it is edited. */
  form?: CaterFormCopy;
}

export function CaterSection({ c, locale }: { c: CaterContent; locale: string }) {
  const gallery = c.gallery ?? [];

  return (
    <section aria-label="Custom orders we cater to" className="py-14 sm:py-20 bg-[#f4ece4]">
      <div className="max-w-7xl mx-auto px-4 sm:px-6">

        <Reveal className="text-center mb-9 sm:mb-11">
          {c.title && (
            <h2 className="font-display text-2xl sm:text-3xl text-gray-800 uppercase tracking-[0.18em]">
              {c.title}
            </h2>
          )}
          {c.subtitle && (
            <p className="font-body text-sm text-gray-500 mt-2.5">{c.subtitle}</p>
          )}
        </Reveal>

        {gallery.length > 0 && (
          <Reveal delay={80} className="mt-10 sm:mt-12">
            <CaterGallery items={gallery} />
          </Reveal>
        )}

        <Reveal delay={120} className="mt-10 sm:mt-14">
          <div className="max-w-2xl mx-auto bg-white border border-secondary/40 shadow-[0_20px_60px_-40px_rgba(138,90,100,0.55)] p-6 sm:p-9">
            <div className="flex items-center gap-2 mb-1.5">
              <Icon name="cake" className="text-primary text-[20px]" />
              <h3 className="font-display text-lg sm:text-xl text-gray-800 uppercase tracking-[0.14em]">
                {c.form?.heading ?? 'Customized cake order'}
              </h3>
            </div>
            <p className="font-body text-sm text-gray-500 mb-6">
              {c.form?.intro ??
                'Tell us what you have in mind and we’ll get back to you to plan the details. This is an enquiry, not an order — nothing is charged.'}
            </p>
            <CaterEnquiryForm copy={c.form} locale={locale} />
          </div>
        </Reveal>

      </div>
    </section>
  );
}
