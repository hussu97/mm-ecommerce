# Aggregator ops runbook — headed browser, GCS, reauth, clean slate

Everything below is a deliberate operator action on the prod VM (`mm-backend`,
`me-central1-a`, project `melting-moments-cakes`). Read the whole step before
running it; the storefront is briefly down during the VM stop.

## 0. Why this runbook exists
The anti-bot channels (Noon=Akamai, Talabat=PerimeterX, Deliveroo=Cloudflare)
drop **headless** Chrome — the warm never rotated their cookie and Talabat died.
**Headed real Chrome under Xvfb passes all three** (verified). The worker image
now runs headed under a virtual display, so warm AND reauth run on the VM. That
needs more RAM than the 1 GB e2-micro comfortably gives, and GCS signed
invoice-download URLs need a wider SA scope — both are one instance stop.

## 1. Resize the VM + widen the service-account scope (one stop)
A headed Chrome under Xvfb dipped the e2-micro to ~110 MB free (survived, but the
api container restarted once). Size up before leaning on the headed warm.

```bash
# Stop (storefront + POS go down for ~1–2 min)
gcloud compute instances stop mm-backend --zone=me-central1-a

# Resize: e2-small (2 GB) is the floor; e2-medium (4 GB) has headroom for headed Chrome
gcloud compute instances set-machine-type mm-backend --zone=me-central1-a \
  --machine-type=e2-medium

# Widen the default SA scope so GCS signed URLs work (IAM signBlob needs
# cloud-platform; the box currently has only devstorage.read_write). Upload/read
# already work; this is only for admin invoice-download links.
gcloud compute instances set-service-account mm-backend --zone=me-central1-a \
  --service-account=136865397988-compute@developer.gserviceaccount.com \
  --scopes=cloud-platform

gcloud compute instances start mm-backend --zone=me-central1-a
```
The SA already holds `roles/iam.serviceAccountTokenCreator` on itself (granted
2026-08). After start, containers come back on their own (compose restart policy).

## 2. Deploy the code
Push to `main`; the deploy workflow does `git reset --hard origin/main`, rebuilds
what changed, runs migrations, and recreates containers. This deploy carries:
- **Migration 161** — TRUNCATEs the scraped aggregator tables (order/item/
  statement/line/payout/reconciliation/sync_run) for a clean slate, and adds the
  reconcile-join indexes. Accounts, sessions, branch maps, GrubOps, Foodics and
  `orders` are untouched. Runs **once** (alembic-tracked).
- **Bootstrap image rebuild** — only when `apps/aggregator-bootstrap/**` changed
  (path filter). The new image adds `xvfb`+`xauth` and runs the CLI under
  `xvfb-run` with `HEADLESS=false`, so warm/login are headed. Force it if needed:
  ```bash
  gh workflow run deploy.yml   # or push a bootstrap change
  ```

## 3. Reauth any dead session (now on the VM, no laptop)
`login` spawns real Chrome on a CDP debug port; under the Xvfb entrypoint it runs
headed and passes the anti-bot wall. OTP channels (Noon/Talabat) auto-read the
code from their connected Graph mailbox.
```bash
gcloud compute ssh mm-backend --zone=me-central1-a
cd /opt/melting-moments-cakes
# --auto fills stored creds + reads the OTP; drop --auto to sign in by hand.
docker compose -f docker-compose.prod.yml run --rm aggregator-worker \
  login --channel talabat --auto
# Confirm it went live:
docker compose -f docker-compose.prod.yml exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "select channel,status,last_bootstrap_at from aggregator_session order by channel;"
```
Only ONE login runs at a time — the Chrome profile is lock-held. If a second
`login` reports "Chrome did not open a debug port", an earlier one still holds the
profile; wait for it to finish (or `docker rm -f` its `*-run-*` container).

## 4. Warm cron (automatic)
`/etc/cron.d/aggregator-warm` (installed by deploy) runs, Asia/Dubai:
- every 3 hours (`0 */3`) — Keeta order pull (in-page), so names/phones are captured before Keeta masks them.
- 22:15 — headed anti-bot warm for Noon + Talabat only (rotates the decaying cookie). Careem self-heals in the API sweep and is not warmed here.
- every 2 minutes — curl `GET /api/v1/aggregators/worker/needs-heal`; only if a channel is not live, `docker compose run --rm aggregator-worker heal-sessions` (no Xvfb). All-live ticks log and skip the container.
The API's daily pass (`AGGREGATOR_RUN_HOUR_DXB=23`) then sweeps sales+finance,
promotes, and reconciles every channel.

## 5. Re-ingest after the clean slate (first pass)
The clean-slate deploy leaves the scraped tables empty; the 23:00 pass refills
them. To backfill immediately without waiting:
```bash
docker compose -f docker-compose.prod.yml exec -T api python - <<'PY'
import asyncio
from app.services.aggregators import ingest
async def main():
    for ch in ("deliveroo","noon","talabat"):   # keeta comes from the 22:00 pull
        print(ch, "sales", await ingest.sweep_channel_once(ch, "sales", lookback_days=2))
        print(ch, "finance", await ingest.sweep_channel_once(ch, "finance", lookback_days=15))
    print("promote", await ingest.sweep_promote_once())
    print("reconcile", await ingest.sweep_reconcile_once())
asyncio.run(main())
PY
```

## 6. Deliveroo invoices/statement lines (in-page, headed)
Deliveroo's invoice DOWNLOAD is Cloudflare-gated to httpx, so it now runs in-page
in the headed worker and is pushed to `POST /aggregators/deliveroo/finance`.
Triggered by the warm; to run it on demand:
```bash
docker compose -f docker-compose.prod.yml run --rm aggregator-worker \
  warm-sessions --channel deliveroo
```

## 7. Verify the coupling closed
```bash
docker compose -f docker-compose.prod.yml exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off -c "
select o.channel,
       count(*)                               orders,
       count(o.mm_order_id)                   promoted,
       count(o.statement_id)                  linked_to_statement,
       count(o.customer_name)                 with_customer
from aggregator_order o group by o.channel order by o.channel;" -c "
select channel, count(*) lines, count(mm_order_id) linked_to_mm,
       count(statement_id) linked_to_statement
from aggregator_statement_line group by channel;" -c "
select channel, count(*) payouts, count(statement_id) linked_to_statement
from aggregator_payout group by channel;"
```
Healthy after a full pass: `promoted`, `linked_to_statement`, `with_customer`
(where the marketplace exposes it) and the statement-line `linked_to_mm` are all
non-zero, and payouts that are 1:1 with a statement carry `statement_id`
(accumulated transfers stay null by design — they reconcile at the period level).

## Guardrails
- **Never run more than one headed Chrome at once** on the VM — RAM. The warm and
  the reauth are already one-channel-at-a-time.
- The clean-slate TRUNCATE is irreversible; the data is re-fetchable, but do not
  re-run migration 161 against a restored older dump unless you mean to re-wipe.
- Watch `free -m` during any headed run until the resize is done.
