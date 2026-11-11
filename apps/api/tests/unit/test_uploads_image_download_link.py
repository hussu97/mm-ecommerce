"""`GET /uploads/image/download` signs a save-as link for a public image in our
own bucket — and nothing else."""

from __future__ import annotations

import pytest

from app.api.v1 import uploads
from app.core.exceptions import BadRequestError

pytestmark = pytest.mark.asyncio

BUCKET = uploads.settings.GCS_IMAGE_BUCKET


@pytest.fixture
def signed(monkeypatch):
    calls: list[dict] = []

    def _fake_sign(**kwargs):
        calls.append(kwargs)
        return f"https://signed/{kwargs['key']}"

    monkeypatch.setattr(uploads.object_storage, "signed_url", _fake_sign)
    return calls


async def test_signs_a_logo_for_download_under_its_name(signed):
    url = f"https://storage.googleapis.com/{BUCKET}/logos/melting-moments.png"
    link = await uploads.image_download_link(
        url=url, name="Melting Moments: Logo", _staff=None
    )
    assert link.filename == "Melting Moments Logo.png"
    assert link.download_url == "https://signed/logos/melting-moments.png"
    assert signed == [
        {
            "bucket": BUCKET,
            "key": "logos/melting-moments.png",
            "download_filename": "Melting Moments Logo.png",
        }
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/logos/x.png",
        f"http://storage.googleapis.com/{BUCKET}/logos/x.png",
        "https://storage.googleapis.com/melting-moments-data/employees/1/photo.jpg",
        f"https://storage.googleapis.com/{BUCKET}/logos/../config.json",
        f"https://storage.googleapis.com/{BUCKET}/backups/db.sql",
    ],
)
async def test_refuses_anything_outside_the_image_bucket(signed, url):
    with pytest.raises(BadRequestError):
        await uploads.image_download_link(url=url, name="x", _staff=None)
    assert signed == []
