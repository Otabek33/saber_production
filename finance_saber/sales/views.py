import datetime
from decimal import Decimal

from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import ListView, UpdateView

from finance_saber.cashbook.models import Counterparty
from finance_saber.cashbook.views import safe_next
from finance_saber.core.permissions import PermissionMixin, permission

from . import services
from .forms import ClientForm, PlanUploadForm, SaleForm, SalesUploadForm
from .models import Client, Plan, Sale

PAGE_SIZE = 20
MONTHS = range(1, 13)


def day_url(date) -> str:
    return f"{reverse('sales:daily')}?date={date.isoformat()}"


@permission(view="sales.view_sale", change="sales.add_sale")
def daily(request):
    """One day's shipments: entered by hand here, or loaded from a SmartUp export (Import page)."""
    date = parse_date(request.GET.get("date") or "") or timezone.localdate()
    form = SaleForm(request.POST or None, initial={"date": date})
    if request.method == "POST":
        if form.is_valid():
            sale = form.save(commit=False)
            sale.created_by = request.user
            services.record_sale(sale)
            messages.success(request, _("Sale added."))
            return redirect(f"{day_url(sale.date)}&entry=1")
    sales = (
        Sale.objects.filter(date=date)
        .select_related("client", "product", "channel", "created_by")
        .order_by("client__name", "product__position", "id")
    )
    posted = [sale for sale in sales if not sale.is_cancelled]
    by_product = {}
    for sale in posted:
        row = by_product.setdefault(sale.product, {"product": sale.product, "quantity": 0, "amount": Decimal(0)})
        row["quantity"] += sale.quantity
        row["amount"] += sale.amount
    return render(
        request,
        "sales/daily.html",
        {
            "date": date,
            "previous_day": date - datetime.timedelta(days=1),
            "next_day": date + datetime.timedelta(days=1),
            "form": form,
            "entry_open": request.method == "POST" or "entry" in request.GET,
            "sales": sales,
            "by_product": sorted(by_product.values(), key=lambda row: row["product"].position),
            "total_quantity": sum(sale.quantity for sale in posted),
            "total_amount": sum((sale.amount for sale in posted), Decimal(0)),
            "clients": len({sale.client_id for sale in posted}),
        },
    )


@permission(view="sales.change_sale", change="sales.change_sale")
def sale_edit(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    form = SaleForm(request.POST or None, instance=sale)
    if request.method == "POST" and form.is_valid():
        services.record_sale(form.save(commit=False))
        messages.success(request, _("Sale updated."))
        return redirect(day_url(sale.date))
    return render(request, "sales/sale_form.html", {"form": form, "sale": sale, "back_url": day_url(sale.date)})


@require_POST
@permission(change="sales.change_sale")
def sale_cancel(request, pk):
    """Cancel a sale (it stops counting in stock and debts) or restore it."""
    sale = get_object_or_404(Sale, pk=pk)
    try:
        services.set_cancelled(sale, not sale.is_cancelled)
    except ValueError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, _("Sale cancelled.") if sale.is_cancelled else _("Sale restored."))
    return redirect(safe_next(request, request.POST.get("next"), day_url(sale.date)))


@permission(view="sales.import_sales")
def import_excel(request):
    """Upload a SmartUp export (sales) or the yearly plan workbook. Files are read in memory, not kept."""
    sales_form = SalesUploadForm(prefix="sales")
    plan_form = PlanUploadForm(prefix="plan")
    sales_report = plan_report = None
    if request.method == "POST" and "sales-file" in request.FILES:
        sales_form = SalesUploadForm(request.POST, request.FILES, prefix="sales")
        if sales_form.is_valid():
            try:
                parsed = services.read_smartup(sales_form.cleaned_data["file"])
            except services.SheetError as error:
                sales_form.add_error(None, str(error))
            else:
                sales_report = services.import_sales(
                    parsed, user=request.user, dry_run=sales_form.cleaned_data["dry_run"]
                )
                if sales_report.saved:
                    messages.success(request, _("%(count)s sales imported.") % {"count": sales_report.created})
    elif request.method == "POST" and "plan-file" in request.FILES:
        plan_form = PlanUploadForm(request.POST, request.FILES, prefix="plan")
        if plan_form.is_valid():
            try:
                parsed = services.read_plan(plan_form.cleaned_data["file"])
            except services.SheetError as error:
                plan_form.add_error(None, str(error))
            else:
                plan_report = services.import_plan(parsed, dry_run=plan_form.cleaned_data["dry_run"])
                if plan_report.saved:
                    messages.success(request, _("Plan for %(year)s imported.") % {"year": plan_report.year})
    elif request.method == "POST":
        messages.error(request, _("Choose a file to upload."))
    return render(
        request,
        "sales/import.html",
        {"sales_form": sales_form, "plan_form": plan_form, "sales_report": sales_report, "plan_report": plan_report},
    )


@permission(view=("sales.view_client", "sales.view_sale"))
def debts(request):
    """Debtors and creditors: what each client (or each payer, for its clients together) owes."""
    rows = services.debt_rows()
    query = request.GET.get("q", "").strip().lower()
    if query:
        rows = [
            row
            for row in rows
            if query in row.name.lower() or any(query in client.name.lower() for client in row.clients)
        ]
    status = request.GET.get("status", "")
    debtors = [row for row in rows if row.balance > 0]
    creditors = [row for row in rows if row.balance < 0]
    shown = {"debtors": debtors, "creditors": creditors}.get(status, rows)
    shown = sorted(shown, key=lambda row: (-abs(row.balance), row.name))
    page = Paginator(shown, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "sales/debts.html",
        {
            "page_obj": page,
            "status": status,
            "receivable": sum((row.balance for row in debtors), Decimal(0)),
            "payable": -sum((row.balance for row in creditors), Decimal(0)),
            "debtor_count": len(debtors),
            "creditor_count": len(creditors),
            "since": services.debts_from(),
            "unlinked": Client.objects.filter(payer__isnull=True, sales__isnull=False).distinct().count(),
        },
    )


