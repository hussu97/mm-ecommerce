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
When adding any new environment variable or secret, update ALL four locations or the secret will be missing in production:

1. `apps/api/.env.example` — document it with a comment
2. `PRODUCTION.md` Step 13c — add to the GitHub Actions secrets table
3. `.github/workflows/deploy.yml` — add to the `printf` block in "Write .env on VM"
4. `.github/workflows/rollback.yml` — same `printf` block (must stay in sync with deploy.yml)

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
