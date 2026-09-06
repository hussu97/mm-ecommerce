"""
The menu trees.

Foodics builds a terminal's menu from groups that nest inside one another, and
an item reaches the register through that tree rather than from the category
taxonomy the website uses. This is that structure — create, nest, reorder, and,
the part everything else depends on, work out which products a cashier can see.

There is now more than one tree:

  * one **branch** root per shop (``root_kind='branch'``, ``branch_id`` set) is
    the menu that shop's terminals render, so Sharjah and Barsha lay their
    counters out independently;
  * one **integrator** root (``root_kind='integrator'``) is the menu MM pushes
    to the marketplaces — membership in it is what the retired
    ``Product.sync_to_aggregators`` flag used to mean, and the catalog sync reads
    it in place of that flag.

Every node carries a denormalised ``root_id`` pointing at its own root, so
"which tree is this in" and "everything in Sharjah's menu" are indexed column
reads rather than walks to the top.

The rules that matter and are easy to get subtly wrong:

  * switching a group off must hide everything beneath it, at any depth — not
    just its direct children;
  * a cycle must be refused rather than hang the walk;
  * POS visibility is scoped to one branch's tree; the integrator tree must
    never leak onto a register;
  * the integrator tree is exactly two deep (category → item), because that is
    the only shape Foodics' Grubtech menu has a place for.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Select, and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, NotFoundError
from app.models import MenuGroup, MenuGroupProduct, Product
from app.models.branch import Branch
from app.models.menu import (
    INTEGRATOR_ROOT_REFERENCE,
    ROOT_KIND_BRANCH,
    ROOT_KIND_INTEGRATOR,
    ROOT_KINDS,
)

#: A tree deeper than this is a mistake or a cycle the check constraint cannot
#: see. The recursive query stops there rather than running away.
MAX_DEPTH = 20


def _active_tree_cte(*, branch_id: uuid.UUID | None = None):
    """
    The **branch** groups a cashier can reach, walked from the roots downwards.

    Anchored on active branch roots — the integrator tree is deliberately
    excluded so a marketplace-only item never appears on a till — and descending
    only through active children, so an inactive group truncates the branch: its
    own children are never visited, however active they are in themselves. Doing
    this the other way round — selecting every active group and checking its
    parent — would let a group whose grandparent is switched off stay on the
    menu.

    With ``branch_id`` the walk starts from just that shop's root; without it,
    from every branch root at once (the admin's estate-wide POS catalogue).
    """
    root_conditions = [
        MenuGroup.parent_id.is_(None),
        MenuGroup.is_active == True,  # noqa: E712
        MenuGroup.deleted_at.is_(None),
        MenuGroup.root_kind == ROOT_KIND_BRANCH,
    ]
    if branch_id is not None:
        root_conditions.append(MenuGroup.branch_id == branch_id)
    roots = (
        select(MenuGroup.id, MenuGroup.parent_id)
        .add_columns(select(1).scalar_subquery().label("depth"))
        .where(*root_conditions)
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


def visible_product_ids_subquery(*, branch_id: uuid.UUID | None = None) -> Select:
    """Product ids reachable through the active part of a branch's tree."""
    tree = _active_tree_cte(branch_id=branch_id)
    return select(MenuGroupProduct.product_id).join(
        tree, tree.c.id == MenuGroupProduct.group_id
    )


def pos_visibility_clause(branch_id: uuid.UUID | None = None):
    """
    Whether a product may be sold on the register.

    Visibility is now *only* the tree: a product is on a terminal because a group
    in that branch's active tree holds it. There is no per-product "POS" flag any
    more — the branch root replaced it — and no whole-catalogue fallback: a
    branch with no menu built shows nothing, which the migration prevents by
    seeding a root for every shop, and which the console fixes for a new shop by
    cloning an existing tree. With ``branch_id`` the answer is scoped to that
    shop; without it, to the union of every branch's tree (the admin's POS list).
    """
    return Product.id.in_(visible_product_ids_subquery(branch_id=branch_id))


# ── Root resolution ───────────────────────────────────────────────────────────


async def branch_root(db: AsyncSession, branch_id: uuid.UUID) -> MenuGroup | None:
    """The live root of one shop's menu, or None if it has none yet."""
    return (
        await db.execute(
            select(MenuGroup).where(
                MenuGroup.parent_id.is_(None),
                MenuGroup.deleted_at.is_(None),
                MenuGroup.root_kind == ROOT_KIND_BRANCH,
                MenuGroup.branch_id == branch_id,
            )
        )
    ).scalar_one_or_none()


async def integrator_root(db: AsyncSession) -> MenuGroup | None:
    """The one live integrator root the catalog sync reads, or None."""
    return (
        await db.execute(
            select(MenuGroup).where(
                MenuGroup.parent_id.is_(None),
                MenuGroup.deleted_at.is_(None),
                MenuGroup.root_kind == ROOT_KIND_INTEGRATOR,
            )
        )
    ).scalar_one_or_none()


async def integrator_l1_group_for_product(
    db: AsyncSession, product_id: uuid.UUID
) -> MenuGroup | None:
    """
    The category group holding this product in the integrator menu.

    A direct child of the integrator root (an L1 category), so its ``reference``
    is the Foodics Grubtech subgroup id and its ``name`` the marketplace
    category. This is what the Foodics create resolves in place of the old
    hardcoded category→subgroup dict. If a product sits in more than one category
    the lowest-ordered one wins, deterministically.
    """
    root = await integrator_root(db)
    if root is None:
        return None
    return (
        await db.execute(
            select(MenuGroup)
            .join(MenuGroupProduct, MenuGroupProduct.group_id == MenuGroup.id)
            .where(
                MenuGroup.parent_id == root.id,
                MenuGroup.deleted_at.is_(None),
                MenuGroup.is_active == True,  # noqa: E712
                MenuGroupProduct.product_id == product_id,
            )
            .order_by(MenuGroup.display_order, MenuGroup.name)
            .limit(1)
        )
    ).scalar_one_or_none()


async def list_tree(
    db: AsyncSession,
    *,
    include_inactive: bool = False,
    branch_id: uuid.UUID | None = None,
    root_kind: str | None = None,
) -> list[dict]:
    """The tree(s), nested, for the console's builder or a terminal.

    ``branch_id`` narrows to one shop's menu (what a terminal fetches);
    ``root_kind`` narrows to the branch trees or the integrator tree. With
    neither, every tree comes back.
    """
    stmt = select(MenuGroup).where(MenuGroup.deleted_at.is_(None))
    if not include_inactive:
        stmt = stmt.where(MenuGroup.is_active == True)  # noqa: E712
    if branch_id is not None:
        stmt = stmt.where(MenuGroup.branch_id == branch_id)
    if root_kind is not None:
        stmt = stmt.where(MenuGroup.root_kind == root_kind)
    stmt = stmt.options(selectinload(MenuGroup.members)).order_by(
        MenuGroup.display_order, MenuGroup.name
    )
    groups = list((await db.execute(stmt)).scalars().unique())

    # A scoped query can return a child whose ancestor was filtered out (a
    # different branch, say). Anchor the build on the roots actually present in
    # the result so orphans are not silently dropped or attached to nothing.
    present = {g.id for g in groups}
    by_parent: dict[uuid.UUID | None, list[MenuGroup]] = {}
    for group in groups:
        anchor = group.parent_id if group.parent_id in present else None
        by_parent.setdefault(anchor, []).append(group)

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
                "root_kind": g.root_kind,
                "branch_id": g.branch_id,
                "root_id": g.root_id,
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


async def _resolve_placement(
    db: AsyncSession, data: dict
) -> tuple[str, uuid.UUID | None, MenuGroup | None]:
    """
    Work out a new group's root_kind / branch_id from where it is being put.

    A **child** inherits both from its parent, and the integrator tree only
    admits a child directly under its root (its two levels are category → item).
    A **root** takes them from the request and must be internally consistent —
    a branch root names a branch, the integrator root does not and is a
    singleton. Returns ``(root_kind, branch_id, parent_or_None)``.
    """
    parent_id = data.get("parent_id")
    if parent_id is not None:
        parent = await get(db, parent_id)  # 404 rather than a foreign-key error
        if parent.root_kind == ROOT_KIND_INTEGRATOR and parent.parent_id is not None:
            raise BadRequestError(
                "The integrator menu is two levels deep: a category, then its "
                "items. You cannot nest a group under a category."
            )
        return parent.root_kind, parent.branch_id, parent

    root_kind = data.get("root_kind", ROOT_KIND_BRANCH)
    if root_kind not in ROOT_KINDS:
        raise BadRequestError(f"Unknown root kind: {root_kind}")
    branch_id = data.get("branch_id")

    if root_kind == ROOT_KIND_BRANCH:
        if branch_id is None:
            raise BadRequestError("A branch menu must belong to a branch")
        if (
            await db.execute(select(Branch.id).where(Branch.id == branch_id))
        ).scalar_one_or_none() is None:
            raise NotFoundError("Branch not found")
        if await branch_root(db, branch_id) is not None:
            raise BadRequestError("That branch already has a menu")
    else:  # integrator
        if branch_id is not None:
            raise BadRequestError("The integrator menu is not tied to a branch")
        if await integrator_root(db) is not None:
            raise BadRequestError("There is already an integrator menu")

    return root_kind, branch_id, None


async def create(db: AsyncSession, data: dict) -> MenuGroup:
    root_kind, branch_id, parent = await _resolve_placement(db, data)

    group = MenuGroup(
        name=data["name"],
        name_localized=data.get("name_localized"),
        translations=data.get("translations") or {},
        reference=data.get("reference")
        or (
            INTEGRATOR_ROOT_REFERENCE
            if root_kind == ROOT_KIND_INTEGRATOR and parent is None
            else None
        ),
        image_url=data.get("image_url"),
        root_kind=root_kind,
        branch_id=branch_id,
        parent_id=data.get("parent_id"),
        display_order=data.get("display_order", 0),
        is_active=data.get("is_active", True),
    )
    db.add(group)
    await db.flush()
    # A root points at itself; a child shares its parent's root.
    group.root_id = parent.root_id if parent is not None else group.id
    await _set_products(db, group, data.get("product_ids"))
    # Flush, not commit. The request-scoped `get_db` dependency owns the
    # commit; a service that commits mid-request turns everything the router
    # did before it into a fait accompli that a later failure in the same
    # request can no longer roll back. The re-read below runs inside the same
    # transaction, so it sees the flushed rows just the same.
    await db.flush()
    return await get(db, group.id)


async def update(db: AsyncSession, group_id: uuid.UUID, data: dict) -> MenuGroup:
    group = await get(db, group_id)

    if "parent_id" in data:
        new_parent_id = data["parent_id"]
        if new_parent_id is None:
            raise BadRequestError(
                "A group cannot be turned into a root; create the root you want "
                "and move the group under it."
            )
        await _assert_no_cycle(db, group_id=group_id, parent_id=new_parent_id)
        parent = await get(db, new_parent_id)
        if parent.root_id != group.root_id:
            raise BadRequestError("A group cannot move into a different menu")
        if group.root_kind == ROOT_KIND_INTEGRATOR and parent.parent_id is not None:
            raise BadRequestError(
                "The integrator menu is two levels deep: a category, then its "
                "items. You cannot nest a group under a category."
            )
        group.parent_id = new_parent_id

    # root_kind / branch_id / root_id are fixed when a root is created; they are
    # not editable here — a menu does not change which shop it belongs to.
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

    # Flush, not commit — see `create`. The request commits once, at the end.
    await db.flush()
    return await get(db, group_id)


async def clone_tree(
    db: AsyncSession,
    source_root_id: uuid.UUID,
    *,
    into_branch_id: uuid.UUID,
    name: str | None = None,
) -> MenuGroup:
    """
    Copy a whole menu into a new branch root — how a new shop launches.

    Barsha opens with Sharjah's menu and diverges from there rather than being
    rebuilt by hand. Structure, ordering, names, images and product membership
    are all copied; the copy is an independent tree the operator then edits.
    """
    source = await get(db, source_root_id)
    if await branch_root(db, into_branch_id) is not None:
        raise BadRequestError("That branch already has a menu")
    if (
        await db.execute(select(Branch.id).where(Branch.id == into_branch_id))
    ).scalar_one_or_none() is None:
        raise NotFoundError("Branch not found")

    # Every node in the source tree, in one read thanks to root_id.
    nodes = list(
        (
            await db.execute(
                select(MenuGroup)
                .where(
                    MenuGroup.root_id == source_root_id,
                    MenuGroup.deleted_at.is_(None),
                )
                .options(selectinload(MenuGroup.members))
                .order_by(MenuGroup.display_order, MenuGroup.name)
            )
        )
        .scalars()
        .unique()
    )

    new_root = MenuGroup(
        name=name or source.name,
        name_localized=source.name_localized,
        translations=source.translations or {},
        image_url=source.image_url,
        root_kind=ROOT_KIND_BRANCH,
        branch_id=into_branch_id,
        parent_id=None,
        display_order=source.display_order,
        is_active=source.is_active,
    )
    db.add(new_root)
    await db.flush()
    new_root.root_id = new_root.id

    id_map: dict[uuid.UUID, uuid.UUID] = {source_root_id: new_root.id}
    # Breadth-first by depth so a parent's clone always exists before its child's.
    remaining = [n for n in nodes if n.id != source_root_id]
    for _ in range(MAX_DEPTH):
        if not remaining:
            break
        ready = [n for n in remaining if n.parent_id in id_map]
        if not ready:
            break
        for node in ready:
            clone = MenuGroup(
                name=node.name,
                name_localized=node.name_localized,
                translations=node.translations or {},
                image_url=node.image_url,
                root_kind=ROOT_KIND_BRANCH,
                branch_id=into_branch_id,
                parent_id=id_map[node.parent_id],
                display_order=node.display_order,
                is_active=node.is_active,
            )
            db.add(clone)
            await db.flush()
            clone.root_id = new_root.id
            for member in node.members:
                db.add(
                    MenuGroupProduct(
                        group_id=clone.id,
                        product_id=member.product_id,
                        display_order=member.display_order,
                    )
                )
            id_map[node.id] = clone.id
        remaining = [n for n in remaining if n.id not in id_map]

    await db.flush()
    return await get(db, new_root.id)


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
    # Flush, not commit — see `create`. The request commits once, at the end.
    await db.flush()
