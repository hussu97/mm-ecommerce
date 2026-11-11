"""Uploaded-image responses."""

from __future__ import annotations

from pydantic import BaseModel


class ImageDownloadLink(BaseModel):
    """A signed link that saves a public image (`Content-Disposition:
    attachment`) instead of showing it. The public bucket ignores that header
    on an unsigned URL, and a browser ignores `<a download>` across origins, so
    this is the only way the console can offer a Download button for a logo."""

    download_url: str
    filename: str
