'use client';

import { useRef, useState } from 'react';
import { Input } from '@/components/ui/Input';
import { Textarea } from '@/components/ui/Textarea';
import { Button } from '@/components/ui/Button';
import { PhoneInput, isValidPhone } from '@/components/ui/PhoneInput';
import { Turnstile, isTurnstileEnabled } from '@/components/ui/Turnstile';
import { Icon } from '@/components/ui/Icon';
import { enquiryApi, ApiError } from '@/lib/api';
import { useTranslation } from '@/lib/i18n/TranslationProvider';

/**
 * Copy for the enquiry form, from the CMS `cater.form` block. Every field is
 * optional and falls back to English below, so the form works before the content
 * is edited; the Arabic strings are seeded alongside so it reads correctly in
 * both languages.
 */
export interface CaterFormCopy {
  heading?: string;
  intro?: string;
  name_label?: string;
  phone_label?: string;
  description_label?: string;
  description_placeholder?: string;
  kg_label?: string;
  images_label?: string;
  images_hint?: string;
  delivery_label?: string;
  delivery_note?: string;
  submit_label?: string;
  success_title?: string;
  success_body?: string;
}

const MAX_IMAGES = 4;
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
// HEIC/HEIF are an iPhone's native photo format. Safari hands us a JPEG when the
// photo is picked from the library, but a raw .heic (Files app / iCloud Drive)
// arrives as image/heic — the API re-encodes it to JPEG, so accept it here too.
const ALLOWED_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif'];

interface Picked {
  file: File;
  preview: string;
}

