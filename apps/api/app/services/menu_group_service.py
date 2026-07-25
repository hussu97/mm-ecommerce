"""
The register's menu tree.

Foodics builds the terminal's menu from groups that nest inside one another,
and an item reaches the register through that tree rather than from the
category taxonomy the website uses. This is that tree: create, nest, reorder,
and — the part everything else depends on — work out which products a cashier
can actually see.

Deactivating a group hides everything beneath it. That is the whole reason the
structure is a tree rather than a list, so the reachability walk has to respect
it at every level, not just the first.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, NotFoundError
from app.models import MenuGroup, MenuGroupProduct, Product

#: A tree deeper than this is a mistake or a cycle the check constraint cannot
#: see. The recursive query stops there rather than running away.
MAX_DEPTH = 20


def _active_tree_cte():
    """
    The groups a cashier can reach, walked from the roots downwards.

    Anchored on active top-level groups and descending only through active
    children, so an inactive group truncates the branch: its own children are
    never visited, however active they are in themselves. Doing this the other
    way round — selecting every active group and checking its parent — would
    let a group whose grandparent is switched off stay on the menu.
    """
    roots = (
        select(MenuGroup.id, MenuGroup.parent_id)
        .add_columns(select(1).scalar_subquery().label("depth"))
        .where(
            MenuGroup.parent_id.is_(None),
            MenuGroup.is_active == True,  # noqa: E712
            MenuGroup.deleted_at.is_(None),
        )
        .cte("visible_groups", recursive=True)
    )
    child = MenuGroup.__table__.alias("child")
    return roots.union_all(
        select(child.c.id, child.c.parent_id, roots.c.depth + 1).where(
            and_(
                child.c.parent_id == roots.c.id,
                child.c.is_active == True,  # noqa: E712
                child.c.deleted_at.is_(None),
                roots.c.depth < MAX_DEPTH,
            )
        )
    )


def visible_product_ids_subquery() -> Select:
    """Product ids reachable through the active part of the tree."""
    tree = _active_tree_cte()
    return select(MenuGroupProduct.product_id).join(
        tree, tree.c.id == MenuGroupProduct.group_id
    )


def pos_visibility_clause():
    """
    Whether a product may be sold on the register.

    The tree gates visibility only once there is a tree. A shop that has not
    built one — a fresh install, or one migrated before any category existed —
    would otherwise get an empty till, which looks like an outage rather than
    a configuration step. With no groups at all the per-product flag governs
    on its own; the moment a group exists, membership is required.
    """
    no_tree_yet = ~(
        select(1).select_from(MenuGroup).where(MenuGroup.deleted_at.is_(None)).exists()
    )
    return and_(
        Product.is_pos_visible == True,  # noqa: E712
        or_(no_tree_yet, Product.id.in_(visible_product_ids_subquery())),
    )


async def list_tree(db: AsyncSession, *, include_inactive: bool = False) -> list[dict]:
    """The whole tree, nested, for the console's menu builder."""
    stmt = select(MenuGroup).where(MenuGroup.deleted_at.is_(None))
    if not include_inactive:
        stmt = stmt.where(MenuGroup.is_active == True)  # noqa: E712
    stmt = stmt.options(selectinload(MenuGroup.members)).order_by(
        MenuGroup.display_order, MenuGroup.name
    )
    groups = list((await db.execute(stmt)).scalars().unique())

    by_parent: dict[uuid.UUID | None, list[MenuGroup]] = {}
    for group in groups:
        by_parent.setdefault(group.parent_id, []).append(group)

    def build(parent_id: uuid.UUID | None, depth: int) -> list[dict]:
        if depth > MAX_DEPTH:
            return []
        return [
            {
                "id": g.id,
                "name": g.name,
                "name_localized": g.name_localized,
                "reference": g.reference,
                "image_url": g.image_url,
                "parent_id": g.parent_id,
                "display_order": g.display_order,
                "is_active": g.is_active,
                "product_ids": [m.product_id for m in g.members],
                "product_count": len(g.members),
                "children": build(g.id, depth + 1),
            }
            for g in by_parent.get(parent_id, [])
        ]

    return build(None, 0)


