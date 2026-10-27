"""Balances and the running-balance ledger shown on the cash desk page."""

from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Q, QuerySet, Sum

from .models import Account, Kind, Operation

ZERO = Decimal("0")


def empty_balances() -> dict[str, Decimal]:
    return {account: ZERO for account in Account.values}


def balances(operations: QuerySet[Operation]) -> dict[str, Decimal]:
    """Net amount per account over ``operations`` (cancelled ones excluded)."""
    sums = {}
    for account in Account.values:
        sums[f"{account}_income"] = Sum("amount", filter=Q(kind=Kind.INCOME, account=account))
        sums[f"{account}_received"] = Sum("to_amount", filter=Q(kind=Kind.TRANSFER, to_account=account))
        sums[f"{account}_spent"] = Sum("amount", filter=Q(kind__in=[Kind.EXPENSE, Kind.TRANSFER], account=account))
    totals = {key: value or ZERO for key, value in operations.posted().aggregate(**sums).items()}
    return {
        account: totals[f"{account}_income"] + totals[f"{account}_received"] - totals[f"{account}_spent"]
        for account in Account.values
    }


@dataclass
class LedgerRow:
    operation: Operation
    income: dict[str, Decimal] = field(default_factory=dict)
    expense: dict[str, Decimal] = field(default_factory=dict)
    balance_before: dict[str, Decimal] = field(default_factory=dict)
    balance: dict[str, Decimal] = field(default_factory=dict)


@dataclass
class Ledger:
    opening: dict[str, Decimal]
    closing: dict[str, Decimal]
    rows: list[LedgerRow]
    income_total: dict[str, Decimal]
    expense_total: dict[str, Decimal]


def build_ledger(desk_operations: QuerySet[Operation], date_from, date_to, shown: QuerySet[Operation]) -> Ledger:
    """Rows of ``shown`` between the dates, each with the desk's balance after it.

    Like the workbook, the running balance (Остаток) always follows every
    operation of the desk: filtering rows hides them but does not change the
    balance shown on the rows that remain. Cancelled operations are listed but
    don't move the balance.
    """
    opening = balances(desk_operations.filter(date__lt=date_from))
    running = dict(opening)
    shown_ids = set(shown.values_list("id", flat=True))
    rows = []
    income_total, expense_total = empty_balances(), empty_balances()

    period = desk_operations.filter(date__gte=date_from, date__lte=date_to).select_related(
        "category__article", "counterparty__region"
    )
    for operation in period.order_by("date", "id"):
        row = LedgerRow(operation, balance_before=dict(running))
        for account, amount in operation.movements().items():
            (row.income if amount > 0 else row.expense)[account] = abs(amount)
            if not operation.is_cancelled:
                running[account] += amount
        row.balance = dict(running)
        if operation.id in shown_ids:
            rows.append(row)
            if not operation.is_cancelled:
                for account, amount in row.income.items():
                    income_total[account] += amount
                for account, amount in row.expense.items():
                    expense_total[account] += amount

    return Ledger(opening, running, rows, income_total, expense_total)
