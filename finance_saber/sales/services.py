"""Recording sales, what clients owe, and reading SmartUp exports and the yearly plan."""

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Max, Min, Q, Sum
from django.utils.translation import gettext as _
from openpyxl import load_workbook

from finance_saber.cashbook.models import Kind, Operation
from finance_saber.production.importer import clean, key
from finance_saber.production.models import MaterialMove, Product, ProductAlias, ProductionRun, product_lookup

from .models import Channel, Client, Plan, Sale

# Product names in SmartUp and the Plan sheet that differ from the products' own names.
PRODUCT_ALIASES = {
    "7 Energy 450 ML 1/24": "7 Energy Drink 0,450 л",
    "ЖБ 450 Мл Sakura Зеленый чай с мёдом": "Green Tea мёд 0,450 л",
    "ЖБ 450 Мл Sakura Черный чай апельсин-корица": "Blak Tea Апельсин-корица 0,450 л",
    "ЖБ 450 Мл Sakura Черный чай лимон-имбирь-мёд": "Blak Tea лимон-мёд-имбир 0,450 л",
    "ЖБ 450 Мл Sakura Черный чай яблоко-корица-гвоздика": "Blak Tea Яблока - корица-гвоздика 0,450 л",
}


class SheetError(Exception):
    pass


def seed_product_aliases() -> int:
    """Create the known SmartUp / Plan aliases for products that exist. Returns how many were added."""
    products = {key(name): name for name in PRODUCT_ALIASES.values()}
    by_key = {key(p.name): p for p in Product.objects.all() if key(p.name) in products}
    added = 0
    for alias, product_name in PRODUCT_ALIASES.items():
        product = by_key.get(key(product_name))
        if product:
            added += ProductAlias.objects.get_or_create(name=alias, defaults={"product": product, "source": "SmartUp"})[
                1
            ]
    return added


# Recording sales -------------------------------------------------------------------------


def stock_tracked_from() -> datetime.date | None:
    """Finished-goods stock is tracked from the warehouse's opening count; earlier sales are history."""
    return MaterialMove.objects.filter(kind=MaterialMove.Kind.OPENING).aggregate(first=Min("date"))["first"]


def is_duplicate(date, client, product, amount, exclude_pk=None) -> bool:
    sales = Sale.objects.posted().filter(date=date, client=client, product=product, amount=amount)
    if exclude_pk:
        sales = sales.exclude(pk=exclude_pk)
    return sales.exists()


def record_sale(sale: Sale) -> Sale:
    """Save a new or changed sale and its stock movement."""
    start = stock_tracked_from()
    sale.affects_stock = start is None or sale.date >= start
    with transaction.atomic():
        sale.save()
        sale.rebuild_moves()
    return sale


def set_cancelled(sale: Sale, cancelled: bool) -> None:
    """Cancel a sale (it stops counting) or restore it, unless restoring would duplicate another."""
    if not cancelled and is_duplicate(sale.date, sale.client, sale.product, sale.amount, exclude_pk=sale.pk):
        raise ValueError(_("An identical sale is already recorded for that day."))
    sale.is_cancelled = cancelled
    with transaction.atomic():
        sale.save(update_fields=["is_cancelled"])
        sale.rebuild_moves()


def client_for(name: str, create=True) -> Client | None:
    """The client with this name, ignoring case and spacing; created if new."""
    name = clean(name)
    wanted = key(name)
    for client in Client.objects.filter(name__iexact=name):
        return client
    for client in Client.objects.all().only("pk", "name"):
        if key(client.name) == wanted:
            return client
    return Client.objects.create(name=name) if create else None


# What clients owe ------------------------------------------------------------------------


@dataclass
class DebtRow:
    """One debtor or creditor: a payer with all its clients, or a client without a payer."""

    name: str
    clients: list = field(default_factory=list)
    payer: object = None
    opening: Decimal = Decimal(0)
    shipped: Decimal = Decimal(0)
    paid: Decimal = Decimal(0)  # received from the client, net of refunds
    last_sale: datetime.date | None = None

    @property
    def balance(self) -> Decimal:
        return self.opening + self.shipped - self.paid

    @property
    def client(self):
        return self.clients[0] if len(self.clients) == 1 and self.payer is None else None


