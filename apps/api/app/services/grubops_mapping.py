"""
Working out which of our items is which of theirs, and letting a human correct it.

GrubOps keys its menu by `recipeId`; we key ours by uuid; nothing joins the two.
The only thing both catalogues share is the name on the item, so that is what
this matches on — and a name match is a guess, which is the entire reason
`grubops_item_map.approved` exists. This proposes; a person disposes, in the
admin console; and nothing is ever pushed for a row nobody has approved.

**Re-runnable on purpose.** Menus change — a new cake, a renamed filling — so
this is a button somebody presses again rather than a migration that ran once.
That is also why it is not in `131_grubops_mapping`: matching needs a live call
to GrubOps and a fuzzy comparison, neither of which belongs in a migration that
has to produce the same database every time.

**It never overwrites a decision.** A row a human has approved, or corrected by
hand, is left exactly as it is; only its name is refreshed for the review
screen. New items appear as unapproved suggestions. So pressing the button twice
is safe, and pressing it after a menu change adds the new things without
undoing any of the work already done on the old ones.

**Options are matched inside their parent, not across the menu.** "Pistachio" is
a filling on four different boxes, and a match made against the whole catalogue
would cheerfully pair one product's pistachio with another's. So an option is
only ever compared with the modifiers of the recipe its own product matched to.

`difflib` rather than a fuzzy-matching dependency: the strings are short, the
catalogue is small, and this runs when somebody presses a button.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.grubops import (
    KIND_OPTION,
    KIND_PRODUCT,
    MATCH_EXACT,
    MATCH_FUZZY,
    TYPE_MODIFIER,
    TYPE_RECIPE,
    GrubOpsItemMap,
    GrubOpsLocationMap,
)
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.product import WEB_CHANNEL, Product, sells_on
from app.services.providers.grubops_provider import GrubOpsError, provider

logger = logging.getLogger(__name__)

#: Below this, a pair is not offered at all. Deliberately high: an unmatched
#: item is visible on the review screen and costs somebody a minute, where a
#: wrong match that gets waved through takes the wrong cake off Talabat.
MATCH_THRESHOLD = 0.82


def normalise(name: str) -> str:
    """
    A name reduced to what two catalogues can be expected to agree on.

    Case, accents, punctuation and runs of whitespace all differ between systems
    that were typed into by different people on different days, and none of them
    change which cake is meant.
    """
    folded = unicodedata.normalize("NFKD", name or "")
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = folded.casefold()
    folded = re.sub(r"[^\w\s]", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


def similarity(left: str, right: str) -> float:
    """How alike two normalised names are, 0 to 1."""
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


@dataclass
class Candidate:
    """One item as GrubOps holds it."""

    item_id: str
    name: str
    brand_id: str
    grubops_type: str
    #: Set for a modifier; the recipe it hangs off, so options can be scoped.
    parent_recipe_id: str | None = None


@dataclass
class SyncSummary:
    """What one press of the button did."""

    created: int = 0
    refreshed: int = 0
    unmatched_ours: list[str] = field(default_factory=list)
    unmatched_theirs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "created": self.created,
            "refreshed": self.refreshed,
            "unmatched_ours": self.unmatched_ours,
            "unmatched_theirs": self.unmatched_theirs,
            "errors": self.errors,
        }


def best_match(
    name: str, candidates: list[Candidate]
) -> tuple[Candidate | None, float, str]:
    """
    The likeliest GrubOps item for one of ours.

    Returns the candidate, its score, and how it was found. An exact match on
    the normalised name short-circuits — it is both the common case and the only
    one worth full confidence.
    """
    target = normalise(name)
    if not target:
        return None, 0.0, MATCH_FUZZY

    best: Candidate | None = None
    best_score = 0.0
    for candidate in candidates:
        theirs = normalise(candidate.name)
        if theirs == target:
            return candidate, 1.0, MATCH_EXACT
        score = similarity(target, theirs)
        if score > best_score:
            best, best_score = candidate, score

    if best is None or best_score < MATCH_THRESHOLD:
        return None, best_score, MATCH_FUZZY
    return best, best_score, MATCH_FUZZY


async def _fetch_catalogue(
    location: GrubOpsLocationMap,
) -> tuple[list[Candidate], list[Candidate]]:
    """Every recipe and every modifier GrubOps serves at one location."""
    brands = await provider.serving_brands(
        location_id=location.grubops_location_id,
        partner_id=location.grubops_partner_id,
    )
    if not brands:
        raise GrubOpsError("GrubOps reports no brands serving this location")

    recipes: list[Candidate] = []
    modifiers: list[Candidate] = []
    for brand in brands:
        brand_id = brand["id"]
        for item in await provider.search_menu_items(
            location_id=location.grubops_location_id, brand_ids=brand_id
        ):
            recipes.append(
                Candidate(
                    item_id=str(item.get("id") or item.get("recipeId") or ""),
                    name=_name_of(item),
                    brand_id=brand_id,
                    grubops_type=TYPE_RECIPE,
                )
            )
        for item in await provider.search_modifiers(
            location_id=location.grubops_location_id, brand_ids=brand_id
        ):
            modifiers.append(
                Candidate(
                    item_id=str(item.get("id") or item.get("modifierId") or ""),
                    name=_name_of(item),
                    brand_id=brand_id,
                    grubops_type=TYPE_MODIFIER,
                    parent_recipe_id=str(item.get("recipeId") or "") or None,
                )
            )
    return recipes, modifiers


def _name_of(item: dict) -> str:
    """GrubOps returns either a bare string or a translated bundle."""
    name = item.get("name")
    if isinstance(name, dict):
        return str(name.get("text") or "")
    return str(name or "")


async def _our_menu(
    db: AsyncSession,
) -> tuple[list[Product], dict[uuid.UUID, list[ModifierOption]]]:
    """The products the website sells, and the options on each."""
    products = list(
        (
            await db.execute(
                select(Product).where(
                    Product.is_active.is_(True), sells_on(WEB_CHANNEL)
                )
            )
        )
        .scalars()
        .all()
    )

    rows = (
        await db.execute(
            select(ProductModifier.product_id, ModifierOption)
            .join(Modifier, Modifier.id == ProductModifier.modifier_id)
            .join(ModifierOption, ModifierOption.modifier_id == Modifier.id)
        )
    ).all()

    options: dict[uuid.UUID, list[ModifierOption]] = {}
    for product_id, option in rows:
        options.setdefault(product_id, []).append(option)
    return products, options


async def sync_mappings(db: AsyncSession) -> SyncSummary:
    """
    Refresh the suggested item map from GrubOps' live menu.

    Reads the catalogue once — recipes and modifiers are the same for every
    location on this account, so the first active location is enough and asking
    twice would only be two ways to get a different answer.
    """
    summary = SyncSummary()

    location = (
        await db.execute(
            select(GrubOpsLocationMap)
            .where(GrubOpsLocationMap.is_active.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    if location is None:
        summary.errors.append("No active GrubOps location is mapped to a branch")
        return summary

    recipes, modifiers = await _fetch_catalogue(location)
    products, options_by_product = await _our_menu(db)

    existing = {
        (row.product_id, row.modifier_option_id): row
        for row in (await db.execute(select(GrubOpsItemMap))).scalars().all()
    }

    claimed: set[str] = set()

    for product in products:
        recipe, score, method = best_match(product.name, recipes)
        if recipe is None:
            summary.unmatched_ours.append(product.name)
            continue
        claimed.add(recipe.item_id)

        _upsert(
            db,
            existing.get((product.id, None)),
            summary,
            kind=KIND_PRODUCT,
            product_id=product.id,
            option_id=None,
            candidate=recipe,
            score=score,
            method=method,
        )

        # Options are compared only with the modifiers of the recipe this
        # product matched — see the module note about "Pistachio".
        scoped = [
            m
            for m in modifiers
            if m.parent_recipe_id is None or m.parent_recipe_id == recipe.item_id
        ]
        for option in options_by_product.get(product.id, []):
            modifier, opt_score, opt_method = best_match(option.name, scoped)
            if modifier is None:
                summary.unmatched_ours.append(f"{product.name} → {option.name}")
                continue
            claimed.add(modifier.item_id)
            _upsert(
                db,
                existing.get((None, option.id)),
                summary,
                kind=KIND_OPTION,
                product_id=None,
                option_id=option.id,
                candidate=modifier,
                score=opt_score,
                method=opt_method,
            )

    summary.unmatched_theirs = [
        c.name for c in recipes + modifiers if c.item_id not in claimed
    ]

    await db.flush()
    return summary


def _upsert(
    db: AsyncSession,
    row: GrubOpsItemMap | None,
    summary: SyncSummary,
    *,
    kind: str,
    product_id: uuid.UUID | None,
    option_id: uuid.UUID | None,
    candidate: Candidate,
    score: float,
    method: str,
) -> None:
    """Write a suggestion without ever overruling a person.

    An existing row keeps its ids, its `approved` flag and its `match_method` —
    somebody may have corrected it by hand, and re-running the matcher must not
    quietly undo that. Only the display name is refreshed, because that is the
    one field whose whole job is to be current on the review screen.
    """
    if row is not None:
        row.grubops_name = candidate.name
        summary.refreshed += 1
        return

    is_recipe = candidate.grubops_type == TYPE_RECIPE
    db.add(
        GrubOpsItemMap(
            mm_kind=kind,
            product_id=product_id,
            modifier_option_id=option_id,
            grubops_brand_id=candidate.brand_id,
            grubops_recipe_id=candidate.item_id if is_recipe else None,
            grubops_modifier_id=None if is_recipe else candidate.item_id,
            grubops_child_modifier_id=None,
            grubops_type=candidate.grubops_type,
            grubops_name=candidate.name,
            match_method=method,
            match_score=round(score * 100, 2),
            approved=False,
        )
    )
    summary.created += 1
