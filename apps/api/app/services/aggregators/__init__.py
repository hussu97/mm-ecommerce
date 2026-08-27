"""Mirroring the delivery marketplaces' own ledgers, and reconciling them.

This subpackage owns the read side of the five aggregators — Careem, Deliveroo,
Talabat, Noon, Keeta. The clients that actually speak each marketplace's private
console protocol live in `app/services/providers/` (one file per channel, on
`aggregator_base`), the way the payment gateways and GrubOps do; what belongs
*here* is everything above that transport:

- `crypto` — the Fernet envelope for the derived session at rest.
- `account_store` — the durable login recipe (`aggregator_account`): method,
  OTP vs not, Fernet-sealed portal email/password, and the IMAP mailbox the
  worker reads an OTP from.
- `session_store` — load/save the encrypted `aggregator_session`, the seam the
  browser bootstrap writes and the httpx providers read.
- `normalized` — the channel-neutral DTOs a provider returns, so the ingest
  never learns a marketplace's vocabulary.
- `ingest` — the hourly sales sweep and daily finance sweep (advisory-locked
  loops, like the GrubOps ones).
- `reconcile` — the maker-checker against MM orders.

Nothing here imports a provider's internals; a provider returns `normalized`
DTOs and this layer writes them to the `aggregator_*` tables.
"""