def debts_from() -> datetime.date | None:
    """Debts are counted from the first recorded sale; earlier bank payments settled older debts."""
    return Sale.objects.posted().aggregate(first=Min("date"))["first"]


def payments(payer_ids, start, end=None) -> dict:
    """{counterparty id: received − refunded} from the cash book, from ``start`` up to ``end``."""
    if not payer_ids:
        return {}
    operations = Operation.objects.posted().filter(counterparty_id__in=payer_ids, kind__in=[Kind.INCOME, Kind.EXPENSE])
    if start:
        operations = operations.filter(date__gte=start)
    if end:
        operations = operations.filter(date__lte=end)
    totals = defaultdict(Decimal)
    for row in operations.values("counterparty_id", "kind").annotate(total=Sum("amount")):
        sign = 1 if row["kind"] == Kind.INCOME else -1
        totals[row["counterparty_id"]] += sign * row["total"]
    return totals


def debt_rows(on_date=None) -> list[DebtRow]:
    """Every client (grouped by payer) with what it was shipped, paid and owes, at the end of ``on_date``."""
    start = debts_from()
    sales = Sale.objects.posted()
    if on_date:
        sales = sales.filter(date__lte=on_date)
    shipped = {
        row["client_id"]: (row["total"], row["last"])
        for row in sales.values("client_id").annotate(total=Sum("amount"), last=Max("date"))
    }
    groups: dict[tuple, DebtRow] = {}
    for client in Client.objects.select_related("payer").order_by("name"):
        group_key = ("payer", client.payer_id) if client.payer_id else ("client", client.pk)
        row = groups.get(group_key)
        if row is None:
            row = groups[group_key] = DebtRow(
                name=client.payer.name if client.payer else client.name, payer=client.payer
            )
        row.clients.append(client)
        row.opening += client.opening_balance
        total, client_last = shipped.get(client.pk, (Decimal(0), None))
        row.shipped += total
        if client_last and (row.last_sale is None or client_last > row.last_sale):
            row.last_sale = client_last
    paid = payments([row.payer.pk for row in groups.values() if row.payer], start, end=on_date)
    for row in groups.values():
        if row.payer:
            row.paid = paid.get(row.payer.pk, Decimal(0))
    return [row for row in groups.values() if row.opening or row.shipped or row.paid]


@dataclass
class StatementLine:
    date: datetime.date
    kind: str  # "opening", "sale", "payment", "refund"
    text: str
    debit: Decimal = Decimal(0)  # increases what is owed
    credit: Decimal = Decimal(0)  # decreases it
    balance: Decimal = Decimal(0)
    sale: Sale | None = None
    operation: Operation | None = None


def statement(clients: list[Client], payer=None) -> list[StatementLine]:
    """Shipments and payments of a debtor in date order, with the balance after each."""
    start = debts_from()
    lines = []
    opening = sum((client.opening_balance for client in clients), Decimal(0))
    if opening:
        lines.append(StatementLine(start or datetime.date.min, "opening", _("Opening balance"), debit=opening))
    for sale in Sale.objects.posted().filter(client__in=clients).select_related("product", "client"):
        text = f"{sale.product.name} × {sale.quantity}"
        if len(clients) > 1:
            text = f"{sale.client.name}: {text}"
        lines.append(StatementLine(sale.date, "sale", text, debit=sale.amount, sale=sale))
    if payer:
        operations = Operation.objects.posted().filter(counterparty=payer, kind__in=[Kind.INCOME, Kind.EXPENSE])
        if start:
            operations = operations.filter(date__gte=start)
        for operation in operations.select_related("desk"):
            if operation.kind == Kind.INCOME:
                lines.append(
                    StatementLine(
                        operation.date, "payment", operation.note, credit=operation.amount, operation=operation
                    )
                )
            else:
                lines.append(
                    StatementLine(operation.date, "refund", operation.note, debit=operation.amount, operation=operation)
                )
    order = {"opening": 0, "sale": 1, "refund": 1, "payment": 2}
    lines.sort(key=lambda line: (line.date, order[line.kind]))
    balance = Decimal(0)
    for line in lines:
        balance += line.debit - line.credit
        line.balance = balance
    return lines