export function CaterEnquiryForm({ copy, locale }: { copy?: CaterFormCopy; locale: string }) {
  const c = copy ?? {};
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [description, setDescription] = useState('');
  const [approxKg, setApproxKg] = useState('');
  const [deliveryBy, setDeliveryBy] = useState('');
  const [images, setImages] = useState<Picked[]>([]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [apiError, setApiError] = useState('');
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [turnstileToken, setTurnstileToken] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);

  // Today, in the browser's local date, so the picker cannot offer a past day.
  const today = new Date().toISOString().slice(0, 10);

  function addFiles(list: FileList | null) {
    if (!list) return;
    const next: Picked[] = [];
    let rejected = '';
    for (const file of Array.from(list)) {
      if (images.length + next.length >= MAX_IMAGES) {
        rejected = c.images_hint ?? `You can add up to ${MAX_IMAGES} photos.`;
        break;
      }
      if (!ALLOWED_TYPES.includes(file.type)) {
        rejected = 'Please use a JPG, PNG, WEBP or HEIC image.';
        continue;
      }
      if (file.size > MAX_IMAGE_BYTES) {
        rejected = 'Each photo must be under 5 MB.';
        continue;
      }
      next.push({ file, preview: URL.createObjectURL(file) });
    }
    if (next.length) setImages((prev) => [...prev, ...next]);
    setErrors((e) => ({ ...e, images: rejected }));
    // Let the same file be re-picked after a removal.
    if (fileInput.current) fileInput.current.value = '';
  }

  function removeImage(i: number) {
    setImages((prev) => {
      const copy = [...prev];
      const [gone] = copy.splice(i, 1);
      if (gone) URL.revokeObjectURL(gone.preview);
      return copy;
    });
  }

  function validate() {
    const e: Record<string, string> = {};
    if (!name.trim()) e.name = locale === 'ar' ? 'الاسم مطلوب' : 'Your name is required';
    // Same validation as checkout's phone field: required + a real number, with
    // the same message. No Firebase OTP step here — this is an enquiry, not an
    // account or a delivery address.
    if (!phone.trim() || !isValidPhone(phone)) e.phone = t('checkout.valid_phone_required');
    if (!description.trim())
      e.description = locale === 'ar' ? 'الوصف مطلوب' : 'Please describe what you’d like';
    if (!approxKg.trim())
      e.approxKg = locale === 'ar' ? 'الوزن التقريبي أو عدد الحصص مطلوب' : 'Approx. weight or servings is required';
    else if (!(Number(approxKg) > 0)) e.approxKg = locale === 'ar' ? 'أدخل قيمة صحيحة' : 'Enter a valid number';
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  async function handleSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    if (!validate()) return;
    setLoading(true);
    setApiError('');
    try {
      // Upload photos first, collecting their URLs. The upload endpoint is not
      // Turnstile-gated (a token is single-use, and there may be several photos);
      // the human check is spent on the submit below.
      const urls: string[] = [];
      for (const { file } of images) {
        const res = await enquiryApi.uploadImage(file);
        urls.push(res.url);
      }
      await enquiryApi.submit({
        customer_name: name.trim(),
        customer_phone: phone,
        description: description.trim(),
        approx_kg: approxKg ? Number(approxKg) : null,
        reference_image_urls: urls,
        delivery_by: deliveryBy || null,
        turnstile_token: turnstileToken || undefined,
      });
      images.forEach((p) => URL.revokeObjectURL(p.preview));
      setDone(true);
    } catch (err) {
      setApiError(
        err instanceof ApiError
          ? err.message
          : locale === 'ar'
            ? 'تعذّر إرسال طلبك. حاول مرة أخرى.'
            : 'We couldn’t send your request. Please try again.',
      );
    } finally {
      setLoading(false);
    }
  }

  if (done) {
    return (
      <div className="text-center py-6">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-green-50 text-green-600">
          <Icon name="check_circle" className="text-[32px]" />
        </div>
        <h4 className="font-display text-lg text-gray-800 mb-1.5">
          {c.success_title ?? (locale === 'ar' ? 'تم استلام طلبك!' : 'Request received!')}
        </h4>
        <p className="font-body text-sm text-gray-500 max-w-md mx-auto">
          {c.success_body ??
            (locale === 'ar'
              ? 'شكراً لك. سنراجع طلبك ونتواصل معك قريباً لتأكيد التفاصيل وموعد التسليم.'
              : 'Thank you — we’ll review your request and get back to you soon to confirm the details and delivery date.')}
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4" noValidate>
      {apiError && (
        <div className="bg-red-50 border border-red-200 text-red-600 text-sm px-4 py-3 rounded-sm">
          {apiError}
        </div>
      )}

      <Input
        label={c.name_label ?? (locale === 'ar' ? 'الاسم' : 'Your name')}
        value={name}
        onChange={(e) => setName(e.target.value)}
        error={errors.name}
        autoComplete="name"
      />

      {/* Exactly the checkout phone field — same PhoneInput, same label and
          validation message — minus the Firebase verification panel. */}
      <PhoneInput
        label={c.phone_label ?? t('common.phone')}
        value={phone}
        onChange={setPhone}
        error={errors.phone}
      />

      <Textarea
        label={c.description_label ?? (locale === 'ar' ? 'ماذا تريد؟' : 'What would you like?')}
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        error={errors.description}
        rows={4}
        placeholder={
          c.description_placeholder ??
          (locale === 'ar'
            ? 'صف الكيكة أو الحلوى، النكهات، الألوان، المناسبة...'
            : 'Describe the cake or dessert — flavours, colours, the occasion…')
        }
      />

      <Input
        label={c.kg_label ?? (locale === 'ar' ? 'الوزن التقريبي (كجم) / عدد الحصص' : 'Approx. weight (kg) / servings')}
        type="number"
        min="0"
        step="0.5"
        inputMode="decimal"
        value={approxKg}
        onChange={(e) => setApproxKg(e.target.value)}
        error={errors.approxKg}
      />

      {/* Inspiration photos — up to four, chosen from the device's photo library
          or files. No `capture`, so mobile opens the library/file picker rather
          than forcing the camera. */}
      <div>
        <label className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1.5">
          {c.images_label ?? (locale === 'ar' ? 'صور للإلهام — اختياري' : 'Inspiration photos — optional')}
        </label>
        <div className="flex flex-wrap gap-3">
          {images.map((img, i) => (
            <div key={img.preview} className="relative h-20 w-20 overflow-hidden rounded-sm border border-gray-300">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={img.preview} alt="" className="h-full w-full object-cover" />
              <button
                type="button"
                onClick={() => removeImage(i)}
                aria-label="Remove photo"
                className="absolute top-0.5 end-0.5 flex h-6 w-6 items-center justify-center rounded-full bg-black/55 text-white hover:bg-black/75"
              >
                <Icon name="close" className="text-[16px]" />
              </button>
            </div>
          ))}
          {images.length < MAX_IMAGES && (
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              className="flex h-20 w-20 flex-col items-center justify-center gap-1 rounded-sm border border-dashed border-gray-300 text-gray-400 hover:border-primary hover:text-primary transition-colors"
            >
              <Icon name="add" className="text-[22px]" />
              <span className="font-body text-[10px]">{locale === 'ar' ? 'أضف' : 'Add'}</span>
            </button>
          )}
        </div>
        <input
          ref={fileInput}
          type="file"
          accept="image/jpeg,image/png,image/webp,image/heic,image/heif"
          multiple
          className="hidden"
          onChange={(e) => addFiles(e.target.files)}
        />
        <p className="mt-1.5 text-xs text-gray-500">
          {errors.images
            ? <span className="text-red-500">{errors.images}</span>
            : c.images_hint ?? (locale === 'ar' ? `حتى ${MAX_IMAGES} صور` : `Up to ${MAX_IMAGES} photos`)}
        </p>
      </div>

      <Input
        label={c.delivery_label ?? (locale === 'ar' ? 'التسليم بحلول' : 'Delivery by')}
        type="date"
        min={today}
        value={deliveryBy}
        onChange={(e) => setDeliveryBy(e.target.value)}
        helper={
          c.delivery_note ??
          (locale === 'ar'
            ? 'موعد التسليم يُؤكَّد فقط بعد مراجعة طلبك.'
            : 'The delivery date will be confirmed only after we review your request.')
        }
      />

      <Turnstile onToken={setTurnstileToken} />

      <Button
        type="submit"
        fullWidth
        loading={loading}
        size="lg"
        disabled={isTurnstileEnabled() && !turnstileToken}
      >
        {c.submit_label ?? (locale === 'ar' ? 'إرسال الطلب' : 'Send request')}
      </Button>
    </form>
  );
}
