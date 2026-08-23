# Documentation

Start here. Everything in this directory is reference or history; the rules
themselves live in [`../CLAUDE.md`](../CLAUDE.md).

## Read these first

| Doc | What it is |
|---|---|
| [`../CLAUDE.md`](../CLAUDE.md) | **The canon.** Eleven system-design conventions plus the workflow rules. Read before changing anything. |
| [`system-shape.md`](system-shape.md) | How the pieces fit: four apps, one database, where a request goes and which layer owns what. |
| [`architecture-audit-2026-08.md`](architecture-audit-2026-08.md) | The audit that produced the canon. Remediated — kept for the reasoning, and for its "what is healthy, do not fix" list. |
| [`schema.md`](schema.md) | ER diagram for the thirteen core storefront tables. Not the whole schema; see the note at the top. |

## Integrations

| Doc | What it is |
|---|---|
| [`grubops-integration.md`](grubops-integration.md) | Aggregator order ingestion, mapping and reconciliation. |
| [`tabby-tamara.md`](tabby-tamara.md) | Onboarding runbook for the two buy-now-pay-later providers. Provider stubs exist; neither is live. |
| [`foodics-coverage-matrix.md`](foodics-coverage-matrix.md) | What the previous POS did, feature by feature. |
| [`pos-foodics-parity.md`](pos-foodics-parity.md) | How far the register has closed that gap. |

## Analytics and performance

| Doc | What it is |
|---|---|
| [`umami-analytics-setup.md`](umami-analytics-setup.md) | Event reference, goals, funnels and a run-book. **Kept in sync by canon workflow rule W10** — changing an event in `apps/web/lib/analytics.ts` means editing this file in the same commit. |
| [`microsoft-clarity-setup.md`](microsoft-clarity-setup.md) | Session recordings and heatmaps, and the privacy position. |
| [`first-party-analytics-feasibility.md`](first-party-analytics-feasibility.md) | Why the storefront proxies Umami rather than embedding it. |
| [`performance-audit-2026-08-08.md`](performance-audit-2026-08-08.md) | Storefront performance audit. Findings, not a plan of record. |
| [`cart-abandonment-email.md`](cart-abandonment-email.md) | Design for the abandoned-cart nudge. |

## Design

| Doc | What it is |
|---|---|
| [`admin-mobile-design-system.md`](admin-mobile-design-system.md) | How the admin behaves on a phone. |

## Elsewhere in the repo

- [`../README.md`](../README.md) — local setup and project structure.
- [`../PRODUCTION.md`](../PRODUCTION.md) — the deployment run-book. Step 13c is the secrets table the five-place checklist points at.
- [`../ROADMAP.md`](../ROADMAP.md) — product roadmap. Superseded for architecture by the audit above.
- [`../tasks/lessons.md`](../tasks/lessons.md) — what went wrong before, and what it taught.
