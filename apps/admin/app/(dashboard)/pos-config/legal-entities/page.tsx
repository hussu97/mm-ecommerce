import { redirect } from 'next/navigation';

/** Legal entities moved to their own section (list + a page per entity). */
export default function LegalEntitiesMoved() {
  redirect('/legal-entities');
}
