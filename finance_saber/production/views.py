import calendar
import datetime
from decimal import Decimal

from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Prefetch, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, ListView, TemplateView, UpdateView

from finance_saber.cashbook import exports
from finance_saber.cashbook.handbooks import HandbookError
from finance_saber.cashbook.models import Company
from finance_saber.core.permissions import PermissionMixin, permission

from . import importer, recipe_excel, services, shipment_import, traceability
from .forms import (
    CalculationForm,
    CalculationLineFormSet,
    DailyProductionForm,
    DayForm,
    ImportForm,
    MaterialForm,
    MaterialReceiptForm,
    MaterialUploadForm,
    MaterialWriteOffForm,
    ProductForm,
    ReceiptLineFormSet,
    RecipeFormSet,
    RecipeUploadForm,
    ShipmentForm,
    ShipmentLineFormSet,
    StockCountForm,
)
from .models import (
    Material,
    MaterialMove,
    MaterialReceipt,
    MaterialReceiptLine,
    Product,
    ProductionRun,
    ProductMove,
    ProductShipment,
    ProductShipmentLine,
    material_stock,
    product_stock,
)

PAGE_SIZE = 20


def selected_date(request) -> datetime.date:
    return parse_date(request.GET.get("date") or "") or timezone.localdate()


@permission(
    view="production.view_productionrun",
    change=("production.add_productionrun", "production.change_productionrun"),
)
def daily(request):
    """Fill in one day's production: a quantity per product, materials written off by recipe.

    GET shows the day (today by default). "Save as draft" keeps the quantities
    without touching stock. "Post" first shows what materials the quantities use
    and warns about any that would go below zero; the user then confirms, or it
    posts straight away when nothing runs short. Only posted production moves stock.
    """
    date = selected_date(request)
    products = Product.objects.filter(is_active=True).prefetch_related("recipe")
    runs = ProductionRun.objects.filter(date=date).not_cancelled()
    initial = {run.product_id: run.quantity for run in runs}
    form = DailyProductionForm(request.POST or None, products=products, initial_quantities=initial)
    can_edit = request.user.has_perms(("production.add_productionrun", "production.change_productionrun"))
    if not can_edit:  # read-only: the day's figures are shown, not offered for editing
        for field in form.fields.values():
            field.disabled = True

    needs = []
    if request.method == "POST" and form.is_valid():
        quantities = form.quantities()
        post = "draft" not in request.POST
        needs = services.material_needs(quantities, date=date)
        if not post or not any(need.is_short for need in needs) or "confirm" in request.POST:
            changed = services.save_day(date, quantities, user=request.user, post=post)
            message = (
                _("Production for %(date)s posted: stock updated (%(count)s products changed).")
                if post
                else _("Production for %(date)s saved as a draft: stock is not affected until it is posted.")
            )
            messages.success(request, message % {"date": date.strftime("%d.%m.%Y"), "count": changed})
            return redirect(f"{reverse('production:daily')}?date={date.isoformat()}")
        messages.warning(request, _("Some materials would go below zero. Check them, then post anyway or correct."))
    elif request.method == "GET":
        # What the saved day used; stock already includes it, so pass the date to add it back first.
        needs = services.material_needs(
            {run.product: run.quantity for run in runs.select_related("product")}, date=date
        )

    total = sum(initial.values())
    possible = services.units_possible(products)
    saved = {run.product_id: run for run in runs}
    for product in form.products:  # its run (draft or posted) and, once posted, its batch
        run = saved.get(product.pk)
        product.run = run
        product.batch = (run, traceability.batch_code(run)) if run and run.is_posted else None
    return render(
        request,
        "production/daily.html",
        {
            "date": date,
            "can_edit": can_edit,
            "day_form": DayForm(initial={"date": date}),
            "form": form,
            # (product, quantity field, how many units the stock allows)
            "rows": [(product, field, possible[product.pk]) for product, field in form.rows()],
            "needs": needs,
            "shortage": any(need.is_short for need in needs),
            "total": total,
            "drafts": [run for run in saved.values() if run.is_draft],
            "has_posted": any(run.is_posted for run in saved.values()),
            "previous_day": date - datetime.timedelta(days=1),
            "next_day": date + datetime.timedelta(days=1),
        },
    )


