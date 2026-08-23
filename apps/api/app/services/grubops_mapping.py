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
from app.models.branch import Branch
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
    #: Set for a modifier: the normalised name of the group it sits in, and the
    #: recipe that group belongs to. Both are needed to tell it from its
    #: namesakes — see `_parent_of`.
    parent_group: str | None = None
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


def match_branch(name: str, branches: list) -> object | None:
    """
    The branch a GrubOps location is.

    Not `best_match`: that one compares two names for a *menu item*, where a
    length difference usually means two different cakes. Here it usually means
    the same shop written two ways — GrubOps says "Sharjah", the branch is
    called "Sharjah Kitchen", and a plain similarity ratio scores that pair
    below the item threshold and matches nothing at all.

    So a location matches when its name **is** the branch's name or city, or
    when every word of it appears in one of them. "Sharjah" finds "Sharjah
    Kitchen"; "Barsha Heights" finds a branch in Barsha Heights; neither finds
    the other, which is the property that matters with four shops in two
    emirates.
    """
    target = normalise(name)
    if not target:
        return None

    words = set(target.split())
    best = None
    best_score = 0.0

    for branch in branches:
        for field_value in (branch.name, branch.city):
            candidate = normalise(field_value or "")
            if not candidate:
                continue
            if candidate == target:
                return branch
            if words and words.issubset(set(candidate.split())):
                # Every word of theirs is in ours: "Sharjah" ⊂ "Sharjah Kitchen".
                score = 0.95
            else:
                score = similarity(target, candidate)
            if score > best_score:
                best, best_score = branch, score

    return best if best_score >= MATCH_THRESHOLD else None


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
        for item in await provider.list_items(
            location_id=location.grubops_location_id,
            brand_id=brand_id,
            item_type=TYPE_RECIPE,
            partner_id=location.grubops_partner_id,
        ):
            recipes.append(
                Candidate(
                    item_id=str(item.get("id") or ""),
                    name=_name_of(item),
                    brand_id=brand_id,
                    grubops_type=TYPE_RECIPE,
                )
            )
        for item in await provider.list_items(
            location_id=location.grubops_location_id,
            brand_id=brand_id,
            item_type=TYPE_MODIFIER,
            partner_id=location.grubops_partner_id,
        ):
            group, recipe_id = _parent_of(item)
            modifiers.append(
                Candidate(
                    item_id=str(item.get("id") or ""),
                    name=_name_of(item),
                    brand_id=brand_id,
                    grubops_type=TYPE_MODIFIER,
                    parent_group=group,
                    parent_recipe_id=recipe_id,
                )
            )
    return recipes, modifiers


def _parent_of(item: dict) -> tuple[str | None, str | None]:
    """The group a modifier sits in, and the recipe that group belongs to.

    Both, because neither identifies it alone. GrubOps duplicates a group per
    recipe — "Your Choice of Quantity" exists seventeen times on this account,
    once for each product that offers it — and the modifiers under those
    seventeen copies share three names between them. So "3 Pieces" in the group
    called "Your Choice of Quantity" describes seventeen different modifiers,
    and only the recipe above them says which.

    Our own catalogue is shaped the same way: seventeen modifier groups of that
    name, each belonging to exactly one product. That symmetry is what makes
    the pairing one-to-one.
    """
    for parent in item.get("parentAssociations") or []:
        if parent.get("type") != "MODIFIER_GROUP":
            continue
        group = normalise(_name_of(parent))
        for grandparent in parent.get("parentAssociations") or []:
            if grandparent.get("type") == "RECIPE":
                return group, str(grandparent.get("id") or "") or None
        return group, None
    return None, None


def _name_of(item: dict) -> str:
    """The English name, whichever of the three shapes it arrives in.

    `serving-brands` answers `{"name": {"text": ...}}`; the item list answers
    `{"name": {"translations": {"en-US": ..., "ar-ae": ...}}}`; and a bare
    string is possible too. English rather than the Arabic beside it, because
    the catalogue this is matched against is written in English.
    """
    name = item.get("name")
    if isinstance(name, dict):
        translations = name.get("translations")
        if isinstance(translations, dict):
            for key in ("en-US", "en-us", "en"):
                if translations.get(key):
                    return str(translations[key])
            # Any language beats nothing; an unmatched row is visible either way.
            return str(next(iter(translations.values()), "") or "")
        return str(name.get("text") or "")
    return str(name or "")


async def _our_menu(
    db: AsyncSession,
) -> tuple[list[Product], dict[uuid.UUID, list[tuple[ModifierOption, str]]]]:
    """The products the website sells, and each one's options with their group.

    Keyed by product because that is what tells two identically-named options
    apart: this catalogue has seventeen modifier groups called "Your Choice of
    Quantity", each belonging to exactly one product, and "3 Pieces" appears
    under every one of them. GrubOps duplicates the same way, so (product,
    group, option) is a unique key on both sides and the pairing is
    one-to-one.
    """
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
            select(ProductModifier.product_id, ModifierOption, Modifier.name)
            .join(Modifier, Modifier.id == ProductModifier.modifier_id)
            .join(ModifierOption, ModifierOption.modifier_id == Modifier.id)
        )
    ).all()

    options: dict[uuid.UUID, list[tuple[ModifierOption, str]]] = {}
    for product_id, option, group_name in rows:
        options.setdefault(product_id, []).append((option, group_name or ""))
    return products, options


