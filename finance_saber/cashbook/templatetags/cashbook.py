from django import template

from ..models import Account, Kind

register = template.Library()


@register.filter
def get(mapping, key):
    """``{{ row.balance|get:account.value }}``: look up a dict by a variable key."""
    return mapping.get(key) if mapping else None


@register.filter
def movement(value) -> str:
    """'20.10.2026 Расход 50,000.00 Наличные UZS' for an operation or an importer.Movement."""
    kind, account = Kind(value.kind).label, Account(value.account).label
    return f"{value.date:%d.%m.%Y} {kind} {value.amount:,.2f} {account}"