@permission(
    view="production.view_productionrun",
    change=("production.add_productionrun", "production.change_productionrun"),
)
def calculation(request, date, product_pk):
    """The production calculation (Калькуляция производства) of one product on one day.

    First the recipe of this production: norms per 1000 l for raw materials and per
    unit for packaging, the production defect and the allowed loss; then how much
    was brewed and produced. Saving records the production with this recipe; if it
    is the product's latest production, the product's recipe becomes these norms
    too, so the next production starts from them.
    """
    day = parse_date(date)
    if day is None:
        raise Http404
    product = get_object_or_404(Product, pk=product_pk)
    run = ProductionRun.objects.filter(date=day, product=product).first()
    saved = run is not None and not run.is_cancelled
    lines = services.recipe_lines(product, run)
    form = CalculationForm(
        request.POST or None,
        initial={"quantity": run.quantity if saved else None, "batch_quantity": run.batch_quantity if saved else None},
    )
    formset = CalculationLineFormSet(
        request.POST or None,
        prefix="lines",
        product=product,
        initial=CalculationLineFormSet.initial_for(product, lines),
    )
    can_edit = request.user.has_perms(("production.add_productionrun", "production.change_productionrun"))
    can_change_recipe = request.user.has_perms(RECIPE_EDIT_PERMISSIONS)
    if not can_edit:  # read-only: the calculation is shown, not offered for editing
        for each in (form, *formset):
            for field in each.fields.values():
                field.disabled = True

    if request.method == "POST" and form.is_valid() and formset.is_valid():
        post = "draft" not in request.POST
        run, recipe_updated = services.save_calculation(
            day,
            product,
            form.cleaned_data["quantity"],
            form.cleaned_data["batch_quantity"],
            formset.lines(),
            user=request.user,
            update_recipe=can_change_recipe,
            post=post,
        )
        message = (
            _("Production posted: %(quantity)s units of %(product)s on %(date)s; stock updated.")
            if post
            else _(
                "Draft saved: %(quantity)s units of %(product)s on %(date)s; stock is not affected until it is posted."
            )
        )
        messages.success(request, message % {
            "quantity": f"{run.quantity:,}".replace(",", " "), "product": product.name,
            "date": day.strftime("%d.%m.%Y"),
        })  # fmt: skip
        if recipe_updated:
            messages.info(request, _("The recipe of %(product)s now has these norms for the next production.") % {
                "product": product.name})  # fmt: skip
        elif can_change_recipe:
            messages.info(request, _("A later production of this product exists, so its recipe was left as it is."))
        return redirect("production:calculation", date=day.isoformat(), product_pk=product.pk)

    materials = Material.objects.all()
    return render(
        request,
        "production/calculation.html",
        {
            "product": product,
            "date": day,
            "run": run if saved else None,
            "form": form,
            "formset": formset,
            "can_edit": can_edit,
            "can_change_recipe": can_change_recipe,
            # For the live calculation: each material's kind and unit.
            "materials": {m.pk: {"kind": m.kind, "unit": m.unit} for m in materials},
        },
    )


@require_POST
@permission(change="production.change_productionrun")
def run_delete(request, pk):
    """Delete a draft production (it never moved stock). Posted production is cancelled, not deleted."""
    run = get_object_or_404(ProductionRun.objects.select_related("product"), pk=pk)
    try:
        services.delete_draft(run)
    except ValueError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, _("Draft of %(product)s for %(date)s deleted.") % {
            "product": run.product.name, "date": run.date.strftime("%d.%m.%Y"),
        })  # fmt: skip
    return redirect(f"{reverse('production:daily')}?date={run.date.isoformat()}")


@permission(view="production.view_productionrun")
def journal(request):
    """A month of production as products × days, like the workbook's Warehouse sheet."""
    today = timezone.localdate()
    month = parse_date((request.GET.get("month") or "") + "-01") or today.replace(day=1)
    days = [month.replace(day=day) for day in range(1, calendar.monthrange(month.year, month.month)[1] + 1)]
    runs = ProductionRun.objects.posted().filter(date__year=month.year, date__month=month.month)
    grid: dict[int, dict[datetime.date, int]] = {}
    for run in runs:
        grid.setdefault(run.product_id, {})[run.date] = run.quantity
    products = Product.objects.filter(Q(is_active=True) | Q(pk__in=grid))
    rows = [
        {"product": product, "cells": [grid.get(product.pk, {}).get(day) for day in days],
         "total": sum(grid.get(product.pk, {}).values())}
        for product in products
    ]  # fmt: skip
    day_totals = [sum(row["cells"][index] or 0 for row in rows) for index in range(len(days))]
    previous_month = (month - datetime.timedelta(days=1)).replace(day=1)
    next_month = (month + datetime.timedelta(days=32)).replace(day=1)
    return render(
        request,
        "production/journal.html",
        {
            "month": month,
            "days": days,
            "today": today,
            "rows": rows,
            "day_totals": day_totals,
            "grand_total": sum(day_totals),
            "previous_month": previous_month,
            "next_month": next_month,
        },
    )


class StockListView(PermissionMixin, ListView):
    """Current stock of materials or products, with this month's movements, searchable and paginated."""

    paginate_by = PAGE_SIZE
    context_object_name = "items"

    def get_queryset(self):
        items = self.model.objects.filter(is_active=True)
        query = self.request.GET.get("q", "").strip()
        if query:
            items = items.filter(name__icontains=query)
        return items

    def month_totals(self, moves, field):
        """{item id: {kind: total}} for this month's movements."""
        today = timezone.localdate()
        rows = (
            moves.posted()
            .filter(date__year=today.year, date__month=today.month)
            .values(field, "kind")
            .annotate(total=Sum("quantity"))
        )
        totals = {}
        for row in rows:
            totals.setdefault(row[field], {})[row["kind"]] = row["total"]
        return totals


class MaterialStockView(StockListView):
    model = Material
    template_name = "production/material_stock.html"
    permission_required = "production.view_material"

    def get_queryset(self):
        return super().get_queryset().order_by("-kind", "name")  # raw materials, then packaging

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        stock = material_stock()
        month = self.month_totals(MaterialMove.objects, "material_id")
        rows = []
        for material in context["items"]:
            totals = month.get(material.pk, {})
            amount = stock.get(material.pk, Decimal(0))
            rows.append({
                "material": material,
                "stock": amount,
                "received": totals.get(MaterialMove.Kind.RECEIPT, 0),
                "used": -(totals.get(MaterialMove.Kind.CONSUMPTION) or 0),
                "low": amount < 0 or (material.min_stock and amount < material.min_stock),
            })  # fmt: skip
        context["rows"] = rows
        context["short_count"] = sum(1 for pk, amount in stock.items() if amount < 0)
        return context


class ProductStockView(StockListView):
    model = Product
    template_name = "production/product_stock.html"
    permission_required = "production.view_product"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        stock = product_stock()
        month = self.month_totals(ProductMove.objects, "product_id")
        context["rows"] = [
            {
                "product": product,
                "stock": stock.get(product.pk, Decimal(0)),
                "produced": month.get(product.pk, {}).get(ProductMove.Kind.OUTPUT, 0),
                # Shipped less what customers returned.
                "shipped": -(month.get(product.pk, {}).get(ProductMove.Kind.SHIPMENT) or 0)
                - (month.get(product.pk, {}).get(ProductMove.Kind.RETURN) or 0),
            }
            for product in context["items"]
        ]
        return context


