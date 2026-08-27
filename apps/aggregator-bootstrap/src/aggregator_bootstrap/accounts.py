"""Pull and push durable login recipes (email/password + method) via the API.

The API's `aggregator_account` row is the source of truth for *how* to sign
in. Env vars (`DELIVEROO_EMAIL` / `DELIVEROO_PASSWORD`) remain a local
override so a first store can be typed once without a round-trip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import settings
from .push import _headers


@dataclass
class PortalAccount:
    """One channel's login recipe, as the worker sees it."""

    channel: str
    account_ref: str = ""
    login_method: str = ""
    otp_required: bool = False
    email: str = ""
    password: str = ""
    mailbox: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)


_ENV_EMAIL = {
    "deliveroo": lambda: settings.DELIVEROO_EMAIL,
    "talabat": lambda: settings.TALABAT_EMAIL,
    "noon": lambda: settings.NOON_EMAIL,
    "keeta": lambda: settings.KEETA_EMAIL,
    "careem": lambda: settings.CAREEM_EMAIL,
}
_ENV_PASSWORD = {
    "deliveroo": lambda: settings.DELIVEROO_PASSWORD,
    "talabat": lambda: settings.TALABAT_PASSWORD,
    "noon": lambda: settings.NOON_PASSWORD,
    "keeta": lambda: settings.KEETA_PASSWORD,
    "careem": lambda: settings.CAREEM_PASSWORD,
}


def overlay_env(account: PortalAccount) -> PortalAccount:
    """Env wins when set, so a laptop override does not need a DB rewrite."""
    env_email = (_ENV_EMAIL.get(account.channel) or (lambda: ""))().strip()
    env_password = (_ENV_PASSWORD.get(account.channel) or (lambda: ""))().strip()
    if env_email:
        account.email = env_email
    if env_password:
        account.password = env_password
    return account


def from_env(channel: str) -> PortalAccount | None:
    email = (_ENV_EMAIL.get(channel) or (lambda: ""))().strip()
    password = (_ENV_PASSWORD.get(channel) or (lambda: ""))().strip()
    if not email or not password:
        return None
    return PortalAccount(channel=channel, email=email, password=password)


async def pull_accounts() -> list[PortalAccount]:
    """GET every login recipe the worker is allowed to drive."""
    url = f"{settings.AGGREGATOR_API_URL}/api/v1/aggregators/worker/accounts"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=_headers())
        resp.raise_for_status()
        body = resp.json()
    rows = body if isinstance(body, list) else body.get("accounts") or []
    return [
        overlay_env(
            PortalAccount(
                channel=str(row.get("channel") or ""),
                account_ref=str(row.get("account_ref") or ""),
                login_method=str(row.get("login_method") or ""),
                otp_required=bool(row.get("otp_required")),
                email=str(row.get("email") or ""),
                password=str(row.get("password") or ""),
                mailbox=dict(row.get("mailbox") or {}),
                extras=dict(row.get("extras") or {}),
            )
        )
        for row in rows
        if row.get("channel")
    ]


async def pull_account(channel: str) -> PortalAccount | None:
    for account in await pull_accounts():
        if account.channel == channel:
            return account
    return from_env(channel)


async def push_account(payload: dict[str, Any]) -> dict[str, Any]:
    """PUT one login recipe to /aggregators/account."""
    url = f"{settings.AGGREGATOR_API_URL}/api/v1/aggregators/account"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(url, json=payload, headers=_headers())
        resp.raise_for_status()
        return resp.json()
