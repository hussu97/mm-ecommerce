# Agent instructions

**The canon for this repository lives in [`CLAUDE.md`](CLAUDE.md). Read it
before changing anything.** It carries the ten System Design Conventions —
order lifecycle, transactions, errors, permissions, email policy, DB statuses,
migrations, TypeScript contracts, frontend fetch, money math — plus the
workflow rules, the secret checklist and the pagination standard.

This file is a pointer and nothing else, on purpose.

It used to be a copy. The copy drifted, and drifted in the one direction that
does damage: it kept the workflow half and lost the entire conventions section,
so an agent reading it got no order-lifecycle rule, no transaction rule and no
migration rule. Worse, it still described the secret checklist as **four**
locations. The fifth — the `docker-compose.prod.yml` allow-list — is the one
whose absence is silent, and on 2026-08-05 its absence had production running
with noon Send inert, no push notifications to any register and the Turnstile
bot check off, for weeks, with every secret correctly present on the VM.

So the rule for this file is: it points at `CLAUDE.md` and it does not restate
it. Two copies of a convention is two answers to one question, which is the
exact failure `CLAUDE.md` opens by describing. `test_agent_docs_do_not_fork.py`
fails if the canon starts growing back here.