def move_form(request, form_class, title, success_url, success_message):
    """Receipt (+), write-off (−) or shipment (−): one movement entered by hand."""
    form = form_class(request.POST or None)
    if request.method == "POST" and form.is_valid():
        move = form.save(commit=False)
        move.quantity = form_class.sign * move.quantity
        move.kind = form_class.kind
        move.created_by = request.user
        move.save()
        messages.success(request, success_message)
        return redirect(success_url)
    return render(request, "production/move_form.html", {"form": form, "title": title, "back_url": success_url})


# --- Material receipts: documents from suppliers, like 1C's «Поступление» ---------------

DUPLICATE_NUMBER = _("This supplier's document № %(number)s is already entered.")


@permission(view="production.add_materialreceipt")
def material_receipt(request):
    """A new receipt: the supplier first, its invoice number and date, then any number of materials."""
    receipt = MaterialReceipt(created_by=request.user)
    form = MaterialReceiptForm(request.POST or None, instance=receipt)
    lines = ReceiptLineFormSet(request.POST or None, instance=receipt, prefix="lines")
    if request.method == "POST" and all([form.is_valid(), lines.is_valid()]):
        try:
            with transaction.atomic():
                receipt = form.save()
                lines.instance = receipt
                lines.save()
                receipt.rebuild_moves()
        except IntegrityError:  # the same number was saved a moment ago, e.g. a double click
            form.add_error("number", DUPLICATE_NUMBER % {"number": receipt.number})
        else:
            messages.success(
                request,
                _("Receipt № %(number)s recorded: %(count)s materials.")
                % {"number": receipt.number, "count": receipt.lines.count()},
            )
            return redirect("production:receipt", pk=receipt.pk)
    units = {material.pk: material.unit for material in Material.objects.filter(is_active=True)}
    return render(request, "production/receipt_form.html", {"form": form, "lines": lines, "units": units})


def filter_receipts(params):
    """The receipts journal's filters (supplier, material, dates, number, status), shared with its Excel file."""
    receipts = MaterialReceipt.objects.select_related("supplier", "created_by")
    if params.get("supplier", "").isdigit():
        receipts = receipts.filter(supplier_id=params["supplier"])
    if params.get("material", "").isdigit():
        receipts = receipts.filter(lines__material_id=params["material"]).distinct()
    date_from, date_to = parse_date(params.get("from") or ""), parse_date(params.get("to") or "")
    if date_from:
        receipts = receipts.filter(date__gte=date_from)
    if date_to:
        receipts = receipts.filter(date__lte=date_to)
    if number := params.get("q", "").strip():
        receipts = receipts.filter(number__contains=number.upper())  # numbers are kept in capitals
    if params.get("status") == "cancelled":
        receipts = receipts.filter(is_cancelled=True)
    elif params.get("status") != "all":
        receipts = receipts.posted()
    return receipts.order_by("-date", "-id")


@permission(view="production.view_materialreceipt")
def receipts(request):
    """Receipts journal: what came from which supplier and when, document by document."""
    lines = MaterialReceiptLine.objects.select_related("material")
    found = filter_receipts(request.GET).prefetch_related(Prefetch("lines", queryset=lines))
    page = Paginator(found, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "production/receipts.html",
        {
            "page_obj": page,
            "suppliers": Company.objects.filter(is_own=False).order_by("name"),
            "materials": Material.objects.all(),
        },
    )


@permission(view="production.view_materialreceipt")
def receipts_export(request):
    """The journal's lines (one row per material) as an .xlsx file, with the filters applied on the page."""
    lines = (
        MaterialReceiptLine.objects.filter(receipt__in=filter_receipts(request.GET))
        .select_related("receipt__supplier", "receipt__created_by", "material")
        .order_by("-receipt__date", "-receipt_id", "id")
    )
    rows = [
        [
            line.receipt.date,
            line.receipt.number,
            line.receipt.supplier.name,
            line.receipt.supplier.inn,
            line.material.name,
            line.material.unit,
            line.quantity,
            line.price,
            line.amount,
            str(_("Cancelled") if line.receipt.is_cancelled else _("Posted")),
            line.receipt.note,
            str(line.receipt.created_by or ""),
        ]
        for line in lines
    ]
    headers = [
        _("Date"), _("Registration number"), _("Supplier"), _("INN"), _("Material"), _("Unit"),
        _("Quantity"), _("Price"), _("Amount"), _("Status"), _("Note"), _("Author"),
    ]  # fmt: skip
    sheet = exports.Sheet(  # not lazy
        str(_("Material receipts")), [str(header) for header in headers], rows, money_columns=(7, 8)
    )
    return exports.xlsx_response(exports.dated_filename(_("Material receipts")), [sheet])


@permission(view="production.view_materialreceipt")
def receipt(request, pk):
    lines = MaterialReceiptLine.objects.select_related("material")
    receipts = MaterialReceipt.objects.select_related("supplier", "created_by")
    document = get_object_or_404(receipts.prefetch_related(Prefetch("lines", queryset=lines)), pk=pk)
    uses = traceability.lot_uses(document) if not document.is_cancelled else []
    for use in uses:
        use.batches = [(run, traceability.batch_code(run), quantity) for run, quantity in use.batches]
    return render(request, "production/receipt_detail.html", {"receipt": document, "uses": uses})


# --- Batch traceability: which deliveries went into which batch (see traceability.py) ---------


