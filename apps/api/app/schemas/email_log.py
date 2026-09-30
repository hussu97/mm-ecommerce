from __future__ import annotations

from pydantic import BaseModel


class EmailTemplateOption(BaseModel):
    """One entry in the admin's Email Log template filter.

    `value` is the `email_logs.template` key the list endpoint filters on;
    `label` is what the admin shows for it, in the filter and in the table.
    """

    value: str
    label: str
