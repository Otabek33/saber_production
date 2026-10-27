"""Plan against fact for days and periods: cash by article, collections by region."""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.db.models import Q, Sum

from finance_saber.cashbook.models import ACCOUNT_CURRENCY, Account, Article, ArticleKind, Kind, Operation, Region

from .models import CashPlan, CollectionPlan

ZERO = Decimal(0)
UZS_ACCOUNTS = [account for account in Account.values if ACCOUNT_CURRENCY[account] == "UZS"]


def week_of(date: datetime.date) -> list[datetime.date]:
    """Monday to Sunday of the week ``date`` falls in."""
    monday = date - datetime.timedelta(days=date.weekday())
    return [monday + datetime.timedelta(days=offset) for offset in range(7)]


def cash_operations(date_from, date_to):
    """Income and expense in the cash flow: not transfers, not bookkeeping articles, not cancelled."""
    return Operation.objects.posted().filter(
        date__gte=date_from,
        date__lte=date_to,
        kind__in=[Kind.INCOME, Kind.EXPENSE],
        category__article__in_cash_flow=True,
    )


def cash_fact(date_from, date_to, rate=None) -> dict:
    """{(article id, date): amount in UZS}; USD cash is converted at ``rate`` (skipped without one)."""
    totals = defaultdict(Decimal)
    rows = (
        cash_operations(date_from, date_to)
        .values("category__article_id", "date", "account")
        .annotate(total=Sum("amount"))
    )
    for row in rows:
        amount = row["total"]
        if ACCOUNT_CURRENCY[row["account"]] == "USD":
            if not rate:
                continue
            amount *= rate
        totals[(row["category__article_id"], row["date"])] += amount
    return totals


def cash_plan_grid(days: list[datetime.date], rate=None):
    """Rows of articles (income first) with plan and fact for each day, and day totals per kind.

    An article is listed when it is active, or has a plan or a movement in these days.
    """
    plans = {(p.article_id, p.date): p.amount for p in CashPlan.objects.filter(date__in=days)}
    facts = cash_fact(days[0], days[-1], rate)
    used = {article_id for article_id, _date in [*plans, *facts]}
    articles = Article.objects.filter(Q(is_active=True, in_cash_flow=True) | Q(pk__in=used)).exclude(
        kind=ArticleKind.TRANSFER
    )
    sections = {Kind.INCOME: [], Kind.EXPENSE: []}
    income_side = income_side_of_both()
    for article in articles.order_by("position", "name"):
        cells = [{"date": day, "plan": plans.get((article.pk, day)), "fact": facts.get((article.pk, day), ZERO)}
                 for day in days]  # fmt: skip
        row = {
            "article": article,
            "cells": cells,
            "plan": sum((cell["plan"] or ZERO for cell in cells), ZERO),
            "fact": sum((cell["fact"] for cell in cells), ZERO),
        }
        if article.kind == ArticleKind.INCOME or (article.kind == ArticleKind.BOTH and income_side.get(article.pk)):
            sections[Kind.INCOME].append(row)
        else:
            sections[Kind.EXPENSE].append(row)
    totals = {
        kind: {
            "days": [
                {
                    "plan": sum((row["cells"][i]["plan"] or ZERO for row in rows), ZERO),
                    "fact": sum((row["cells"][i]["fact"] for row in rows), ZERO),
                }
                for i in range(len(days))
            ],
            "plan": sum((row["plan"] for row in rows), ZERO),
            "fact": sum((row["fact"] for row in rows), ZERO),
        }
        for kind, rows in sections.items()
    }
    return sections, totals