def filter_batches(params):
    """The batches list's filters, shared with its Excel register.

    Product, dates and batch code narrow the runs directly; a material keeps the batches made
    with it; a supplier or a receipt keeps the batches whose materials came from its deliveries
    (forward tracing, first in, first out).
    """
    runs = ProductionRun.objects.posted().filter(quantity__gt=0).select_related("product", "created_by")
    if params.get("product", "").isdigit():
        runs = runs.filter(product_id=params["product"])
    date_from, date_to = parse_date(params.get("from") or ""), parse_date(params.get("to") or "")
    if date_from:
        runs = runs.filter(date__gte=date_from)
    if date_to:
        runs = runs.filter(date__lte=date_to)
    if code := params.get("code", "").strip():
        parsed = traceability.parse_batch_code(code)
        runs = runs.filter(date=parsed[0], product_id=parsed[1]) if parsed else runs.none()
    if params.get("material", "").isdigit():
        runs = runs.filter(lines__material_id=params["material"]).distinct()
    receipts = MaterialReceipt.objects.posted()
    if params.get("receipt", "").isdigit():
        receipts = receipts.filter(pk=params["receipt"])
    elif params.get("supplier", "").isdigit():
        receipts = receipts.filter(supplier_id=params["supplier"])
    else:
        receipts = None
    if receipts is not None:
        runs = runs.filter(pk__in=traceability.runs_using(receipts))
    return runs.order_by("-date", "product__position", "id")


@permission(view="production.view_productionrun")
def batches(request):
    """Production batches: each product's run of a day, with its code, to trace from or to."""
    page = Paginator(filter_batches(request.GET), PAGE_SIZE).get_page(request.GET.get("page"))
    for run in page:
        run.code = traceability.batch_code(run)
    receipt_pk = request.GET.get("receipt", "")
    receipt = (
        MaterialReceipt.objects.select_related("supplier").filter(pk=receipt_pk).first()
        if receipt_pk.isdigit()
        else None
    )
    suppliers = Company.objects.filter(is_own=False, material_receipts__isnull=False).distinct().order_by("name")
    return render(
        request,
        "production/batches.html",
        {
            "page_obj": page,
            "products": Product.objects.all(),
            "materials": Material.objects.all(),
            "suppliers": suppliers,
            "receipt": receipt,  # forward tracing from one delivery
        },
    )


@permission(view="production.view_productionrun")
def batch(request, pk):
    """One batch: what it was made of, as recorded that day, and the deliveries each material came from."""
    run = get_object_or_404(ProductionRun.objects.select_related("product", "created_by"), pk=pk)
    return render(
        request,
        "production/batch_detail.html",
        {
            "run": run,
            "code": traceability.batch_code(run),
            "materials": traceability.batch_materials(run),
            "shipping": traceability.batch_destinations(run),
        },
    )


@permission(view="production.view_productionrun")
def batches_export(request):
    """The traceability register: one row per batch, material and delivery, with the page's filters."""
    rows = []
    for row in traceability.register_rows(filter_batches(request.GET)):
        run, line, piece = row["run"], row["line"], row["piece"]
        lot = piece.lot if piece else None
        if piece is None:
            source = str(_("Not tracked (history imported before stock was kept)")) if not run.affects_stock else ""
        elif lot is None:
            source = str(_("More than the recorded stock"))
        else:
            source = lot.label
        rows.append(
            [
                traceability.batch_code(run), run.date, run.product.name, run.quantity, line.material.name,
                line.material.unit, line.consumed, piece.quantity if piece else None, source,
                lot.supplier.name if lot and lot.supplier else "", lot.supplier.inn if lot and lot.supplier else "",
                lot.date if lot else None,
            ]
        )  # fmt: skip
    headers = [
        _("Batch"), _("Production date"), _("Product"), _("Units"), _("Material"), _("Unit"), _("Used"),
        _("From this delivery"), _("Delivery"), _("Supplier"), _("INN"), _("Delivery date"),
    ]  # fmt: skip
    sheet = exports.Sheet(str(_("Batch traceability")), [str(header) for header in headers], rows)
    return exports.xlsx_response(exports.dated_filename(_("Batch traceability")), [sheet])


@require_POST
@permission(change="production.change_materialreceipt")
def receipt_cancel(request, pk):
    """Cancel a receipt (its materials leave stock) or restore it. A wrong receipt is cancelled, then entered again."""
    document = get_object_or_404(MaterialReceipt, pk=pk)
    restoring = document.is_cancelled
    same_number = MaterialReceipt.objects.posted().filter(supplier=document.supplier_id, number=document.number)
    if restoring and same_number.exists():
        messages.error(request, DUPLICATE_NUMBER % {"number": document.number})
        return redirect("production:receipt", pk=document.pk)
    with transaction.atomic():
        document.is_cancelled = not restoring
        document.save(update_fields=["is_cancelled"])
        document.rebuild_moves()
    messages.success(request, _("Receipt restored.") if restoring else _("Receipt cancelled."))
    return redirect("production:receipt", pk=document.pk)


@permission(view="production.add_materialmove")
def material_write_off(request):
    return move_form(
        request, MaterialWriteOffForm, _("Material write-off"), reverse("production:materials"),
        _("Write-off recorded."),
    )  # fmt: skip


