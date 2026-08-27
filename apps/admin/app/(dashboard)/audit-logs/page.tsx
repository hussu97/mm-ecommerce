import { redirect } from 'next/navigation';

// Moved into the Logs section (Audit tab). Kept as a redirect so old bookmarks
// and muscle-memory URLs still land.
export default function AuditLogsRedirect() {
  redirect('/logs/audit');
}
