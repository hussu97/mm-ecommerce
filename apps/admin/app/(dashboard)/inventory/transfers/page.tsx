import { redirect } from 'next/navigation';

/**
 * The Transfers & Production tab lands on the create form.
 *
 * This route used to be the transfer-template configurator; templates were
 * never used to raise a transfer and were dropped with their tables
 * (migration 307). Raising an order is `/inventory/transfers/new`, an order is
 * `/inventory/transfers/[id]`, and the log lives under Report submissions.
 */
export default function TransfersIndexPage() {
  redirect('/inventory/transfers/new');
}
