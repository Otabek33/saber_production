"""A partner's settlement worked out from the documents and the money, in UZS.

A partner is a head company with its filials, a customer, a supplier or both. From
our side (as the акт сверки shows it):

* debit, what the partner owes us more: our shipments to it, and money we paid it
  (paying a supplier, or a refund);
* credit, what it owes us less: its returns, what it supplied us (material receipts
  with their amounts, and bills for anything else), and money it paid us.

Positive: the partner owes us; negative: we owe the partner.

Shipments and returns come from Production › Shipments, receipts from Production ›
Material receipts, bills from Purchases › Bills, money from the cash book: a bank line
counts for the company its counterparty is connected to, a cash desk line for the
partner chosen on it. USD amounts count in UZS at the line's rate, else the day's
exchange rate. A partner's opening balance (OpeningBalance) is where counting starts:
what came before its date is in that figure. A partner without one is counted from
the first document in the system (counting_start): money from before it settled debts
the system doesn't know.
"""

import bisect
import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Min, Q, Sum
from django.urls import reverse
from django.utils.translation import gettext as _

from finance_saber.cashbook.models import ACCOUNT_CURRENCY, Company, ExchangeRate, Kind, Operation
from finance_saber.production.models import MaterialReceipt, ProductShipment
from finance_saber.purchases.models import Bill

from .models import OpeningBalance

ZERO = Decimal(0)
DEBIT_KINDS = {"shipment", "paid"}  # the others (return, receipt, bill, received) are credit
ORDER = {"shipment": 0, "receipt": 0, "bill": 0, "return": 1, "paid": 2, "received": 2}  # within a day


@dataclass
class Entry:
    date: datetime.date
    kind: str  # shipment, return, receipt, bill, received, paid
    company: Company  # the partner itself or one of its filials
    text: str
    number: str = ""
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    url: str = ""
    balance: Decimal = ZERO  # after this entry


@dataclass
class Statement:
    partner: Company
    date_from: datetime.date | None
    date_to: datetime.date | None
    opening: Decimal
    entries: list[Entry]
    opening_balance: OpeningBalance | None = None

    @property
    def opening_credit(self) -> Decimal:
        """A negative opening balance as the credit it is (we owe the partner)."""
        return -self.opening if self.opening < 0 else ZERO

    @property
    def debit(self) -> Decimal:
        return sum((entry.debit for entry in self.entries), ZERO)

    @property
    def credit(self) -> Decimal:
        return sum((entry.credit for entry in self.entries), ZERO)

    @property
    def closing(self) -> Decimal:
        return self.opening + self.debit - self.credit


class Rates:
    """UZS per USD on a date: the latest exchange rate from that day back (None before the first)."""

    def __init__(self):
        rows = list(ExchangeRate.objects.order_by("date").values_list("date", "rate"))
        self.dates, self.rates = [row[0] for row in rows], [row[1] for row in rows]

    def on(self, date) -> Decimal | None:
        index = bisect.bisect_right(self.dates, date) - 1
        return self.rates[index] if index >= 0 else None


def in_uzs(operation: Operation, rates: Rates) -> Decimal | None:
    if ACCOUNT_CURRENCY[operation.account] == "UZS":
        return operation.amount
    rate = operation.rate or rates.on(operation.date)
    return (operation.amount * rate).quantize(Decimal("0.01")) if rate else None


def partner_operations(company_ids):
    """Income and expense lines of the cash book that count for these companies."""
    return (
        Operation.objects.posted()
        .filter(kind__in=[Kind.INCOME, Kind.EXPENSE])
        .filter(Q(partner_id__in=company_ids) | Q(partner__isnull=True, counterparty__company_id__in=company_ids))
    )


@dataclass
class Document:
    date: datetime.date
    kind: str  # shipment, return, receipt, bill
    company_id: int
    amount: Decimal
    number: str
    text: str
    url: str