# SmartUp export --------------------------------------------------------------------------

SMARTUP_COLUMNS = {
    "date": "датаотгрузки",
    "channel": "названиепроекта",
    "product": "тмц",
    "client": "клиент",
    "quantity": "количество",
    "amount": "сумма",
}


@dataclass
class SaleRow:
    row: int
    date: datetime.date
    client: str
    product: str
    channel: str
    quantity: int
    amount: Decimal


@dataclass
class SmartUpFile:
    rows: list[SaleRow]
    export_channels: set[str]  # channels the sheet's lookup table marks "экс"
    errors: list[str]


def _open_workbook(file):
    """An .xlsx path or upload, read-only with formula values; the caller closes it."""
    try:
        return load_workbook(file, data_only=True, read_only=True)
    except Exception as error:  # zip / xml errors: not a readable .xlsx
        label = getattr(file, "name", str(file))
        raise SheetError(_("%(file)s is not a readable .xlsx workbook.") % {"file": label}) from error


def _to_date(value) -> datetime.date | None:
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
    return None


def _to_decimal(value) -> Decimal | None:
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value.replace("\xa0", "").replace(" ", "").replace(",", "."))
        except InvalidOperation:
            return None
    return None


def read_smartup(file) -> SmartUpFile:
    """Sale lines from a SmartUp shipment export: the sheet with columns Дата отгрузки … Сумма.

    A sheet named "SmartUp" is used if there is one, else the first sheet with those headers.
    """
    workbook = _open_workbook(file)
    try:
        names = (["SmartUp"] if "SmartUp" in workbook.sheetnames else []) + workbook.sheetnames
        for name in dict.fromkeys(names):
            rows = list(workbook[name].iter_rows(values_only=True))
            parsed = _parse_smartup(rows)
            if parsed is not None:
                return parsed
    finally:
        workbook.close()
    raise SheetError(_("No sheet with the SmartUp columns (Дата отгрузки, ТМЦ, Клиент, Количество, Сумма) was found."))


def _parse_smartup(rows) -> SmartUpFile | None:
    for header_index, header in enumerate(rows[:20]):  # noqa: B007 (used after the loop breaks)
        found, export_lookup = {}, None
        for column, title in enumerate(header):
            title_key = key(title)
            for field_name, prefix in SMARTUP_COLUMNS.items():
                if title_key.startswith(key(prefix)):
                    if field_name in found:
                        if field_name == "channel":  # a second "Название проекта": the channel → market table
                            export_lookup = column
                        continue
                    found[field_name] = column
        if len(found) == len(SMARTUP_COLUMNS):
            break
    else:
        return None

    result = SmartUpFile(rows=[], export_channels=set(), errors=[])
    for number, values in enumerate(rows[header_index + 1 :], start=header_index + 2):
        if export_lookup is not None and export_lookup + 1 < len(values):
            channel, market = values[export_lookup], values[export_lookup + 1]
            if channel and str(market or "").strip().lower().startswith("эк"):
                result.export_channels.add(clean(channel))
        cell = {name: values[column] if column < len(values) else None for name, column in found.items()}
        if cell["quantity"] in (None, "") and cell["amount"] in (None, ""):
            continue  # an empty line, or a client/product listed with nothing shipped
        date, quantity, amount = _to_date(cell["date"]), _to_decimal(cell["quantity"]), _to_decimal(cell["amount"])
        problems = []
        if date is None:
            problems.append(_("date"))
        if not clean(cell["client"]):
            problems.append(_("client"))
        if not clean(cell["product"]):
            problems.append(_("product"))
        if quantity is None or quantity <= 0 or quantity != quantity.to_integral_value():
            problems.append(_("quantity"))
        if amount is None or amount < 0:
            problems.append(_("amount"))
        if problems:
            result.errors.append(_("Row %(row)s: check %(fields)s.") % {"row": number, "fields": ", ".join(problems)})
            continue
        result.rows.append(
            SaleRow(
                row=number,
                date=date,
                client=clean(cell["client"]),
                product=clean(cell["product"]),
                channel=clean(cell["channel"]),
                quantity=int(quantity),
                amount=amount.quantize(Decimal("0.01")),
            )
        )
    return result


