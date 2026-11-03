"""Shipments from SmartUp's sales report («Конструктор отчётов по продажам»): read, match, load.

The report has a title block (period, organisation, filters), a header row — Дата
доставки (or Дата отгрузки), Организация, Название проекта, ТМЦ, Клиент, Количество
продажи, Сумма со скидкой/наценкой — and a closing ИТОГО row.

Each day, customer and project becomes one shipment document («SmartUp report»), its
lines the products with their quantity and amount (a product listed twice is added
up); its stock movements take the goods out of the warehouse. Loading a report again
finds each document by its key and updates its lines; a document cancelled here stays
cancelled. Products are matched by name, sales name or alias (product_lookup),
customers by company name or alias. Names the user matches by hand are remembered as
aliases, and names that aren't our products (resold water, say) as skipped names, so
the next report is matched by itself.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import openpyxl
from django.db import transaction
from django.utils.translation import gettext as _

from finance_saber.cashbook.models import Company, CompanyAlias

from .importer import clean, key
from .models import Product, ProductAlias, ProductShipment, ProductShipmentLine, SkippedName, product_lookup

SOURCE = "SmartUp"
COLUMNS = {  # field -> header prefixes (as matching keys)
    "date": ("датадоставки", "датаотгрузки"),
    "channel": ("названиепроекта",),
    "product": ("тмц",),
    "customer": ("клиент",),
    "quantity": ("количество",),
    "amount": ("сумма",),
}
TOTAL = key("ИТОГО")


class ReportError(Exception):
    """The file isn't a readable SmartUp sales report."""


@dataclass
class ReportRow:
    row: int
    date: datetime.date
    channel: str
    product: str
    customer: str
    quantity: int
    amount: Decimal

    def to_json(self) -> dict:
        return {**self.__dict__, "date": self.date.isoformat(), "amount": str(self.amount)}

    @classmethod
    def from_json(cls, data: dict) -> "ReportRow":
        return cls(**{**data, "date": datetime.date.fromisoformat(data["date"]), "amount": Decimal(data["amount"])})


