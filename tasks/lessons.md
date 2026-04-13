# Melting Moments Ecommerce - Lessons Learned

> Updated as mistakes are corrected. Review at session start.

## Project Context
- UAE-based artisanal bakery (Melting Moments Cakes)
- Turborepo monorepo: Next.js 15 storefront + admin, FastAPI backend, PostgreSQL
- Design: dusty mauve (#8a5a64), Playfair Display + Jost, mobile-first, clean minimal
- ~33 SKUs across 5 categories, delivery across UAE

## Lessons

### [2026-04-13] New env vars must be added to all 4 places
- **What went wrong**: Added `GCP_PROJECT_ID` to `docker-compose.prod.yml` and `.env.example` but missed the GitHub Actions `deploy.yml` and `rollback.yml` — the secret would never reach the VM's `.env` file
- **Why**: The `.env` on the VM is written entirely by the CI `printf` block; any secret not listed there is silently absent at runtime
- **Rule**: Any new env var/secret must be added to ALL four locations simultaneously:
  1. `apps/api/.env.example` — with a comment
  2. `PRODUCTION.md` Step 13c — in the secrets table
  3. `.github/workflows/deploy.yml` — in the `printf` block ("Write .env on VM")
  4. `.github/workflows/rollback.yml` — same `printf` block (must stay in sync)

### [2026-03-05] Python: Don't use passlib with Python 3.14+
- **What went wrong**: `passlib[bcrypt]` crashes on Python 3.14 — `bcrypt.__about__` attribute was removed in bcrypt 4.x, causing passlib's backend detection to fail
- **Why**: passlib is largely unmaintained; it hasn't caught up with newer bcrypt API changes
- **Rule**: Use `bcrypt` directly instead of `passlib`. Pattern:
  ```python
  import bcrypt
  def hash_password(pw: str) -> str:
      return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
  def verify_password(plain: str, hashed: str) -> bool:
      return bcrypt.checkpw(plain.encode(), hashed.encode())
  ```
- **pyproject.toml**: Use `"bcrypt>=4.0.0"` — remove `"passlib[bcrypt]"`
