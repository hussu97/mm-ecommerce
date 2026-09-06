"""Menu root groups: per-branch POS trees + one integrator tree; retire the flags.

This is the schema half of the "rethink menu groups" change. It turns the single
global ``menu_groups`` tree into:

  * one **branch** root per shop (``root_kind='branch'``, ``branch_id`` set) — a
    terminal renders its own branch's tree, so shops lay their counters out
    independently;
  * one **integrator** root (``root_kind='integrator'``) — the menu MM pushes to
    the marketplaces. Membership of this tree replaces the retired
    ``Product.sync_to_aggregators`` flag.

Backfill, forward-only and environment-agnostic (branches are resolved by query,
never hardcoded — dev's references differ from prod's):

  1. Wrap the existing global tree under the **first active branch**'s new root
    and stamp the whole tree as that branch's; a fresh install with no tree gets
    a root built from its POS products' categories instead.
  2. **Clone** that tree into every other active branch (Barsha opens as a copy
    of Sharjah, then diverges). Clones drop ``reference`` to avoid colliding with
    the unique index.
  3. Build the **integrator** root: one L1 category group per distinct category
    among ``sync_to_aggregators`` products, its ``reference`` set to the Foodics
    Grubtech subgroup id (the values the sync used to hardcode), with those
    products as members.
  4. Retire the flags: strip ``'pos'`` from ``sales_channels`` (POS visibility is
    the tree now), and drop ``sync_to_aggregators`` / ``sync_channels`` from both
    products and categories.

Downgrade restores the columns and constraints so Alembic can step back, but the
tree transform itself is one-way (the extra root and integrator nodes remain as
plain groups).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "191_menu_root_groups"
down_revision: Union[str, None] = "190_report_template_revisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The Grubtech category subgroups, keyed by MM category name — the mapping the
# sync used to hold in `foodics_provider.FOODICS_GRUBTECH_SUBGROUPS`. Inlined so
# the migration does not import app code, and so it stays fixed even if the
# constant is later edited. Each becomes an integrator L1 group's `reference`.
_GRUBTECH_SUBGROUPS: dict[str, str] = {
    "Cookie Melt": "a05d8155-d43b-469d-953b-aeaefed7b326",
    "Cakes": "a05d8176-5a8b-480f-862f-bfe40b4bc8d3",
    "Extras": "a05d8188-9809-4e1d-8314-cf647f867de4",
    "Cookies": "a063495f-292d-40af-9fcf-faca7a5d0c88",
    "Mix Boxes": "a0634a0f-96c9-4050-9e7e-65680d9c807b",
    "Brownies": "a0634a39-10f9-4c93-bdb8-00fb02767be4",
    "Eggless": "a0634a85-aa22-4fa8-98ca-377a202521e1",
    "Desserts": "a0634b49-3245-4749-8efd-73109b696769",
    "New In": "a1887db1-b3cc-48b6-a395-5de8ebf5b2b7",
}


def _subgroups_values_sql() -> str:
    """The subgroup map as a SQL ``VALUES`` list to LEFT JOIN a category name on."""
    rows = ", ".join(
        f"({_q(name)}, {_q(ref)})" for name, ref in _GRUBTECH_SUBGROUPS.items()
    )
    return rows


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    # ── 1. Columns (branch check + unique roots added after the backfill) ──────
    op.add_column(
        "menu_groups",
        sa.Column(
            "root_kind", sa.String(length=20), nullable=False, server_default="branch"
        ),
    )
    op.add_column(
        "menu_groups", sa.Column("branch_id", UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "menu_groups", sa.Column("root_id", UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_menu_groups_branch",
        "menu_groups",
        "branches",
        ["branch_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_menu_groups_root",
        "menu_groups",
        "menu_groups",
        ["root_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_menu_groups_branch_id", "menu_groups", ["branch_id"])
    op.create_index("ix_menu_groups_root_id", "menu_groups", ["root_id"])
    op.create_check_constraint(
        "ck_menu_groups_root_kind",
        "menu_groups",
        "root_kind IN ('branch', 'integrator')",
    )

    # ── 2. Branch roots: wrap the existing tree, clone it to the other shops ───
    op.execute(
        """
        DO $$
        DECLARE
            v_primary_branch uuid;
            v_root_id uuid;
            v_had_tree boolean;
            v_other uuid;
        BEGIN
            SELECT id INTO v_primary_branch FROM branches
                WHERE is_active = true
                ORDER BY display_order, created_at, id
                LIMIT 1;
            IF v_primary_branch IS NULL THEN
                RETURN;  -- no branches yet (a bare dev db); nothing to seed
            END IF;

            SELECT EXISTS(SELECT 1 FROM menu_groups WHERE deleted_at IS NULL)
                INTO v_had_tree;

            v_root_id := gen_random_uuid();
            INSERT INTO menu_groups
                (id, name, translations, root_kind, branch_id, root_id, parent_id,
                 display_order, is_active, created_at, updated_at)
            VALUES
                (v_root_id, 'Menu', '{}'::jsonb, 'branch', v_primary_branch, v_root_id,
                 NULL, 0, true, now(), now());

            IF v_had_tree THEN
                -- Re-parent the old top-level groups under the new root, then
                -- stamp the whole existing tree as this branch's.
                UPDATE menu_groups SET parent_id = v_root_id
                    WHERE parent_id IS NULL AND deleted_at IS NULL AND id <> v_root_id;
                UPDATE menu_groups
                    SET root_kind = 'branch',
                        branch_id = v_primary_branch,
                        root_id = v_root_id
                    WHERE id <> v_root_id;
            ELSE
                -- No tree yet: one category group per POS product, then members.
                INSERT INTO menu_groups
                    (id, name, name_localized, translations, root_kind, branch_id,
                     root_id, parent_id, display_order, is_active, created_at, updated_at)
                SELECT gen_random_uuid(), c.name,
                       c.translations->'ar'->>'name', c.translations,
                       'branch', v_primary_branch, v_root_id, v_root_id,
                       c.display_order, true, now(), now()
                FROM categories c
                WHERE EXISTS (
                    SELECT 1 FROM products p
                    WHERE p.category_id = c.id AND p.sales_channels @> ARRAY['pos']::varchar[]
                );
                INSERT INTO menu_group_products (id, group_id, product_id, display_order)
                SELECT gen_random_uuid(), g.id, p.id, p.display_order
                FROM products p
                JOIN categories c ON c.id = p.category_id
                JOIN menu_groups g
                    ON g.parent_id = v_root_id AND g.name = c.name
                    AND g.root_kind = 'branch' AND g.branch_id = v_primary_branch
                WHERE p.sales_channels @> ARRAY['pos']::varchar[];
            END IF;

            -- Clone the primary tree into every other active branch.
            CREATE TEMP TABLE _clone_map (old_id uuid, new_id uuid) ON COMMIT DROP;
            FOR v_other IN
                SELECT id FROM branches
                WHERE is_active = true AND id <> v_primary_branch
            LOOP
                DELETE FROM _clone_map;
                INSERT INTO _clone_map (old_id, new_id)
                    SELECT id, gen_random_uuid()
                    FROM menu_groups
                    WHERE root_id = v_root_id AND deleted_at IS NULL;

                INSERT INTO menu_groups
                    (id, name, name_localized, translations, image_url, reference,
                     root_kind, branch_id, root_id, parent_id, display_order,
                     is_active, created_at, updated_at)
                SELECT m.new_id, g.name, g.name_localized, g.translations, g.image_url,
                       NULL,  -- reference is unique; a clone cannot copy it
                       'branch', v_other,
                       (SELECT new_id FROM _clone_map WHERE old_id = v_root_id),
                       CASE WHEN g.parent_id IS NULL THEN NULL
                            ELSE (SELECT new_id FROM _clone_map WHERE old_id = g.parent_id)
                       END,
                       g.display_order, g.is_active, now(), now()
                FROM menu_groups g
                JOIN _clone_map m ON m.old_id = g.id
                WHERE g.root_id = v_root_id AND g.deleted_at IS NULL;

                INSERT INTO menu_group_products (id, group_id, product_id, display_order)
                SELECT gen_random_uuid(), m.new_id, mp.product_id, mp.display_order
                FROM menu_group_products mp
                JOIN _clone_map m ON m.old_id = mp.group_id;
            END LOOP;
        END $$;
        """
    )

    # ── 3. Integrator root: L1 category groups (with subgroup refs) + members ──
    op.execute(
        f"""
        DO $$
        DECLARE
            v_int_root uuid;
            v_cat RECORD;
            v_lg_id uuid;
            v_ref text;
        BEGIN
            v_int_root := gen_random_uuid();
            INSERT INTO menu_groups
                (id, name, reference, translations, root_kind, branch_id, root_id,
                 parent_id, display_order, is_active, created_at, updated_at)
            VALUES
                (v_int_root, 'Integrator Menu', 'integrator-root', '{{}}'::jsonb,
                 'integrator', NULL, v_int_root, NULL, 0, true, now(), now());

            FOR v_cat IN
                SELECT DISTINCT c.id, c.name, c.translations, c.display_order
                FROM categories c
                JOIN products p ON p.category_id = c.id
                WHERE p.sync_to_aggregators = true
                ORDER BY c.display_order, c.name
            LOOP
                SELECT s.ref INTO v_ref
                FROM (VALUES {_subgroups_values_sql()}) AS s(nm, ref)
                WHERE s.nm = v_cat.name;

                v_lg_id := gen_random_uuid();
                INSERT INTO menu_groups
                    (id, name, name_localized, translations, reference, root_kind,
                     branch_id, root_id, parent_id, display_order, is_active,
                     created_at, updated_at)
                VALUES
                    (v_lg_id, v_cat.name, v_cat.translations->'ar'->>'name',
                     v_cat.translations, v_ref, 'integrator', NULL, v_int_root,
                     v_int_root, v_cat.display_order, true, now(), now());

                INSERT INTO menu_group_products (id, group_id, product_id, display_order)
                SELECT gen_random_uuid(), v_lg_id, p.id,
                       row_number() OVER (ORDER BY p.display_order, p.name)
                FROM products p
                WHERE p.category_id = v_cat.id AND p.sync_to_aggregators = true;
            END LOOP;

            -- Sync-flagged products with no category land in one bucket.
            IF EXISTS (
                SELECT 1 FROM products
                WHERE sync_to_aggregators = true AND category_id IS NULL
            ) THEN
                v_lg_id := gen_random_uuid();
                INSERT INTO menu_groups
                    (id, name, translations, root_kind, branch_id, root_id,
                     parent_id, display_order, is_active, created_at, updated_at)
                VALUES
                    (v_lg_id, 'Uncategorised', '{{}}'::jsonb, 'integrator', NULL,
                     v_int_root, v_int_root, 9999, true, now(), now());
                INSERT INTO menu_group_products (id, group_id, product_id, display_order)
                SELECT gen_random_uuid(), v_lg_id, p.id,
                       row_number() OVER (ORDER BY p.display_order, p.name)
                FROM products p
                WHERE p.sync_to_aggregators = true AND p.category_id IS NULL;
            END IF;
        END $$;
        """
    )

    # ── 4. Now-safe root invariants ────────────────────────────────────────────
    op.create_check_constraint(
        "ck_menu_groups_branch_root_has_branch",
        "menu_groups",
        "parent_id IS NOT NULL OR (root_kind = 'branch') = (branch_id IS NOT NULL)",
    )
    op.create_index(
        "uq_menu_groups_branch_root",
        "menu_groups",
        ["branch_id"],
        unique=True,
        postgresql_where=sa.text(
            "parent_id IS NULL AND deleted_at IS NULL AND root_kind = 'branch'"
        ),
    )
    op.create_index(
        "uq_menu_groups_integrator_root",
        "menu_groups",
        ["root_kind"],
        unique=True,
        postgresql_where=sa.text(
            "parent_id IS NULL AND deleted_at IS NULL AND root_kind = 'integrator'"
        ),
    )

    # ── 5. Retire "POS Only": POS visibility is the branch tree now ────────────
    op.execute(
        "UPDATE products SET sales_channels = array_remove(sales_channels, 'pos') "
        "WHERE 'pos' = ANY(sales_channels)"
    )
    op.drop_constraint("ck_products_sales_channels_known", "products")
    op.create_check_constraint(
        "ck_products_sales_channels_known",
        "products",
        "sales_channels <@ ARRAY['web']::varchar[]",
    )

    # ── 6. Retire the aggregator-sync flags (read above, dropped here) ─────────
    op.drop_column("products", "sync_to_aggregators")
    op.drop_column("products", "sync_channels")
    op.drop_column("categories", "sync_to_aggregators")
    op.drop_column("categories", "sync_channels")


def downgrade() -> None:
    # Schema-only reversal — enough for Alembic to step back. The tree transform
    # (branch roots + the integrator tree) is forward-only; those rows survive as
    # ordinary groups once the discriminator columns are gone.
    op.add_column(
        "categories",
        sa.Column("sync_channels", sa.ARRAY(sa.String()), nullable=True),
    )
    op.add_column(
        "categories",
        sa.Column(
            "sync_to_aggregators",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "products",
        sa.Column("sync_channels", sa.ARRAY(sa.String()), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column(
            "sync_to_aggregators",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )

    op.drop_constraint("ck_products_sales_channels_known", "products")
    op.create_check_constraint(
        "ck_products_sales_channels_known",
        "products",
        "sales_channels <@ ARRAY['pos','web']::varchar[]",
    )

    op.drop_index("uq_menu_groups_integrator_root", table_name="menu_groups")
    op.drop_index("uq_menu_groups_branch_root", table_name="menu_groups")
    op.drop_constraint("ck_menu_groups_branch_root_has_branch", "menu_groups")
    op.drop_constraint("ck_menu_groups_root_kind", "menu_groups")
    op.drop_index("ix_menu_groups_root_id", table_name="menu_groups")
    op.drop_index("ix_menu_groups_branch_id", table_name="menu_groups")
    op.drop_constraint("fk_menu_groups_root", "menu_groups", type_="foreignkey")
    op.drop_constraint("fk_menu_groups_branch", "menu_groups", type_="foreignkey")
    op.drop_column("menu_groups", "root_id")
    op.drop_column("menu_groups", "branch_id")
    op.drop_column("menu_groups", "root_kind")
