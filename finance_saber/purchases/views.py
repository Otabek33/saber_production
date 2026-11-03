from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from finance_saber.cashbook import exports
from finance_saber.cashbook.models import Company, Kind
from finance_saber.core.permissions import permission
from finance_saber.production.models import MaterialReceipt
from finance_saber.settlements import ledger

from .forms import BillForm
from .models import Bill

VIEW = "purchases.view_bill"
CHANGE = ("purchases.add_bill", "purchases.change_bill")
PAGE_SIZE = 50
ZERO = Decimal(0)


def supplier_heads() -> set[int]:
    """Head companies that supplied us something: a material receipt or a bill, ever."""
    heads = ledger.head_ids()
    suppliers = set(MaterialReceipt.objects.values_list("supplier_id", flat=True))
    suppliers |= set(Bill.objects.values_list("supplier_id", flat=True))
    return {heads[pk] for pk in suppliers}


def _period(params):
    return parse_date(params.get("from") or ""), parse_date(params.get("to") or "")


@permission(view=VIEW)
def vendors(request):
    """Suppliers with what they supplied and what we paid them: what we owe each, at the end of a day."""
    on_date = parse_date(request.GET.get("date") or "") or timezone.localdate()
    heads = supplier_heads()
    rows = [row for row in ledger.balances(on_date) if row.partner.pk in heads]
    if query := request.GET.get("q", "").strip().casefold():
        rows = [row for row in rows if query in row.partner.name.casefold() or query in row.partner.inn]
    if request.GET.get("show") == "owed":
        rows = [row for row in rows if row.balance < 0]
    if request.GET.get("export") == "xlsx":
        headers = [_("Supplier"), _("INN"), _("Opening balance"), _("Purchased"), _("Paid"), _("Balance")]
        data = [[row.partner.name, row.partner.inn, row.opening, row.purchased, row.paid, row.balance] for row in rows]
        sheet = exports.Sheet(_("Vendors"), headers, data, money_columns=(2, 3, 4, 5))
        return exports.xlsx_response(f"{_('Vendors')} {on_date.isoformat()}.xlsx", [sheet])
    owed = -sum((row.balance for row in rows if row.balance < 0), ZERO)
    prepaid = sum((row.balance for row in rows if row.balance > 0), ZERO)
    return render(
        request,
        "purchases/vendors.html",
        {"rows": rows, "on_date": on_date, "owed": owed, "prepaid": prepaid, "counting_start": ledger.counting_start()},
    )


def _bill_rows(params):
    """Material receipts (with their amounts) and bills, newest first, with the journal's filters."""
    receipts = MaterialReceipt.objects.posted().select_related("supplier").annotate(amount_total=Sum("lines__amount"))
    bills = Bill.objects.select_related("supplier", "created_by")
    if params.get("status") != "all":
        bills = bills.posted()
    if params.get("supplier", "").isdigit():
        company = Company.objects.filter(pk=params["supplier"]).first()
        group = company.descendant_ids() if company else set()
        receipts, bills = receipts.filter(supplier_id__in=group), bills.filter(supplier_id__in=group)
    date_from, date_to = _period(params)
    if date_from:
        receipts, bills = receipts.filter(date__gte=date_from), bills.filter(date__gte=date_from)
    if date_to:
        receipts, bills = receipts.filter(date__lte=date_to), bills.filter(date__lte=date_to)
    kind = params.get("kind", "")
    rows = []
    if kind in ("", "materials"):
        rows += [
            {
                "kind": "receipt", "date": receipt.date, "number": receipt.number, "supplier": receipt.supplier,
                "text": _("Materials"), "amount": receipt.amount_total or ZERO, "object": receipt,
                "url": reverse("production:receipt", args=[receipt.pk]),
            }
            for receipt in receipts
        ]  # fmt: skip
    if kind in ("", "bills"):
        rows += [
            {
                "kind": "bill", "date": bill.date, "number": bill.number, "supplier": bill.supplier,
                "text": bill.description, "amount": bill.amount, "object": bill, "url": "",
            }
            for bill in bills
        ]  # fmt: skip
    rows.sort(key=lambda row: (row["date"], row["object"].pk), reverse=True)
    return rows