@permission(view="production.add_productshipment")
def shipment(request):
    """A new shipment: the customer first, then its products and quantities (and the waybill number).

    If a product would go below zero in stock, the page says so first; the user then
    corrects or ships anyway (stock not yet posted, for example).
    """
    document = ProductShipment(created_by=request.user)
    form = ShipmentForm(request.POST or None, instance=document)
    lines = ShipmentLineFormSet(request.POST or None, instance=document, prefix="lines")
    short = []
    if request.method == "POST" and all([form.is_valid(), lines.is_valid()]):
        stock = product_stock()
        short = [
            (product, quantity, stock.get(product.pk, 0))
            for product, quantity in lines.items().items()
            if quantity > stock.get(product.pk, 0) and not form.cleaned_data["is_return"]  # a return brings goods in
        ]
        if not short or "confirm" in request.POST:
            try:
                with transaction.atomic():
                    document = form.save()
                    document.assign_number()
                    lines.instance = document
                    lines.save()
                    document.rebuild_moves()
            except IntegrityError:  # the same number was saved a moment ago, e.g. a double click
                form.add_error("number", _("Waybill № %(number)s is already used.") % {"number": document.number})
            else:
                messages.success(
                    request,
                    _("Shipment № %(number)s to %(customer)s recorded: %(count)s products.")
                    % {"number": document.number, "customer": document.customer.name, "count": document.lines.count()},
                )
                return redirect("production:shipment_detail", pk=document.pk)
        else:
            messages.warning(
                request, _("Some products would go below zero in stock. Check them, then ship anyway or correct.")
            )
    return render(request, "production/shipment_form.html", {"form": form, "lines": lines, "short": short})


def filter_shipments(params):
    """The shipments journal's filters (customer, product, dates, number, status), shared with its Excel file."""
    shipments = ProductShipment.objects.select_related("customer", "created_by")
    if params.get("customer", "").isdigit():
        shipments = shipments.filter(customer_id=params["customer"])
    if params.get("product", "").isdigit():
        shipments = shipments.filter(lines__product_id=params["product"]).distinct()
    date_from, date_to = parse_date(params.get("from") or ""), parse_date(params.get("to") or "")
    if date_from:
        shipments = shipments.filter(date__gte=date_from)
    if date_to:
        shipments = shipments.filter(date__lte=date_to)
    if number := params.get("q", "").strip():
        shipments = shipments.filter(number__contains=number.upper())  # numbers are kept in capitals
    if params.get("status") == "cancelled":
        shipments = shipments.filter(is_cancelled=True)
    elif params.get("status") != "all":
        shipments = shipments.posted()
    return shipments.order_by("-date", "-id")


@permission(view="production.view_productshipment")
def shipments(request):
    """Shipments journal: what each customer got, and when, document by document, with totals per product."""
    found = filter_shipments(request.GET)
    lines = ProductShipmentLine.objects.select_related("product")
    page = Paginator(found.prefetch_related(Prefetch("lines", queryset=lines)), PAGE_SIZE).get_page(
        request.GET.get("page")
    )
    totals = (
        ProductShipmentLine.objects.filter(shipment__in=found, shipment__is_return=False)
        .values("product__name")
        .annotate(units=Sum("quantity"), amount=Sum("amount"))
        .order_by("product__name")
    )
    return render(
        request,
        "production/shipments.html",
        {
            "page_obj": page,
            "totals": totals,
            "customers": Company.objects.filter(is_own=False).order_by("name"),
            "products": Product.objects.all(),
            # A SmartUp report read but not loaded yet (shipments_import_review): nothing left stock.
            "pending_import": request.session.get(IMPORT_SESSION_KEY),
        },
    )


@permission(view="production.view_productshipment")
def shipments_export(request):
    """The journal's lines (one row per product) as an .xlsx file, with the filters applied on the page."""
    lines = (
        ProductShipmentLine.objects.filter(shipment__in=filter_shipments(request.GET))
        .select_related("shipment__customer", "shipment__created_by", "product")
        .order_by("-shipment__date", "-shipment_id", "id")
    )
    rows = [
        [
            line.shipment.date, line.shipment.number, line.shipment.customer.name, line.shipment.customer.inn,
            str(_("Return") if line.shipment.is_return else _("Shipment")), line.shipment.channel,
            line.product.name, line.quantity, line.price, line.amount,
            str(_("Cancelled") if line.shipment.is_cancelled else _("Posted")),
            line.shipment.note, str(line.shipment.created_by or ""),
        ]
        for line in lines
    ]  # fmt: skip
    headers = [
        _("Date"), _("Waybill number"), _("Customer"), _("INN"), _("Type"), _("Project"), _("Product"), _("Units"),
        _("Price"), _("Amount"), _("Status"), _("Note"), _("Author"),
    ]  # fmt: skip
    sheet = exports.Sheet(str(_("Shipments")), [str(header) for header in headers], rows, money_columns=(8, 9))
    return exports.xlsx_response(exports.dated_filename(_("Shipments")), [sheet])


@permission(view="production.view_productshipment")
def shipment_detail(request, pk):
    """One shipment, and the batches each of its products came from (oldest stock shipped first)."""
    lines = ProductShipmentLine.objects.select_related("product")
    document = get_object_or_404(
        ProductShipment.objects.select_related("customer", "created_by").prefetch_related(
            Prefetch("lines", queryset=lines)
        ),
        pk=pk,
    )
    batches = {} if document.is_return else traceability.shipment_batches(document)
    rows = [(line, batches.get(line.pk, [])) for line in document.lines.all()]
    units = sum(line.quantity for line in document.lines.all())
    return render(request, "production/shipment_detail.html", {"shipment": document, "rows": rows, "units": units})


@require_POST
@permission(change="production.change_productshipment")
def shipment_cancel(request, pk):
    """Cancel a shipment (its goods return to stock) or restore it. A wrong one is cancelled, then entered again."""
    document = get_object_or_404(ProductShipment, pk=pk)
    restoring = document.is_cancelled
    if restoring and ProductShipment.objects.posted().filter(number=document.number).exists():
        messages.error(request, _("Waybill № %(number)s is already used.") % {"number": document.number})
        return redirect("production:shipment_detail", pk=document.pk)
    with transaction.atomic():
        document.is_cancelled = not restoring
        document.save(update_fields=["is_cancelled"])
        document.rebuild_moves()
    messages.success(request, _("Shipment restored.") if restoring else _("Shipment cancelled."))
    return redirect("production:shipment_detail", pk=document.pk)


