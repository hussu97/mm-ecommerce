import { redirect } from 'next/navigation';

// The Logs section opens on the Email tab; there is no combined view.
export default function LogsIndexPage() {
  redirect('/logs/email');
}
