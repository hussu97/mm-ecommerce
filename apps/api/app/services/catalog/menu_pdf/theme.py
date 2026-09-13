"""Per-brand visual theme for the printed menu.

The theme is chosen from the legal entity the menu trades under — never from a
branch name or a hard-coded switch — so a new trading name gets a sensible look
by falling to the default, and the two MM runs today (Attibassi coffee, Melting
Moments patisserie) each get their own palette and display face.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Legal-entity `reference` slugs we have bespoke palettes for. Anything else
#: (a new trade licence) renders under DEFAULT_THEME — the Melting Moments look.
ATTIBASSI_REFERENCE = "najm"
MELTING_MOMENTS_REFERENCE = "fatema"


@dataclass(frozen=True)
class Theme:
    key: str
    #: Page + panel colours.
    page_bg: str
    panel_bg: str
    #: Ink: primary text, muted (descriptions), and the brand accent / rules.
    ink: str
    muted: str
    accent: str
    price_ink: str
    #: Faint hairline for rules and thumbnail rings.
    hairline: str
    #: Display face for the masthead + section titles, and the body face.
    display_font: str
    body_font: str
    #: Whether section titles are set in the serif display face (Attibassi) or
    #: in a heavier weight of the body face (Melting Moments).
    title_uses_display: bool
    #: Uppercase section titles (coffee-shop convention) or title-case.
    uppercase_titles: bool
    #: How the entity logo is masked in the masthead — "circle" crops a round
    #: emblem (Attibassi) cleanly off its white square; "rounded" keeps a square
    #: wordmark (Melting Moments) with soft corners.
    logo_shape: str = "rounded"


ATTIBASSI_THEME = Theme(
    key="attibassi",
    page_bg="#efe7d6",
    panel_bg="#f6f1e4",
    ink="#2b2015",
    muted="#6f6151",
    accent="#7a5a34",
    price_ink="#2b2015",
    hairline="#c9bca3",
    display_font="Marcellus",
    body_font="Poppins",
    title_uses_display=True,
    uppercase_titles=True,
    logo_shape="circle",
)

MELTING_MOMENTS_THEME = Theme(
    key="melting-moments",
    page_bg="#f3e4e2",
    panel_bg="#e9d3d1",
    ink="#33242a",
    muted="#836a6f",
    accent="#9c5b63",
    price_ink="#33242a",
    hairline="#d8bdbb",
    display_font="Poppins",
    body_font="Poppins",
    title_uses_display=False,
    uppercase_titles=False,
    logo_shape="rounded",
)

DEFAULT_THEME = MELTING_MOMENTS_THEME


def theme_for_entity_reference(reference: str | None) -> Theme:
    """The palette for a legal entity's `reference` slug."""
    if reference == ATTIBASSI_REFERENCE:
        return ATTIBASSI_THEME
    if reference == MELTING_MOMENTS_REFERENCE:
        return MELTING_MOMENTS_THEME
    return DEFAULT_THEME
