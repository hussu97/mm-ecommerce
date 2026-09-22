"""One guarded, server-side Geocoding API seam for marketplace addresses.

Marketplace feeds normally already provide a pin.  When they do not, this
module turns the address parts they do expose into a UAE-biased geocoding query.
It deliberately returns an outcome instead of raising: an incomplete or stale
address must not make a sales ingestion fail, and the caller persists terminal
outcomes so an hourly pull cannot repeatedly spend the Google quota.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import google.auth
import httpx
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request

from app.core.config import settings

logger = logging.getLogger(__name__)

_URL = "https://geocode.googleapis.com/v4/geocode/address"
_OAUTH_SCOPE = "https://www.googleapis.com/auth/maps-platform.geocode.address"
_ADDRESS_KEYS = (
    "address",
    "text",
    "line",
    "street",
    "address_line_1",
    "address_line_2",
    "area",
    "city",
    "building",
    "house",
    "unit",
)
_credentials: Credentials | None = None
_credentials_lock = asyncio.Lock()


@dataclass(frozen=True)
class GeocodingResult:
    address: dict[str, Any]
    status: str


def coordinates(address: dict[str, Any] | None) -> tuple[Decimal, Decimal] | None:
    """Return a valid UAE pin from either channel spelling, else ``None``."""
    if not isinstance(address, dict):
        return None
    latitude = _decimal(address.get("latitude", address.get("lat")))
    longitude = _decimal(address.get("longitude", address.get("lng")))
    if latitude is None or longitude is None or not is_uae(latitude, longitude):
        return None
    return latitude, longitude


def normalise_coordinates(address: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert provider E7 coordinates to degrees while retaining field names.

    Noon and Deliveroo payloads have both emitted integer degrees multiplied by
    10^7. A decimal latitude/longitude cannot exceed 180, so the conversion is
    safe without tying the generic ingestion seam to either provider.
    """
    if not isinstance(address, dict):
        return address
    normalized = dict(address)
    for long_name, short_name in (("latitude", "lat"), ("longitude", "lng")):
        key = long_name if long_name in normalized else short_name
        value = _raw_decimal(normalized.get(key))
        if value is not None and abs(value) > 180:
            normalized[key] = float(value / Decimal("10000000"))
    return normalized


def has_coordinate_values(address: dict[str, Any] | None) -> bool:
    """Whether a source supplied both coordinate fields, valid or otherwise."""
    if not isinstance(address, dict):
        return False
    return (
        _decimal(address.get("latitude", address.get("lat"))) is not None
        and _decimal(address.get("longitude", address.get("lng"))) is not None
    )


def is_uae(latitude: Decimal | float, longitude: Decimal | float) -> bool:
    """Broad UAE envelope; prevents a bad geocode becoming a delivery heat cell."""
    return Decimal("22.5") <= Decimal(str(latitude)) <= Decimal("26.5") and Decimal(
        "51.3"
    ) <= Decimal(str(longitude)) <= Decimal("56.7")


def address_text(address: dict[str, Any] | None) -> str | None:
    """Build one complete, de-duplicated address query without logging PII."""
    if not isinstance(address, dict):
        return None
    parts: list[str] = []
    seen: set[str] = set()
    for key in _ADDRESS_KEYS:
        value = address.get(key)
        if value is None:
            continue
        text = str(value).strip()
        normalized = text.casefold()
        if text and "*" not in text and normalized not in seen:
            seen.add(normalized)
            parts.append(text)
    if not parts:
        return None
    if not any("united arab emirates" in part.casefold() for part in parts):
        parts.append("United Arab Emirates")
    return ", ".join(parts)


async def geocode(address: dict[str, Any] | None) -> GeocodingResult:
    """Add a UAE coordinate to an address, or return a durable no-retry result."""
    normalized = normalise_coordinates(address) or {}
    if coordinates(normalized) is not None:
        return GeocodingResult(normalized, "provided")
    if has_coordinate_values(normalized):
        return GeocodingResult(normalized, "outside_uae")
    query = address_text(normalized)
    if query is None:
        return GeocodingResult(normalized, "failed")
    token = await _access_token()
    if token is None:
        return GeocodingResult(normalized, "not_configured")
    try:
        async with httpx.AsyncClient(
            timeout=settings.GOOGLE_GEOCODING_TIMEOUT_SECONDS
        ) as client:
            response = await client.get(
                _URL,
                params={
                    "address.addressLines": query,
                    "address.regionCode": "AE",
                    "regionCode": "AE",
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Goog-FieldMask": "results.location",
                    "X-Goog-User-Project": settings.GOOGLE_GEOCODING_BILLING_PROJECT,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("aggregator address geocode failed: %s", exc)
        return GeocodingResult(normalized, "failed")

    result = (payload.get("results") or [None])[0]
    location = result.get("location", {}) if isinstance(result, dict) else {}
    latitude, longitude = (
        _decimal(location.get("latitude")),
        _decimal(location.get("longitude")),
    )
    if latitude is None or longitude is None:
        return GeocodingResult(normalized, "failed")
    if not is_uae(latitude, longitude):
        return GeocodingResult(normalized, "outside_uae")
    normalized["latitude"] = float(latitude)
    normalized["longitude"] = float(longitude)
    return GeocodingResult(normalized, "resolved")


async def _access_token() -> str | None:
    """Mint an ADC token from the attached VM identity without a key file."""
    if not settings.GOOGLE_GEOCODING_BILLING_PROJECT:
        return None
    try:
        async with _credentials_lock:
            return await asyncio.to_thread(_refresh_access_token)
    except Exception as exc:
        # Missing local ADC or a VM IAM/scope misconfiguration is operational
        # configuration, not an ingestion failure. Do not include address text.
        logger.warning("aggregator geocoding ADC unavailable: %s", exc)
        return None


def _refresh_access_token() -> str:
    global _credentials
    if _credentials is None:
        _credentials, _ = google.auth.default(scopes=[_OAUTH_SCOPE])
    # `refresh()` itself does not check expiry. Reusing the short-lived metadata
    # token avoids a metadata round-trip for every address in one pull.
    if not _credentials.valid:
        _credentials.refresh(Request())
    if not _credentials.token:
        raise RuntimeError("ADC did not return an access token")
    return _credentials.token


def _decimal(value: Any) -> Decimal | None:
    number = _raw_decimal(value)
    return (
        number / Decimal("10000000")
        if number is not None and abs(number) > 180
        else number
    )


def _raw_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