def income_side_of_both() -> dict[int, bool]:
    """{article id: True} for income-or-expense articles mostly used for money coming in.

    E.g. "Месный продажа" is income with the odd refund; "Местные поставщики" the reverse.
    """
    totals = defaultdict(lambda: {Kind.INCOME: ZERO, Kind.EXPENSE: ZERO})
    rows = (
        Operation.objects.posted()
        .filter(category__article__kind=ArticleKind.BOTH, kind__in=[Kind.INCOME, Kind.EXPENSE])
        .values("category__article_id", "kind")
        .annotate(total=Sum("amount"))
    )
    for row in rows:
        totals[row["category__article_id"]][row["kind"]] = row["total"]
    return {article_id: sides[Kind.INCOME] >= sides[Kind.EXPENSE] for article_id, sides in totals.items()}


def save_cash_plan(data, days, user=None) -> int:
    """Write edited cells named plan-<article id>-<yyyy-mm-dd>; an empty cell removes the plan."""
    return _save_grid(data, days, CashPlan, "article", user)


def collections_fact(date_from, date_to) -> dict:
    """{(region id, date): received}: bank income from counterparties of each region."""
    rows = (
        Operation.objects.posted()
        .filter(
            date__gte=date_from,
            date__lte=date_to,
            kind=Kind.INCOME,
            account__in=UZS_ACCOUNTS,
            counterparty__region__isnull=False,
            category__article__in_cash_flow=True,
        )
        .values("counterparty__region_id", "date")
        .annotate(total=Sum("amount"))
    )
    return {(row["counterparty__region_id"], row["date"]): row["total"] for row in rows}


def collection_grid(days: list[datetime.date]):
    plans = {(p.region_id, p.date): p.amount for p in CollectionPlan.objects.filter(date__in=days)}
    facts = collections_fact(days[0], days[-1])
    used = {region_id for region_id, _date in [*plans, *facts]}
    rows = []
    for region in Region.objects.filter(Q(is_active=True) | Q(pk__in=used)).order_by("manager", "name"):
        cells = [{"date": day, "plan": plans.get((region.pk, day)), "fact": facts.get((region.pk, day), ZERO)}
                 for day in days]  # fmt: skip
        plan = sum((cell["plan"] or ZERO for cell in cells), ZERO)
        fact = sum((cell["fact"] for cell in cells), ZERO)
        rows.append({"region": region, "cells": cells, "plan": plan, "fact": fact, "percent": percent(fact, plan)})
    totals = [
        {
            "plan": sum((row["cells"][i]["plan"] or ZERO for row in rows), ZERO),
            "fact": sum((row["cells"][i]["fact"] for row in rows), ZERO),
        }
        for i in range(len(days))
    ]
    return rows, totals


def save_collection_plan(data, days, user=None) -> int:
    return _save_grid(data, days, CollectionPlan, "region", user)


def _save_grid(data, days, model, field, user) -> int:
    existing = {(getattr(p, f"{field}_id"), p.date): p for p in model.objects.filter(date__in=days)}
    allowed_days = {day.isoformat(): day for day in days}
    changed = 0
    for name, value in data.items():
        parts = name.split("-", 2)
        if len(parts) != 3 or parts[0] != "plan" or not parts[1].isdigit() or parts[2] not in allowed_days:
            continue
        key = (int(parts[1]), allowed_days[parts[2]])
        amount = parse_amount(value)
        current = existing.get(key)
        if amount is None:  # emptied: no plan for that day
            if current:
                current.delete()
                changed += 1
            continue
        if current is None:
            model.objects.create(**{f"{field}_id": key[0]}, date=key[1], amount=amount, updated_by=user)
            changed += 1
        elif current.amount != amount:
            current.amount, current.updated_by = amount, user
            current.save(update_fields=["amount", "updated_by", "updated_at"])
            changed += 1
    return changed


def parse_amount(value) -> Decimal | None:
    """ "1 250 000" or "1250000,50" → Decimal; empty → None; invalid or negative → None."""
    text = str(value or "").replace("\xa0", "").replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        amount = Decimal(text)
    except ArithmeticError:
        return None
    return amount if amount >= 0 else None


def percent(fact, plan):
    return round(fact * 100 / plan) if plan else None
