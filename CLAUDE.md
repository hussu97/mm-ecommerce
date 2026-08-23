## System Design Conventions (the canon — copy THESE patterns, not a neighbouring file's)

Every rule here exists because the codebase once had 2–5 coexisting variants of
the convention and agents copied the wrong one (see
`docs/architecture-audit-2026-08.md`). When touching code that predates a rule,
migrate it opportunistically.

1. **Order status**: only `app/services/orders/order_lifecycle.transition()` may assign
   `Order.status`. It validates against `VALID_TRANSITIONS` and carries the
   consequences (refund, restock, register void, publish, dispatch). An AST test
   (`test_order_lifecycle_guard.py`) fails on new direct assignments.
2. **Transactions**: services `flush()`; the request-scoped `get_db` dependency
   commits. Never `commit()` in a service or dependency. Side-writes that must
   survive a rollback (logs, `last_seen_at` stamps) go on a dedicated session,
   like `webhook_log_service.Recorder`. Where a service must commit the
   caller's session anyway — a courier booking that spent real money and cannot
   be rolled back with the request — **say so at the line**. One file explaining
   itself and two neighbours copying the deviation in silence is how a rule
   becomes folklore.
3. **Errors**: raise `AppError` subclasses (`app/core/exceptions.py`) from
   routers and services. `HTTPException` only where a comment explains why.
4. **Permissions**: route dependencies via the `require("perm.name")` factory —
   never a hand-rolled first-statement check.
5. **Emails/push**: inline-await through the never-raise `email_service` funnel
   (journalled to `email_log`). **No `BackgroundTasks`, for any work, not only
   email** — the process can be reaped the moment the response goes out and the
   task is dropped with nothing recording it. Fire-and-forget that genuinely
   must not block the response uses a tracked `asyncio.Task` held in a
   module-level set, the way `indexnow_service` and the GrubOps services do.
6. **DB statuses**: internal lifecycle columns are `String` + CHECK constraint
   (values spelled out in the migration and mirrored in `__table_args__`).
   Provider-verbatim columns (courier words) stay unconstrained by design.
   Native PG enums are legacy — do not add new ones.
7. **Migrations**: schema, structural backfills, **and content the deploy has to
   carry** — CMS copy, a corrected seed value, a commercial figure the shop has
   agreed. That last part used to say content belongs in `scripts/`, and it was
   wrong: every CMS rewrite here (`008`, `009`, `054`, `058`, `061`, `107`) is a
   migration, because a script is only as good as somebody remembering to run
   it, and until they do the site keeps saying the wrong thing. A content
   migration must be **guarded so it cannot fight the admin**: match the exact
   value it means to replace (`WHERE minutes = 60`, whole-string swaps) so that
   once a human edits it in the console, the migration matches nothing and does
   nothing — including on a database restored from an older dump. `scripts/` is
   for operator tools a human runs deliberately, not for changes that need to
   land. Revision ids ≤32 chars.
   **One exception, and it is not optional knowledge: `ui_translations`.** Every
   UI string is owned by `apps/api/scripts/seed_i18n.py`, which `app_setup` runs
   in the API's lifespan — so it executes on every boot and overwrites any row
   whose value differs. A migration that edits a UI string applies, the API
   restarts, the seed puts the old text back and invalidates the Redis cache, so
   the restored value is serving within seconds. Migrations `121` and `122` were
   both written before anyone noticed, deployed green, and changed nothing that
   lasted. The Translations console loses the same argument on the next deploy.
   To change a UI string, edit `ALL_TRANSLATIONS` in that file. To retire a key
   you need both: delete the line there so it stops being restored, *and* a
   migration to remove the row that already exists.
8. **TypeScript contracts**: generated, never hand-written. `packages/types` is
   built from the API's OpenAPI document (`python -m scripts.export_openapi`
   then `pnpm --filter @mm/types generate`); CI fails on drift. Change a
   Pydantic schema ⇒ regenerate in the same commit.
   **Adoption is unfinished, and knowing that is part of the rule.** Both apps
   declare `@mm/types` and path-alias it, and neither imports it: ~2,650 lines
   of hand-written types still shadow the contract in `apps/admin/lib/types.ts`,
   `apps/admin/lib/pos-types.ts` and `apps/web/lib/types.ts`, and they have
   drifted before — silently, into a money bug. Do not add a type to those
   files that the generated contract already carries, and prefer moving one
   over when you touch it.
9. **Frontend fetch**: web uses `lib/api-client.ts` (browser) /
   `lib/api-server.ts` (RSC) — never raw `fetch` to the API. Admin uses the one
   `request()` in `lib/api.ts` (pos-api.ts is bindings only).
10. **Money math**: computed server-side. The client renders what the API
    quotes; a client-side formula mirroring a server one is a bug. Server-side,
    every quantisation goes through `app/core/money.py` — one precision and one
    rounding mode. Two modes over the same figures is not a style difference:
    `ROUND_HALF_UP` makes 0.125 into 0.13 and bankers' rounding makes it 0.12.