@permission(view=("sales.view_client", "sales.view_sale"))
def statement(request, kind, pk):
    """Shipments and payments of one client, or of one payer's clients, with the running balance."""
    if kind == "payer":
        payer = get_object_or_404(Counterparty, pk=pk)
        clients = list(payer.sales_clients.all())
        name = payer.name
    else:
        client = get_object_or_404(Client, pk=pk)
        if client.payer_id:  # owes together with the payer's other clients
            return redirect("sales:statement", kind="payer", pk=client.payer_id)
        payer, clients, name = None, [client], client.name
    lines = services.statement(clients, payer=payer)
    newest_first = list(reversed(lines))
    page = Paginator(newest_first, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "sales/statement.html",
        {
            "name": name,
            "payer": payer,
            "clients": clients,
            "page_obj": page,
            "balance": lines[-1].balance if lines else Decimal(0),
            "shipped": sum((line.debit for line in lines if line.kind == "sale"), Decimal(0)),
            "paid": sum((line.credit for line in lines), Decimal(0)),
            "since": services.debts_from(),
        },
    )


class ClientListView(PermissionMixin, ListView):
    permission_required = "sales.view_client"
    template_name = "sales/client_list.html"
    context_object_name = "clients"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        clients = Client.objects.select_related("payer", "region").annotate(
            shipped=Sum("sales__amount", filter=Q(sales__is_cancelled=False)),
            lines=Count("sales", filter=Q(sales__is_cancelled=False)),
        )
        query = self.request.GET.get("q", "").strip()
        if query:
            clients = clients.filter(Q(name__icontains=query) | Q(payer__name__icontains=query))
        if self.request.GET.get("status") == "unlinked":
            clients = clients.filter(payer__isnull=True)
        return clients.order_by("name")


class ClientUpdateView(PermissionMixin, SuccessMessageMixin, UpdateView):
    permission_required = "sales.change_client"
    model = Client
    form_class = ClientForm
    template_name = "sales/client_form.html"
    success_message = _("Saved.")

    def get_success_url(self):
        return safe_next(self.request, self.request.GET.get("next"), reverse("sales:clients"))

    def get_context_data(self, **kwargs):
        return super().get_context_data(back_url=self.get_success_url(), **kwargs)


@permission(view="sales.view_plan", change="sales.change_plan")
def plan(request):
    """The year's plan by product and month next to what was actually sold (or produced)."""
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year") or today.year)
    except ValueError:
        year = today.year
    kind = request.GET.get("kind") if request.GET.get("kind") in Plan.Kind.values else Plan.Kind.SALES
    if request.method == "POST":
        changed = save_plan_grid(request.POST, kind, year)
        messages.success(request, _("Plan saved (%(count)s figures changed).") % {"count": changed})
        return redirect(f"{reverse('sales:plan')}?year={year}&kind={kind}")
    rows, totals = services.plan_vs_fact(kind, year)
    for row in rows:
        row["cells"] = [
            {"month": month, "plan": plan_value, "fact": fact, "percent": percent(fact, plan_value)}
            for month, (plan_value, fact) in zip(MONTHS, row["months"], strict=True)
        ]
        row["percent"] = percent(row["fact"], row["plan"])
    plan_total, fact_total = sum(p for p, _f in totals), sum(f for _p, f in totals)
    return render(
        request,
        "sales/plan.html",
        {
            "year": year,
            "kind": kind,
            "kinds": Plan.Kind.choices,
            "rows": rows,
            "totals": [{"plan": p, "fact": f, "percent": percent(f, p)} for p, f in totals],
            "plan_total": plan_total,
            "fact_total": fact_total,
            "percent_total": percent(fact_total, plan_total),
            "months": [datetime.date(year, month, 1) for month in MONTHS],
            "current_month": today.month if today.year == year else None,
            "editing": "edit" in request.GET and request.user.has_perm("sales.change_plan"),
        },
    )


def percent(fact, plan_value):
    return round(fact * 100 / plan_value) if plan_value else None


def save_plan_grid(data, kind, year) -> int:
    """Write the edited plan cells (inputs named plan-<product id>-<month>)."""
    changed = 0
    existing = {(p.product_id, p.month): p for p in Plan.objects.filter(kind=kind, year=year)}
    for name, value in data.items():
        parts = name.split("-")
        if len(parts) != 3 or parts[0] != "plan" or not parts[1].isdigit() or not parts[2].isdigit():
            continue
        product_id, month = int(parts[1]), int(parts[2])
        if month not in MONTHS:
            continue
        try:
            quantity = max(int(value.replace(" ", "").replace("\xa0", "") or 0), 0)
        except ValueError:
            continue
        current = existing.get((product_id, month))
        if current is None:
            if quantity:
                Plan.objects.create(kind=kind, year=year, month=month, product_id=product_id, quantity=quantity)
                changed += 1
        elif current.quantity != quantity:
            current.quantity = quantity
            current.save(update_fields=["quantity", "updated_at"])
            changed += 1
    return changed
