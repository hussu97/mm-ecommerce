"""Microsoft Graph mailbox access for aggregator OTPs.

Each aggregator stores its *own* Azure app (client id + secret) and, after a
one-time `mailbox-auth`, its own refresh token — on that channel's
`aggregator_account` mailbox blob, not a global env pair. Hotmail / personal
Microsoft accounts reject the app-only (client-credentials) flow; `Mail.Read`
application permission only covers work/school mailboxes. The confidential
client is used to exchange an authorization code for a refresh token, then to
mint short-lived Graph access tokens for `GET /me/messages`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
AUTHORIZE_SCOPES = (
    "offline_access https://graph.microsoft.com/Mail.Read "
    "https://graph.microsoft.com/User.Read"
)
TOKEN_SCOPES = "https://graph.microsoft.com/Mail.Read offline_access"
DEFAULT_REDIRECT = "http://127.0.0.1:8765/callback"
DEFAULT_TENANT = "consumers"


class GraphMailboxError(RuntimeError):
    """Graph token or mail call failed."""


@dataclass(frozen=True)
class GraphApp:
    """One aggregator's Azure app — never shared across channels."""

    client_id: str
    client_secret: str
    tenant: str = DEFAULT_TENANT
    redirect_uri: str = DEFAULT_REDIRECT

    @classmethod
    def from_mailbox(cls, mailbox: dict[str, Any] | None) -> "GraphApp":
        box = mailbox or {}
        client_id = str(box.get("client_id") or "").strip()
        client_secret = str(box.get("client_secret") or "")
        if not client_id or not client_secret:
            raise GraphMailboxError(
                "this aggregator's Microsoft app is missing client id or secret. "
                "Save them on Admin → Aggregators → Logins for this channel."
            )
        tenant = str(box.get("tenant") or DEFAULT_TENANT).strip() or DEFAULT_TENANT
        redirect = str(box.get("redirect_uri") or DEFAULT_REDIRECT).strip() or DEFAULT_REDIRECT
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            tenant=tenant,
            redirect_uri=redirect,
        )

    def token_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token"

    def authorize_url(self, *, state: str = "mailbox") -> str:
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "response_mode": "query",
            "scope": AUTHORIZE_SCOPES,
            "prompt": "select_account",
            "state": state,
        }
        return (
            f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/authorize"
            f"?{urlencode(params)}"
        )

    def token_form(self) -> dict[str, str]:
        return {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
        }


def exchange_code(app: GraphApp, code: str) -> dict[str, Any]:
    """Swap the authorize-code for access + refresh tokens. Never log the body."""
    form = app.token_form()
    form.update(
        {"grant_type": "authorization_code", "code": code, "scope": TOKEN_SCOPES}
    )
    return _post_token(app, form, need="refresh_token")


def refresh_access_token(app: GraphApp, refresh_token: str) -> dict[str, Any]:
    form = app.token_form()
    form.update(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": TOKEN_SCOPES,
        }
    )
    return _post_token(app, form, need="access_token")


def _post_token(app: GraphApp, form: dict[str, str], *, need: str) -> dict[str, Any]:
    with httpx.Client(timeout=30) as client:
        resp = client.post(app.token_url(), data=form)
    ctype = resp.headers.get("content-type", "")
    data = resp.json() if ctype.startswith("application/json") else {}
    if resp.status_code >= 400 or not data.get(need):
        err = data.get("error_description") or data.get("error") or resp.text[:200]
        raise GraphMailboxError(f"token call failed: {err}")
    return data


def _message_text(message: dict[str, Any]) -> str:
    body = message.get("body") if isinstance(message.get("body"), dict) else {}
    content = str(body.get("content") or message.get("bodyPreview") or "")
    if str(body.get("contentType") or "").lower() == "html":
        content = re.sub(r"<[^>]+>", " ", content)
    return content


def _sender(message: dict[str, Any]) -> str:
    frm = message.get("from") if isinstance(message.get("from"), dict) else {}
    email_addr = (
        frm.get("emailAddress") if isinstance(frm.get("emailAddress"), dict) else {}
    )
    return f"{email_addr.get('name') or ''} {email_addr.get('address') or ''}".strip()


def _received_at(message: dict[str, Any]) -> datetime | None:
    raw = message.get("receivedDateTime")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def list_recent_messages(*, access_token: str, top: int = 25) -> list[dict[str, Any]]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    params = {
        "$top": str(top),
        "$orderby": "receivedDateTime desc",
        "$select": "subject,from,body,bodyPreview,receivedDateTime",
    }
    with httpx.Client(timeout=30) as client:
        resp = client.get(f"{GRAPH_BASE}/me/messages", headers=headers, params=params)
    if resp.status_code >= 400:
        raise GraphMailboxError(f"Graph mail list returned {resp.status_code}")
    payload = resp.json()
    rows = payload.get("value") if isinstance(payload, dict) else None
    return [row for row in (rows or []) if isinstance(row, dict)]


def fetch_latest_otp(
    *,
    mailbox: dict[str, Any],
    refresh_token: str,
    sender_filter: str | None,
    subject_filter: str | None,
    since: datetime | None,
    otp_pattern: re.Pattern[str],
    max_messages: int = 25,
) -> tuple[str | None, str | None]:
    """Return (otp, new_refresh_token). new_refresh_token is set when Graph rotated it."""
    from .mailbox import _message_matches, extract_otp_from_text

    app = GraphApp.from_mailbox(mailbox)
    tokens = refresh_access_token(app, refresh_token)
    access = str(tokens.get("access_token") or "")
    rotated = str(tokens.get("refresh_token") or "") or None
    if rotated == refresh_token:
        rotated = None
    messages = list_recent_messages(access_token=access, top=max_messages)
    best: tuple[datetime, str] | None = None
    for message in messages:
        received = _received_at(message) or datetime.min.replace(tzinfo=UTC)
        if since is not None and received < since:
            continue
        subject = str(message.get("subject") or "")
        sender = _sender(message)
        if not _message_matches(
            subject=subject,
            sender=sender,
            sender_filter=sender_filter,
            subject_filter=subject_filter,
        ):
            continue
        code = extract_otp_from_text(_message_text(message), otp_pattern=otp_pattern)
        if not code:
            continue
        if best is None or received >= best[0]:
            best = (received, code)
    return (best[1] if best else None, rotated)
