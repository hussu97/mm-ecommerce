'use client';

import { type ReactNode, useState } from 'react';

import { RecipeEditor } from '@/components/inventory/RecipeEditor';
import { Modal } from '@/components/pos/ResourcePage';

type OwnerKind = 'product' | 'modifier_option' | 'inventory_item';

/**
 * Opens the recipe editor in a popup **in place** — wherever a recipe is viewed
 * (an inventory item, a product, a modifier option) — instead of deep-linking to
 * `/recipes/<tab>?open=<id>`, which navigated to the recipes page first and only
 * then opened the very same modal from the `open` query param.
 *
 * `RecipeEditor` fetches its own data from `ownerKind` + `ownerId`, so the popup
 * stands alone with no dependency on the recipes page. `children` is a render prop
 * handed an `open` callback, so each caller keeps its own trigger styling (a table
 * RowAction, a text link, a ghost Button) rather than this imposing one.
 */
export function RecipeButton({
  ownerId,
  ownerKind,
  ownerLabel,
  children,
}: {
  ownerId: string;
  ownerKind: OwnerKind;
  ownerLabel: string;
  children: (open: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      {children(() => setOpen(true))}
      {open && (
        <Modal title={`Recipe for ${ownerLabel}`} onClose={() => setOpen(false)} wide>
          <RecipeEditor ownerKind={ownerKind} ownerId={ownerId} ownerLabel={ownerLabel} />
        </Modal>
      )}
    </>
  );
}
