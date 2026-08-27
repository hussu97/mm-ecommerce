import { redirect } from 'next/navigation';

// Moved into the Logs section (Webhooks tab). Kept as a redirect so old
// bookmarks and muscle-memory URLs still land.
export default function WebhookLogsRedirect() {
  redirect('/logs/webhooks');
}
