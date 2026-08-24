"""
Business logic, grouped by domain.

This directory was 74 flat modules. It is now eight subpackages — `catalog`,
`couriers`, `delivery`, `grubops`, `inventory`, `orders`, `payments`, `pos` —
plus `providers/` for the HTTP clients that speak somebody else's protocol, and
the modules that genuinely belong to no one domain (audit, email, push, cache,
auth, the CRUD helper) at this level.

**This file deliberately exports nothing.** It used to re-export nineteen of
the seventy-four with no stated criterion, so `from app.services import
order_service` and `from app.services.orders import order_service` were both
correct and neither was canonical. Import the module from its domain; the
import line then says which part of the system a file reaches into, which is
most of what you want to know about a file you have not read.
"""