# --- Shipments from SmartUp's sales report (shipment_import.py) ---------------------------

IMPORT_SESSION_KEY = "shipment_import"


@permission(view="production.add_productshipment", change="production.add_productshipment")
def shipments_import(request):
    """Upload SmartUp's sales report; it is read and kept for the review page, nothing is saved yet."""
    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload is None or not upload.name.lower().endswith(".xlsx"):
            messages.error(request, _("Choose the report as an .xlsx file."))
        else:
            try:
                report = shipment_import.read_report(upload)
            except shipment_import.ReportError as error:
                messages.error(request, str(error))
            else:
                request.session[IMPORT_SESSION_KEY] = {
                    "file": upload.name,
                    "title": report.title,
                    "rows": [row.to_json() for row in report.rows],
                    "errors": report.errors,
                }
                return redirect("production:shipments_import_review")
    return render(request, "production/shipment_import.html", {"step": "upload"})


@permission(view="production.add_productshipment", change="production.add_productshipment")
def shipments_import_review(request):
    """Match the report's unknown products and customers, see the documents it makes, then load them."""
    data = request.session.get(IMPORT_SESSION_KEY)
    if data is None:
        return redirect("production:shipments_import")
    rows = [shipment_import.ReportRow.from_json(row) for row in data["rows"]]
    matching = shipment_import.match(rows)

    if request.method == "POST":
        if "discard" in request.POST:
            del request.session[IMPORT_SESSION_KEY]
            return redirect("production:shipments")
        if "match" in request.POST:
            if _remember_matches(request, matching):
                messages.success(request, _("Names matched; the next report will be matched by itself."))
            return redirect("production:shipments_import_review")
        if "load" in request.POST and matching.complete:
            the_plan = shipment_import.load(shipment_import.plan(rows, matching), user=request.user)
            del request.session[IMPORT_SESSION_KEY]
            messages.success(
                request,
                _("Report loaded: %(new)s new documents, %(changed)s updated, %(same)s unchanged.")
                % {"new": the_plan.count("new"), "changed": the_plan.count("changed"), "same": the_plan.count("same")},
            )
            dates = [document.date for document in the_plan.documents]
            query = f"?from={min(dates).isoformat()}&to={max(dates).isoformat()}" if dates else ""
            return redirect(reverse("production:shipments") + query)

    context = {"step": "review", "data": data, "matching": matching, "rows": len(rows)}
    if matching.complete:
        the_plan = shipment_import.plan(rows, matching)
        stock, change = product_stock(), the_plan.stock_change()
        products = Product.objects.in_bulk(change)
        shipped = [document for document in the_plan.documents if not document.is_return]
        returned = [document for document in the_plan.documents if document.is_return]
        context |= {
            "plan": the_plan,
            "documents": the_plan.documents,
            "counts": {status: the_plan.count(status) for status in ("new", "changed", "same", "cancelled")},
            "totals": {
                "shipped_units": sum(document.quantity for document in shipped),
                "shipped_amount": sum((document.amount for document in shipped), Decimal(0)),
                "returned_units": sum(document.quantity for document in returned),
                "returned_amount": sum((document.amount for document in returned), Decimal(0)),
            },
            "short": [
                (products[pk], units, stock.get(pk, 0), stock.get(pk, 0) - units)
                for pk, units in sorted(change.items(), key=lambda item: products[item[0]].name)
                if units > 0 and stock.get(pk, 0) - units < 0
            ],
        }
    else:
        context |= {
            "products": Product.objects.all(),
            "companies": Company.objects.select_related("parent").order_by("name"),
        }
    return render(request, "production/shipment_import.html", context)


def _remember_matches(request, matching) -> bool:
    """Save the names matched on the review page; False (and an error message) if one was left unmatched."""
    products = Product.objects.in_bulk()
    companies = Company.objects.in_bulk()
    choices = []
    for index, name in enumerate(matching.unknown_products):
        value = request.POST.get(f"product-{index}", "")
        if value == "skip":
            choices.append((shipment_import.remember_product, name, None))
        elif value.isdigit() and int(value) in products:
            choices.append((shipment_import.remember_product, name, products[int(value)]))
    for index, name in enumerate(matching.unknown_customers):
        value = request.POST.get(f"customer-{index}", "")
        if value == "new":
            choices.append((shipment_import.remember_customer, name, None))
        elif value.isdigit() and int(value) in companies:
            choices.append((shipment_import.remember_customer, name, companies[int(value)]))
    if len(choices) < len(matching.unknown_products) + len(matching.unknown_customers):
        messages.error(request, _("Choose a match for every name, then save."))
        return False
    with transaction.atomic():
        for remember, name, target in choices:
            remember(name, target)
    return True


def stock_count(request, kind):
    """Record a stock count for a material or a product; the books are corrected to it."""
    model, back = (Material, "production:materials") if kind == "material" else (Product, "production:products")
    if not request.user.has_perm(f"production.add_{model._meta.model_name}move"):
        raise PermissionDenied
    form = StockCountForm(request.POST or None, model=model, initial={"item": request.GET.get("item")})
    if request.method == "POST" and form.is_valid():
        move = services.count_stock(
            form.cleaned_data["item"],
            form.cleaned_data["counted"],
            form.cleaned_data["date"],
            note=form.cleaned_data["note"],
            user=request.user,
        )
        messages.success(request, _("Stock count recorded: correction %(difference)s.") % {"difference": move.quantity})
        return redirect(back)
    return render(
        request, "production/move_form.html", {"form": form, "title": _("Stock count"), "back_url": reverse(back)}
    )


