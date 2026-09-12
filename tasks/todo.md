# Task: Add Microsoft Windows Hello support (alongside Apple passkeys)

## Finding
The admin passkey feature (`apps/api/app/api/v1/auth.py`, `apps/admin`) is already
standards-based WebAuthn via py_webauthn 3.0.0 + `@simplewebauthn/browser`. There is
NO Apple-specific code — it is not Apple-locked. So Windows Hello is a supported
authenticator in principle. The one real interop gap is the classic Windows Hello
gotcha: Windows Hello's TPM-backed authenticators commonly produce **RS256 (RSA)**
credentials, whereas Apple platform authenticators use **ES256**. Registration only
succeeds if the RP advertises RS256 in `pubKeyCredParams`.

Today the code passes no `supported_pub_key_algs`, so it relies on py_webauthn's
*default* (currently `[EdDSA, ES256, RS256]`). That default is an invisible dependency:
if a future library version trims RS256, Windows Hello registration breaks silently
while Apple keeps working — exactly the kind of silent, default-driven regression this
repo's conventions exist to prevent (cf. the compose env-allowlist story in CLAUDE.md).

## Plan
- [x] Backend: make the supported algorithms **explicit and guarded** — a module-level
      `WEBAUTHN_SUPPORTED_PUB_KEY_ALGS = [ES256, RS256, EdDSA]` constant, used in
      `passkey_registration_options`, with a comment naming the Windows Hello / TPM RSA
      requirement.
- [x] Test: DB-free unit test asserting the generated registration options advertise
      RS256 (-257) and ES256 (-7), so Windows Hello support can't silently regress.
- [x] Frontend: update admin Security page copy + the "Passkey Name" placeholder so the
      Apple-only framing ("MacBook Touch ID") acknowledges Windows Hello / Touch ID /
      Face ID / security keys.
- [x] Verify (lint/typecheck where runnable), commit, push, open PR.

## Notes on scope (Simplicity First / Minimal Impact)
- No schema change: `admin_passkeys.public_key` is `Text` (holds larger RSA COSE keys),
  `credential_id` is `String(512)` (fits Windows Hello's longer wrapped IDs), and
  `sign_count` already tracks the incrementing counter Windows Hello uses. So no
  migration, no `packages/types` regen, no env/compose/analytics changes are triggered.

## Review
- `apps/api/app/api/v1/auth.py`: added `WEBAUTHN_SUPPORTED_PUB_KEY_ALGS`
  (ES256, RS256, EdDSA) and passed it to `generate_registration_options`. RS256 is the
  one that unlocks Windows Hello; making the set explicit means a py_webauthn default
  change can't silently drop it.
- `apps/api/tests/unit/test_admin_passkey_algorithms.py`: guard test — verified its
  assertions against a real py_webauthn 3.0.0 install (advertised algs `[-257, -8, -7]`
  in both the option struct and the serialised browser payload). Could not run the repo
  suite here (no venv/node_modules in this session); `py_compile` clean.
- `apps/admin/app/(dashboard)/security/page.tsx`: copy + placeholder now name Windows
  Hello alongside Touch ID / Face ID / security keys (string-literal only, no type impact).
- Confirmed no migration / `packages/types` / env-allowlist / analytics touch is needed
  (existing columns already fit RSA keys, longer credential IDs, and the sign counter).