@dataclass
class Report:
    title: list[str] = field(default_factory=list)  # the lines above the table: period, filters
    rows: list[ReportRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def read_report(file) -> Report:
    """The report's lines; rows that can't be read are listed in ``errors``."""
    try:
        workbook = openpyxl.load_workbook(file, data_only=True, read_only=True)
    except Exception as error:  # not a readable .xlsx
        raise ReportError(_("The file is not a readable .xlsx workbook.")) from error
    try:
        for sheet in workbook.worksheets:
            report = _parse(list(sheet.iter_rows(values_only=True)))
            if report is not None:
                return report
    finally:
        workbook.close()
    raise ReportError(
        _("No SmartUp sales report found: the file needs the columns Дата доставки, ТМЦ, Клиент, Количество, Сумма.")
    )


def _columns(header) -> dict[str, int] | None:
    found = {}
    for column, title in enumerate(header):
        title_key = key(title)
        for name, prefixes in COLUMNS.items():
            if name not in found and title_key and title_key.startswith(tuple(map(key, prefixes))):
                found[name] = column
                break
    return found if len(found) == len(COLUMNS) else None


def _parse(rows) -> Report | None:
    for index, header in enumerate(rows[:30]):  # noqa: B007 (used after the loop breaks)
        if columns := _columns(header):
            break
    else:
        return None
    report = Report(title=[clean(row[0]) for row in rows[:index] if row and clean(row[0])])
    for number, values in enumerate(rows[index + 1 :], start=index + 2):
        cell = {name: values[column] if column < len(values) else None for name, column in columns.items()}
        if not any(clean(value) for value in cell.values()) or key(cell["date"]) == TOTAL:
            continue
        date, quantity, amount = _date(cell["date"]), _number(cell["quantity"]), _number(cell["amount"])
        if not quantity and not amount:
            continue  # a product listed with nothing shipped
        # A negative quantity is a return: its amount is negative too (or nothing).
        problems = [
            label for label, bad in (
                (_("date"), date is None),
                (_("product"), not clean(cell["product"])),
                (_("customer"), not clean(cell["customer"])),
                (_("quantity"), not quantity or quantity != quantity.to_integral_value()),
                (_("amount"), amount is None or (quantity and amount and (amount < 0) != (quantity < 0))),
            ) if bad
        ]  # fmt: skip
        if problems:
            report.errors.append(_("Row %(row)s: check %(fields)s.") % {"row": number, "fields": ", ".join(problems)})
            continue
        report.rows.append(
            ReportRow(
                row=number, date=date, channel=clean(cell["channel"]), product=clean(cell["product"]),
                customer=clean(cell["customer"]), quantity=int(quantity), amount=amount.quantize(Decimal("0.01")),
            )
        )  # fmt: skip
    return report


def _date(value) -> datetime.date | None:
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.datetime.strptime(clean(value), "%d.%m.%Y").date()
    except ValueError:
        return None


def _number(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(" ", "").replace("\xa0", "").replace(",", "."))
    except InvalidOperation:
        return None


# --- Matching names ---------------------------------------------------------------------


@dataclass
class Matching:
    products: dict[str, Product | None]  # the report's product name -> product (None: skipped, not ours)
    customers: dict[str, Company]
    unknown_products: list[str]
    unknown_customers: list[str]

    @property
    def complete(self) -> bool:
        return not self.unknown_products and not self.unknown_customers


def company_lookup() -> dict[str, Company]:
    """{matching key: Company} for every company name and alias (head companies win a shared name)."""
    found = {}
    for alias in CompanyAlias.objects.select_related("company"):
        found.setdefault(key(alias.name), alias.company)
    for company in Company.objects.order_by("parent_id", "pk"):  # heads (no parent) first
        found.setdefault(key(company.name), company)
    return found


def match(rows: list[ReportRow]) -> Matching:
    products, companies = product_lookup(), company_lookup()
    skipped = {key(name) for name in SkippedName.objects.values_list("name", flat=True)}
    matching = Matching({}, {}, [], [])
    for row in rows:
        if row.product not in matching.products:
            product = products.get(key(row.product))
            matching.products[row.product] = product
            if product is None and key(row.product) not in skipped:
                matching.unknown_products.append(row.product)
        if row.customer not in matching.customers and row.customer not in matching.unknown_customers:
            if company := companies.get(key(row.customer)):
                matching.customers[row.customer] = company
            else:
                matching.unknown_customers.append(row.customer)
    return matching


def remember_product(name: str, product: Product | None):
    """Match a report's product name from now on: to ``product``, or skip it (None: not our product)."""
    if product is None:
        SkippedName.objects.get_or_create(name=name, defaults={"source": SOURCE})
    else:
        ProductAlias.objects.update_or_create(name=name, defaults={"product": product, "source": SOURCE})


def remember_customer(name: str, company: Company | None) -> Company:
    """Match a report's customer name from now on: to ``company``, or to a new company of that name."""
    if company is None:
        return Company.objects.create(name=name)
    if key(company.name) != key(name):
        CompanyAlias.objects.update_or_create(name=name, defaults={"company": company, "source": SOURCE})
    return company


# --- The documents a report makes ---------------------------------------------------------


@dataclass
class Document:
    key: str
    date: datetime.date
    customer: Company
    channel: str
    is_return: bool
    lines: dict[Product, list]  # product -> [quantity, amount], both positive
    existing: ProductShipment | None = None
    status: str = "new"  # new, changed, same, cancelled (cancelled here: left alone)

    @property
    def quantity(self) -> int:
        return sum(quantity for quantity, _amount in self.lines.values())

    @property
    def amount(self) -> Decimal:
        return sum((amount for _quantity, amount in self.lines.values()), Decimal(0))


@dataclass
class Plan:
    documents: list[Document]
    skipped_rows: int = 0  # rows of products that aren't ours

    def count(self, status: str) -> int:
        return sum(document.status == status for document in self.documents)

    def stock_change(self) -> dict[int, int]:
        """{product id: units the load takes out of stock (negative: brings back)}, net of what is loaded already."""
        change = defaultdict(int)
        for document in self.documents:
            if document.status not in ("new", "changed"):
                continue
            sign = -1 if document.is_return else 1
            for product, (quantity, _amount) in document.lines.items():
                change[product.pk] += sign * quantity
            if document.existing:
                for line in document.existing.lines.all():
                    change[line.product_id] -= sign * line.quantity
        return change


def document_key(row: ReportRow) -> str:
    """One document per day, customer (as SmartUp names it), project and direction."""
    direction = ":return" if row.quantity < 0 else ""
    return f"smartup:{row.date.isoformat()}:{key(row.customer)}:{key(row.channel)}{direction}"


def plan(rows: list[ReportRow], matching: Matching) -> Plan:
    """The documents the report makes and how each compares with what is already loaded."""
    grouped: dict[str, Document] = {}
    skipped = 0
    for row in rows:
        product = matching.products.get(row.product)
        if product is None:
            skipped += 1
            continue
        document_id = document_key(row)
        document = grouped.get(document_id)
        if document is None:
            customer = matching.customers[row.customer]
            document = grouped[document_id] = Document(
                document_id, row.date, customer, row.channel, row.quantity < 0, {}
            )
        line = document.lines.setdefault(product, [0, Decimal(0)])
        line[0] += abs(row.quantity)
        line[1] += abs(row.amount)
    existing = ProductShipment.objects.filter(external_key__in=grouped).prefetch_related("lines")
    for shipment in existing:
        document = grouped[shipment.external_key]
        document.existing = shipment
        loaded = {line.product_id: [line.quantity, line.amount] for line in shipment.lines.all()}
        wanted = {product.pk: values for product, values in document.lines.items()}
        if shipment.is_cancelled:
            document.status = "cancelled"
        elif loaded == wanted and shipment.customer_id == document.customer.pk and shipment.date == document.date:
            document.status = "same"
        else:
            document.status = "changed"
    documents = sorted(grouped.values(), key=lambda document: (document.date, document.customer.name, document.channel))
    return Plan(documents, skipped)


def load(the_plan: Plan, user=None) -> Plan:
    """Save the new and changed documents and their stock movements, all or nothing."""
    with transaction.atomic():
        for document in the_plan.documents:
            if document.status not in ("new", "changed"):
                continue
            shipment = document.existing or ProductShipment(
                external_key=document.key,
                source=ProductShipment.Source.SMARTUP,
                is_return=document.is_return,
                created_by=user,
            )
            shipment.date, shipment.customer, shipment.channel = document.date, document.customer, document.channel
            shipment.note = f"SmartUp: {document.channel}"[:255] if document.channel else "SmartUp"
            shipment.save()
            shipment.assign_number()
            shipment.lines.all().delete()
            ProductShipmentLine.objects.bulk_create(
                ProductShipmentLine(shipment=shipment, product=product, quantity=quantity, amount=amount)
                for product, (quantity, amount) in document.lines.items()
            )
            shipment.rebuild_moves()
    return the_plan
