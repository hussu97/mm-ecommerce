import { redirect } from 'next/navigation';

// The POS Configuration section opens on the Payment Methods tab; there is no
// combined view.
export default function PosConfigIndexPage() {
  redirect('/pos-config/payment-methods');
}
