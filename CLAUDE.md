## System Design Conventions (the canon — copy THESE patterns, not a neighbouring file's)

Every rule here exists because the codebase once had 2–5 coexisting variants of
the convention and agents copied the wrong one (see
`docs/architecture-audit-2026-08.md`). When touching code that predates a rule,
migrate it opportunistically.

1. **Order status**: only `app/services/order_lifecycle.transition()` may assign
   `Order.status`. It validates against `VALID_TRANSITIONS` and carries the
   consequences (refund, restock, register void, publish, dispatch). An AST test
   (`test_order_lifecycle_guard.py`) fails on new direct assignments.
2. **Transactions**: services `flush()`; the request-scoped `get_db` dependency
   commits. Never `commit()` in a service or dependency. Side-writes that must
   survive a rollback (logs, `last_seen_at` stamps) go on a dedicated session,
   like `webhook_log_service.Recorder`.
3. **Errors**: raise `AppError` subclasses (`app/core/exceptions.py`) from
   routers and services. `HTTPException` only where a comment explains why.
4. **Permissions**: route dependencies via the `require("perm.name")` factory —
   never a hand-rolled first-statement check.
5. **Emails/push**: inline-await through the never-raise `email_service` funnel
   (journalled to `email_log`). No `BackgroundTasks` — dropped on serverless.
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
8. **TypeScript contracts**: generated, never hand-written. `packages/types` is
   built from the API's OpenAPI document (`python -m scripts.export_openapi`
   then `pnpm --filter @mm/types generate`); CI fails on drift. Change a
   Pydantic schema ⇒ regenerate in the same commit.
9. **Frontend fetch**: web uses `lib/api-client.ts` (browser) /
   `lib/api-server.ts` (RSC) — never raw `fetch` to the API. Admin uses the one
   `request()` in `lib/api.ts` (pos-api.ts is bindings only).
10. **Money math**: computed server-side. The client renders what the API
    quotes; a client-side formula mirroring a server one is a bug.

## Workflow Orchestration

### 1. Plan Mode Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately -- don't keep pushing
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### 2. Subagent Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One task per subagent for focused execution

### 3. Self-Improvement Loop
- After ANY correction from the user: update 'tasks/lessons.md' with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

### 4. Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### 5. Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes -- don't over-engineer
- Challenge your own work before presenting it

### 6. Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests -> then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

### 7. Git Commit After Feature Changes
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

### 8. Admin Pagination Standard
All paginated tables in `apps/admin` must use the following page size options, in this exact order:

| Option | Value |
|--------|-------|
| Default | **50 / page** |
| — | 100 / page |
| — | 200 / page |
| — | 500 / page |
| — | 1000 / page |
| — | 2000 / page |

## Task Management

1. **Plan First**: Write plan to 'tasks/todo.md' with checkable items
2. **Verify Plan**: Check in before starting implementation
3. **Track Progress**: Mark items complete as you go
4. **Explain Changes**: High-level summary at each step
5. **Document Results**: Add review to 'tasks/todo.md'
6. **Capture Lessons**: Update 'tasks/lessons.md' after corrections

### 9. Secret/Env Var Checklist
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

`apps/api/tests/unit/test_compose_env_allowlist.py` now fails if a setting in
`Settings` cannot be configured on production, so this is enforced rather than
remembered.

### 10. Analytics Tracking Rule
Whenever you add, remove, or rename any event in `apps/web/lib/analytics.ts`, you **must** also update `docs/umami-analytics-setup.md`:

1. Add/remove the event row in the **Custom Events Reference** table (include payload fields, phase, and the file it fires from)
2. Add/remove any related **Goals** entries if the event is used as a goal trigger
3. Add/remove any related **Funnels** steps
4. Append a row to the **Changelog** table with today's date and a summary of what changed

Failure to keep this file in sync means the Umami dashboard will be misconfigured in production.

## Core Principles

- **Simplicity First**: Make every change as simple as possible. Impact minimal code.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact**: Changes should only touch what's necessary. Avoid introducing bugs.

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

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

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.
