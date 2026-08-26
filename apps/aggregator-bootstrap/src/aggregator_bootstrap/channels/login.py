"""Per-channel automated login flows, driving a Playwright context to logged-in.

`ensure_session` (browser.py) calls one of these when a stored `storage_state`
has gone stale, so the worker re-establishes the session by itself instead of
waiting on a human to hand-build one. Each flow navigates to the channel's
login surface, fills the portal credentials from `config.settings`, clears the
one-time-code / anti-bot gate where it can, and leaves the passed-in context
authenticated; the caller persists `storage_state` afterwards.

These are async ports of the login portions of the standalone
mm-aggregator-automation scraper (`channels/<ch>/{discovery,exports}.py`). The
scraper drove Playwright's *sync* API; the worker is async, so the mechanics —
`await` on every locator call, frame lookups, keyboard entry — are rewritten,
but the selectors, URLs, and gate handling are carried across verbatim.

Playwright is imported lazily (only its exception types, inside the functions)
so the module imports without the browser library, exactly like browser.py.

Two gates are surfaced rather than bypassed:
- `AntiBotChallengeError` — a PerimeterX / "press and hold to confirm you are a
  human" wall (Talabat) or a captcha/device-verification wall (Keeta). The
  automation deliberately does not try to defeat it.
- `LoginChallengeError` — an OTP was required but none could be read from the
  mailbox (IMAP not configured, or it never arrived).
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


async def login_deliveroo(context) -> None:
    if not settings.DELIVEROO_EMAIL or not settings.DELIVEROO_PASSWORD:
        raise LoginError("DELIVEROO_EMAIL / DELIVEROO_PASSWORD are not configured.")
    page = await context.new_page()
    await page.goto(DELIVEROO_LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
    await _dismiss_deliveroo_cookie_banner(page)
    if "/login" not in page.url:
        return  # the stored session still lands us inside the hub
    await page.get_by_test_id("login-email").fill(settings.DELIVEROO_EMAIL)
    await page.get_by_test_id("login-password").fill(settings.DELIVEROO_PASSWORD)
    await page.get_by_test_id("login-submit").click()
    await page.wait_for_url("**/analytics**", timeout=30_000)


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


async def login_talabat(context) -> None:
    from playwright.async_api import Error as PlaywrightError  # lazy  # noqa: F401

    if not settings.TALABAT_EMAIL or not settings.TALABAT_PASSWORD:
        raise LoginError("TALABAT_EMAIL / TALABAT_PASSWORD are not configured.")
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

    await email_input.fill(settings.TALABAT_EMAIL)
    await password_input.fill(settings.TALABAT_PASSWORD)
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
        )
    except OTPPollingError as exc:
        raise LoginChallengeError(
            "Talabat requested a 2FA OTP but none could be read from the IMAP "
            "mailbox. Configure OTP_IMAP_* or complete the login manually."
        ) from exc

    inputs = page.locator("input[type='tel']")
    if await inputs.count() != 6:
        raise LoginError(f"Unexpected Talabat OTP input count: {await inputs.count()}")
    await inputs.first.click()
    await page.keyboard.type(otp[:6])
    await page.wait_for_url("**/dashboard", timeout=30_000)
    await page.wait_for_timeout(5_000)


# --- Noon -------------------------------------------------------------------
# Ported from channels/noon/exports.py::_ensure_noon_rms_authenticated. Noon RMS
# hosts its sign-in inside an embedded iframe (login-webview-embed.noon.partners):
# fill the channel identifier (email), request the emailed OTP, then fill the
# one-time-code input inside the same frame and dismiss the passkey nudge.

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


async def login_noon(context) -> None:
    if not settings.NOON_EMAIL:
        raise LoginError("NOON_EMAIL is not configured.")
    page = await context.new_page()
    await page.goto(NOON_RMS_URL, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(5_000)

    login_frame = _noon_login_frame(page)
    if not login_frame:
        return  # already authenticated — the RMS route rendered without the frame

    identifier_input = login_frame.locator("input[name='channelIdentifier']")
    otp_since = datetime.now(UTC)
    if await identifier_input.count():
        await identifier_input.fill(settings.NOON_EMAIL)
        await login_frame.locator("button[type='submit']").click()
        await page.wait_for_timeout(5_000)

    login_frame = _noon_login_frame(page)
    if not login_frame:
        return
    otp_input = login_frame.locator("input[data-input-otp='true']")
    if not await otp_input.count():
        return
    try:
        otp = await wait_for_otp(
            sender_filter=NOON_OTP_SENDER,
            subject_filter=NOON_OTP_SUBJECT,
            since=otp_since,
            timeout=90,
        )
    except OTPPollingError as exc:
        raise LoginChallengeError(
            "Noon RMS requested an email OTP but none could be read from the "
            "IMAP mailbox. Configure OTP_IMAP_* or complete the login manually."
        ) from exc
    await otp_input.fill(otp)
    await login_frame.locator("button[type='submit']").click()
    await page.wait_for_timeout(8_000)
    await _dismiss_noon_passkey_prompt(page)
    await page.wait_for_timeout(4_000)


# --- Keeta ------------------------------------------------------------------
# Ported from channels/keeta/discovery.py::ensure_keeta_authenticated. Best
# effort: prime the UAE region, then email -> password across two steps. Keeta
# routinely fronts login with a captcha / device-verification wall; that wall is
# detected and surfaced (AntiBotChallengeError) rather than bumped against.

KEETA_PORTAL_URL = "https://merchant.mykeeta.com/?region=AE"
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
    if "passport.mykeeta.com" in page.url:
        return False
    body = await _keeta_body_lower(page)
    if "sign in" in body or "become a keeta partner" in body:
        return False
    for marker in (
        "order manager",
        "store management",
        "shop management",
        "finance",
        "settlement",
        "dashboard",
    ):
        if marker in body:
            return True
    try:
        no_password = await page.locator("input[type='password']").count() == 0
    except Exception:  # noqa: BLE001
        no_password = False
    return no_password and "merchant.mykeeta.com" in page.url


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
# Not ported: the standalone mm-aggregator-automation repo has no `careem`
# channel package (only deliveroo, keeta, noon, talabat), so there is no login
# flow to carry across. Careem sessions still have to be hand-established until a
# scraper login exists to port.


async def login_careem(context) -> None:
    raise NotImplementedError(
        "Careem automated login is not ported: mm-aggregator-automation has no "
        "channels/careem package to port a login flow from. Establish the Careem "
        "storage_state manually (partners.careem.com) until a scraper login "
        "exists. See channels/login.py."
    )


#: Channel -> login flow. `ensure_session` looks the channel up here after a
#: stale-session probe; a channel absent from this map has no automated login.
LOGIN_FLOWS: dict[str, Callable[..., Awaitable[None]]] = {
    "deliveroo": login_deliveroo,
    "talabat": login_talabat,
    "noon": login_noon,
    "keeta": login_keeta,
    "careem": login_careem,
}