class RecipeListView(PermissionMixin, TemplateView):
    """Products with their recipe size and how many units current stock allows."""

    template_name = "production/recipe_list.html"
    permission_required = "production.view_recipeline"

    def get_context_data(self, **kwargs):
        products = Product.objects.prefetch_related("recipe")
        possible = services.units_possible(products)
        return super().get_context_data(
            rows=[{"product": product, "possible": possible[product.pk]} for product in products],
            can_load=self.request.user.has_perms(RECIPE_IMPORT_PERMISSIONS),
            **kwargs,
        )


RECIPE_EDIT_PERMISSIONS = (
    "production.change_product",
    "production.add_recipeline",
    "production.change_recipeline",
    "production.delete_recipeline",
)


@permission(view="production.view_recipeline", change=RECIPE_EDIT_PERMISSIONS)
def recipe_edit(request, pk):
    """A product and its recipe (norm per unit for each material)."""
    product = get_object_or_404(Product, pk=pk)
    lines = Prefetch("recipe", queryset=product.recipe.select_related("material"))
    product = Product.objects.prefetch_related(lines).get(pk=pk)
    form = ProductForm(request.POST or None, instance=product)
    formset = RecipeFormSet(request.POST or None, instance=product)
    can_edit = request.user.has_perms(RECIPE_EDIT_PERMISSIONS)
    if not can_edit:  # read-only: the norms are shown, not offered for editing
        for each in (form, *formset):
            for field in each.fields.values():
                field.disabled = True
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            form.save()
            formset.save()
        messages.success(request, _("Recipe saved. It applies to production entered from now on."))
        return redirect("production:recipes")
    stock = material_stock()
    for line in formset:
        line.stock = stock.get(line.instance.material_id, Decimal(0))
    return render(
        request,
        "production/recipe_form.html",
        {"product": product, "form": form, "formset": formset, "can_edit": can_edit},
    )


@permission(view="production.view_recipeline")
def recipe_export(request):
    """Products, materials, recipes and other product names in Excel, in the form the upload reads back."""
    return exports.xlsx_response(exports.dated_filename(_("Recipes")), recipe_excel.recipe_sheets())


def seed_file(kind: str) -> dict | None:
    """The project's file of ``kind`` (production/seed/), as the load pages list it."""
    path = recipe_excel.seed_path(kind)
    if not path.is_file():
        return None
    stat = path.stat()
    modified = datetime.datetime.fromtimestamp(stat.st_mtime, tz=datetime.UTC)
    return {"path": path, "size": stat.st_size, "modified": modified}


def missing_seed_message(request, kind: str):
    messages.error(
        request,
        _("The project has no such file yet. Write it with: manage.py %(command)s --seed")
        % {"command": f"export_{kind}"},
    )


def seed_download(request, kind: str, back: str):
    response = recipe_excel.seed_response(kind)
    if response is None:
        missing_seed_message(request, kind)
        return redirect(back)
    return response


def load_message(request, report, saved_message):
    if report.errors:
        messages.error(request, _("Nothing was saved: correct the rows listed below and upload again."))
    elif report.saved:
        messages.success(request, saved_message)
    else:
        messages.info(request, _("Checked: nothing was saved. Untick “Only check” to load."))


@permission(view="production.view_recipeline")
def recipe_seed(request):
    """The project's recipes file (production/seed/), e.g. to load into a new server."""
    return seed_download(request, "recipes", "production:recipes")


# Loading recipes may create products and materials, as on a new server.
RECIPE_IMPORT_PERMISSIONS = (
    *RECIPE_EDIT_PERMISSIONS,
    "production.add_product",
    "production.add_productalias",
    "production.change_productalias",
    "production.add_material",
    "production.change_material",
)


@permission(view="production.view_recipeline", change=RECIPE_IMPORT_PERMISSIONS)
def recipe_import(request):
    """Load products, materials, recipes and other product names from Excel, e.g. into a new server's database."""
    seed = seed_file("recipes")
    using_seed = request.method == "POST" and "use_seed" in request.POST and seed
    form = RecipeUploadForm(None if using_seed else request.POST or None, request.FILES or None)
    report = None
    try:
        if using_seed:
            report = recipe_excel.load_recipes(seed["path"], dry_run="dry_run" in request.POST)
        elif request.method == "POST" and form.is_valid():
            report = recipe_excel.load_recipes(form.cleaned_data["file"], dry_run=form.cleaned_data["dry_run"])
    except HandbookError as error:
        if using_seed:
            messages.error(request, str(error))
        else:
            form.add_error("file", str(error))
    if report is not None:
        load_message(request, report, _("Recipes loaded. They apply to production entered from now on."))
    return render(
        request,
        "production/recipe_import.html",
        {
            "form": form,
            "report": report,
            "seed_file": seed,
            "seed_dry_run": not using_seed or "dry_run" in request.POST,
            "can_load": request.user.has_perms(RECIPE_IMPORT_PERMISSIONS),
        },
    )


@permission(view="production.view_material")
def material_export(request):
    """Raw materials and packaging with their current stock in Excel, in the form the upload reads back."""
    filename = exports.dated_filename(_("Materials and packaging"))
    return exports.xlsx_response(filename, recipe_excel.material_sheets())


@permission(view="production.view_material")
def material_seed(request):
    """The project's materials file (production/seed/), e.g. to load into a new server."""
    return seed_download(request, "materials", "production:materials")


