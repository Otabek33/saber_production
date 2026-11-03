from decimal import Decimal

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from finance_saber.cashbook import exports
from finance_saber.cashbook.forms import HandbookUploadForm
from finance_saber.cashbook.handbooks import HandbookError
from finance_saber.cashbook.models import Company
from finance_saber.core.permissions import permission

from . import akt, ledger, openings
from .forms import OpeningBalanceForm
from .models import OpeningBalance

VIEW = "settlements.view_openingbalance"
CHANGE = ("settlements.add_openingbalance", "settlements.change_openingbalance")


def _filter(rows, params):
    """The balances list's filters: debtors, creditors or all; a name or INN."""
    show = params.get("show", "")
    if show == "debtors":
        rows = [row for row in rows if row.balance > 0]
    elif show == "creditors":
        rows = [row for row in rows if row.balance < 0]
    elif show != "all":
        rows = [row for row in rows if row.balance]
    if query := params.get("q", "").strip().casefold():
        rows = [row for row in rows if query in row.partner.name.casefold() or query in row.partner.inn]
    return rows


@permission(view=VIEW)
def balances(request):
    """Every partner with what it was shipped, returned and paid, and what it owes at the end of a day."""
    on_date = parse_date(request.GET.get("date") or "") or timezone.localdate()
    rows = _filter(ledger.balances(on_date), request.GET)
    if request.GET.get("export") == "xlsx":
        return _balances_xlsx(rows, on_date)
    totals = {
        name: sum((getattr(row, name) for row in rows), Decimal(0))
        for name in ("opening", "shipped", "returned", "purchased", "received", "paid", "balance")
    }
    totals["owed_to_us"] = sum((row.balance for row in rows if row.balance > 0), Decimal(0))
    totals["owed_by_us"] = -sum((row.balance for row in rows if row.balance < 0), Decimal(0))
    return render(
        request,
        "settlements/balances.html",
        {
            "rows": rows,
            "on_date": on_date,
            "totals": totals,
            "upload_form": HandbookUploadForm(),
            "counting_start": ledger.counting_start(),
        },
    )


def _balances_xlsx(rows, on_date):
    headers = [
        _("Partner"), _("INN"), _("Opening balance"), _("Shipped"), _("Returned"), _("Purchased"), _("Received"),
        _("Paid"), _("Balance"), _("Last movement"),
    ]  # fmt: skip
    data = [
        [
            row.partner.name, row.partner.inn, row.opening, row.shipped, row.returned, row.purchased, row.received,
            row.paid, row.balance, row.last_date,
        ]
        for row in rows
    ]  # fmt: skip
    sheet = exports.Sheet(_("Settlements"), headers, data, money_columns=(2, 3, 4, 5, 6, 7, 8))
    return exports.xlsx_response(f"{_('Settlements')} {on_date.isoformat()}.xlsx", [sheet])


@permission(view=VIEW, change=CHANGE)
def statement(request, pk):
    """A partner's акт сверки for a period: opening balance, every document and payment, closing balance."""
    company = get_object_or_404(Company, pk=pk)
    partner = company.head
    if partner != company:
        return redirect("settlements:statement", pk=partner.pk)
    opening_balance = OpeningBalance.objects.filter(company=partner).first()
    form = OpeningBalanceForm(
        request.POST or None,
        instance=opening_balance or OpeningBalance(company=partner, created_by=request.user),
        initial={} if opening_balance else {"date": timezone.localdate().replace(day=1)},
    )
    if request.method == "POST":
        if "remove" in request.POST and request.user.has_perm("settlements.delete_openingbalance"):
            if opening_balance:
                opening_balance.delete()
                messages.success(request, _("Opening balance removed."))
            return redirect("settlements:statement", pk=partner.pk)
        if form.is_valid():
            form.save()
            messages.success(request, _("Opening balance saved."))
            return redirect("settlements:statement", pk=partner.pk)

    today = timezone.localdate()
    date_from = parse_date(request.GET.get("from") or "")
    date_to = parse_date(request.GET.get("to") or "") or today
    result = ledger.statement(partner, date_from, date_to)
    if request.GET.get("export") == "xlsx":
        ours = Company.objects.filter(is_own=True).first()
        name = f"{_('Reconciliation statement')} {partner.name[:60]} {date_to.isoformat()}.xlsx"
        return exports.attachment(akt.akt_bytes(result, ours), name)
    periods = {
        "month": (today.replace(day=1), today),
        "quarter": (today.replace(month=(today.month - 1) // 3 * 3 + 1, day=1), today),
        "year": (today.replace(month=1, day=1), today),
    }
    return render(
        request,
        "settlements/statement.html",
        {
            "partner": partner,
            "statement": result,
            "form": form,
            "opening_balance": opening_balance,
            "periods": periods,
            "filials": partner.filials.count(),
            "date_to": date_to,
        },
    )


@require_POST
@permission(view=VIEW, change=CHANGE)
def openings_import(request):
    """Load partners' opening balances from Excel (the file of Download opening balances, filled in)."""
    form = HandbookUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        for error in form.errors.get("file", []):
            messages.error(request, error)
        return redirect("settlements:balances")
    try:
        report = openings.load_openings(form.cleaned_data["file"], request.user, form.cleaned_data["dry_run"])
    except HandbookError as error:
        messages.error(request, str(error))
        return redirect("settlements:balances")
    if report.errors:
        shown = " ".join(report.errors[:10]) + (" …" if len(report.errors) > 10 else "")
        messages.error(request, _("Nothing was saved: %(errors)s") % {"errors": shown})
    elif report.saved:
        messages.success(request, _("Opening balances loaded: %(count)s.") % {"count": report.saved_count})
    else:
        messages.info(
            request, _("Checked, nothing was saved: %(count)s opening balances.") % {"count": report.saved_count}
        )
    return redirect("settlements:balances")


@permission(view=VIEW)
def openings_export(request):
    """Every partner's opening balance as an Excel file, to fill in and load back."""
    return exports.xlsx_response(exports.dated_filename(_("Opening balances")), [openings.openings_sheet()])
