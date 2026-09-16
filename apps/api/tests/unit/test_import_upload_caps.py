"""F-INV-20: an import upload is bounded in bytes and rows.

`upload.read()` then `list(reader)` both pull the whole file into memory on a
single-worker API, so an unbounded upload is a way to stall the storefront with
one request. The routes cap the file at 5 MB and the parse at 5,000 rows.
"""

from __future__ import annotations

import pytest

from app.api.v1 import import_data
from app.core.exceptions import BadRequestError


def test_a_file_over_the_size_cap_is_refused():
    too_big = b"x" * (import_data._MAX_UPLOAD_BYTES + 1)
    with pytest.raises(BadRequestError, match="too large|Maximum size"):
        import_data._guard_upload_size(too_big)


def test_a_file_at_the_size_cap_is_accepted():
    ok = b"x" * import_data._MAX_UPLOAD_BYTES
    import_data._guard_upload_size(ok)  # does not raise


def test_a_parse_over_the_row_cap_is_refused():
    too_many = [{"a": "1"}] * (import_data._MAX_UPLOAD_ROWS + 1)
    with pytest.raises(BadRequestError, match="Too many rows"):
        import_data._guard_row_count(too_many)


def test_a_parse_at_the_row_cap_is_accepted():
    rows = [{"a": "1"}] * import_data._MAX_UPLOAD_ROWS
    assert import_data._guard_row_count(rows) is rows


def test_csv_content_parse_applies_the_row_cap():
    header = "name\n"
    body = "".join(f"row{i}\n" for i in range(import_data._MAX_UPLOAD_ROWS + 1))
    with pytest.raises(BadRequestError, match="Too many rows"):
        import_data._parse_csv_content((header + body).encode("utf-8"))