MATERIAL_IMPORT_PERMISSIONS = ("production.add_material", "production.change_material")
OPENING_PERMISSIONS = ("production.add_materialmove", "production.change_materialmove")


@permission(view="production.view_material", change=MATERIAL_IMPORT_PERMISSIONS)
def material_import(request):
    """Load raw materials and packaging from Excel, and their stock as opening balances if asked.

    The project's file or an uploaded one. The stock is loaded only for materials
    with no stock movements yet, so a server already in use is never double-counted.
    """
    seed = seed_file("materials")
    using_seed = request.method == "POST" and "use_seed" in request.POST
    form = MaterialUploadForm(request.POST or None, request.FILES or None, use_seed=using_seed)
    can_open = request.user.has_perms(OPENING_PERMISSIONS)
    if not can_open:  # stock is recorded only by those who record stock movements
        del form.fields["opening"], form.fields["opening_date"]
    report = None
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        if using_seed and not seed:
            missing_seed_message(request, "materials")
        else:
            try:
                report = recipe_excel.load_recipes(
                    seed["path"] if using_seed else data["file"],
                    dry_run=data["dry_run"],
                    only=(recipe_excel.MATERIALS,),
                    opening_date=data["opening_date"] if data.get("opening") else None,
                    user=request.user,
                )
            except HandbookError as error:
                form.add_error(None if using_seed else "file", str(error))
    if report is not None:
        load_message(request, report, _("Materials loaded."))
    return render(
        request,
        "production/material_import.html",
        {
            "form": form,
            "report": report,
            "seed_file": seed,
            "can_load": request.user.has_perms(MATERIAL_IMPORT_PERMISSIONS),
            "can_open": can_open,
        },
    )


class MaterialCreateView(PermissionMixin, SuccessMessageMixin, CreateView):
    permission_required = "production.add_material"
    model = Material
    form_class = MaterialForm
    template_name = "production/simple_form.html"
    success_url = reverse_lazy("production:materials")
    success_message = _("Saved.")
    extra_context = {"title": _("New material"), "cancel_url": success_url}


class MaterialUpdateView(PermissionMixin, SuccessMessageMixin, UpdateView):
    permission_required = "production.change_material"
    model = Material
    form_class = MaterialForm
    template_name = "production/simple_form.html"
    success_url = reverse_lazy("production:materials")
    success_message = _("Saved.")
    extra_context = {"title": _("Edit material"), "cancel_url": success_url}


class ProductCreateView(PermissionMixin, SuccessMessageMixin, CreateView):
    permission_required = "production.add_product"
    model = Product
    form_class = ProductForm
    template_name = "production/simple_form.html"
    success_message = _("Product added. Now enter its recipe.")
    extra_context = {"title": _("New product"), "cancel_url": reverse_lazy("production:recipes")}

    def get_success_url(self):
        return reverse("production:recipe_edit", args=[self.object.pk])


def moves(request):
    """All stock movements of materials or products, filtered and paginated."""
    target = request.GET.get("target", "materials")
    model, field = (ProductMove, "product") if target == "products" else (MaterialMove, "material")
    if not request.user.has_perm(f"production.view_{model._meta.model_name}"):
        raise PermissionDenied
    items = model.objects.select_related(field, "created_by").order_by("-date", "-id")
    if request.GET.get("item"):
        items = items.filter(**{f"{field}_id": request.GET["item"]})
    if request.GET.get("kind"):
        items = items.filter(kind=request.GET["kind"])
    date_from, date_to = parse_date(request.GET.get("from") or ""), parse_date(request.GET.get("to") or "")
    if date_from:
        items = items.filter(date__gte=date_from)
    if date_to:
        items = items.filter(date__lte=date_to)
    page = Paginator(items, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "production/moves.html",
        {
            "page_obj": page,
            "target": target,
            "field": field,
            "kinds": model.Kind.choices,
            "choices": (Product if target == "products" else Material).objects.all(),
            "can_cancel": request.user.has_perm(f"production.change_{model._meta.model_name}"),
        },
    )


@require_POST
def cancel_move(request, target, pk):
    """Cancel (or restore) a movement entered by hand. Production movements change with their day."""
    model = ProductMove if target == "products" else MaterialMove
    if not request.user.has_perm(f"production.change_{model._meta.model_name}"):
        raise PermissionDenied
    move = get_object_or_404(model, pk=pk, run__isnull=True)
    if move.made_by_document:
        raise Http404
    move.is_cancelled = not move.is_cancelled
    move.save(update_fields=["is_cancelled"])
    messages.success(request, _("Movement cancelled.") if move.is_cancelled else _("Movement restored."))
    return redirect(f"{reverse('production:moves')}?target={target}")


@permission(view="production.import_excel")
def import_excel(request):
    """Load products, materials, stock and recipes from uploaded workbooks.

    The files are read in memory and not kept, so this works on a server that has
    no access to the workbooks' folder. "Only check" shows what would be imported
    without saving; importing again only adds what is still missing.
    """
    form = ImportForm(request.POST or None, request.FILES or None)
    report = None
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            books = importer.read_workbooks(data["recipes"], data["warehouse"])
            report = importer.import_workbooks(
                books,
                data["stock_date"],
                source=f"{data['recipes'].name}/Сбор",
                user=request.user,
                dry_run=data["dry_run"],
            )
        except importer.SheetError as error:
            form.add_error(None, str(error))
        else:
            if report.saved:
                messages.success(request, _("Excel data imported."))
            elif report.unmatched:
                messages.error(request, _("Nothing was imported: some recipe materials are not in the stock list."))
            else:
                messages.info(request, _("Checked: nothing was saved. Untick “Only check” to import."))
    return render(request, "production/import.html", {"form": form, "report": report})
