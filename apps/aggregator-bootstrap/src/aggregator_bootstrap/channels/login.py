"""Per-channel login surfaces and "are we in?" probes.

The worker does **not** mint a session by driving OTP from a mailbox by
default. A human signs in once (`aggregator-bootstrap login --channel X`);
the database then holds the Playwright state and the worker hydrates from it.

Channels whose recipe is `email_password` (Deliveroo today) can re-auth from
the encrypted `aggregator_account` row: `login --channel deliveroo --auto`
fills the form after Cloudflare has passed. OTP/captcha channels stay headed.

"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from ..config import settings
from ..mailbox import OTPPollingError, wait_for_otp


class LoginError(RuntimeError):
    """A channel login could not be completed automatically."""


class LoginChallengeError(LoginError):
    """Login stalled on a challenge the worker could not satisfy (e.g. no OTP)."""


class AntiBotChallengeError(LoginError):
    """Login hit a human-verification / anti-bot wall; not bypassed by design."""


# --- Deliveroo --------------------------------------------------------------
# Ported from channels/deliveroo/discovery.py::login_deliveroo. Email + password
# only; no OTP. The stable "we are logged in" signal is the redirect to the
# analytics landing page.

DELIVEROO_LOGIN_URL = "https://partner-hub.deliveroo.com/login"
#: Deliveroo is email+password, no OTP. The worker can fill this from the
#: `aggregator_account` row once Cloudflare has let the login form through.
DELIVEROO_LOGIN_METHOD = "email_password"


async def _dismiss_deliveroo_cookie_banner(page) -> None:
    for name in ("Continue without accepting", "Accept all"):
        button = page.get_by_role("button", name=name)
        if await button.count():
            try:
                await button.first.click(timeout=2_000)
                await page.wait_for_timeout(500)
                return
            except Exception:  # noqa: BLE001 — best-effort banner dismissal
                continue


async def deliveroo_login_form_visible(page) -> bool:
    try:
        return await page.get_by_test_id("login-email").count() > 0
    except Exception:  # noqa: BLE001
        return False


async def fill_deliveroo_login(page, *, email: str, password: str) -> None:
    """Type credentials into an already-open Partner Hub login page."""
    await _dismiss_deliveroo_cookie_banner(page)
    if "/login" not in page.url:
        return
    if not await deliveroo_login_form_visible(page):
        raise LoginError("Deliveroo login form was not on the page after Cloudflare.")
    await page.get_by_test_id("login-email").fill(email)
    await page.get_by_test_id("login-password").fill(password)
    await page.get_by_test_id("login-submit").click()
    # Hub landing is usually /analytics; reporting-platform is also a win.
    # The honest signal is that we left /login.
    await page.wait_for_function(
        "() => !location.pathname.includes('/login')",
        timeout=60_000,
    )


def _deliveroo_credentials(
    email: str | None = None, password: str | None = None
) -> tuple[str, str]:
    e = (email or settings.DELIVEROO_EMAIL or "").strip()
    p = (password or settings.DELIVEROO_PASSWORD or "").strip()
    if not e or not p:
        raise LoginError(
            "Deliveroo email/password are not configured "
            "(store them with `store-account --channel deliveroo`, or set "
            "DELIVEROO_EMAIL / DELIVEROO_PASSWORD)."
        )
    return e, p


async def login_deliveroo(
    context, *, email: str | None = None, password: str | None = None
) -> None:
    e, p = _deliveroo_credentials(email, password)
    page = await context.new_page()
    await page.goto(DELIVEROO_LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
    await fill_deliveroo_login(page, email=e, password=p)


# --- Talabat ----------------------------------------------------------------
# Ported from channels/talabat/discovery.py::ensure_talabat_authenticated. Email
# + password, then a 6-digit OTP typed across six <input type="tel"> boxes. The
# portal is fronted by PerimeterX; the "press and hold" wall is surfaced, never
# defeated.

TALABAT_LOGIN_URL = "https://partner-app.talabat.com/login"
TALABAT_OTP_SENDER = "no reply"
TALABAT_OTP_SUBJECT = "partner portal"


async def _talabat_human_verification_present(page) -> bool:
    try:
        body = await page.locator("body").inner_text(timeout=5_000)
    except Exception:  # noqa: BLE001
        return False
    return "Press and hold to confirm you are a human" in body


async def _dismiss_talabat_cookie_banner(page) -> None:
    for name in ("Accept", "Accept all", "Allow all", "Agree", "Got it"):
        button = page.get_by_role("button", name=name)
        if await button.count():
            try:
                await button.first.click(timeout=2_000)
                await page.wait_for_timeout(500)
                return
            except Exception:  # noqa: BLE001
                continue


async def _talabat_is_authenticated_app(page) -> bool:
    if "partner-app.talabat.com" not in page.url:
        return False
    if (
        page.url.endswith("/dashboard")
        or "/orders" in page.url
        or "/finance" in page.url
    ):
        return True
    try:
        body = await page.locator("body").inner_text(timeout=5_000)
    except Exception:  # noqa: BLE001
        return False
    return any(
        m in body for m in ("Order History", "Payments", "Opening Times", "Settings")
    )


async def login_talabat(
    context,
    *,
    mailbox: dict | None = None,
    email: str | None = None,
    password: str | None = None,
    page=None,
) -> None:
    address = (email or settings.TALABAT_EMAIL or "").strip()
    pwd = password or settings.TALABAT_PASSWORD or ""
    if not address or not pwd:
        raise LoginError(
            "Talabat login needs email/password on the account recipe or "
            "TALABAT_EMAIL / TALABAT_PASSWORD."
        )
    owned_page = page is None
    if page is None:
        page = await context.new_page()
    await page.goto(TALABAT_LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(3_000)
    await _dismiss_talabat_cookie_banner(page)

    if await _talabat_is_authenticated_app(page):
        return
    if await _talabat_human_verification_present(page):
        raise AntiBotChallengeError(
            "Talabat showed a 'press and hold' human-verification wall at the "
            "login gate; the worker will not attempt to bypass it."
        )

    email_input = page.locator("input[name='email']")
    password_input = page.locator("input[name='password']")
    try:
        await email_input.wait_for(state="visible", timeout=15_000)
        await password_input.wait_for(state="visible", timeout=15_000)
    except Exception as exc:  # noqa: BLE001
        if await _talabat_is_authenticated_app(page):
            return
        raise LoginError(
            f"Talabat login page did not expose credential inputs at {page.url}"
        ) from exc

    await email_input.fill(address)
    await password_input.fill(pwd)
    otp_since = datetime.now(UTC)
    await page.locator("button[type='submit']").click(timeout=5_000)
    await page.wait_for_timeout(4_000)

    if not page.url.endswith("/2fa"):
        if await _talabat_human_verification_present(page):
            raise AntiBotChallengeError(
                "Talabat showed a human-verification wall after submitting "
                "credentials; not bypassed."
            )
        return  # no 2FA step — already through

    try:
        otp = await wait_for_otp(
            sender_filter=TALABAT_OTP_SENDER,
            subject_filter=TALABAT_OTP_SUBJECT,
            since=otp_since,
            timeout=90,
            mailbox=mailbox,
            channel="talabat",
        )
    except OTPPollingError as exc:
        raise LoginChallengeError(
            "Talabat requested a 2FA OTP but none could be read from the "
            "linked mailbox. Save this channel's Microsoft app on Admin → "
            "Logins, run mailbox-auth, or complete the login manually."
        ) from exc

    inputs = page.locator("input[type='tel']")
    if await inputs.count() != 6:
        raise LoginError(f"Unexpected Talabat OTP input count: {await inputs.count()}")
    await inputs.first.click()
    await page.keyboard.type(otp[:6])
    await page.wait_for_url("**/dashboard", timeout=30_000)
    await page.wait_for_timeout(5_000)
    if owned_page:
        return
    return page


# --- Noon -------------------------------------------------------------------
# Noon RMS redirects unauthenticated users to `login.noon.partners` (full page)
# or, on older builds, embeds `login-webview-embed.noon.partners` in an iframe.
# Both surfaces share the same email → OTP flow.

NOON_RMS_URL = "https://restaurant.noon.partners/_food-restaurant/finance/wallet"
NOON_OTP_SENDER = "noon"
NOON_OTP_SUBJECT = "verify"


def _noon_login_frame(page):
    for frame in page.frames:
        if (
            "login-webview-embed.noon.partners" in frame.url
            or "login.noon.partners" in frame.url
        ):
            return frame
    return None


def _noon_on_login_surface(page) -> bool:
    if "login.noon.partners" in page.url:
        return True
    return _noon_login_frame(page) is not None


def _noon_login_surface(page):
    """The frame or top-level page that hosts the email/OTP controls."""
    frame = _noon_login_frame(page)
    if frame is not None:
        return frame
    if "login.noon.partners" in page.url:
        return page
    return None


async def _noon_submit_email(surface, page, *, address: str) -> None:
    """Email step: remembered-user shortcut, or type into channelIdentifier."""
    remembered = surface.get_by_text("Continue with this user", exact=False)
    if await remembered.count():
        try:
            body = (await surface.locator("body").inner_text(timeout=5_000)).lower()
        except Exception:  # noqa: BLE001
            body = ""
        if address.lower() in body:
            await remembered.first.click(timeout=5_000)
            await page.wait_for_timeout(5_000)
            return
        different = surface.get_by_text("Use a different account", exact=False)
        if await different.count():
            await different.first.click(timeout=5_000)
            await page.wait_for_timeout(3_000)

    identifier_input = surface.locator("input[name='channelIdentifier']")
    if await identifier_input.count():
        await identifier_input.fill(address)
        await surface.locator("button[type='submit']").click()
        await page.wait_for_timeout(5_000)


async def _dismiss_noon_passkey_prompt(page) -> None:
    for label in ("Skip for now", "Not now", "Maybe later", "Skip"):
        button = page.get_by_role("button", name=label)
        try:
            if await button.count():
                await button.first.click(timeout=2_000)
                await page.wait_for_timeout(1_000)
                return
        except Exception:  # noqa: BLE001
            continue
    frame = _noon_login_frame(page)
    if not frame:
        return
    for label in ("Skip for now", "Not now", "Maybe later", "Skip"):
        button = frame.get_by_role("button", name=label)
        try:
            if await button.count():
                await button.first.click(timeout=2_000)
                await page.wait_for_timeout(1_000)
                return
        except Exception:  # noqa: BLE001
            continue


async def _wait_noon_login_surface_gone(page, *, timeout_ms: int = 90_000) -> None:
    """Block until Noon leaves the login page / iframe after OTP."""
    import time

    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if not _noon_on_login_surface(page):
            return
        await page.wait_for_timeout(500)
    raise LoginError("Noon login page did not disappear after submitting the OTP")


async def login_noon(
    context,
    *,
    mailbox: dict | None = None,
    email: str | None = None,
    page=None,
):
    """Drive Noon RMS email → Graph OTP → optional passkey skip.

    `email` comes from `aggregator_account` when `--auto` runs; falls back to
    `NOON_EMAIL` so a laptop override still works without a DB rewrite.
    Returns the Playwright page that holds the authenticated RMS session.
    """
    address = (email or settings.NOON_EMAIL or "").strip()
    if not address:
        raise LoginError(
            "Noon login needs an email on the account recipe or NOON_EMAIL."
        )
    owned_page = page is None
    if page is None:
        page = await context.new_page()
    if not _noon_on_login_surface(page) and not await _noon_is_authenticated(page):
        await page.goto(NOON_RMS_URL, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(5_000)

    surface = _noon_login_surface(page)
    if surface is None:
        if await _noon_is_authenticated(page):
            return page
        return page

    otp_since = datetime.now(UTC)
    await _noon_submit_email(surface, page, address=address)

    surface = _noon_login_surface(page)
    if surface is None:
        if await _noon_is_authenticated(page):
            return page
        return page
    otp_input = surface.locator("input[data-input-otp='true']")
    if not await otp_input.count():
        return page
    try:
        # Prefer Admin recipe filters (verify@noon.com / Verify your email);
        # fall back to the hard-coded substrings that match Noon OTPs.
        box = mailbox or {}
        otp = await wait_for_otp(
            sender_filter=str(box.get("sender_filter") or "") or NOON_OTP_SENDER,
            subject_filter=str(box.get("subject_filter") or "") or NOON_OTP_SUBJECT,
            since=otp_since,
            timeout=90,
            mailbox=mailbox,
            channel="noon",
        )
    except OTPPollingError as exc:
        if owned_page:
            await page.close()
        raise LoginChallengeError(
            "Noon RMS requested an email OTP but none could be read from the "
            "linked mailbox. Save this channel's Microsoft app on Admin → "
            "Logins, run mailbox-auth, or complete the login manually."
        ) from exc
    await otp_input.fill(otp)
    await surface.locator("button[type='submit']").click()
    await _wait_noon_login_surface_gone(page)
    await _dismiss_noon_passkey_prompt(page)
    await page.goto(NOON_RMS_URL, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(5_000)
    await _dismiss_noon_passkey_prompt(page)
    return page


# --- Keeta ------------------------------------------------------------------
# Ported from channels/keeta/discovery.py::ensure_keeta_authenticated. Best
# effort: prime the UAE region, then email -> password across two steps. Keeta
# routinely fronts login with a captcha / device-verification wall; that wall is
# detected and surfaced (AntiBotChallengeError) rather than bumped against.

KEETA_PORTAL_URL = "https://merchant.mykeeta.com/?region=AE"
#: Headed login must open the AE login form directly. Opening `/?region=AE`
#: alone often redirects to `pc/login?...region=HK` (Hong Kong), which then
#: sticks the session on the wrong marketplace.
KEETA_LOGIN_URL = (
    "https://merchant.mykeeta.com/pc/login"
    "?service=merchants&locale=en&region=AE&loginRegion=AE"
    "&backurl=https%3A%2F%2Fmerchant.mykeeta.com%2F%3Fregion%3DAE"
)
_KEETA_VERIFICATION_PATTERNS = (
    "captcha",
    "verification code",
    "security verification",
    "device verification",
    "one-time",
    "otp",
    "slide to",
    "risk control",
)


async def _keeta_body_lower(page) -> str:
    try:
        return (await page.locator("body").inner_text(timeout=5_000)).lower()
    except Exception:  # noqa: BLE001
        return ""


async def _keeta_verification_wall(page) -> bool:
    body = await _keeta_body_lower(page)
    return any(pattern in body for pattern in _KEETA_VERIFICATION_PATTERNS)


async def _keeta_has_authenticated_surface(page) -> bool:
    """True only when the portal has a real merchant session, not the AE landing.

    The marketing / pre-login `/?region=AE` page has no password field and no
    "sign in" string in some locales, so the old body/URL heuristic treated it
    as logged-in and pushed an empty session. Require LOGIN_ACCOUNTID (and
    prefer SHOP_IDS) in sessionStorage instead.
    """
    if "passport.mykeeta.com" in page.url:
        return False
    if "/pc/login" in page.url or "/login" in page.url.lower():
        return False
    try:
        markers = await page.evaluate(
            """() => ({
              accountId: sessionStorage.getItem("LOGIN_ACCOUNTID") || "",
              shopIds: sessionStorage.getItem("SHOP_IDS") || "",
              region: sessionStorage.getItem("region") || "",
            })"""
        )
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(markers, dict):
        return False
    account = str(markers.get("accountId") or "").strip()
    if not account:
        return False
    body = await _keeta_body_lower(page)
    if "sign in" in body or "become a keeta partner" in body:
        return False
    return True


async def _keeta_first_visible(page, selectors):
    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = min(await locator.count(), 5)
        except Exception:  # noqa: BLE001
            continue
        for index in range(count):
            candidate = locator.nth(index)
            try:
                if await candidate.is_visible(timeout=1_000):
                    return candidate
            except Exception:  # noqa: BLE001
                continue
    return None


async def _keeta_click_submit(page) -> None:
    for selector in (".submit-btn", "button[type='submit']"):
        target = page.locator(selector).first
        if await target.count():
            await target.click(timeout=5_000)
            return
    await page.keyboard.press("Enter")


async def _keeta_prime_region(context, page) -> None:
    await context.add_cookies(
        [
            {"name": "region", "value": "AE", "domain": ".mykeeta.com", "path": "/"},
            {
                "name": "register_region",
                "value": "AE",
                "domain": ".mykeeta.com",
                "path": "/",
            },
        ]
    )
    await page.add_init_script(
        "() => { try {"
        " window.sessionStorage && window.sessionStorage.setItem('region', 'AE');"
        " window.localStorage && window.localStorage.setItem('region', 'AE');"
        " } catch (e) {} }"
    )


async def login_keeta(context) -> None:
    if not settings.KEETA_EMAIL or not settings.KEETA_PASSWORD:
        raise LoginError("KEETA_EMAIL / KEETA_PASSWORD are not configured.")
    page = await context.new_page()
    await _keeta_prime_region(context, page)
    await page.goto(KEETA_PORTAL_URL, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(5_000)

    if await _keeta_verification_wall(page):
        raise AntiBotChallengeError(
            "Keeta requires a captcha / device verification at the login gate; "
            "not bypassed by the worker."
        )
    if await _keeta_has_authenticated_surface(page):
        return

    email_input = await _keeta_first_visible(
        page,
        (
            "input[type='email']",
            "input[name*='email' i]",
            "input[name*='account' i]",
            "input[name*='login' i]",
            "input[placeholder*='email' i]",
            "input[placeholder*='account' i]",
            "input[type='text']",
        ),
    )
    if not email_input:
        raise LoginError("Keeta email login control was not found.")
    await email_input.fill(settings.KEETA_EMAIL)
    await _keeta_click_submit(page)
    await page.wait_for_timeout(8_000)
    if await _keeta_verification_wall(page):
        raise AntiBotChallengeError(
            "Keeta demanded verification after the email step; not bypassed."
        )

    password_input = await _keeta_first_visible(
        page,
        (
            "input[type='password']",
            "input[name*='password' i]",
            "input[placeholder*='password' i]",
        ),
    )
    if not password_input:
        raise LoginError("Keeta password control was not found after email entry.")
    await password_input.fill(settings.KEETA_PASSWORD)
    await _keeta_click_submit(page)
    await page.wait_for_timeout(8_000)
    if await _keeta_verification_wall(page):
        raise AntiBotChallengeError(
            "Keeta demanded verification after the password step; not bypassed."
        )
    if not await _keeta_has_authenticated_surface(page):
        raise LoginError("Keeta login did not reach an authenticated portal surface.")


# --- Careem -----------------------------------------------------------------
# Partner portal (partners.careem.com → auth-partners.careem.com) offers Phone /
# Email / Staff-Credentials; the Email method redirects to auth.careem.com for an
# email → one-time-code flow (reCAPTCHA-gated, no password on this path). The OTP
# arrives from `go@careem.com` / "Careem One Time Password", read via the linked
# Graph mailbox — the same mechanism noon/talabat use.


CAREEM_PORTAL_URL = "https://partners.careem.com/"
CAREEM_OTP_SENDER = "go@careem.com"
CAREEM_OTP_SUBJECT = "Careem One Time Password"


async def _careem_submit(page, field) -> None:
    """Advance a Careem auth step. The "Continue" button lags behind the
    reCAPTCHA-v3 token and field validation, so a bare `button[type=submit]`
    click races it and times out — wait for the button, then fall back to
    pressing Enter in the field (which submits the form just the same)."""
    await page.wait_for_timeout(1_500)
    button = page.get_by_role("button", name="Continue")
    try:
        await button.wait_for(state="visible", timeout=15_000)
        await button.click(timeout=10_000)
        return
    except Exception:  # noqa: BLE001 — the button never became actionable; use Enter
        try:
            await field.press("Enter")
        except Exception:  # noqa: BLE001 — best effort; the caller re-checks state
            pass


async def login_careem(
    context,
    *,
    mailbox: dict | None = None,
    email: str | None = None,
    password: str | None = None,
    page=None,
):
    """Drive Careem Partners email → Graph OTP.

    The portal (`partners.careem.com` → `auth-partners.careem.com`) offers Phone /
    Email / Staff-Credentials; the **Email** method redirects to `auth.careem.com`
    for an email → one-time-code flow. The OTP is the second factor — this path
    takes no password (Careem's `email_password_otp` stores one for the
    staff-credentials path, unused here). `password` is accepted for signature
    symmetry with the other flows and ignored. Returns the authenticated page.
    """
    address = (email or settings.CAREEM_EMAIL or "").strip()
    if not address:
        raise LoginError(
            "Careem login needs an email on the account recipe or CAREEM_EMAIL."
        )
    owned_page = page is None
    if page is None:
        page = await context.new_page()

    on_auth = "auth.careem.com" in page.url or "auth-partners.careem.com" in page.url
    if not on_auth and not await _careem_is_authenticated(page):
        await page.goto(
            CAREEM_PORTAL_URL, wait_until="domcontentloaded", timeout=60_000
        )
        await page.wait_for_timeout(4_000)
    if await _careem_is_authenticated(page):
        return page

    # Partner-portal method chooser → pick the Email option (→ auth.careem.com).
    if "auth.careem.com" not in page.url:
        for label in ("Receive a one-time code via email", "Email Address"):
            try:
                await page.get_by_text(label, exact=False).first.click(timeout=8_000)
                break
            except Exception:  # noqa: BLE001 — try the next label / assume redirect
                continue
        await page.wait_for_timeout(3_000)

    # auth.careem.com email step.
    email_input = page.locator("input[type='email']")
    try:
        await email_input.wait_for(state="visible", timeout=20_000)
    except Exception as exc:  # noqa: BLE001
        if await _careem_is_authenticated(page):
            return page
        raise LoginError(
            f"Careem login did not expose an email input at {page.url}"
        ) from exc
    await email_input.fill(address)
    otp_since = datetime.now(UTC)
    await _careem_submit(page, email_input)
    await page.wait_for_timeout(3_000)

    # Verification-code step — one text box, submit, done.
    otp_input = page.locator("input[type='text']").first
    try:
        await otp_input.wait_for(state="visible", timeout=20_000)
    except Exception as exc:  # noqa: BLE001
        if await _careem_is_authenticated(page):
            return page
        raise LoginError(
            f"Careem did not present a verification-code input at {page.url}"
        ) from exc
    box = mailbox or {}
    try:
        otp = await wait_for_otp(
            sender_filter=str(box.get("sender_filter") or "") or CAREEM_OTP_SENDER,
            subject_filter=str(box.get("subject_filter") or "") or CAREEM_OTP_SUBJECT,
            since=otp_since,
            timeout=120,
            mailbox=mailbox,
            channel="careem",
        )
    except OTPPollingError as exc:
        if owned_page:
            await page.close()
        raise LoginChallengeError(
            "Careem requested an email OTP but none could be read from the linked "
            "mailbox. Save this channel's Microsoft app on Admin → Logins, run "
            "mailbox-auth, or complete the login manually."
        ) from exc
    await otp_input.fill(otp.strip())
    await _careem_submit(page, otp_input)
    await page.wait_for_timeout(6_000)
    return page


#: Channel -> login flow. `login --auto` calls these for email_password
#: channels after Cloudflare has passed; OTP/captcha channels stay headed.
LOGIN_FLOWS: dict[str, Callable[..., Awaitable[None]]] = {
    "deliveroo": login_deliveroo,
    "talabat": login_talabat,
    "noon": login_noon,
    "keeta": login_keeta,
    "careem": login_careem,
}

#: Where the headed login opens. Careem has no dedicated `/login` path — the
#: partners origin redirects to identity if the session is empty.
LOGIN_START_URLS: dict[str, str] = {
    "deliveroo": DELIVEROO_LOGIN_URL,
    "talabat": TALABAT_LOGIN_URL,
    "noon": NOON_RMS_URL,
    "keeta": KEETA_LOGIN_URL,
    "careem": "https://partners.careem.com/",
}

_LOGIN_URL_MARKERS = ("login", "signin", "identity", "auth", "passport")


def _url_looks_like_login(url: str) -> bool:
    lower = url.lower()
    return any(marker in lower for marker in _LOGIN_URL_MARKERS)


async def _careem_is_authenticated(page) -> bool:
    if "partners.careem.com" not in page.url:
        return False
    return not _url_looks_like_login(page.url)


async def _deliveroo_is_authenticated(page) -> bool:
    if "partner-hub.deliveroo.com" not in page.url:
        return False
    return "/login" not in page.url


async def _noon_is_authenticated(page) -> bool:
    """RMS is authenticated when the login surface is gone and the app rendered."""
    if _noon_on_login_surface(page):
        return False
    if "restaurant.noon.partners" not in page.url:
        return False
    if "/_food-restaurant/" in page.url or "/finance/" in page.url:
        return True
    if _url_looks_like_login(page.url):
        return False
    try:
        body = (await page.locator("body").inner_text(timeout=3_000)).lower()
    except Exception:  # noqa: BLE001
        return False
    return any(
        marker in body for marker in ("wallet", "finance", "payout", "statement")
    )


async def page_looks_authenticated(channel: str, page) -> bool:
    """Whether the current page is a logged-in portal surface for `channel`."""
    if channel == "deliveroo":
        return await _deliveroo_is_authenticated(page)
    if channel == "talabat":
        return await _talabat_is_authenticated_app(page)
    if channel == "noon":
        return await _noon_is_authenticated(page)
    if channel == "keeta":
        return await _keeta_has_authenticated_surface(page)
    if channel == "careem":
        return await _careem_is_authenticated(page)
    return not _url_looks_like_login(page.url)
