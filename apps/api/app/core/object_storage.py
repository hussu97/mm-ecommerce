"""Google Cloud Storage helper, driven by Application Default Credentials.

Production runs on a GCE VM whose default service account
(`…-compute@developer.gserviceaccount.com`) has `roles/editor` and the
`devstorage.read_write` scope, and the API container can reach the metadata
server — so `google.auth.default()` / `storage.Client()` work in-container with
no key file at all. That same ADC path also works on a maintainer's machine that
has run `gcloud auth application-default login`.

Everything here is defensive: a storage hiccup must never crash the request that
happened to touch object storage. Callers get a falsy/None result and decide.

Signed URLs are the one awkward case. ADC on a GCE VM has no private key in
hand, so a blob cannot self-sign; instead we sign through the IAM
`signBlob` API using the VM service account's own identity (it now holds
`roles/iam.serviceAccountTokenCreator` on itself). `generate_signed_url` does
this transparently when handed the SA email and a fresh access token.
"""

from __future__ import annotations

import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    """Lazily build and cache a module-level `storage.Client()`.

    `google.auth.default()` (called inside `storage.Client()`) picks up the VM
    service account via the metadata server, or the local ADC file in dev.
    """
    global _client
    if _client is None:
        from google.cloud import storage

        _client = storage.Client()
    return _client


def public_url(bucket: str, key: str) -> str:
    """The canonical public object URL for a public-read bucket."""
    return f"https://storage.googleapis.com/{bucket}/{key}"


def upload_object(
    *,
    bucket: str,
    key: str,
    body: bytes,
    content_type: str,
    cache_control: str | None = None,
) -> None:
    """Write `body` to `gs://{bucket}/{key}`.

    Raises on failure so callers that care (image upload) can surface a 502;
    the read/delete/sign helpers below swallow instead.
    """
    client = _get_client()
    blob = client.bucket(bucket).blob(key)
    if cache_control is not None:
        blob.cache_control = cache_control
    blob.upload_from_string(body, content_type=content_type)


def delete_object(*, bucket: str, key: str) -> None:
    """Delete an object, treating a missing object as success."""
    try:
        from google.api_core.exceptions import NotFound

        try:
            _get_client().bucket(bucket).blob(key).delete()
        except NotFound:
            return
    except Exception:
        logger.exception("failed to delete gs://%s/%s", bucket, key)
        raise


def signed_url(
    *,
    bucket: str,
    key: str,
    expires_seconds: int = 3600,
) -> str | None:
    """A short-lived V4 signed GET URL, or None on any failure.

    ADC on GCE has no private key, so we sign via IAM: take the creds + SA
    email from `google.auth.default()`, mint a fresh access token, and let
    `generate_signed_url` call the IAM `signBlob` API with them.
    """
    try:
        import google.auth
        from google.auth.transport.requests import Request

        creds, _ = google.auth.default()
        creds.refresh(Request())

        email = getattr(creds, "service_account_email", None)
        if not email or email == "default":
            email = _metadata_service_account_email() or email
        if not email:
            logger.warning("cannot resolve service account email for signing")
            return None

        blob = _get_client().bucket(bucket).blob(key)
        return blob.generate_signed_url(
            version="v4",
            expiration=timedelta(seconds=expires_seconds),
            method="GET",
            service_account_email=email,
            access_token=creds.token,
        )
    except Exception:
        logger.exception("failed to sign gs://%s/%s", bucket, key)
        return None


def _metadata_service_account_email() -> str | None:
    """Ask the GCE metadata server for the default SA email."""
    try:
        from google.auth.compute_engine import _metadata
        from google.auth.transport.requests import Request

        return _metadata.get(
            Request(),
            "instance/service-accounts/default/email",
        )
    except Exception:
        logger.exception("metadata server did not return an SA email")
        return None