def documents(company_ids=None, start=None, end=None) -> list[Document]:
    """Shipments, returns, material receipts with amounts and bills (of these companies, or all), not cancelled."""
    shipments = ProductShipment.objects.posted().annotate(amount_total=Sum("lines__amount"))
    receipts = MaterialReceipt.objects.posted().annotate(amount_total=Sum("lines__amount")).filter(amount_total__gt=0)
    bills = Bill.objects.posted()
    sources = {"shipments": shipments, "receipts": receipts, "bills": bills}
    owner = {"shipments": "customer_id", "receipts": "supplier_id", "bills": "supplier_id"}
    for name, queryset in sources.items():
        if company_ids is not None:
            queryset = queryset.filter(**{f"{owner[name]}__in": company_ids})
        if start:
            queryset = queryset.filter(date__gte=start)
        if end:
            queryset = queryset.filter(date__lte=end)
        sources[name] = queryset

    found = []
    for shipment in sources["shipments"]:
        label = _("Return № %(number)s") if shipment.is_return else _("Shipment № %(number)s")
        text = label % {"number": shipment.number}
        text = f"{text} ({shipment.channel})" if shipment.channel else text
        found.append(Document(
            shipment.date, "return" if shipment.is_return else "shipment", shipment.customer_id,
            shipment.amount_total or ZERO, shipment.number, text,
            reverse("production:shipment_detail", args=[shipment.pk]),
        ))  # fmt: skip
    for receipt in sources["receipts"]:
        found.append(Document(
            receipt.date, "receipt", receipt.supplier_id, receipt.amount_total, receipt.number,
            _("Material receipt № %(number)s") % {"number": receipt.number},
            reverse("production:receipt", args=[receipt.pk]),
        ))  # fmt: skip
    for bill in sources["bills"]:
        found.append(Document(
            bill.date, "bill", bill.supplier_id, bill.amount, bill.number,
            _("Bill № %(number)s: %(what)s") % {"number": bill.number, "what": bill.description},
            bill.get_absolute_url(),
        ))  # fmt: skip
    return found


def entries(company_ids, start=None, end=None) -> list[Entry]:
    """Every document and payment of these companies from ``start`` to ``end``, in date order."""
    companies = Company.objects.in_bulk(company_ids)
    found = [
        Entry(
            date=document.date, kind=document.kind, company=companies[document.company_id], text=document.text,
            number=document.number, url=document.url,
            debit=document.amount if document.kind in DEBIT_KINDS else ZERO,
            credit=ZERO if document.kind in DEBIT_KINDS else document.amount,
        )
        for document in documents(company_ids, start, end)
    ]  # fmt: skip
    operations = partner_operations(company_ids).select_related("desk", "category", "counterparty")
    if start:
        operations = operations.filter(date__gte=start)
    if end:
        operations = operations.filter(date__lte=end)
    rates = Rates()
    for operation in operations:
        amount = in_uzs(operation, rates)
        if amount is None:
            continue  # USD with no rate known for that day
        received = operation.kind == Kind.INCOME
        found.append(
            Entry(
                date=operation.date,
                kind="received" if received else "paid",
                company=companies[operation.partner_id or operation.counterparty.company_id],
                text=f"{operation.desk.name}: {operation.note or operation.category.name}",
                number=operation.document_number,
                debit=ZERO if received else amount,
                credit=amount if received else ZERO,
                url=reverse("cashbook:operation_edit", args=[operation.desk.slug, operation.pk]),
            )
        )
    found.sort(key=lambda entry: (entry.date, ORDER[entry.kind], entry.number))
    return found


def counting_start() -> datetime.date | None:
    """Where a partner without an opening balance is counted from: the first document in the system."""
    firsts = [
        ProductShipment.objects.posted().aggregate(first=Min("date"))["first"],
        MaterialReceipt.objects.posted().filter(lines__amount__gt=0).aggregate(first=Min("date"))["first"],
        Bill.objects.posted().aggregate(first=Min("date"))["first"],
    ]
    return min((first for first in firsts if first), default=None)