@permission(view=VIEW)
def bills(request):
    """What suppliers billed us: material receipts with their amounts, and bills for everything else."""
    rows = _bill_rows(request.GET)
    if request.GET.get("export") == "xlsx":
        headers = [_("Date"), _("Number"), _("Supplier"), _("INN"), _("Type"), _("What for"), _("Amount")]
        data = [
            [
                row["date"], row["number"], row["supplier"].name, row["supplier"].inn,
                str(_("Material receipt") if row["kind"] == "receipt" else _("Bill")), row["text"], row["amount"],
            ]
            for row in rows
        ]  # fmt: skip
        sheet = exports.Sheet(_("Bills"), headers, data, money_columns=(6,))
        return exports.xlsx_response(exports.dated_filename(_("Bills")), [sheet])
    unpriced = (
        MaterialReceipt.objects.posted().annotate(amount_total=Sum("lines__amount")).filter(amount_total=0).count()
    )
    return render(
        request,
        "purchases/bills.html",
        {
            "page_obj": Paginator(rows, PAGE_SIZE).get_page(request.GET.get("page")),
            "total": sum((row["amount"] for row in rows if not row["object"].is_cancelled), ZERO),
            "suppliers": Company.objects.filter(pk__in=supplier_heads()).order_by("name"),
            "unpriced": unpriced,
        },
    )


@permission(view=VIEW, change=CHANGE)
def bill_new(request):
    """A supplier's bill for anything that isn't a material received (materials: Production › Material receipts)."""
    form = BillForm(request.POST or None, initial={"supplier": request.GET.get("supplier")})
    if request.method == "POST" and form.is_valid():
        bill = form.save(commit=False)
        bill.created_by = request.user
        bill.save()
        messages.success(
            request,
            _("Bill № %(number)s of %(supplier)s saved.") % {"number": bill.number, "supplier": bill.supplier.name},
        )
        return redirect(reverse("purchases:bills") + f"?supplier={bill.supplier_id}")
    return render(request, "purchases/bill_form.html", {"form": form})


@require_POST
@permission(view=VIEW, change=CHANGE)
def bill_cancel(request, pk):
    """Cancel a bill (it stops counting) or restore it."""
    bill = get_object_or_404(Bill, pk=pk)
    if bill.is_cancelled and Bill.objects.posted().filter(supplier=bill.supplier, number=bill.number).exists():
        messages.error(request, _("This supplier's bill № %(number)s is already entered.") % {"number": bill.number})
    else:
        bill.is_cancelled = not bill.is_cancelled
        bill.save(update_fields=["is_cancelled"])
        messages.success(request, _("Bill cancelled.") if bill.is_cancelled else _("Bill restored."))
    return redirect(request.POST.get("next") or reverse("purchases:bills"))


@permission(view=VIEW)
def payments(request):
    """Money paid to suppliers, from the bank and the cash desks."""
    heads = ledger.head_ids()
    suppliers = supplier_heads()
    if request.GET.get("supplier", "").isdigit():
        suppliers &= {int(request.GET["supplier"])}
    group = [pk for pk, head in heads.items() if head in suppliers]
    operations = (
        ledger.partner_operations(group)
        .filter(kind=Kind.EXPENSE)
        .select_related("desk", "category", "counterparty__company", "partner")
        .order_by("-date", "-id")
    )
    date_from, date_to = _period(request.GET)
    if date_from:
        operations = operations.filter(date__gte=date_from)
    if date_to:
        operations = operations.filter(date__lte=date_to)
    rates = ledger.Rates()
    rows = [
        {"operation": operation, "company": operation.partner or operation.counterparty.company,
         "amount": ledger.in_uzs(operation, rates)}
        for operation in operations
    ]  # fmt: skip
    if request.GET.get("export") == "xlsx":
        headers = [_("Date"), _("Cash desk"), _("Supplier"), _("Document no."), _("Note"), _("Amount, UZS")]
        data = [
            [row["operation"].date, row["operation"].desk.name, row["company"].name, row["operation"].document_number,
             row["operation"].note, row["amount"]]
            for row in rows
        ]  # fmt: skip
        sheet = exports.Sheet(_("Payments made"), headers, data, money_columns=(5,))
        return exports.xlsx_response(exports.dated_filename(_("Payments made")), [sheet])
    return render(
        request,
        "purchases/payments.html",
        {
            "page_obj": Paginator(rows, PAGE_SIZE).get_page(request.GET.get("page")),
            "total": sum((row["amount"] or ZERO for row in rows), ZERO),
            "suppliers": Company.objects.filter(pk__in=supplier_heads()).order_by("name"),
        },
    )
