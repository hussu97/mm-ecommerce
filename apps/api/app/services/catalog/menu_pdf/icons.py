"""Small inline SVG line-icons for section headers.

Single-stroke, `currentColor`, 24x24 — they inherit the theme accent from CSS,
the way the reference menus set a little cup/glass/dessert glyph beside each
category. The key is guessed from the section's (English) name so the choice
survives an Arabic render, and anything unrecognised gets a neutral fork-and-
spoon rather than nothing.
"""

from __future__ import annotations

# Each value is the inner markup of a 0 0 24 24 SVG; render.py wraps it.
_ICONS: dict[str, str] = {
    "coffee": (
        '<path d="M4 8h13v4a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5V8Z"/>'
        '<path d="M17 9h2.5a2.5 2.5 0 0 1 0 5H17"/>'
        '<path d="M8 3c-.6.8-.6 1.7 0 2.5M12 3c-.6.8-.6 1.7 0 2.5"/>'
    ),
    "iced": (
        '<path d="M6 8h11l-1.3 11.2a2 2 0 0 1-2 1.8h-4.4a2 2 0 0 1-2-1.8L6 8Z"/>'
        '<path d="M5 8h13"/><path d="M10 3v3M14 3.5v2.5"/>'
    ),
    "smoothie": (
        '<path d="M7 9h9l-1 10.3a2 2 0 0 1-2 1.7h-3a2 2 0 0 1-2-1.7L7 9Z"/>'
        '<path d="M7 9c0-3 2.4-5 4.5-5S16 6 16 9"/><path d="M13.5 4.2 16 2"/>'
    ),
    "juice": (
        '<path d="M8 8h8l-.8 11a2 2 0 0 1-2 1.9h-2.4a2 2 0 0 1-2-1.9L8 8Z"/>'
        '<path d="M8.4 12.5h7.2"/><path d="M12 8V4l3-2"/>'
    ),
    "tea": (
        '<path d="M5 9h12v3a5 5 0 0 1-5 5H10a5 5 0 0 1-5-5V9Z"/>'
        '<path d="M17 10h2a2 2 0 0 1 0 4h-2"/><path d="M4 21h14"/>'
        '<path d="M10 3c-.6.8-.6 1.7 0 2.5M13 3c-.6.8-.6 1.7 0 2.5"/>'
    ),
    "cake": (
        '<path d="M4 21V12a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v9"/>'
        '<path d="M3 21h18"/><path d="M4 15c1.6 1.4 3.2 1.4 4.8 0s3.2-1.4 4.8 0 3.2 1.4 4.8 0"/>'
        '<path d="M12 4v3"/><circle cx="12" cy="3.2" r="1"/>'
    ),
    "cookie": (
        '<path d="M21 12a9 9 0 1 1-9-9 3.2 3.2 0 0 0 3 3 3.2 3.2 0 0 0 3 3 3.2 3.2 0 0 0 3 3Z"/>'
        '<circle cx="9" cy="10" r="1"/><circle cx="14" cy="14" r="1"/><circle cx="9.5" cy="15" r="1"/>'
    ),
    "brownie": (
        '<path d="M4 8h16v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8Z"/>'
        '<path d="M4 12h16M12 8v10"/><path d="M8 5.5c.8-1 1.6-1 2.4 0M13.6 5.5c.8-1 1.6-1 2.4 0"/>'
    ),
    "dessert": (
        '<path d="M6 11h12l-1.2 8.2a2 2 0 0 1-2 1.8H9.2a2 2 0 0 1-2-1.8L6 11Z"/>'
        '<path d="M8 11a4 4 0 0 1 8 0"/><path d="M12 7V4"/><circle cx="12" cy="3.2" r="1"/>'
    ),
    "default": (
        '<path d="M6 3v8a3 3 0 0 0 6 0V3"/><path d="M9 3v18"/>'
        '<path d="M17 3c-1.5 1-2 3-2 6s.5 4 2 5v7"/>'
    ),
}

# (key, keywords) — first keyword found in the lowered section name wins, in order.
_KEYWORD_ORDER: list[tuple[str, tuple[str, ...]]] = [
    ("iced", ("iced", "cold", "frappe", "frappé")),
    ("smoothie", ("smoothie", "shake", "milkshake")),
    ("juice", ("juice", "mocktail", "lemonade", "refresher")),
    ("tea", ("tea", "karak", "matcha", "chai")),
    (
        "coffee",
        ("coffee", "espresso", "latte", "cappuccino", "beverage", "hot", "drink"),
    ),
    ("brownie", ("brownie",)),
    ("cookie", ("cookie", "biscuit")),
    ("cake", ("cake", "gateau")),
    ("dessert", ("dessert", "sweet", "pudding", "mousse", "tiramisu", "cheesecake")),
]


def icon_key_for(section_name: str | None) -> str:
    name = (section_name or "").lower()
    for key, words in _KEYWORD_ORDER:
        if any(w in name for w in words):
            return key
    return "default"


def icon_svg(key: str, *, size: int = 22, stroke: float = 1.6) -> str:
    inner = _ICONS.get(key, _ICONS["default"])
    return (
        f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" '
        f'stroke="currentColor" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true">{inner}</svg>'
    )
