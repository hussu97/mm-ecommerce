'use client';

/**
 * Which card processor is taking money, right now.
 *
 * This screen is the entire reason the gateway abstraction exists. Card
 * payments used to be a Stripe integration with the word "stripe" compiled into
 * the checkout page, so answering a Stripe incident meant a branch, a review, a
 * build and a deploy — at 2am, under load, with the shop's takings stopped.
 * Now it is a toggle, and it takes effect on the next checkout: the router
 * re-reads these rows on every request rather than caching them.
 *
 * The customer sees none of this. They chose "card". Which processor settles it
 * is ours, and it is deliberately not something the browser can ask for.
 *
 * **Ziina is built and switched off on production.** Two locks hold it there and
 * only one of them is on this screen: the row is inactive (undoable here), and
 * the container has no Ziina credentials (not undoable here, at all). A gateway
 * that is not configured cannot be activated — which is what makes it safe for
 * this page to exist in production while Ziina is not signed off.
 */

import { useCallback, useEffect, useState } from 'react';
import { paymentGatewaysApi } from '@/lib/api';
import type { PaymentGateway } from '@/lib/types';
import { Badge, Button, Input, LoadError, Spinner } from '@/components/ui';

export default function PaymentGatewaysPage() {
  const [gateways, setGateways] = useState<PaymentGateway[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setGateways(await paymentGatewaysApi.list());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load payment gateways');
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const patch = useCallback(
    async (code: string, data: Parameters<typeof paymentGatewaysApi.update>[1]) => {
      setBusy(code);
      setNotice(null);
      try {
        const updated = await paymentGatewaysApi.update(code, data);
        setGateways((rows) =>
          (rows ?? []).map((row) => (row.code === code ? updated : row)),
        );
      } catch (err) {
        // The server refuses two things on purpose — activating a gateway with
        // no credentials, and switching off the last working one — and both
        // refusals carry a sentence worth reading. Surfaced verbatim rather
        // than flattened into "something went wrong".
        setNotice(err instanceof Error ? err.message : 'Could not update the gateway');
      } finally {
        setBusy(null);
      }
    },
    [],
  );

  if (error) return <LoadError message={error} onRetry={load} />;
  if (!gateways) {
    return (
      <div className="flex justify-center py-16">
        <Spinner />
      </div>
    );
  }

  const routable = gateways.filter((g) => g.is_routable);

  return (
    <div className="space-y-4">
      <header>
        <h1 className="font-display text-xl uppercase tracking-widest text-primary">
          Payment Gateways
        </h1>
        <p className="font-body text-xs text-gray-500 mt-1 max-w-2xl">
          Which processor settles a card payment. Customers only ever see
          &ldquo;card&rdquo; — this decides who handles it, and changes apply to
          the next checkout. Lower priority goes first; the next one down is
          used automatically if the first cannot create a session.
        </p>
      </header>

      {routable.length === 0 && (
        <div className="border border-red-200 bg-red-50 px-4 py-3">
          <p className="font-body text-xs text-red-700">
            <span className="font-semibold">Card checkout is down.</span> No
            gateway is both active and configured, so every card order will be
            refused. Cash-on-collection is unaffected.
          </p>
        </div>
      )}

      {routable.length === 1 && (
        <div className="border border-amber-200 bg-amber-50 px-4 py-3">
          <p className="font-body text-xs text-amber-800">
            Only <span className="font-semibold">{routable[0].name}</span> can
            take cards. There is nothing to fail over to if it has an incident.
          </p>
        </div>
      )}

      {notice && (
        <div className="border border-amber-200 bg-amber-50 px-4 py-3">
          <p className="font-body text-xs text-amber-800">{notice}</p>
        </div>
      )}

      <div className="space-y-3">
        {gateways.map((gateway) => (
          <GatewayCard
            key={gateway.code}
            gateway={gateway}
            busy={busy === gateway.code}
            onPatch={patch}
          />
        ))}
      </div>
    </div>
  );
}

function GatewayCard({
  gateway,
  busy,
  onPatch,
}: {
  gateway: PaymentGateway;
  busy: boolean;
  onPatch: (
    code: string,
    data: Parameters<typeof paymentGatewaysApi.update>[1],
  ) => Promise<void>;
}) {
  // Uncontrolled, and re-keyed on the saved value rather than resynced by an
  // effect: the field only needs to follow the server after a save, and `key`
  // says that in one line where an effect said it in four and re-rendered twice.

  return (
    <div className="bg-white border border-gray-200 p-4">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="font-body text-sm font-semibold text-gray-800">
              {gateway.name}
            </h2>
            {gateway.is_routable ? (
              <Badge variant="success">Taking payments</Badge>
            ) : gateway.is_active ? (
              <Badge variant="danger">Active but unusable</Badge>
            ) : (
              <Badge variant="neutral">Off</Badge>
            )}
            {gateway.test_mode && <Badge variant="warning">Test mode</Badge>}
            {!gateway.supports_failover && (
              <Badge variant="neutral">No auto-failover</Badge>
            )}
          </div>
          <p className="font-body text-[11px] text-gray-400 mt-1">
            {gateway.is_configured
              ? `Priority ${gateway.priority}`
              : 'No credentials on this server — cannot be switched on here'}
            {gateway.min_amount !== null && ` · min AED ${gateway.min_amount}`}
            {gateway.max_amount !== null && ` · max AED ${gateway.max_amount}`}
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Input
            key={gateway.priority}
            aria-label={`${gateway.name} priority`}
            type="number"
            min={1}
            max={999}
            defaultValue={gateway.priority}
            onBlur={(e) => {
              const next = Number(e.target.value);
              if (Number.isFinite(next) && next !== gateway.priority) {
                void onPatch(gateway.code, { priority: next });
              }
            }}
            className="w-20"
          />
          <Button
            variant={gateway.is_active ? 'secondary' : 'primary'}
            size="sm"
            loading={busy}
            // The refusal lives on the server, which is the only place it can
            // actually be enforced. Disabling here as well so the impossible
            // action does not look available in the first place.
            disabled={!gateway.is_configured && !gateway.is_active}
            onClick={() => void onPatch(gateway.code, { is_active: !gateway.is_active })}
          >
            {gateway.is_active ? 'Switch off' : 'Switch on'}
          </Button>
        </div>
      </div>
    </div>
  );
}
