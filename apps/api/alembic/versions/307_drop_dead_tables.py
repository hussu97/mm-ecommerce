"""Drop the tables no feature reads or writes, and the columns that pointed at them.

Every one of these was built — most against the Foodics parity matrix — and
never used by this business. Each was verified in production before this was
written: empty, or holding only rows nothing reads.

* **Allergens** (`allergens`, `product_allergens`) — never had a router. The
  allergen copy the shop does publish is CMS text, and stays.
* **Combos** (`combos`, `combo_sizes`, `combo_items`, `combo_options`) — only
  the Foodics importer ever wrote one, inactive and empty. Marketplace combo
  payloads are handled by the providers and never touched these.
* **Courses** (`courses`, `order_items.course_id`) — dine-in course firing.
  No line in production has a course; the register never sent or read one.
* **Timed events** (`timed_events`) and **notification rules**
  (`notification_rules`) — CRUD only; nothing ever evaluated either.
* **Predefined discounts** (`discounts`) — the table behind the till's
  `source="predefined"` discount. Every `order_discounts` row in production is a
  `promotion`; the path and its permission `pos.discounts.predefined` go too,
  and the slug is stripped from `roles.permissions` here so the role editor
  does not reject the roles that still carry it as unknown.
  `order_discounts.source` is a plain `varchar` with no CHECK, so there is no
  database vocabulary to narrow.
* **Legacy recipes** (`product_ingredients`, `modifier_option_ingredients`,
  `inventory_item_ingredients`) — superseded by versioned recipes (186). Empty;
  the fallbacks that still read them could only ever find nothing.
* **Lots** (`inventory_lots`, `inventory_transaction_items.lot_id`) — "lot
  identity, allocation later". No ledger line carries a lot.
* **Transfer templates** (`inventory_transfer_templates`,
  `inventory_transfer_template_items`, `transfer_orders.template_id` /
  `template_version` / `template_snapshot`) — the admin raises transfers from
  the fan-out grid; no transfer order in production names a template. (Shift
  *report* templates, `inventory_report_templates`, are unrelated and live.)
* **Content backups** (`seo_content_backup_054`, `about_copy_backup_061`) —
  the pre-rewrite copies 054/061 kept for their own downgrade. Those rewrites
  have been edited over in the console since; restoring them would be wrong.

Order matters on three hot tables (`order_items`, `inventory_transaction_items`,
`transfer_orders`): each loses a nullable column and its foreign key, which is
catalog-only (no rewrite) but takes an ACCESS EXCLUSIVE lock, so the migration
gives up after 5s rather than queue the tills behind it. Dropping a column does
not fire the `inventory_transaction_line_immutable` row trigger, and nothing
here updates or deletes a ledger row.

Downgrade puts every table and column back structurally — empty, under the
names, keys and indexes the database had — and re-grants
`pos.discounts.predefined` to the roles that hold `pos.discounts.open` (the
closest record of who had it; production's holders were exactly those).

Revision ID: 307_drop_dead_tables
Revises: 306_marketplace_returns
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "307_drop_dead_tables"
down_revision: Union[str, None] = "306_marketplace_returns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RETIRED_PERMISSION = "pos.discounts.predefined"

#: Children before parents, so no drop waits on a foreign key.
_TABLES = (
    "product_allergens",
    "allergens",
    "combo_options",
    "combo_items",
    "combo_sizes",
    "combos",
    "courses",
    "timed_events",
    "notification_rules",
    "product_ingredients",
    "modifier_option_ingredients",
    "inventory_item_ingredients",
    "inventory_lots",
    "inventory_transfer_template_items",
    "inventory_transfer_templates",
    "discounts",
    "seo_content_backup_054",
    "about_copy_backup_061",
)


def upgrade() -> None:
    # A column drop on a table the tills write every second must not sit in
    # the lock queue holding everyone behind it: fail fast, redeploy later.
    op.execute("SET LOCAL lock_timeout = '5s'")

    # ── Columns (and their keys) before the tables they point at ──────────────
    op.execute(
        "ALTER TABLE order_items DROP CONSTRAINT IF EXISTS fk_order_items_course_id"
    )
    op.execute("DROP INDEX IF EXISTS ix_order_items_course_id")
    op.execute("ALTER TABLE order_items DROP COLUMN IF EXISTS course_id")

    op.execute(
        "ALTER TABLE inventory_transaction_items "
        "DROP CONSTRAINT IF EXISTS fk_inventory_line_lot"
    )
    op.execute("ALTER TABLE inventory_transaction_items DROP COLUMN IF EXISTS lot_id")

    op.execute(
        "ALTER TABLE transfer_orders "
        "DROP CONSTRAINT IF EXISTS transfer_orders_template_id_fkey"
    )
    op.execute("DROP INDEX IF EXISTS ix_transfer_orders_template_id")
    op.execute(
        "ALTER TABLE transfer_orders "
        "DROP COLUMN IF EXISTS template_id, "
        "DROP COLUMN IF EXISTS template_version, "
        "DROP COLUMN IF EXISTS template_snapshot"
    )

    # ── The retired permission ────────────────────────────────────────────────
    # A literal, not a bound param: asyncpg types parameters strictly and
    # `roles.permissions` is varchar[] (see 261/282).
    op.execute(
        f"""
        UPDATE roles
           SET permissions = array_remove(permissions, '{_RETIRED_PERMISSION}')
         WHERE permissions @> ARRAY['{_RETIRED_PERMISSION}']::varchar[]
        """
    )

    # ── The tables ────────────────────────────────────────────────────────────
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")


#: Structure as the database held it at 306 (read off `pg_dump -s` of a database
#: migrated to 306), so a downgrade lands on exactly the schema 306 left. The two
#: content backups come back empty: 054/061's own downgrades then restore nothing.
#: One statement per entry — asyncpg prepares each `execute`, and a prepared
#: statement cannot hold more than one command.
_RESTORE_TABLES = """
        CREATE TABLE allergens (
            name varchar(120) NOT NULL,
            name_localized varchar(120),
            translations jsonb DEFAULT '{}'::jsonb NOT NULL,
            icon varchar(60),
            is_active boolean DEFAULT true NOT NULL,
            deleted_at timestamptz,
            id uuid NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT allergens_pkey PRIMARY KEY (id),
            CONSTRAINT ck_allergens_deleted_means_inactive
                CHECK ((deleted_at IS NULL) OR (is_active = false))
        );
        CREATE TABLE product_allergens (
            product_id uuid NOT NULL
                REFERENCES products(id) ON DELETE CASCADE,
            allergen_id uuid NOT NULL
                REFERENCES allergens(id) ON DELETE CASCADE,
            id uuid NOT NULL,
            CONSTRAINT product_allergens_pkey PRIMARY KEY (id),
            CONSTRAINT uq_product_allergen UNIQUE (product_id, allergen_id)
        );
        CREATE INDEX ix_product_allergens_allergen_id
            ON product_allergens (allergen_id);
        CREATE INDEX ix_product_allergens_product_id
            ON product_allergens (product_id);

        CREATE TABLE combos (
            sku varchar(100) NOT NULL,
            name varchar(200) NOT NULL,
            name_localized varchar(200),
            translations jsonb DEFAULT '{}'::jsonb NOT NULL,
            description text,
            image_url varchar(500),
            category_id uuid REFERENCES categories(id) ON DELETE SET NULL,
            tax_group_id uuid REFERENCES tax_groups(id) ON DELETE SET NULL,
            is_active boolean DEFAULT true NOT NULL,
            display_order integer DEFAULT 0 NOT NULL,
            deleted_at timestamptz,
            id uuid NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT combos_pkey PRIMARY KEY (id),
            CONSTRAINT ck_combos_deleted_means_inactive
                CHECK ((deleted_at IS NULL) OR (is_active = false))
        );
        CREATE UNIQUE INDEX ix_combos_sku ON combos (sku);
        CREATE TABLE combo_sizes (
            combo_id uuid NOT NULL REFERENCES combos(id) ON DELETE CASCADE,
            name varchar(120) NOT NULL,
            name_localized varchar(120),
            price numeric(10,2) DEFAULT '0'::numeric NOT NULL,
            display_order integer DEFAULT 0 NOT NULL,
            id uuid NOT NULL,
            CONSTRAINT combo_sizes_pkey PRIMARY KEY (id)
        );
        CREATE INDEX ix_combo_sizes_combo_id ON combo_sizes (combo_id);
        CREATE TABLE combo_items (
            combo_id uuid NOT NULL REFERENCES combos(id) ON DELETE CASCADE,
            name varchar(120) NOT NULL,
            name_localized varchar(120),
            quantity integer DEFAULT 1 NOT NULL,
            display_order integer DEFAULT 0 NOT NULL,
            id uuid NOT NULL,
            CONSTRAINT combo_items_pkey PRIMARY KEY (id)
        );
        CREATE INDEX ix_combo_items_combo_id ON combo_items (combo_id);
        CREATE TABLE combo_options (
            combo_item_id uuid NOT NULL
                REFERENCES combo_items(id) ON DELETE CASCADE,
            product_id uuid NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            combo_size_id uuid REFERENCES combo_sizes(id) ON DELETE CASCADE,
            extra_price numeric(10,2) DEFAULT '0'::numeric NOT NULL,
            is_default boolean DEFAULT false NOT NULL,
            display_order integer DEFAULT 0 NOT NULL,
            id uuid NOT NULL,
            CONSTRAINT combo_options_pkey PRIMARY KEY (id),
            CONSTRAINT uq_combo_option
                UNIQUE (combo_item_id, product_id, combo_size_id)
        );
        CREATE INDEX ix_combo_options_combo_item_id
            ON combo_options (combo_item_id);
        CREATE INDEX ix_combo_options_combo_size_id
            ON combo_options (combo_size_id);
        CREATE INDEX ix_combo_options_product_id ON combo_options (product_id);

        CREATE TABLE courses (
            id uuid NOT NULL,
            name varchar(100) NOT NULL,
            name_localized varchar(100),
            translations jsonb DEFAULT '{}'::jsonb NOT NULL,
            display_order integer DEFAULT 0 NOT NULL,
            is_active boolean DEFAULT true NOT NULL,
            created_at timestamptz DEFAULT now(),
            updated_at timestamptz DEFAULT now(),
            CONSTRAINT courses_pkey PRIMARY KEY (id)
        );

        CREATE TABLE timed_events (
            name varchar(150) NOT NULL,
            name_localized varchar(150),
            translations jsonb DEFAULT '{}'::jsonb NOT NULL,
            type varchar(20) NOT NULL,
            value numeric(12,4) DEFAULT '0'::numeric NOT NULL,
            product_ids uuid[] DEFAULT '{}'::uuid[] NOT NULL,
            category_ids uuid[] DEFAULT '{}'::uuid[] NOT NULL,
            branch_ids uuid[] DEFAULT '{}'::uuid[] NOT NULL,
            order_types varchar[] DEFAULT '{}'::varchar[] NOT NULL,
            priority integer DEFAULT 100 NOT NULL,
            is_active boolean DEFAULT true NOT NULL,
            deleted_at timestamptz,
            id uuid NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            from_date date,
            to_date date,
            from_time integer DEFAULT 0 NOT NULL,
            to_time integer DEFAULT 1439 NOT NULL,
            is_mon boolean DEFAULT true NOT NULL,
            is_tue boolean DEFAULT true NOT NULL,
            is_wed boolean DEFAULT true NOT NULL,
            is_thu boolean DEFAULT true NOT NULL,
            is_fri boolean DEFAULT true NOT NULL,
            is_sat boolean DEFAULT true NOT NULL,
            is_sun boolean DEFAULT true NOT NULL,
            CONSTRAINT timed_events_pkey PRIMARY KEY (id),
            CONSTRAINT ck_timed_events_deleted_means_inactive
                CHECK ((deleted_at IS NULL) OR (is_active = false))
        );

        CREATE TABLE notification_rules (
            name varchar(150) NOT NULL,
            event varchar(60) NOT NULL,
            threshold numeric(12,2),
            branch_ids uuid[] DEFAULT '{}'::uuid[] NOT NULL,
            recipient_user_ids uuid[] DEFAULT '{}'::uuid[] NOT NULL,
            recipient_emails varchar[] DEFAULT '{}'::varchar[] NOT NULL,
            channels varchar[] DEFAULT '{email}'::varchar[] NOT NULL,
            is_active boolean DEFAULT true NOT NULL,
            meta jsonb DEFAULT '{}'::jsonb NOT NULL,
            deleted_at timestamptz,
            id uuid NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT notification_rules_pkey PRIMARY KEY (id),
            CONSTRAINT ck_notification_rules_deleted_means_inactive
                CHECK ((deleted_at IS NULL) OR (is_active = false))
        );
        CREATE INDEX ix_notification_rules_event ON notification_rules (event);

        CREATE TABLE product_ingredients (
            product_id uuid NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            item_id uuid NOT NULL
                REFERENCES inventory_items(id) ON DELETE CASCADE,
            quantity numeric(16,4) NOT NULL,
            inactive_in_order_types jsonb DEFAULT '[]'::jsonb NOT NULL,
            id uuid NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT product_ingredients_pkey PRIMARY KEY (id),
            CONSTRAINT uq_product_ingredient UNIQUE (product_id, item_id)
        );
        CREATE INDEX ix_product_ingredients_item_id
            ON product_ingredients (item_id);
        CREATE INDEX ix_product_ingredients_product_id
            ON product_ingredients (product_id);
        CREATE TABLE modifier_option_ingredients (
            modifier_option_id uuid NOT NULL
                REFERENCES modifier_options(id) ON DELETE CASCADE,
            item_id uuid NOT NULL
                REFERENCES inventory_items(id) ON DELETE CASCADE,
            quantity numeric(16,4) NOT NULL,
            id uuid NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT modifier_option_ingredients_pkey PRIMARY KEY (id),
            CONSTRAINT uq_modifier_option_ingredient
                UNIQUE (modifier_option_id, item_id)
        );
        CREATE INDEX ix_modifier_option_ingredients_item_id
            ON modifier_option_ingredients (item_id);
        CREATE INDEX ix_modifier_option_ingredients_modifier_option_id
            ON modifier_option_ingredients (modifier_option_id);
        CREATE TABLE inventory_item_ingredients (
            parent_item_id uuid NOT NULL
                REFERENCES inventory_items(id) ON DELETE CASCADE,
            item_id uuid NOT NULL
                REFERENCES inventory_items(id) ON DELETE CASCADE,
            quantity numeric(16,4) NOT NULL,
            id uuid NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT inventory_item_ingredients_pkey PRIMARY KEY (id),
            CONSTRAINT uq_inventory_item_ingredient UNIQUE (parent_item_id, item_id)
        );
        CREATE INDEX ix_inventory_item_ingredients_item_id
            ON inventory_item_ingredients (item_id);
        CREATE INDEX ix_inventory_item_ingredients_parent_item_id
            ON inventory_item_ingredients (parent_item_id);

        CREATE TABLE inventory_lots (
            warehouse_id uuid NOT NULL
                REFERENCES warehouses(id) ON DELETE CASCADE,
            item_id uuid NOT NULL
                REFERENCES inventory_items(id) ON DELETE CASCADE,
            lot_reference varchar(120) NOT NULL,
            manufactured_at timestamptz,
            expires_at timestamptz,
            is_active boolean DEFAULT true NOT NULL,
            id uuid NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT inventory_lots_pkey PRIMARY KEY (id),
            CONSTRAINT uq_inventory_lot
                UNIQUE (warehouse_id, item_id, lot_reference)
        );

        CREATE TABLE inventory_transfer_templates (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            source_branch_id uuid NOT NULL
                REFERENCES branches(id) ON DELETE CASCADE,
            destination_branch_id uuid
                REFERENCES branches(id) ON DELETE CASCADE,
            name varchar(150) NOT NULL,
            is_active boolean DEFAULT true NOT NULL,
            display_order numeric(6,0) DEFAULT '0'::numeric NOT NULL,
            created_at timestamptz DEFAULT now() NOT NULL,
            updated_at timestamptz DEFAULT now() NOT NULL,
            version_number integer DEFAULT 1 NOT NULL,
            CONSTRAINT inventory_transfer_templates_pkey PRIMARY KEY (id),
            CONSTRAINT uq_inventory_transfer_template_revision
                UNIQUE (source_branch_id, name, version_number)
        );
        CREATE INDEX ix_inventory_transfer_templates_source_branch_id
            ON inventory_transfer_templates (source_branch_id);
        CREATE TABLE inventory_transfer_template_items (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            template_id uuid NOT NULL
                REFERENCES inventory_transfer_templates(id) ON DELETE CASCADE,
            item_id uuid NOT NULL
                REFERENCES inventory_items(id) ON DELETE RESTRICT,
            display_order numeric(6,0) DEFAULT '0'::numeric NOT NULL,
            CONSTRAINT inventory_transfer_template_items_pkey PRIMARY KEY (id),
            CONSTRAINT uq_inventory_transfer_template_item
                UNIQUE (template_id, item_id)
        );
        CREATE INDEX ix_inventory_transfer_template_items_template_id
            ON inventory_transfer_template_items (template_id);

        CREATE TABLE discounts (
            name varchar(150) NOT NULL,
            name_localized varchar(150),
            translations jsonb DEFAULT '{}'::jsonb NOT NULL,
            reference varchar(50),
            qualification varchar(20) DEFAULT 'order'::varchar NOT NULL,
            amount numeric(10,4) DEFAULT '0'::numeric NOT NULL,
            is_percentage boolean DEFAULT true NOT NULL,
            is_taxable boolean DEFAULT true NOT NULL,
            minimum_order_price numeric(10,2) DEFAULT '0'::numeric NOT NULL,
            minimum_product_price numeric(10,2) DEFAULT '0'::numeric NOT NULL,
            maximum_amount numeric(10,2),
            branch_ids uuid[] DEFAULT '{}'::uuid[] NOT NULL,
            order_types varchar[] DEFAULT '{}'::varchar[] NOT NULL,
            is_active boolean DEFAULT true NOT NULL,
            deleted_at timestamptz,
            id uuid NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT discounts_pkey PRIMARY KEY (id),
            CONSTRAINT ck_discounts_deleted_means_inactive
                CHECK ((deleted_at IS NULL) OR (is_active = false))
        );
        CREATE UNIQUE INDEX ix_discounts_reference ON discounts (reference);

        CREATE TABLE seo_content_backup_054 (
            id uuid NOT NULL,
            kind varchar(20) NOT NULL,
            slug varchar(150) NOT NULL,
            content jsonb,
            CONSTRAINT seo_content_backup_054_pkey PRIMARY KEY (id)
        );
        CREATE TABLE about_copy_backup_061 (
            id uuid NOT NULL,
            slug varchar(150) NOT NULL,
            content jsonb,
            CONSTRAINT about_copy_backup_061_pkey PRIMARY KEY (id)
        )