async def _assert_no_cycle(
    db: AsyncSession, *, group_id: uuid.UUID, parent_id: uuid.UUID | None
) -> None:
    """
    Refuse a move that would put a group underneath itself.

    The database rejects a group parenting itself directly; a longer loop —
    A under B under A — needs the walk. A cycle would make the menu
    unreachable from the roots and hang any naive recursion over it.
    """
    if parent_id is None:
        return
    if parent_id == group_id:
        raise BadRequestError("A group cannot be its own parent")

    seen = {group_id}
    current: uuid.UUID | None = parent_id
    for _ in range(MAX_DEPTH):
        if current is None:
            return
        if current in seen:
            raise BadRequestError("That move would put the group inside itself")
        seen.add(current)
        current = (
            await db.execute(select(MenuGroup.parent_id).where(MenuGroup.id == current))
        ).scalar_one_or_none()
    raise BadRequestError(f"Menu groups cannot nest more than {MAX_DEPTH} deep")


async def get(db: AsyncSession, group_id: uuid.UUID) -> MenuGroup:
    group = (
        await db.execute(
            select(MenuGroup)
            .options(selectinload(MenuGroup.members))
            # The session is configured with expire_on_commit=False, so a group
            # already in the identity map is handed back with the collection it
            # was loaded with — which, when this runs straight after a write, is
            # the membership from *before* it. The console would show the old
            # contents the instant you saved. This forces the row and its
            # collections to be re-read.
            .execution_options(populate_existing=True)
            .where(MenuGroup.id == group_id, MenuGroup.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if group is None:
        raise NotFoundError("Menu group not found")
    return group


async def create(db: AsyncSession, data: dict) -> MenuGroup:
    parent_id = data.get("parent_id")
    if parent_id is not None:
        await get(db, parent_id)  # 404 rather than a foreign-key error
    group = MenuGroup(
        name=data["name"],
        name_localized=data.get("name_localized"),
        translations=data.get("translations") or {},
        reference=data.get("reference"),
        image_url=data.get("image_url"),
        parent_id=parent_id,
        display_order=data.get("display_order", 0),
        is_active=data.get("is_active", True),
    )
    db.add(group)
    await db.flush()
    await _set_products(db, group, data.get("product_ids"))
    await db.commit()
    return await get(db, group.id)


async def update(db: AsyncSession, group_id: uuid.UUID, data: dict) -> MenuGroup:
    group = await get(db, group_id)

    if "parent_id" in data:
        await _assert_no_cycle(db, group_id=group_id, parent_id=data["parent_id"])
        group.parent_id = data["parent_id"]

    for field in (
        "name",
        "name_localized",
        "translations",
        "reference",
        "image_url",
        "display_order",
        "is_active",
    ):
        if field in data and data[field] is not None:
            setattr(group, field, data[field])

    if "product_ids" in data:
        await _set_products(db, group, data["product_ids"])

    await db.commit()
    return await get(db, group_id)


async def _set_products(
    db: AsyncSession, group: MenuGroup, product_ids: list[uuid.UUID] | None
) -> None:
    if product_ids is None:
        return

    found = set(
        (await db.execute(select(Product.id).where(Product.id.in_(product_ids))))
        .scalars()
        .all()
    )
    missing = [str(p) for p in product_ids if p not in found]
    if missing:
        raise BadRequestError(f"No such product: {', '.join(missing)}")

    # Query the link rows rather than reading `group.members`. On a group that
    # was only just added and flushed the relationship is unloaded, so touching
    # it emits a lazy load — which raises under asyncio rather than returning
    # the empty list it looks like it should.
    existing = {
        link.product_id: link
        for link in (
            await db.execute(
                select(MenuGroupProduct).where(MenuGroupProduct.group_id == group.id)
            )
        )
        .scalars()
        .all()
    }
    for product_id in existing.keys() - set(product_ids):
        await db.delete(existing[product_id])
    for order, product_id in enumerate(product_ids):
        if product_id in existing:
            existing[product_id].display_order = order
        else:
            db.add(
                MenuGroupProduct(
                    group_id=group.id, product_id=product_id, display_order=order
                )
            )
    await db.flush()


async def delete(db: AsyncSession, group_id: uuid.UUID) -> None:
    """
    Soft-delete a group and everything under it.

    Leaving the children behind would orphan them: their parent is gone, so
    they are unreachable from any root and silently vanish from the register
    while still showing in the console.
    """
    group = await get(db, group_id)
    doomed = [group]
    frontier = [group_id]
    for _ in range(MAX_DEPTH):
        if not frontier:
            break
        children = list(
            (
                await db.execute(
                    select(MenuGroup).where(
                        MenuGroup.parent_id.in_(frontier),
                        MenuGroup.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        doomed.extend(children)
        frontier = [c.id for c in children]

    from app.models.base import utcnow

    stamp = utcnow()
    for node in doomed:
        node.deleted_at = stamp
        node.is_active = False
    await db.commit()
