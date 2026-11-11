"""
`POST /uploads/image` only writes into the folders the admin console uses (P2-10).

`folder` used to be free text, so a `catalogue.manage` token could write any
prefix of the public bucket. It is now checked against `ALLOWED_FOLDERS`
before anything is decoded or uploaded — and that list is checked against the
admin source, so a new upload site cannot ship without its folder.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.api.v1 import uploads
from app.core.exceptions import BadRequestError

ADMIN = Path(__file__).resolve().parents[3] / "admin"

#: Every way the admin names an upload folder today.
_FOLDER_PATTERNS = [
    re.compile(r"uploadImage\([^,()]+,\s*'([^']+)'\)"),
    re.compile(r"\bfolder=\"([^\"]+)\""),
    re.compile(r"\bfolder:\s*'([^']+)'"),
    re.compile(r"\bfolder\s*=\s*'([^']+)'"),
    re.compile(r"\bfolder\s*\?\?\s*'([^']+)'"),
]


class _Upload:
    def __init__(self, body: bytes, content_type: str = "image/jpeg"):
        self.content_type = content_type
        self._body = body

    async def read(self) -> bytes:
        return self._body


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (1, 2, 3)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def recorded_uploads(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        uploads.object_storage,
        "upload_object",
        lambda **kw: calls.append(kw["key"]),
    )
    monkeypatch.setattr(
        uploads.image_warm_service, "warm_in_background", lambda urls: None
    )
    return calls


def test_the_admin_source_only_uses_allowed_folders():
    found: set[str] = set()
    for path in ADMIN.rglob("*.ts*"):
        if "node_modules" in path.parts or ".next" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "folder" not in text:
            continue
        for pattern in _FOLDER_PATTERNS:
            found.update(pattern.findall(text))
    # The scan must actually see the call sites, or this proves nothing.
    assert {"products", "logos", "cater", "banners"} <= found
    # `mailbox_folder` and friends are not upload folders.
    found.discard("INBOX")
    assert found <= uploads.ALLOWED_FOLDERS, found - uploads.ALLOWED_FOLDERS


@pytest.mark.parametrize("folder", sorted(uploads.ALLOWED_FOLDERS))
async def test_every_admin_folder_still_uploads(folder, recorded_uploads):
    res = await uploads.upload_image(
        file=_Upload(_jpeg()), folder=folder, _admin=SimpleNamespace()
    )
    assert res.key.startswith(f"{folder}/")
    assert recorded_uploads == [res.key]


@pytest.mark.parametrize(
    "folder",
    [
        "enquiries",
        "enquiries/pending",
        "products/../enquiries",
        "../products",
        "products/",
        "Products",
        "",
        "config",
    ],
)
async def test_any_other_folder_is_a_400_before_upload(folder, recorded_uploads):
    with pytest.raises(BadRequestError):
        await uploads.upload_image(
            file=_Upload(_jpeg()), folder=folder, _admin=SimpleNamespace()
        )
    assert recorded_uploads == []


async def test_a_non_image_is_refused_not_published(recorded_uploads):
    with pytest.raises(BadRequestError):
        await uploads.upload_image(
            file=_Upload(b"<svg onload=alert(1)>"),
            folder="products",
            _admin=SimpleNamespace(),
        )
    assert recorded_uploads == []
