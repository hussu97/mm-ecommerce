import { redirect } from 'next/navigation';

// Moved into the Logs section (Email tab). Kept as a redirect so old bookmarks
// and muscle-memory URLs still land.
export default function EmailLogsRedirect() {
  redirect('/logs/email');
}
