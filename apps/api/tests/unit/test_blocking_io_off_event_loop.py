"""F-OPS-18: Pillow resize, GCS upload/delete, and signed-URL generation must
not block the event loop from inside `async def` routes.

Each of these used to drop a blocking call straight into the request
coroutine with a bare call, not an `await asyncio.to_thread(...)`: Pillow's
decode/resize/encode is pure CPU work, and both the GCS upload and the IAM
`signBlob` signed-URL call are synchronous network I/O. On the single
uvicorn worker this API runs, one slow call — a big batch of admin photo
uploads, a slow signBlob round trip — stalled every other request in flight.

These tests assert the blocking function is actually dispatched through
`asyncio.to_thread` (not called directly on the loop) and that return values
and exceptions still flow through correctly once the call goes via a thread.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1 import aggregators, uploads
from app.core.exceptions import BadGatewayError

pytestmark = pytest.mark.asyncio


class _RecordingToThread:
    """Stand-in for `asyncio.to_thread` that runs the call inline (so the test
    stays fast and deterministic) while recording that dispatch went through
    it rather than a bare synchronous call."""

    def __init__(self):
        self.calls: list[tuple] = []

    async def __call__(self, func, *args, **kwargs):
        self.calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    @property
    def dispatched_funcs(self):
        return [call[0] for call in self.calls]


class _FakeUploadFile:
    def __init__(self, content_type: str, body: bytes):
        self.content_type = content_type
        self._body = body

    async def read(self) -> bytes:
        return self._body


@pytest.fixture
def recording_to_thread(monkeypatch):
    recorder = _RecordingToThread()
    monkeypatch.setattr(uploads.asyncio, "to_thread", recorder)
    return recorder


async def test_upload_image_resizes_and_uploads_via_to_thread(
    monkeypatch, recording_to_thread
):
    """Both the Pillow resize and the GCS upload go through
    `asyncio.to_thread`, and the route still returns the resulting URL/key
    untouched."""
    optimize_calls = []

    def fake_optimize(data: bytes, content_type: str):
        optimize_calls.append((data, content_type))
        return b"optimized-bytes", "image/jpeg"

    upload_calls = []

    def fake_upload_object(*, bucket, key, body, content_type, cache_control=None):
        upload_calls.append(
            {
                "bucket": bucket,
                "key": key,
                "body": body,
                "content_type": content_type,
                "cache_control": cache_control,
            }
        )

    monkeypatch.setattr(uploads, "optimize_image", fake_optimize)
    monkeypatch.setattr(uploads.object_storage, "upload_object", fake_upload_object)
    monkeypatch.setattr(
        uploads.object_storage,
        "public_url",
        lambda bucket, key: f"https://storage.googleapis.com/{bucket}/{key}",
    )
    monkeypatch.setattr(
        uploads.image_warm_service, "warm_in_background", lambda urls: None
    )

    file = _FakeUploadFile("image/png", b"raw-bytes")

    result = await uploads.upload_image(
        file=file, folder="products", _admin=SimpleNamespace()
    )

    # Both blocking calls were dispatched through asyncio.to_thread, not
    # called directly on the event loop.
    assert fake_optimize in recording_to_thread.dispatched_funcs
    assert fake_upload_object in recording_to_thread.dispatched_funcs

    # Values still flow through correctly once dispatched via the thread.
    assert optimize_calls == [(b"raw-bytes", "image/png")]
    assert len(upload_calls) == 1
    assert upload_calls[0]["body"] == b"optimized-bytes"
    assert upload_calls[0]["content_type"] == "image/jpeg"
    assert upload_calls[0]["key"].endswith(".jpg")
    assert result.url.endswith(result.key)


async def test_upload_image_propagates_gcs_errors_as_bad_gateway(
    monkeypatch, recording_to_thread
):
    """An exception raised inside the threaded GCS call must still surface as
    the route's usual BadGatewayError — the thread hop must not swallow it."""
    monkeypatch.setattr(
        uploads, "optimize_image", lambda data, content_type: (data, content_type)
    )

    def failing_upload(**kwargs):
        raise RuntimeError("bucket is on fire")

    monkeypatch.setattr(uploads.object_storage, "upload_object", failing_upload)

    file = _FakeUploadFile("image/jpeg", b"raw-bytes")

    with pytest.raises(BadGatewayError):
        await uploads.upload_image(
            file=file, folder="products", _admin=SimpleNamespace()
        )

    assert failing_upload in recording_to_thread.dispatched_funcs


async def test_delete_image_deletes_via_to_thread(monkeypatch, recording_to_thread):
    delete_calls = []

    def fake_delete_object(*, bucket, key):
        delete_calls.append({"bucket": bucket, "key": key})

    monkeypatch.setattr(uploads.object_storage, "delete_object", fake_delete_object)

    await uploads.delete_image(key="products/abc.jpg", _admin=SimpleNamespace())

    assert fake_delete_object in recording_to_thread.dispatched_funcs
    assert delete_calls == [
        {"bucket": uploads.settings.GCS_IMAGE_BUCKET, "key": "products/abc.jpg"}
    ]


async def test_statement_invoice_url_signs_via_to_thread(monkeypatch):
    """The signed-URL route (`presigned_get_url` -> IAM `signBlob`, a blocking
    network call) must also dispatch through `asyncio.to_thread`."""
    recorder = _RecordingToThread()
    monkeypatch.setattr(aggregators.asyncio, "to_thread", recorder)

    sign_calls = []

    def fake_presigned_get_url(object_key: str, *, expires_seconds: int):
        sign_calls.append((object_key, expires_seconds))
        return "https://signed.example/invoice.pdf"

    monkeypatch.setattr(
        aggregators.statement_docs, "presigned_get_url", fake_presigned_get_url
    )

    row = SimpleNamespace(
        invoice_object_key="invoices/deliveroo/123/statement.pdf",
        invoice_original_filename="statement.pdf",
        invoice_content_type="application/pdf",
    )
    db = SimpleNamespace(get=AsyncMock(return_value=row))

    result = await aggregators.statement_invoice_url(uuid.uuid4(), db=db)

    assert fake_presigned_get_url in recorder.dispatched_funcs
    assert sign_calls == [("invoices/deliveroo/123/statement.pdf", 3600)]
    assert result.url == "https://signed.example/invoice.pdf"