@dataclass
class SalesImportReport:
    rows: int = 0
    created: int = 0
    duplicates: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    unknown_products: list[str] = field(default_factory=list)
    new_clients: list[str] = field(default_factory=list)
    new_channels: list[str] = field(default_factory=list)
    quantity: int = 0
    amount: Decimal = Decimal(0)
    first_date: datetime.date | None = None
    last_date: datetime.date | None = None
    saved: bool = False


class _DryRun(Exception):
    pass


def import_sales(parsed: SmartUpFile, user=None, dry_run=False) -> SalesImportReport:
    """Record the file's sale lines. Duplicates (same day, client, product and total price, in the
    database or earlier in the file) are skipped. Nothing is saved if a product is unknown or a row
    is unreadable, or for a dry run."""
    report = SalesImportReport(rows=len(parsed.rows), errors=list(parsed.errors))
    seed_product_aliases()
    products = product_lookup()
    report.unknown_products = sorted({row.product for row in parsed.rows if key(row.product) not in products})
    try:
        with transaction.atomic():
            channels = {key(c.name): c for c in Channel.objects.all()}
            clients, seen = {}, set()
            for row in parsed.rows:
                product = products.get(key(row.product))
                if product is None:
                    continue
                client = clients.get(key(row.client))
                if client is None:
                    existing = client_for(row.client, create=False)
                    client = existing or Client.objects.create(name=row.client)
                    if existing is None:
                        report.new_clients.append(row.client)
                    clients[key(row.client)] = client
                channel = None
                if row.channel:
                    channel = channels.get(key(row.channel))
                    if channel is None:
                        channel = Channel.objects.create(
                            name=row.channel, is_export=row.channel in parsed.export_channels
                        )
                        channels[key(row.channel)] = channel
                        report.new_channels.append(row.channel)
                signature = (row.date, client.pk, product.pk, row.amount)
                description = f"{row.date:%d.%m.%Y} · {row.client} · {row.product} · {row.amount:,.2f}".replace(
                    ",", " "
                )
                if signature in seen or is_duplicate(row.date, client, product, row.amount):
                    report.duplicates.append(_("Row %(row)s: %(sale)s") % {"row": row.row, "sale": description})
                    continue
                seen.add(signature)
                record_sale(
                    Sale(
                        date=row.date,
                        client=client,
                        product=product,
                        channel=channel,
                        quantity=row.quantity,
                        amount=row.amount,
                        source=Sale.Source.EXCEL,
                        created_by=user,
                    )
                )
                report.created += 1
                report.quantity += row.quantity
                report.amount += row.amount
                report.first_date = min(filter(None, (report.first_date, row.date)))
                report.last_date = max(filter(None, (report.last_date, row.date)))
            if dry_run or report.unknown_products or report.errors:
                raise _DryRun
            report.saved = True
    except _DryRun:
        pass
    except IntegrityError as error:  # a sale recorded at the same moment by someone else
        report.errors.append(str(error))
    return report


# The yearly plan ("Plan 2026") -----------------------------------------------------------

PLAN_TITLES = {"планреализации": Plan.Kind.SALES, "планпроизводства": Plan.Kind.PRODUCTION}


@dataclass
class PlanFile:
    year: int | None
    # {kind: {product name: {month: quantity}}}
    blocks: dict = field(default_factory=dict)