def statement(partner: Company, date_from=None, date_to=None) -> Statement:
    """The partner's settlement for a period: its balance at the start, then every entry with the balance after it."""
    opening_balance = OpeningBalance.objects.filter(company=partner).first()
    start = opening_balance.date if opening_balance else counting_start()
    if date_from is None or (start and date_from < start):
        date_from = start
    every = entries(partner.descendant_ids(), start, date_to)
    if date_from is None and every:
        date_from = every[0].date
    opening = opening_balance.amount if opening_balance else ZERO
    opening += sum((entry.debit - entry.credit for entry in every if date_from and entry.date < date_from), ZERO)
    period = [entry for entry in every if not date_from or entry.date >= date_from]
    balance = opening
    for entry in period:
        balance += entry.debit - entry.credit
        entry.balance = balance
    return Statement(partner, date_from, date_to, opening, period, opening_balance)


# --- Every partner's balance ----------------------------------------------------------------


@dataclass
class BalanceRow:
    partner: Company
    opening: Decimal = ZERO
    shipped: Decimal = ZERO
    returned: Decimal = ZERO
    purchased: Decimal = ZERO  # material receipts and bills: what it supplied us
    received: Decimal = ZERO
    paid: Decimal = ZERO
    last_date: datetime.date | None = None
    filials: set = field(default_factory=set)  # filials with documents or money

    @property
    def balance(self) -> Decimal:
        return self.opening + self.shipped - self.returned - self.purchased - self.received + self.paid

    def touch(self, date):
        if self.last_date is None or date > self.last_date:
            self.last_date = date


def head_ids() -> dict[int, int]:
    """{company id: id of the head company at the top of its chain}."""
    parents = dict(Company.objects.values_list("pk", "parent_id"))

    def top(pk):
        seen = {pk}
        while parents.get(pk) and parents[pk] not in seen:
            pk = parents[pk]
            seen.add(pk)
        return pk

    return {pk: top(pk) for pk in parents}


def balances(on_date=None) -> list[BalanceRow]:
    """Every partner with documents or an opening balance, and its balance at the end of ``on_date``.

    Money alone (taxes, salaries, a bank's fees) doesn't make a company a partner here.
    """
    heads = head_ids()
    openings = {balance.company_id: balance for balance in OpeningBalance.objects.all()}
    default_start = counting_start()
    every = documents(end=on_date)
    rows = {pk: BalanceRow(partner=None) for pk in {heads[document.company_id] for document in every} | set(openings)}

    def counts(company_id, date) -> BalanceRow | None:
        pk = heads[company_id]
        row = rows.get(pk)
        start = openings[pk].date if pk in openings else default_start
        if row is None or (start and date < start) or (on_date and date > on_date):
            return None
        if company_id != pk:
            row.filials.add(company_id)
        row.touch(date)
        return row

    for pk, opening in openings.items():
        rows[pk].opening = opening.amount
    totals = {"shipment": "shipped", "return": "returned", "receipt": "purchased", "bill": "purchased"}
    for document in every:
        if row := counts(document.company_id, document.date):
            setattr(row, totals[document.kind], getattr(row, totals[document.kind]) + document.amount)
    group = [pk for pk, head in heads.items() if head in rows]
    operations = (
        partner_operations(group)
        .select_related("counterparty")
        .only("date", "kind", "amount", "account", "rate", "partner_id", "counterparty__company_id")
    )
    rates = Rates()
    for operation in operations:
        if not (row := counts(operation.partner_id or operation.counterparty.company_id, operation.date)):
            continue
        amount = in_uzs(operation, rates)
        if amount is None:
            continue
        if operation.kind == Kind.INCOME:
            row.received += amount
        else:
            row.paid += amount
    partners = Company.objects.in_bulk(rows)
    for pk, row in rows.items():
        row.partner = partners[pk]
    return sorted(rows.values(), key=lambda row: row.partner.name.casefold())
