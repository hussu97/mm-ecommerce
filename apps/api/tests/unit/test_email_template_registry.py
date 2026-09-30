"""
Every email the app sends must be in the `EmailTemplate` registry.

The admin's Email Log used to keep its own hardcoded list of twelve templates
while the shop grew to twenty-one. The inventory report, both abandoned-cart
reminders, the custom-order invoice and enquiry, the transfer and PO variance
alerts, the counter pricing alarm and the auto-availability email all showed as
raw keys in the Template column, and none of them could be filtered for. The
list now lives in one place (`app/models/email_log.py`) and the admin reads it
from the API — so the only way to miss a template again is to not register it,
which is what this file catches.

The scan is static: every call into the email funnel that names a template, and
every file in `app/templates/emails/`, across the whole of `app/`. A literal, a
module-level constant and an `EmailTemplate.X` attribute are all followed; a
bare parameter (the funnel forwarding what it was handed) is not a new key.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from app.models.email_log import EmailTemplate
from app.services import email_service

APP_DIR = Path(__file__).resolve().parents[2] / "app"
TEMPLATES_DIR = APP_DIR / "templates" / "emails"

#: Layout partials, not emails.
_NOT_EMAILS = {"base.html", "_parts.html"}

#: Funnel function -> (positional index, keyword) of its template argument.
_TEMPLATE_ARG = {
    "_log": (0, "template"),
    "_render": (0, "template_name"),
    "render_email": (0, "template_name"),
    "_send_user_email": (0, "template"),
    "_send_order_email": (None, "template"),
    "send_with_attachment": (None, "template"),
    "already_sent": (1, "template"),
    "reference_sent": (1, "template"),
    "sent_to_recently": (1, "templates"),
}


def _key(name: str) -> str:
    return name.removesuffix(".html")


def _module_constants(tree: ast.Module) -> dict[str, ast.expr]:
    constants: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                constants[node.target.id] = node.value
    return constants


def _resolve(node: ast.expr, constants: dict[str, ast.expr]) -> list[str]:
    """The template keys *node* names, or [] when it is a pass-through."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [_key(node.value)]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return [key for element in node.elts for key in _resolve(element, constants)]
    if isinstance(node, ast.Name) and node.id in constants:
        return _resolve(constants[node.id], constants)
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "EmailTemplate"
    ):
        # `EmailTemplate.NOPE` would be an AttributeError at import, but say so
        # here rather than rely on the module being imported by some test.
        member = EmailTemplate.__members__.get(node.attr)
        return [member.value if member else f"EmailTemplate.{node.attr}"]
    return []


def _keys_used_in_app() -> dict[str, list[str]]:
    """Every template key a call site names -> where it is named."""
    used: dict[str, list[str]] = {}
    for path in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        constants = _module_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name not in _TEMPLATE_ARG:
                continue
            index, keyword = _TEMPLATE_ARG[name]
            args = [kw.value for kw in node.keywords if kw.arg == keyword]
            if index is not None and len(node.args) > index:
                args.append(node.args[index])
            for arg in args:
                for key in _resolve(arg, constants):
                    where = f"{path.relative_to(APP_DIR.parent)}:{node.lineno}"
                    used.setdefault(key, []).append(where)
    return used


REGISTERED = {t.value for t in EmailTemplate}
USED = _keys_used_in_app()


def test_the_scan_actually_finds_the_send_sites():
    # A scan that silently matched nothing would pass everything below.
    assert len(USED) >= 15, sorted(USED)


def test_every_template_a_call_site_names_is_registered():
    missing = {key: sites for key, sites in USED.items() if key not in REGISTERED}
    assert not missing, (
        "Add these to EmailTemplate in app/models/email_log.py, with the label "
        f"the admin should show: {missing}"
    )


def test_every_email_template_file_is_registered():
    files = {
        _key(path.name)
        for path in TEMPLATES_DIR.glob("*.html")
        if path.name not in _NOT_EMAILS
    }
    assert files - REGISTERED == set()


def test_every_registered_template_is_still_sent():
    # A retired email should leave the registry; its old rows keep their name
    # through the endpoint's legacy half and `EmailTemplate.label_for`.
    assert REGISTERED - set(USED) == set()


def test_labels_are_present_and_distinct():
    labels = [t.label for t in EmailTemplate]
    assert all(label.strip() for label in labels)
    assert len(labels) == len(set(labels))


def test_customer_templates_are_the_registrys_customer_emails():
    assert {"order_confirmation.html", "abandoned_cart.html"} <= (
        email_service.CUSTOMER_TEMPLATES
    )
    assert "owner_order_notification.html" not in email_service.CUSTOMER_TEMPLATES
    assert "inventory_report_submitted.html" not in email_service.CUSTOMER_TEMPLATES


@pytest.mark.parametrize(
    ("key", "label"),
    [
        ("inventory_report_submitted", "Inventory Report Submitted"),
        ("order_out_for_delivery", "Out for Delivery"),
        # Not registered (a retired key): made readable, never shown raw.
        ("report", "Report"),
        ("some_retired_email", "Some retired email"),
    ],
)
def test_label_for(key, label):
    assert EmailTemplate.label_for(key) == label


class _CapturingSession:
    def __init__(self, rows: list):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, row):
        self._rows.append(row)

    async def commit(self):
        return None


@pytest.mark.asyncio
async def test_an_unregistered_key_is_journalled_and_named_in_the_error_log(
    monkeypatch, caplog
):
    rows: list = []
    monkeypatch.setattr(
        email_service, "AsyncSessionFactory", lambda: _CapturingSession(rows)
    )
    result = {"status": "sent", "resend_id": "re_1", "error": None}

    with caplog.at_level(logging.ERROR, logger=email_service.logger.name):
        await email_service._log("brand_new_email", "a@example.com", "Hi", result)
        await email_service._log(
            EmailTemplate.WELCOME, "b@example.com", "Welcome", result
        )

    # The send still lands in the journal — losing the row would be worse.
    assert [row.template for row in rows] == ["brand_new_email", "welcome"]
    # A registered key is stored as its plain string, not the enum member.
    assert type(rows[1].template) is str
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "brand_new_email" in errors[0]


class _DistinctTemplatesDb:
    """Answers the endpoint's one query with the keys the table holds."""

    def __init__(self, keys: list[str | None]):
        self._keys = keys

    async def execute(self, _stmt):
        keys = self._keys

        class _Result:
            def scalars(self):
                return self

            def all(self):
                return keys

        return _Result()


@pytest.mark.asyncio
async def test_the_endpoint_offers_the_registry_then_the_tables_retired_keys():
    from app.api.v1.email_logs import list_email_templates

    options = await list_email_templates(
        db=_DistinctTemplatesDb(
            ["welcome", "report", None, "inventory_report_submitted"]
        ),
        _admin=None,
    )

    values = [option.value for option in options]
    # Every registered template, sent yet or not, in the registry's order...
    assert values[: len(EmailTemplate)] == [t.value for t in EmailTemplate]
    # ...then only the journalled key the registry no longer carries, named.
    assert [(o.value, o.label) for o in options[len(EmailTemplate) :]] == [
        ("report", "Report")
    ]