def read_plan(file, sheet_name=None) -> PlanFile:
    """The sales and production blocks of a yearly plan sheet: a title row ("Всего план реализации
    на 2026 год"), a header with the months as dates from column D, then one row per product
    (name in C)."""
    workbook = _open_workbook(file)
    try:
        names = [sheet_name] if sheet_name else [n for n in workbook.sheetnames if key(n).startswith("plan")]
        names += [n for n in workbook.sheetnames if n not in names]
        for name in names:
            parsed = _parse_plan(list(workbook[name].iter_rows(values_only=True)))
            if parsed.blocks:
                return parsed
    finally:
        workbook.close()
    raise SheetError(_("No plan sheet found: expected a block titled “Всего план реализации на … год”."))


def _parse_plan(rows) -> PlanFile:
    result = PlanFile(year=None)
    index = 0
    while index < len(rows):
        title = key(" ".join(str(value) for value in rows[index] if isinstance(value, str)))
        kind = next((kind for prefix, kind in PLAN_TITLES.items() if key(prefix) in title), None)
        if kind is None or kind in result.blocks:
            index += 1
            continue
        header = rows[index + 1] if index + 1 < len(rows) else ()
        months = {}
        for column, value in enumerate(header):
            date = _to_date(value)
            if date:
                months[column] = date.month
                result.year = result.year or date.year
        block = {}
        row = index + 3  # title, header, "План" row
        while row < len(rows):
            values = rows[row]
            name = clean(values[2] if len(values) > 2 else "")
            if not name:
                break
            block[name] = {
                month: int(_to_decimal(values[column]) or 0)
                for column, month in months.items()
                if column < len(values) and _to_decimal(values[column]) is not None
            }
            row += 1
        result.blocks[kind] = block
        index = row
    return result


@dataclass
class PlanImportReport:
    year: int | None = None
    products: dict = field(default_factory=dict)  # {kind: number of products}
    values: int = 0
    unknown_products: list[str] = field(default_factory=list)
    saved: bool = False


@transaction.atomic
def import_plan(parsed: PlanFile, dry_run=False) -> PlanImportReport:
    """Write the plan's figures (replacing that year's earlier ones). Nothing is saved if a product is unknown."""
    report = PlanImportReport(year=parsed.year)
    seed_product_aliases()
    products = product_lookup()
    unknown = {name for block in parsed.blocks.values() for name in block if key(name) not in products}
    report.unknown_products = sorted(unknown)
    for kind, block in parsed.blocks.items():
        report.products[kind] = len(block)
        report.values += sum(len(months) for months in block.values())
    if unknown or dry_run or not parsed.year:
        return report
    for kind, block in parsed.blocks.items():
        for name, months in block.items():
            for month, quantity in months.items():
                Plan.objects.update_or_create(
                    kind=kind,
                    year=parsed.year,
                    month=month,
                    product=products[key(name)],
                    defaults={"quantity": max(quantity, 0)},
                )
    report.saved = True
    return report


def plan_vs_fact(kind: str, year: int):
    """Rows of {product, months: [(plan, fact)…12], plan, fact} and the month totals."""
    plans = defaultdict(dict)
    for plan in Plan.objects.filter(kind=kind, year=year):
        plans[plan.product_id][plan.month] = plan.quantity
    facts = defaultdict(dict)
    if kind == Plan.Kind.SALES:
        fact_rows = (
            Sale.objects.posted()
            .filter(date__year=year)
            .values("product_id", "date__month")
            .annotate(total=Sum("quantity"))
        )
    else:
        fact_rows = (
            ProductionRun.objects.filter(date__year=year, is_cancelled=False)
            .values("product_id", "date__month")
            .annotate(total=Sum("quantity"))
        )
    for row in fact_rows:
        facts[row["product_id"]][row["date__month"]] = row["total"]
    rows = []
    for product in Product.objects.filter(Q(is_active=True) | Q(pk__in=plans) | Q(pk__in=facts)):
        months = [(plans[product.pk].get(m, 0), facts[product.pk].get(m, 0)) for m in range(1, 13)]
        rows.append(
            {
                "product": product,
                "months": months,
                "plan": sum(p for p, _f in months),
                "fact": sum(f for _p, f in months),
            }
        )
    totals = [(sum(row["months"][m][0] for row in rows), sum(row["months"][m][1] for row in rows)) for m in range(12)]
    return rows, totals
