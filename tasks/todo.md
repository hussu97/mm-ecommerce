# Courier aggregator rates — Deliveroo / Keeta / Careem / Talabat

## Agreed rules (from product owner)
- **Deliveroo** — Sharjah (K001) 27% + VAT, Barsha (B001) 31% + VAT. **0** payment fee. Per-branch commission.
- **Keeta** (all branches) — commission = `4 + 25% × (item_value − 4)`, **VAT-inclusive** (no gross-up). Payment = 2%, **VAT-inclusive**.
- **Careem** (all branches) — 25% + VAT commission. +4 AED **only for Careem-Plus members**. Payment 2% + VAT **only on cashless (non-cash)** orders.
- **Talabat** (all branches) — 30% + VAT commission. +4 AED **only for Pro/VIP/loyalty members**. Payment 2% + VAT on **all** orders (incl. cash).
- **Noon Food** — unchanged (25% + 2%, before VAT).

## Confirmed facts (prod, 2026-08-24)
- Branches: `K001` Sharjah, `B001` Barsha Heights (Dubai) → Deliveroo split = `order.branch_id`.
- Cash vs cashless = `grubops raw.orderHeader.paymentStatus` (`POSTPAID`=cash / `PREPAID`=cashless). Ingest currently discards it (hardcodes `payment_method='cod'`).
- **No loyalty/Pro/Plus identifier exists in any GrubOps payload** (`additionalInfos` null, `orderLoyaltyPoint` empty, no `tpro`/`careem-plus`/`vip`/`member` token in 68/68 payloads).
  → Decision: **model the 4 AED but keep it dormant** via a per-order `aggregator_customer_is_member` flag that stays unpopulated; the fee never applies until a real signal exists.

## Plan
- [ ] `couriers` gains 5 config flags: `commission_vat_inclusive`, `payment_fee_vat_inclusive`,
      `commission_fixed_net_of_base` (Keeta's `fixed + %·(base−fixed)`),
      `payment_fee_cash_exempt` (Careem), `commission_fixed_requires_member` (Careem+Talabat).
- [ ] New `courier_branch_rate` table — per-(courier,branch) numeric rate overrides (Deliveroo).
- [ ] `orders` gains `aggregator_payment_type` (`prepaid`/`postpaid`) + `aggregator_customer_is_member` (dormant).
- [ ] `order_fees._aggregator_fees` rewritten for overrides + flags + cash-exempt + member gate + net-of-base + vat-inclusive.
- [ ] GrubOps ingest captures `aggregator_payment_type`; member flag left null.
- [ ] Migration `140_courier_branch_rates`: schema + seed rates + branch overrides + backfill `aggregator_payment_type` from raw + recompute `aggregator_fee`/`payment_fee` for all aggregator orders.
- [ ] Tests for every courier's arithmetic + the dormant member fee.
- [ ] Admin schema exposure of new flags + branch overrides (+ TS regen) — follow-up commit.

## Review

Implemented and verified end-to-end on a throwaway DB (full 131→140 chain + a
scenario order per courier). Computed fees, all correct:

| Order | commission | payment |
|---|---|---|
| Deliveroo K001 (Sharjah), 100 | 28.35 (27%+VAT) | 0.00 |
| Deliveroo B001 (Barsha), 100 | 32.55 (31%+VAT) | 0.00 |
| Keeta, 40 | 13.00 = 4+25%·(40−4), VAT incl | 0.80 (2% incl) |
| Careem card, 100 | 26.25 (25%+VAT) | 2.10 (2%+VAT) |
| Careem cash, 100 | 26.25 | 0.00 (waived) |
| Talabat cash, 100 | 31.50 (30%+VAT) | 2.10 (charged on cash too) |
| Noon, 100 | 26.25 (unchanged) | 2.10 |

- Per-order `aggregator_payment_type` backfilled from GrubOps `raw.paymentStatus`.
- The 4 AED member fee is seeded but dormant (`aggregator_customer_is_member`
  never set true), so no non-member is over-charged.
- 2007 unit tests pass (17 in `test_order_fees.py`, incl. one per new rule); ruff clean.

### Follow-up (not in this change)
- Admin console can already edit the numeric rates (`CourierUpdate`), but the new
  grammar flags and Deliveroo's per-branch overrides are seed-only for now —
  exposing them in `CourierResponse`/`CourierUpdate` + a branch-rate endpoint
  (and the admin UI) is a separate change that also regenerates `@mm/types`.
- Wire `aggregator_customer_is_member` to a real signal if GrubOps ever exposes
  Careem-Plus / Talabat-Pro, or add a manual per-order toggle.
