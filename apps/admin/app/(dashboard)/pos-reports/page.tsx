import { redirect } from 'next/navigation';

// POS Reports opens on the Sales tab; there is no combined view.
export default function PosReportsIndexPage() {
  redirect('/pos-reports/sales');
}