11. **Module layout**: a service lives in the subpackage for its domain
    (`app/services/couriers/`, `pos/`, `orders/`, …), following
    `app/services/providers/`. Request and response models live in
    `app/schemas/`, not inline in a router — a schema declared beside the route
    that returns it is invisible to everything that reads `app/schemas/` to
    learn the shape of the API.

## Workflow Orchestration

### W1. Plan Mode Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately -- don't keep pushing
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### W2. Subagent Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One task per subagent for focused execution

### W3. Self-Improvement Loop
- After ANY correction from the user: update 'tasks/lessons.md' with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

### W4. Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### W5. Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes -- don't over-engineer
- Challenge your own work before presenting it

### W6. Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests -> then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

### W7. Git Commit After Feature Changes
After any feature change (or coherent set of changes), commit the work with a clear, descriptive message. One logical change per commit where practical. Do not leave implemented work uncommitted.

Every commit **must** be authored by exactly:
```
Hussain Abbasi <h_abbasi97@hotmail.com>
```
Always pass `--author` explicitly:
```
git commit --author="Hussain Abbasi <h_abbasi97@hotmail.com>" -m "..."
```
No variations (`h-abbasi`, `hussain`, other emails). No `Co-Authored-By` trailer of any kind.

### W8. Admin Pagination Standard
All paginated tables in `apps/admin` must use the following page size options, in this exact order:

| Option | Value |
|--------|-------|
| Default | **50 / page** |
| — | 100 / page |
| — | 200 / page |
| — | 500 / page |
| — | 1000 / page |
| — | 2000 / page |


### W9. Secret/Env Var Checklist
When adding any new environment variable or secret, update ALL **five** locations or the secret will be missing in production:

1. `apps/api/.env.example` — document it with a comment
2. `PRODUCTION.md` Step 13c — add to the GitHub Actions secrets table
3. `.github/workflows/deploy.yml` — add to the `printf` block in "Write .env on VM"
4. `.github/workflows/rollback.yml` — same `printf` block (must stay in sync with deploy.yml)
5. **`docker-compose.prod.yml` — add it to the `environment:` block of the `api`
   service** (and `pos-api` if the register needs it)

Number 5 is the one that gets forgotten, and its absence is silent. That block
is an **allow-list, not an `env_file`**: a variable written to `.env` on the VM
and not named there never reaches the container, and the app simply sees its
default. On 2026-08-05 production was found running with noon Send entirely
inert, no push notifications to any register and the Turnstile bot check off,
because every one of those had been added to items 1–4 and not to item 5. The
secrets were all present on the VM the whole time.

`apps/api/tests/unit/test_compose_env_allowlist.py` reads **all five** places
and fails if a setting is missing from any of them — plus the reverse case, a
variable written to the VM that no container is passed, and any drift between
the two workflows. So this is enforced rather than remembered.

It was only ever enforcing item 5. The other four were named in that file's
docstring as narrative, and the gap cost something: the three `NOON_SEND_*`
fares sat in `.env.example` and compose and in neither workflow, so nothing
copied them to the VM and `gh secret set NOON_SEND_BASE` changed nothing. That
is this outage inverted — not a default reaching production instead of a
secret, but a secret that could never reach production at all.

### W10. Analytics Tracking Rule
Whenever you add, remove, or rename any event in `apps/web/lib/analytics.ts`, you **must** also update `docs/umami-analytics-setup.md`:

1. Add/remove the event row in the **Custom Events Reference** table (include payload fields, phase, and the file it fires from)
2. Add/remove any related **Goals** entries if the event is used as a goal trigger
3. Add/remove any related **Funnels** steps
4. Append a row to the **Changelog** table with today's date and a summary of what changed

Failure to keep this file in sync means the Umami dashboard will be misconfigured in production.

## Task Management

1. **Plan First**: Write plan to 'tasks/todo.md' with checkable items
2. **Verify Plan**: Check in before starting implementation
3. **Track Progress**: Mark items complete as you go
4. **Explain Changes**: High-level summary at each step
5. **Document Results**: Add review to 'tasks/todo.md'
6. **Capture Lessons**: Update 'tasks/lessons.md' after corrections

## Core Principles

- **Simplicity First**: Make every change as simple as possible. Impact minimal code.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact**: Changes should only touch what's necessary. Avoid introducing bugs.

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**Check the graph is built before relying on it: `list_graph_stats`.** If it
reports zero nodes, build it (`code-review-graph update`) or use Grep/Glob/Read
without apology — `.code-review-graph/` is gitignored, so every fresh clone and
every CI or cloud session starts with no graph at all, and an instruction to
always reach for it first is a dead end there.

Once it is built, prefer it: it is faster, cheaper in tokens, and gives you
structural context (callers, dependents, test coverage) that file scanning
cannot.

### What to reach for when it is built

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read when the graph is unbuilt or doesn't cover what you need.

### Key Tools

| Tool | Use when |
|------|----------|
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks) once it exists.
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.
