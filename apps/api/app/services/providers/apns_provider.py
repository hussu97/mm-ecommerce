"""
Apple Push Notification service, over HTTP/2, signed with a `.p8` key.

Transport only. What is worth waking a register for lives in `push_service`.

Three things about APNs decide the shape of this file.

**It is HTTP/2 or nothing.** Apple's endpoint refuses HTTP/1.1, which is why
`httpx[http2]` is a dependency rather than plain `httpx`. A missing `h2` package
produces a connection error that says nothing about the cause.

**Authentication is a JWT you mint yourself**, ES256 over the `.p8`, valid for an
hour and reusable for every notification in that hour. Apple rate-limits token
minting hard enough that a fresh token per push earns a `TooManyProviderTokenUpdates`,
so it is cached until it is nearly stale.

**Sandbox and production are different hosts and different tokens.** A TestFlight
build registers a sandbox token; the same code against the production host answers
`BadDeviceToken` and names nothing useful. The token carries which host it came
from, so each one is sent where it belongs.

The key itself is account-wide and team-scoped: one key serves every app in the
team and both hosts. Apple caps an account at two, so it is shared with the other
apps rather than a new one being issued per project.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from jose import jwt

from app.core.config import settings

logger = logging.getLogger(__name__)

__all__ = ["ApnsClient", "ApnsError", "ApnsResult", "provider"]

HOSTS = {
    "production": "https://api.push.apple.com",
    "sandbox": "https://api.sandbox.push.apple.com",
}

#: Apple rejects a token older than an hour and refuses one minted too often.
#: Fifty minutes leaves room for clock skew without courting either.
_TOKEN_TTL_SECONDS = 50 * 60

#: The device is gone — uninstalled, restored, or the token was reissued. The
#: only correct response is to stop sending to it.
_DEAD_TOKEN_REASONS = frozenset(
    {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}
)


class ApnsError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, reason: str = ""):
        super().__init__(message)
        self.status = status
        self.reason = reason

    @property
    def is_dead_token(self) -> bool:
        return self.reason in _DEAD_TOKEN_REASONS


@dataclass(frozen=True)
class ApnsResult:
    token: str
    delivered: bool
    reason: str = ""
    #: True when the token should be retired rather than retried.
    dead: bool = False


class ApnsClient:
    def __init__(self) -> None:
        self._jwt: str | None = None
        self._jwt_minted_at: float = 0.0

    @property
    def is_configured(self) -> bool:
        return bool(
            settings.APNS_KEY_ID
            and settings.APNS_TEAM_ID
            and settings.APNS_KEY_P8.strip()
        )

    # ── authentication ────────────────────────────────────────────────────────

    def _auth_token(self) -> str:
        """The provider JWT, minted at most once every fifty minutes."""
        now = time.time()
        if self._jwt and now - self._jwt_minted_at < _TOKEN_TTL_SECONDS:
            return self._jwt

        # The workflow writes the key as a single line with literal \n, because
        # a multi-line GitHub secret does not survive a `printf` into .env.
        key = settings.APNS_KEY_P8.replace("\\n", "\n").strip()
        self._jwt = jwt.encode(
            {"iss": settings.APNS_TEAM_ID, "iat": int(now)},
            key,
            algorithm="ES256",
            headers={"kid": settings.APNS_KEY_ID},
        )
        self._jwt_minted_at = now
        return self._jwt

    def reset(self) -> None:
        """Forget the cached JWT — for a key rotation, and for tests."""
        self._jwt = None
        self._jwt_minted_at = 0.0

    # ── sending ───────────────────────────────────────────────────────────────

    async def send(
        self,
        *,
        token: str,
        bundle_id: str,
        payload: dict[str, Any],
        sandbox: bool = False,
        collapse_id: str | None = None,
        push_type: str = "alert",
        priority: int = 10,
        expiration: int = 0,
    ) -> ApnsResult:
        """
        Deliver one notification, and say plainly whether it landed.

        Never raises for a per-device failure. One dead iPad must not stop the
        other three being told an order has arrived, so the outcome is returned
        and the caller decides what to retire.
        """
        if not self.is_configured:
            raise ApnsError("APNs is not configured")

        host = HOSTS["sandbox" if sandbox else "production"]
        headers = {
            "authorization": f"bearer {self._auth_token()}",
            "apns-topic": bundle_id,
            "apns-push-type": push_type,
            "apns-priority": str(priority),
            # 0 means "deliver now or discard". A register that was offline when
            # the order came in should not be told about it an hour later.
            "apns-expiration": str(expiration),
        }
        if collapse_id:
            # Several updates about one order replace each other rather than
            # stacking up on the lock screen.
            headers["apns-collapse-id"] = collapse_id[:64]

        try:
            async with httpx.AsyncClient(
                http2=True, timeout=settings.APNS_TIMEOUT_SECONDS
            ) as client:
                response = await client.post(
                    f"{host}/3/device/{token}", json=payload, headers=headers
                )
        except httpx.HTTPError as exc:
            logger.warning("APNs unreachable for %s…: %s", token[:12], exc)
            return ApnsResult(token=token, delivered=False, reason=str(exc))

        if response.status_code == 200:
            return ApnsResult(token=token, delivered=True)

        reason = ""
        try:
            reason = (response.json() or {}).get("reason", "")
        except ValueError:
            reason = response.text[:120]

        dead = reason in _DEAD_TOKEN_REASONS or response.status_code == 410
        # A 403 is almost always the key, the team id or the wrong `.p8` — and
        # it will be every push, not one. Worth saying loudly once.
        if response.status_code == 403:
            logger.error(
                "APNs rejected our credentials (%s). Check APNS_KEY_ID, "
                "APNS_TEAM_ID and that the .p8 is the push key, not the App "
                "Store Connect one.",
                reason,
            )
            self.reset()
        elif not dead:
            logger.warning(
                "APNs %s for %s…: %s", response.status_code, token[:12], reason
            )

        return ApnsResult(
            token=token,
            delivered=False,
            reason=reason or str(response.status_code),
            dead=dead,
        )


provider = ApnsClient()