"""


def downgrade() -> None:
    for statement in _RESTORE_TABLES.split(";"):
        if statement.strip():
            op.execute(statement)

    # ── The columns, under the names 041/186/222 gave their keys and indexes ──
    op.execute("ALTER TABLE order_items ADD COLUMN course_id uuid")
    op.execute(
        "ALTER TABLE order_items ADD CONSTRAINT fk_order_items_course_id "
        "FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE SET NULL"
    )
    op.execute("CREATE INDEX ix_order_items_course_id ON order_items (course_id)")

    op.execute("ALTER TABLE inventory_transaction_items ADD COLUMN lot_id uuid")
    op.execute(
        "ALTER TABLE inventory_transaction_items ADD CONSTRAINT fk_inventory_line_lot "
        "FOREIGN KEY (lot_id) REFERENCES inventory_lots(id) ON DELETE SET NULL"
    )

    op.execute(
        "ALTER TABLE transfer_orders "
        "ADD COLUMN template_id uuid, "
        "ADD COLUMN template_version integer, "
        "ADD COLUMN template_snapshot jsonb"
    )
    op.execute(
        "ALTER TABLE transfer_orders ADD CONSTRAINT transfer_orders_template_id_fkey "
        "FOREIGN KEY (template_id) REFERENCES inventory_transfer_templates(id) "
        "ON DELETE RESTRICT"
    )
    op.execute(
        "CREATE INDEX ix_transfer_orders_template_id ON transfer_orders (template_id)"
    )

    op.execute(
        f"""
        UPDATE roles
           SET permissions = array_append(permissions, '{_RETIRED_PERMISSION}')
         WHERE permissions @> ARRAY['pos.discounts.open']::varchar[]
           AND NOT (permissions @> ARRAY['{_RETIRED_PERMISSION}']::varchar[])
        """
    )
