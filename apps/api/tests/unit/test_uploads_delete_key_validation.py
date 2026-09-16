"""
`DELETE /uploads/image` only ever deletes a key from our own namespace (F-OPS-35).

The handler used to pass ANY caller-supplied `key` straight to
`object_storage.delete_object`, so a `catalogue.manage` caller could delete an
arbitrary object in the shared image bucket. It now validates the resolved
object key against `_OBJECT_KEY_RE` — the exact `<folder>/<uuid4>.<ext>` shape
`upload_image` mints — and rejects anything else with a 400 BEFORE any bucket
call. These tests pin both halves: a real uploaded key (raw and as a full public
URL) still deletes, and a malformed or arbitrary key is refused without ever
reaching `delete_object`.
"""

from __future__ import annotations

import uuid

import pytest

from app.api.v1 import uploads
from app.core.exceptions import BadRequestError

pytestmark = pytest.mark.asyncio


def _real_key(folder: str = "products") -> str:
    """A key exactly as `upload_image` builds it."""
    return f"{folder}/{uuid.uuid4()}.jpg"


@pytest.fixture
def recorded_deletes(monkeypatch):
    """Replace `delete_object` with a recorder so no real GCS call is made."""
    calls: list[str] = []

    def _fake_delete(*, bucket: str, key: str) -> None:
        calls.append(key)

    monkeypatch.setattr(uploads.object_storage, "delete_object", _fake_delete)
    return calls


async def test_a_well_formed_raw_key_is_deleted(recorded_deletes):
    key = _real_key()
    # `_admin` is unused in the body; the dependency only gates access.
    await uploads.delete_image(key=key, _admin=None)
    assert recorded_deletes == [key]


async def test_a_full_public_url_is_stripped_to_its_key_and_deleted(recorded_deletes):
    key = _real_key("categories")
    url = uploads.object_storage.public_url(uploads.settings.GCS_IMAGE_BUCKET, key)
    await uploads.delete_image(key=url, _admin=None)
    assert recorded_deletes == [key]


@pytest.mark.parametrize(
    "bad_key",
    [
        "config.json",  # not in the namespace at all
        "products/../secrets/key.jpg",  # path traversal
        "products/not-a-uuid.jpg",  # stem is not a UUID we minted
        "products/" + str(uuid.uuid4()) + ".txt",  # extension we never store
        "/products/" + str(uuid.uuid4()) + ".jpg",  # leading slash
        str(uuid.uuid4()) + ".jpg",  # no folder segment
        "",  # empty
    ],
)
async def test_a_key_outside_the_namespace_is_refused(recorded_deletes, bad_key):
    with pytest.raises(BadRequestError):
        await uploads.delete_image(key=bad_key, _admin=None)
    assert recorded_deletes == [], "a rejected key must never reach delete_object"