async def sync_locations(db: AsyncSession) -> tuple[int, list[str]]:
    """
    Make sure every GrubOps location we can recognise has a branch row.

    Matched on name, like the items — GrubOps calls them "Sharjah" and "Barsha
    Heights" and so do we, near enough. Hardcoding branch references was the
    first attempt and it seeded nothing on a database whose Sharjah kitchen is
    called `SHJ` rather than `K001`; discovering them works whatever the local
    references are.

    **Every row is created inactive.** Whether a branch should sync is a
    question about whether its staff are marking things out on the terminal,
    and pushing a confident "everything is available" from a shop that is not
    doing that would overwrite what its counter maintains in GrubOps by hand.
    Somebody turns each one on in the console.

    Existing rows are never touched, so this cannot flip a branch somebody has
    already decided about.
    """
    theirs = await provider.list_locations()
    if not theirs:
        return 0, ["GrubOps reports no locations on this account"]

    branches = list((await db.execute(select(Branch))).scalars().all())
    existing = {
        row.branch_id
        for row in (await db.execute(select(GrubOpsLocationMap))).scalars().all()
    }

    created = 0
    unmatched: list[str] = []
    for location in theirs:
        name = _name_of(location)
        branch = match_branch(name, branches)
        if branch is None:
            unmatched.append(f"GrubOps location {name!r} matched no branch")
            continue
        if branch.id in existing:
            continue
        db.add(
            GrubOpsLocationMap(
                branch_id=branch.id,
                grubops_location_id=str(location.get("id") or ""),
                grubops_partner_id=str(
                    location.get("partnerId") or provider.config.partner_id
                ),
                is_active=False,
            )
        )
        existing.add(branch.id)
        created += 1

    await db.flush()
    return created, unmatched


async def sync_mappings(db: AsyncSession) -> SyncSummary:
    """
    Refresh the suggested item map from GrubOps' live menu.

    Reads the catalogue once — recipes and modifiers are the same for every
    location on this account, so the first active location is enough and asking
    twice would only be two ways to get a different answer.
    """
    summary = SyncSummary()

    # Branches first: a location discovered now is one the item map can be
    # read against in the same press of the button.
    try:
        created, unmatched = await sync_locations(db)
        summary.created += created
        summary.errors.extend(unmatched)
    except GrubOpsError as exc:
        summary.errors.append(f"Could not read GrubOps locations: {exc}")

    # Any mapped location will do, active or not: the catalogue is the same for
    # every location on the account, and the item map has to be reviewable
    # before any branch is switched on.
    location = (
        await db.execute(select(GrubOpsLocationMap).limit(1))
    ).scalar_one_or_none()
    if location is None:
        summary.errors.append("No GrubOps location could be matched to a branch")
        return summary

    recipes, modifiers = await _fetch_catalogue(location)
    products, options_by_product = await _our_menu(db)

    existing = {
        (row.product_id, row.modifier_option_id): row
        for row in (await db.execute(select(GrubOpsItemMap))).scalars().all()
    }

    claimed: set[str] = set()
    seen_options: set[uuid.UUID] = set()

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

        # Now this product's options, scoped to the modifiers hanging off the
        # recipe it just matched, in the group of the same name.
        #
        # Both halves of that scope are load-bearing. Without the recipe,
        # "3 Pieces" describes seventeen different modifiers and the matcher
        # picks whichever it sees first; without the group, a quantity option
        # could pair with a filling. Together they leave exactly one candidate
        # per option, which is why every match below comes out exact.
        for option, group_name in options_by_product.get(product.id, []):
            if option.id in seen_options:
                # Defensive. Every modifier group here belongs to exactly one
                # product today, so an option is reached once — but the unique
                # index on `modifier_option_id` would refuse the second row
                # rather than tolerate it, and a catalogue edit is all it would
                # take to make that true.
                continue
            seen_options.add(option.id)

            group = normalise(group_name)
            scoped = [
                m
                for m in modifiers
                if m.parent_recipe_id == recipe.item_id and m.parent_group == group
            ]
            modifier, opt_score, opt_method = best_match(option.name, scoped)
            if modifier is None:
                summary.unmatched_ours.append(
                    f"{product.name} → {group_name}: {option.name}"
                )
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
            # A modifier keeps its recipe as well as its own id. GrubOps takes
            # the whole identity on a write and answers `{"recipeId": ["must
            # not be null"]}` without it — a modifier is only meaningful under
            # the recipe it belongs to, which is the same reason it took both
            # to match in the first place.
            grubops_recipe_id=(
                candidate.item_id if is_recipe else candidate.parent_recipe_id
            ),
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
