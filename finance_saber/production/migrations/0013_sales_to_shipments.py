"""The old Sales module's sales become shipments; its clients, companies of the handbook.

Each day, client and project's sales become one shipment document, as the SmartUp
report's import makes them (and with the same key, so loading that period's report
again updates them instead of adding them twice). A sale that didn't count in stock
(history from before the warehouse was counted) makes a shipment that doesn't either;
one that did moves its stock movement to the shipment.

A client is matched to a company by name or alias, or added as a company; its region
goes to the company if it has none, its bank payer is connected to the company if the
payer isn't connected yet, and its opening balance becomes the company's (from the
first sale's day) if it has none. Sales and clients stay in their tables, unused.
"""

from collections import defaultdict
from decimal import Decimal

from django.db import migrations

from finance_saber.production.importer import key

NOTE = "Из модуля «Продажи»"


def copy_sales(apps, schema_editor):
    Sale = apps.get_model("sales", "Sale")
    Client = apps.get_model("sales", "Client")
    Company = apps.get_model("cashbook", "Company")
    CompanyAlias = apps.get_model("cashbook", "CompanyAlias")
    Counterparty = apps.get_model("cashbook", "Counterparty")
    OpeningBalance = apps.get_model("settlements", "OpeningBalance")
    ProductShipment = apps.get_model("production", "ProductShipment")
    ProductShipmentLine = apps.get_model("production", "ProductShipmentLine")
    ProductMove = apps.get_model("production", "ProductMove")

    sales = list(Sale.objects.filter(is_cancelled=False).select_related("channel").order_by("date", "pk"))
    if not sales:
        return
    lookup = {}
    for alias in CompanyAlias.objects.select_related("company"):
        lookup.setdefault(key(alias.name), alias.company)
    for company in Company.objects.order_by("parent_id", "pk"):  # head companies (no parent) first
        lookup.setdefault(key(company.name), company)
    parents = dict(Company.objects.values_list("pk", "parent_id"))

    def head_of(pk):
        seen = {pk}
        while parents.get(pk) and parents[pk] not in seen:
            pk = parents[pk]
            seen.add(pk)
        return pk

    companies, openings = {}, defaultdict(Decimal)
    used = {sale.client_id for sale in sales}
    for client in Client.objects.filter(pk__in=used) | Client.objects.exclude(opening_balance=0):
        company = lookup.get(key(client.name))
        if company is None:
            company = Company.objects.create(name=client.name[:255], region_id=client.region_id)
            lookup[key(client.name)] = company
            parents[company.pk] = None
        elif company.region_id is None and client.region_id:
            company.region_id = client.region_id
            company.save(update_fields=["region"])
        companies[client.pk] = company
        if client.payer_id:
            Counterparty.objects.filter(pk=client.payer_id, company__isnull=True).update(company=company)
        if client.opening_balance:
            openings[head_of(company.pk)] += client.opening_balance
    for pk, amount in openings.items():
        if not OpeningBalance.objects.filter(company_id=pk).exists():
            OpeningBalance.objects.create(company_id=pk, date=sales[0].date, amount=amount, note=NOTE)

    groups = {}
    clients = Client.objects.in_bulk(used)
    for sale in sales:
        channel = sale.channel.name if sale.channel_id else ""
        from_smartup = sale.source != "manual"
        document_key = (
            f"smartup:{sale.date.isoformat()}:{key(clients[sale.client_id].name)}:{key(channel)}"
            if from_smartup
            else ""
        )
        group = groups.setdefault(
            (sale.date, sale.client_id, channel, sale.affects_stock, document_key),
            {"lines": defaultdict(lambda: [0, Decimal(0)]), "sales": [], "smartup": from_smartup},
        )
        line = group["lines"][sale.product_id]
        line[0] += sale.quantity
        line[1] += sale.amount
        group["sales"].append(sale)

    for (date, client_id, channel, affects_stock, document_key), group in groups.items():
        if document_key and ProductShipment.objects.filter(external_key=document_key).exists():
            continue  # loaded already from the SmartUp report
        shipment = ProductShipment.objects.create(
            date=date,
            customer=companies[client_id],
            channel=channel,
            source="smartup" if group["smartup"] else "manual",
            external_key=document_key,
            affects_stock=affects_stock,
            note=NOTE,
            created_by_id=group["sales"][0].created_by_id,
        )
        shipment.number = f"ОТ-{shipment.pk:05d}"
        shipment.save(update_fields=["number"])
        lines = ProductShipmentLine.objects.bulk_create(
            ProductShipmentLine(shipment=shipment, product_id=product_id, quantity=quantity, amount=amount)
            for product_id, (quantity, amount) in group["lines"].items()
        )
        ProductMove.objects.filter(source__in=[f"sale:{sale.pk}" for sale in group["sales"]]).delete()
        if affects_stock:
            note = f"{shipment.customer.name}, № {shipment.number}"[:255]
            ProductMove.objects.bulk_create(
                ProductMove(
                    product_id=line.product_id, date=date, kind="shipment", quantity=-line.quantity, note=note,
                    source=f"shipment:{shipment.pk}:{line.pk}", created_by_id=shipment.created_by_id,
                )
                for line in lines
            )  # fmt: skip


class Migration(migrations.Migration):
    dependencies = [
        ("production", "0012_shipment_affects_stock"),
        ("sales", "0001_initial"),
        ("settlements", "0001_initial"),
        ("cashbook", "0015_operation_partner"),
    ]

    operations = [migrations.RunPython(copy_sales, migrations.RunPython.noop)]
