'use client';

import { useState } from 'react';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';

export function ContactForm() {
  const [form, setForm] = useState({ name: '', email: '', phone: '', message: '' });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitted, setSubmitted] = useState(false);

  function validate() {
    const e: Record<string, string> = {};
    if (!form.name.trim()) e.name = 'Name is required';
    if (!form.email.trim()) e.email = 'Email is required';
    if (!form.message.trim()) e.message = 'Message is required';
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!validate()) return;

    // Build a WhatsApp message with the form data
    const text = [
      `*New enquiry from meltingmomentscakes.com*`,
      `Name: ${form.name}`,
      `Email: ${form.email}`,
      form.phone ? `Phone: ${form.phone}` : null,
      `\nMessage:\n${form.message}`,
    ]
      .filter(Boolean)
      .join('\n');

    const url = `https://wa.me/971503687757?text=${encodeURIComponent(text)}`;
    window.open(url, '_blank', 'noopener,noreferrer');
    setSubmitted(true);
  }

  if (submitted) {
    return (
      <div className="text-center py-10">
        <span className="material-icons text-5xl text-primary mb-4 block">check_circle</span>
        <h3 className="font-display text-2xl text-primary mb-2">Message sent!</h3>
        <p className="font-body text-sm text-gray-500">
          Your WhatsApp has opened with the message. We&apos;ll get back to you as soon as possible.
        </p>
        <button
          onClick={() => { setSubmitted(false); setForm({ name: '', email: '', phone: '', message: '' }); }}
          className="mt-4 text-xs text-primary hover:underline font-body uppercase tracking-widest"
        >
          Send another message
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="grid sm:grid-cols-2 gap-4">
        <Input
          label="Name"
          value={form.name}
          onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
          error={errors.name}
          autoComplete="name"
        />
        <Input
          label="Email"
          type="email"
          value={form.email}
          onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
          error={errors.email}
          autoComplete="email"
        />
      </div>
      <Input
        label="Phone (optional)"
        type="tel"
        value={form.phone}
        onChange={e => setForm(f => ({ ...f, phone: e.target.value }))}
        placeholder="+971 50 000 0000"
        autoComplete="tel"
      />
      <div className="w-full">
        <label className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1.5">
          Message
        </label>
        <textarea
          value={form.message}
          onChange={e => setForm(f => ({ ...f, message: e.target.value }))}
          rows={5}
          className={`w-full px-3.5 py-2.5 text-sm font-body bg-white border rounded-sm outline-none transition-all duration-200 placeholder:text-gray-400 focus:border-primary focus:ring-2 focus:ring-primary/20 resize-none ${
            errors.message ? 'border-red-400 focus:border-red-400 focus:ring-red-400/20' : 'border-gray-300'
          }`}
          placeholder="Tell us about your order, event, or any questions you have..."
        />
        {errors.message && <p className="mt-1.5 text-xs text-red-500">{errors.message}</p>}
      </div>
      <Button type="submit" fullWidth size="lg">
        <span className="material-icons text-[16px]">send</span>
        Send Message via WhatsApp
      </Button>
      <p className="text-xs text-center text-gray-400 font-body">
        Submitting will open WhatsApp with your message pre-filled.
      </p>
    </form>
  );
}
