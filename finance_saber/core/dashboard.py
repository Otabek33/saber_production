"""The home dashboard: the company's situation for a chosen period, from the data already in the app.

Everything is computed on the fly from the cash book, sales, production and the
plans; nothing is copied or stored. Each block is built only for users whose role
lets them see its data (see ``build``).

The workbook sheets it replaces: SWOT (accounts and the month's production and
sales against plan for a date), "Сash flow" (money in and out by article and
account for a period, against plan), "1-week" (the week's cash plan against fact)
and "Sells plan" (collections by region against plan).
"""

import calendar
import datetime
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext_lazy as _

ZERO = Decimal(0)

PRESETS = {
    "today": _("Today"),
    "week": _("This week"),
    "month": _("This month"),
    "quarter": _("This quarter"),
    "year": _("This year"),
}


@dataclass
class Period:
    date_from: datetime.date
    date_to: datetime.date
    preset: str = ""

    @property
    def days(self) -> int:
        return (self.date_to - self.date_from).days + 1

    @property
    def month_start(self) -> datetime.date:
        return self.date_to.replace(day=1)


def preset_period(name: str, today: datetime.date) -> Period:
    if name == "today":
        return Period(today, today, name)
    if name == "week":
        return Period(today - datetime.timedelta(days=today.weekday()), today, name)
    if name == "quarter":
        return Period(today.replace(month=(today.month - 1) // 3 * 3 + 1, day=1), today, name)
    if name == "year":
        return Period(today.replace(month=1, day=1), today, name)
    return Period(today.replace(day=1), today, "month")


def period_from(params) -> Period:
    """The period asked for (?from=…&to=… or ?period=week…); this month to date by default."""
    today = timezone.localdate()
    date_from, date_to = parse_date(params.get("from") or ""), parse_date(params.get("to") or "")
    if date_from or date_to:
        date_to = date_to or today
        date_from = date_from or date_to.replace(day=1)
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        return Period(date_from, date_to)
    return preset_period(params.get("period", "month"), today)


def percent(fact, plan):
    return round(fact * 100 / plan) if plan else None


def exchange_rate(on_date):
    """UZS per USD on the date: the rate table, else the last rate typed on a cash operation."""
    from finance_saber.cashbook.models import ExchangeRate, Operation

    rate = ExchangeRate.on(on_date)
    if rate is None:
        last = Operation.objects.filter(rate__isnull=False, date__lte=on_date).order_by("-date", "-id").first()
        rate = last.rate if last else None
    return rate


# Money ---------------------------------------------------------------------------------


def accounts(period: Period, rate):
    """Each cash desk account: balance at the start, money in and out in the period, balance at the end."""
    from finance_saber.cashbook.ledger import balances
    from finance_saber.cashbook.models import ACCOUNT_CURRENCY, ACCOUNT_DISPLAY, Account, CashDesk, Kind

    rows = []
    for desk in CashDesk.objects.filter(is_active=True):
        operations = desk.operations.posted()
        opening = balances(operations.filter(date__lt=period.date_from))
        flows = operations.filter(date__gte=period.date_from, date__lte=period.date_to)
        for account in desk.accounts:
            totals = flows.aggregate(
                income=Sum("amount", filter=Q(kind=Kind.INCOME, account=account)),
                received=Sum("to_amount", filter=Q(kind=Kind.TRANSFER, to_account=account)),
                spent=Sum("amount", filter=Q(kind__in=[Kind.EXPENSE, Kind.TRANSFER], account=account)),
            )
            money_in = (totals["income"] or ZERO) + (totals["received"] or ZERO)
            money_out = totals["spent"] or ZERO
            currency = ACCOUNT_CURRENCY[account]
            closing = opening[account] + money_in - money_out
            rows.append(
                {
                    "desk": desk,
                    "account": Account(account),
                    "label": f"{desk.name} · {Account(account).label}" if len(desk.accounts) > 1 else desk.name,
                    "currency": currency,
                    "display": ACCOUNT_DISPLAY[account],
                    "opening": opening[account],
                    "in": money_in,
                    "out": money_out,
                    "closing": closing,
                    "closing_uzs": closing * rate if currency == "USD" and rate else closing,
                }
            )
    return rows


def channels(by_account: dict, rate) -> list[dict]:
    """Amounts per account grouped into cash desks, card and bank, in UZS (USD cash at ``rate``).

    Each entry keeps the USD part as typed, so cards can show "… incl. 18 USD".
    """
    from finance_saber.cashbook.models import ACCOUNT_CURRENCY, Account

    groups = (
        ("cash", _("Cash desks"), "solar:wallet-money-line-duotone", (Account.CASH_UZS, Account.CASH_USD)),
        ("card", _("Card"), "solar:card-line-duotone", (Account.CARD,)),
        ("bank", _("Bank"), "solar:buildings-2-line-duotone", (Account.BANK,)),
    )
    result = []
    for key, label, icon, accounts_ in groups:
        usd = sum((by_account.get(a, ZERO) for a in accounts_ if ACCOUNT_CURRENCY[a] == "USD"), ZERO)
        uzs = sum((by_account.get(a, ZERO) for a in accounts_ if ACCOUNT_CURRENCY[a] == "UZS"), ZERO)
        result.append({"key": key, "label": label, "icon": icon, "amount": uzs + usd * (rate or ZERO), "usd": usd})
    # Each channel's share of the largest one, for the little bars on the cards.
    largest = max((abs(channel["amount"]) for channel in result), default=ZERO) or ZERO
    for channel in result:
        channel["share"] = round(abs(channel["amount"]) * 100 / largest) if largest else 0
    return result


def money_totals(rows, rate) -> dict:
    uzs = sum((row["closing"] for row in rows if row["currency"] == "UZS"), ZERO)
    usd = sum((row["closing"] for row in rows if row["currency"] == "USD"), ZERO)
    by_account = defaultdict(Decimal)
    for row in rows:
        by_account[row["account"]] += row["closing"]
    return {
        "uzs": uzs,
        "usd": usd,
        "total_uzs": sum((row["closing_uzs"] for row in rows), ZERO),
        "channels": channels(by_account, rate),
    }


def cash_flow(period: Period, rate):
    """Money in and out by article and account for the period, next to the cash plan.

    Like the workbook's "Сash flow" sheet. Transfers between accounts and bookkeeping
    articles (opening balances, corrections) are left out; USD cash is shown in USD
    and counted in the UZS total at ``rate``.
    """
    from finance_saber.cashbook.models import ACCOUNT_CURRENCY, Account, Article, ArticleKind, Kind
    from finance_saber.planning.models import CashPlan
    from finance_saber.planning.services import cash_operations, income_side_of_both

    # {article: {account: {kind: amount}}}: kept by direction, as income-or-expense articles have both.
    facts = defaultdict(lambda: defaultdict(lambda: defaultdict(Decimal)))
    for row in (
        cash_operations(period.date_from, period.date_to)
        .values("category__article_id", "account", "kind")
        .annotate(total=Sum("amount"))
    ):
        facts[row["category__article_id"]][row["account"]][row["kind"]] += row["total"]
    plans = dict(
        CashPlan.objects.filter(date__gte=period.date_from, date__lte=period.date_to)
        .values_list("article_id")
        .annotate(total=Sum("amount"))
    )
    income_side = income_side_of_both()
    sections = {Kind.INCOME: [], Kind.EXPENSE: []}
    for article in Article.objects.filter(pk__in={*facts, *plans}).order_by("position", "name"):
        is_income = article.kind == ArticleKind.INCOME or (
            article.kind == ArticleKind.BOTH and income_side.get(article.pk, True)
        )
        side, other = (Kind.INCOME, Kind.EXPENSE) if is_income else (Kind.EXPENSE, Kind.INCOME)
        # A movement the other way (a refund) reduces the article's total on its side.
        by_account = {
            account: kinds.get(side, ZERO) - kinds.get(other, ZERO)
            for account, kinds in facts.get(article.pk, {}).items()
        }
        total = sum(
            (amount * (rate or ZERO) if ACCOUNT_CURRENCY[account] == "USD" else amount)
            for account, amount in by_account.items()
        )
        plan = plans.get(article.pk, ZERO)
        row = {
            "article": article,
            "accounts": [by_account.get(account, ZERO) for account in Account.values],
            "total": total,
            "plan": plan,
            "percent": percent(total, plan),
        }
        sections[side].append(row)
    totals = {
        kind: {
            "accounts": [sum((row["accounts"][i] for row in rows), ZERO) for i in range(len(Account.values))],
            "total": sum((row["total"] for row in rows), ZERO),
            "plan": sum((row["plan"] for row in rows), ZERO),
        }
        for kind, rows in sections.items()
    }
    for kind, total in totals.items():
        # Measured on the planned articles only: unplanned money would otherwise inflate the share.
        planned_fact = sum((row["total"] for row in sections[kind] if row["plan"]), ZERO)
        total["percent"] = percent(planned_fact, total["plan"])
        total["channels"] = channels(dict(zip(Account.values, total["accounts"], strict=True)), rate)
    net_channels = [
        {**money_in, "amount": money_in["amount"] - money_out["amount"], "usd": money_in["usd"] - money_out["usd"]}
        for money_in, money_out in zip(totals[Kind.INCOME]["channels"], totals[Kind.EXPENSE]["channels"], strict=True)
    ]
    return {
        "income": sections[Kind.INCOME],
        "expense": sections[Kind.EXPENSE],
        "totals": totals,
        "net": totals[Kind.INCOME]["total"] - totals[Kind.EXPENSE]["total"],
        "net_plan": totals[Kind.INCOME]["plan"] - totals[Kind.EXPENSE]["plan"],
        "net_channels": net_channels,
        "accounts": [(account, label) for account, label in Account.choices],
    }


def daily_chart(period: Period, rate) -> dict:
    """Money in and out per day (per month for periods over three months), for the chart."""
    from finance_saber.cashbook.models import ACCOUNT_CURRENCY, ArticleKind, Kind
    from finance_saber.planning.services import cash_operations, income_side_of_both

    monthly = period.days > 92
    totals = defaultdict(lambda: {"in": ZERO, "out": ZERO})
    income_side = income_side_of_both()
    rows = (
        cash_operations(period.date_from, period.date_to)
        .values("date", "kind", "account", "category__article_id", "category__article__kind")
        .annotate(total=Sum("amount"))
    )
    for row in rows:
        amount = row["total"]
        if ACCOUNT_CURRENCY[row["account"]] == "USD":
            if not rate:
                continue
            amount *= rate
        bucket = row["date"].replace(day=1) if monthly else row["date"]
        # A refund on an income article reduces money in, rather than counting as money out.
        side = "in" if row["kind"] == Kind.INCOME else "out"
        if row["category__article__kind"] == ArticleKind.BOTH:
            if income_side.get(row["category__article_id"], True):
                side, amount = "in", amount if row["kind"] == Kind.INCOME else -amount
            else:
                side, amount = "out", amount if row["kind"] == Kind.EXPENSE else -amount
        totals[bucket][side] += amount
    labels, money_in, money_out = [], [], []
    day = period.date_from.replace(day=1) if monthly else period.date_from
    while day <= period.date_to:
        labels.append(day.strftime("%m.%Y") if monthly else day.strftime("%d.%m"))
        money_in.append(float(totals[day]["in"]))
        money_out.append(float(totals[day]["out"]))
        day = (day + datetime.timedelta(days=32)).replace(day=1) if monthly else day + datetime.timedelta(days=1)
    return {"labels": labels, "in": money_in, "out": money_out, "monthly": monthly}


# Sales, production and debts -------------------------------------------------------------


def sales_summary(period: Period) -> dict:
    from finance_saber.sales.models import Sale

    sales = Sale.objects.posted().filter(date__gte=period.date_from, date__lte=period.date_to)
    totals = sales.aggregate(amount=Sum("amount"), quantity=Sum("quantity"))
    by_channel = (
        sales.values("channel__name").annotate(amount=Sum("amount"), quantity=Sum("quantity")).order_by("-amount")
    )
    return {
        "amount": totals["amount"] or ZERO,
        "quantity": totals["quantity"] or 0,
        "clients": sales.values("client").distinct().count(),
        "by_channel": list(by_channel),
    }


def products_month(period: Period, show_sales: bool, show_production: bool):
    """The month of the period's end: plan, fact to date and what is left, per product.

    Like the right-hand side of the SWOT sheet: production and sales, month plan against fact.
    """
    from finance_saber.production.models import Product, ProductionRun
    from finance_saber.sales.models import Plan, Sale

    month_end = period.date_to
    month_start = period.month_start
    plans = defaultdict(dict)
    for plan in Plan.objects.filter(year=month_end.year, month=month_end.month):
        plans[plan.kind][plan.product_id] = plan.quantity
    sold = dict(
        Sale.objects.posted()
        .filter(date__gte=month_start, date__lte=month_end)
        .values_list("product_id")
        .annotate(total=Sum("quantity"))
    )
    produced = dict(
        ProductionRun.objects.filter(is_cancelled=False, date__gte=month_start, date__lte=month_end)
        .values_list("product_id")
        .annotate(total=Sum("quantity"))
    )
    days_in_month = calendar.monthrange(month_end.year, month_end.month)[1]
    rows = []
    for product in Product.objects.filter(is_active=True):
        row = {"product": product}
        if show_production:
            plan = plans[Plan.Kind.PRODUCTION].get(product.pk, 0)
            row["production"] = {"plan": plan, "fact": produced.get(product.pk, 0)}
            row["production"]["percent"] = percent(row["production"]["fact"], plan)
        if show_sales:
            plan = plans[Plan.Kind.SALES].get(product.pk, 0)
            row["sales"] = {"plan": plan, "fact": sold.get(product.pk, 0)}
            row["sales"]["percent"] = percent(row["sales"]["fact"], plan)
        if any(part["plan"] or part["fact"] for key, part in row.items() if key != "product"):
            rows.append(row)
    totals = {}
    for key in ("production", "sales"):
        if (key == "sales" and show_sales) or (key == "production" and show_production):
            plan = sum(row[key]["plan"] for row in rows)
            fact = sum(row[key]["fact"] for row in rows)
            totals[key] = {"plan": plan, "fact": fact, "percent": percent(fact, plan)}
    return {
        "rows": rows,
        "totals": totals,
        "month": month_start,
        # How far through the month the period's end is, to judge the percentages against.
        "month_progress": round(month_end.day * 100 / days_in_month),
    }


def debts(on_date) -> dict:
    from finance_saber.sales import services

    rows = services.debt_rows(on_date=on_date)
    debtors = sorted((row for row in rows if row.balance > 0), key=lambda row: -row.balance)
    creditors = [row for row in rows if row.balance < 0]
    return {
        "receivable": sum((row.balance for row in debtors), ZERO),
        "payable": -sum((row.balance for row in creditors), ZERO),
        "debtor_count": len(debtors),
        "creditor_count": len(creditors),
        "top": debtors[:5],
    }


def stock(on_date) -> dict:
    from finance_saber.production.models import Material, Product, material_stock, product_stock

    materials = material_stock(on_date)
    goods = product_stock(on_date)
    short = [
        {"material": material, "stock": materials.get(material.pk, ZERO)}
        for material in Material.objects.filter(is_active=True)
        if materials.get(material.pk, ZERO) < 0
        or (material.min_stock and materials.get(material.pk, ZERO) < material.min_stock)
    ]
    return {
        "short": sorted(short, key=lambda row: row["stock"])[:6],
        "short_count": len(short),
        "goods_units": sum(goods.values(), ZERO),
        "goods": [
            {"product": product, "stock": goods.get(product.pk, ZERO)}
            for product in Product.objects.filter(is_active=True)
            if goods.get(product.pk)
        ],
    }


# Plans for the week and by region --------------------------------------------------------


def week(period: Period, rate) -> dict:
    """The cash plan against fact for each day of the week the period ends in (the "1-week" sheet)."""
    from finance_saber.planning.services import cash_plan_grid, week_of

    days = week_of(period.date_to)
    _sections, totals = cash_plan_grid(days, rate=rate)
    return {
        "days": [
            {"date": day, "income": totals["income"]["days"][i], "expense": totals["expense"]["days"][i]}
            for i, day in enumerate(days)
        ],
        "income": totals["income"],
        "expense": totals["expense"],
    }


def collections(period: Period) -> dict:
    """Collections plan against bank receipts by region for the period (the "Sells plan" sheet)."""
    from finance_saber.cashbook.models import Region
    from finance_saber.planning.models import CollectionPlan
    from finance_saber.planning.services import collections_fact

    plans = dict(
        CollectionPlan.objects.filter(date__gte=period.date_from, date__lte=period.date_to)
        .values_list("region_id")
        .annotate(total=Sum("amount"))
    )
    facts = defaultdict(Decimal)
    for (region_id, _day), amount in collections_fact(period.date_from, period.date_to).items():
        facts[region_id] += amount
    rows = [
        {
            "region": region,
            "plan": plans.get(region.pk, ZERO),
            "fact": facts.get(region.pk, ZERO),
            "percent": percent(facts.get(region.pk, ZERO), plans.get(region.pk, ZERO)),
        }
        for region in Region.objects.filter(pk__in={*plans, *facts}).order_by("manager", "name")
    ]
    plan, fact = sum((row["plan"] for row in rows), ZERO), sum((row["fact"] for row in rows), ZERO)
    return {"rows": rows, "plan": plan, "fact": fact, "percent": percent(fact, plan)}


# Putting it together -------------------------------------------------------------------


def build(user, period: Period) -> dict:
    """The dashboard's blocks this user may see, for the period."""
    can = user.has_perm
    rate = exchange_rate(period.date_to)
    context = {"period": period, "rate": rate, "presets": PRESETS}
    if can("cashbook.view_operation"):
        rows = accounts(period, rate)
        context["accounts"] = rows
        context["money"] = money_totals(rows, rate)
        context["cash_flow"] = cash_flow(period, rate)
        context["net_negative"] = context["cash_flow"]["net"] < 0
        context["net_tone"] = "danger" if context["net_negative"] else "success"
        context["chart"] = daily_chart(period, rate)
    if can("sales.view_sale"):
        context["sales"] = sales_summary(period)
    if can("sales.view_sale") and can("sales.view_client"):
        context["debts"] = debts(period.date_to)
    show_sales = can("sales.view_plan")
    show_production = can("sales.view_plan") and can("production.view_productionrun")
    if show_sales or show_production:
        context["products"] = products_month(period, show_sales, show_production)
    if can("production.view_material"):
        context["stock"] = stock(period.date_to)
    if can("planning.view_cashplan") and can("cashbook.view_operation"):
        context["week"] = week(period, rate)
    if can("planning.view_collectionplan") and can("cashbook.view_operation"):
        context["collections"] = collections(period)
    return context
