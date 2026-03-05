'use client';

import { useState } from 'react';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { useAuth } from '@/lib/auth-context';
import { authApi, ApiError } from '@/lib/api';
import { useToast } from '@/components/ui';

export default function SettingsPage() {
  const { user, setUser } = useAuth();
  const { addToast } = useToast();

  const [profile, setProfile] = useState({
    first_name: user?.first_name || '',
    last_name: user?.last_name || '',
    phone: user?.phone || '',
  });
  const [profileErrors, setProfileErrors] = useState<Record<string, string>>({});
  const [savingProfile, setSavingProfile] = useState(false);

  const [sendingReset, setSendingReset] = useState(false);
  const [resetSent, setResetSent] = useState(false);

  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  if (!user) return null;

  function validateProfile() {
    const e: Record<string, string> = {};
    if (!profile.first_name.trim()) e.first_name = 'Required';
    if (!profile.last_name.trim()) e.last_name = 'Required';
    setProfileErrors(e);
    return Object.keys(e).length === 0;
  }

  async function handleSaveProfile(e: React.FormEvent) {
    e.preventDefault();
    if (!validateProfile()) return;
    setSavingProfile(true);
    try {
      const updated = await authApi.updateMe({
        first_name: profile.first_name.trim(),
        last_name: profile.last_name.trim(),
        phone: profile.phone || undefined,
      });
      setUser(updated);
      addToast('Profile updated', 'success');
    } catch (err) {
      addToast(err instanceof ApiError ? err.message : 'Failed to update profile', 'error');
    } finally {
      setSavingProfile(false);
    }
  }

  async function handleSendReset() {
    if (!user) return;
    setSendingReset(true);
    try {
      await authApi.forgotPassword(user.email);
      setResetSent(true);
      addToast('Password reset email sent', 'success');
    } catch {
      addToast('Failed to send reset email', 'error');
    } finally {
      setSendingReset(false);
    }
  }

  return (
    <div className="space-y-10">
      <h1 className="font-display text-2xl text-primary">Settings</h1>

      {/* Profile Section */}
      <section>
        <h2 className="text-xs font-body uppercase tracking-widest text-gray-500 mb-4 pb-2 border-b border-gray-100">
          Profile Information
        </h2>
        <form onSubmit={handleSaveProfile} className="space-y-4 max-w-md">
          <div className="grid grid-cols-2 gap-3">
            <Input
              label="First Name"
              value={profile.first_name}
              onChange={e => setProfile(p => ({ ...p, first_name: e.target.value }))}
              error={profileErrors.first_name}
              autoComplete="given-name"
            />
            <Input
              label="Last Name"
              value={profile.last_name}
              onChange={e => setProfile(p => ({ ...p, last_name: e.target.value }))}
              error={profileErrors.last_name}
              autoComplete="family-name"
            />
          </div>
          <Input
            label="Email"
            value={user.email}
            disabled
            helper="Contact support to change your email address"
          />
          <Input
            label="Phone (optional)"
            type="tel"
            value={profile.phone}
            onChange={e => setProfile(p => ({ ...p, phone: e.target.value }))}
            placeholder="+971 50 000 0000"
            autoComplete="tel"
          />
          <Button type="submit" loading={savingProfile}>
            Save Changes
          </Button>
        </form>
      </section>

      {/* Change Password Section */}
      <section>
        <h2 className="text-xs font-body uppercase tracking-widest text-gray-500 mb-4 pb-2 border-b border-gray-100">
          Change Password
        </h2>
        <div className="max-w-md">
          {resetSent ? (
            <div className="bg-green-50 border border-green-200 p-4">
              <p className="text-sm text-green-700 font-body">
                A password reset link has been sent to <strong>{user.email}</strong>.
                Check your inbox and follow the instructions.
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-sm text-gray-500 font-body">
                We&apos;ll send a reset link to your email address so you can choose a new password.
              </p>
              <Button variant="ghost" onClick={handleSendReset} loading={sendingReset}>
                Send Password Reset Email
              </Button>
            </div>
          )}
        </div>
      </section>

      {/* Delete Account Section */}
      <section>
        <h2 className="text-xs font-body uppercase tracking-widest text-gray-500 mb-4 pb-2 border-b border-gray-100">
          Delete Account
        </h2>
        <div className="max-w-md">
          {!showDeleteConfirm ? (
            <div className="space-y-3">
              <p className="text-sm text-gray-500 font-body">
                Permanently delete your account and all associated data. This action cannot be undone.
              </p>
              <Button
                variant="ghost"
                onClick={() => setShowDeleteConfirm(true)}
                className="border-red-300 text-red-500 hover:bg-red-50"
              >
                Delete My Account
              </Button>
            </div>
          ) : (
            <div className="border border-red-200 bg-red-50 p-4 space-y-3">
              <p className="text-sm font-medium text-red-700 font-body">Are you sure?</p>
              <p className="text-sm text-red-600 font-body">
                To delete your account, please contact us at{' '}
                <a href="mailto:hello@meltingmomentscakes.com" className="underline">
                  hello@meltingmomentscakes.com
                </a>{' '}
                or via{' '}
                <a
                  href="https://wa.me/971501234567"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline"
                >
                  WhatsApp
                </a>
                . We&apos;ll process your request within 48 hours.
              </p>
              <Button variant="ghost" size="sm" onClick={() => setShowDeleteConfirm(false)}>
                Cancel
              </Button>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
