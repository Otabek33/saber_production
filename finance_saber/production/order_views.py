"""Goods in transit (товары в пути): orders to suppliers, from ordering to receiving them into stock."""

from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from finance_saber.cashbook import exports
from finance_saber.cashbook.models import Company
from finance_saber.core.permissions import permission

from .models import (
    Material,
    MaterialOrder,
    MaterialOrderLine,
    MaterialReceipt,
    MaterialReceiptLine,
    with_received,
)
from .order_forms import MaterialOrderForm, OrderLineFormSet, ReceiveOrderForm

VIEW = "production.view_materialorder"
CHANGE = ("production.add_materialorder", "production.change_materialorder")
PAGE_SIZE = 20


def filter_orders(params):
    """The list's filters: status (open by default), supplier, material; late ones only."""
    orders = MaterialOrder.objects.select_related("supplier")
    status = params.get("status", "")
    if status == "all":
        pass
    elif status in MaterialOrder.Status.values:
        orders = orders.filter(status=status)
    else:
        orders = orders.filter(status__in=MaterialOrder.OPEN)
    if params.get("supplier", "").isdigit():
        orders = orders.filter(supplier_id=params["supplier"])
    if params.get("material", "").isdigit():
        orders = orders.filter(lines__material_id=params["material"]).distinct()
    if params.get("late"):
        orders = orders.filter(status__in=MaterialOrder.OPEN, expected_date__lt=timezone.localdate())
    return orders.order_by("expected_date", "id")


@permission(view=VIEW)
def orders(request):
    """Goods in transit: what was ordered from whom, and when it is expected."""
    found = filter_orders(request.GET)
    lines = with_received(MaterialOrderLine.objects.select_related("material"))
    found = found.prefetch_related(Prefetch("lines", queryset=lines))
    if request.GET.get("export") == "xlsx":
        return _orders_xlsx(found)
    open_orders = MaterialOrder.objects.filter(status__in=MaterialOrder.OPEN)
    return render(
        request,
        "production/orders.html",
        {
            "page_obj": Paginator(found, PAGE_SIZE).get_page(request.GET.get("page")),
            "open_count": open_orders.count(),
            "late_count": open_orders.filter(expected_date__lt=timezone.localdate()).count(),
            "statuses": MaterialOrder.Status.choices,
            "suppliers": Company.objects.filter(material_orders__isnull=False).distinct().order_by("name"),
            "materials": Material.objects.filter(order_lines__isnull=False).distinct().order_by("name"),
        },
    )


def _orders_xlsx(found):
    headers = [
        _("Order number"), _("Supplier"), _("Order date"), _("Expected arrival"), _("Status"), _("Material"),
        _("Unit"), _("Quantity"), _("Currency"), _("Amount"),
    ]  # fmt: skip
    rows = [
        [
            order.number, order.supplier.name, order.order_date, order.expected_date, str(order.get_status_display()),
            line.material.name, line.material.unit, line.quantity, order.currency, line.amount,
        ]
        for order in found
        for line in order.lines.all()
    ]  # fmt: skip
    sheet = exports.Sheet(str(_("Goods in transit")), [str(header) for header in headers], rows, money_columns=(9,))
    return exports.xlsx_response(exports.dated_filename(_("Goods in transit")), [sheet])


@permission(view=CHANGE, change=CHANGE)  # the form itself is only for those who may enter orders
def order_form(request, pk=None):
    """A new order, or an open one changed: the supplier first, then the materials, quantities and amounts."""
    if pk is None:
        order = MaterialOrder(created_by=request.user)
    else:
        order = get_object_or_404(MaterialOrder, pk=pk)
        if not order.is_open:
            messages.error(request, _("A received or cancelled order can't be changed."))
            return redirect("production:order", pk=order.pk)
    form = MaterialOrderForm(request.POST or None, instance=order)
    lines = OrderLineFormSet(request.POST or None, instance=order, prefix="lines")
    if request.method == "POST" and all([form.is_valid(), lines.is_valid()]):
        with transaction.atomic():
            order = form.save()
            order.assign_number()
            lines.instance = order
            lines.save()
        messages.success(
            request,
            _("Order № %(number)s saved: expected on %(date)s.")
            % {"number": order.number, "date": order.expected_date.strftime("%d.%m.%Y")},
        )
        return redirect("production:order", pk=order.pk)
    units = {material.pk: material.unit for material in Material.objects.filter(is_active=True)}
    return render(request, "production/order_form.html", {"form": form, "lines": lines, "units": units, "order": order})


@permission(view=VIEW, change=CHANGE)
def order_detail(request, pk):
    """One order: its lines with what arrived and what is still to come; their status and arrival; receiving."""
    order = _order(pk)
    return _detail(request, order, ReceiveOrderForm(order=order, lines=order.lines.all()))


def _order(pk) -> MaterialOrder:
    lines = with_received(MaterialOrderLine.objects.select_related("material"))
    receipts = MaterialReceipt.objects.prefetch_related("lines").order_by("date", "id")
    return get_object_or_404(
        MaterialOrder.objects.select_related("supplier", "created_by").prefetch_related(
            Prefetch("lines", queryset=lines), Prefetch("receipts", queryset=receipts)
        ),
        pk=pk,
    )


def _detail(request, order, receive):
    statuses = [(value, label) for value, label in MaterialOrder.Status.choices if value in MaterialOrder.ON_THE_WAY]
    context = {"order": order, "receive": receive, "statuses": statuses}
    return render(request, "production/order_detail.html", context)


@require_POST
@permission(view=VIEW, change=CHANGE)
def order_status(request, pk):
    """The whole order's goods: ordered, in transit, at customs (every line still to come); cancel or restore it."""
    order = get_object_or_404(MaterialOrder, pk=pk)
    status = request.POST.get("status")
    if order.status == MaterialOrder.Status.RECEIVED:
        messages.error(request, _("The order is received: cancel its material receipt instead."))
    elif status == MaterialOrder.Status.CANCELLED:
        if order.receipts.posted().exists():
            messages.error(request, _("Part of the order has arrived: cancel its material receipts first."))
        else:
            order.status = status
            order.save(update_fields=["status", "updated_at"])
            messages.success(request, _("Status: %(status)s.") % {"status": order.get_status_display()})
    elif status in MaterialOrder.ON_THE_WAY:
        with transaction.atomic():
            for line in with_received(order.lines.all()):
                if line.remaining:
                    line.status = status
                    line.save(update_fields=["status"])
            if order.status != MaterialOrder.Status.PARTIAL:
                order.status = status
                order.save(update_fields=["status", "updated_at"])
        messages.success(request, _("Status: %(status)s.") % {"status": MaterialOrder.Status(status).label})
    return redirect("production:order", pk=order.pk)


@require_POST
@permission(view=VIEW, change=CHANGE)
def order_lines(request, pk):
    """Each line's own status and expected arrival: the goods may travel separately."""
    order = get_object_or_404(MaterialOrder, pk=pk)
    if not order.is_open:
        messages.error(request, _("A received or cancelled order can't be changed."))
        return redirect("production:order", pk=order.pk)
    changed = 0
    for line in order.lines.all():
        status = request.POST.get(f"line-{line.pk}-status")
        expected = parse_date(request.POST.get(f"line-{line.pk}-expected") or "")
        if status not in MaterialOrder.ON_THE_WAY:
            continue
        if expected and expected < order.order_date:
            messages.error(request, _("The goods can't arrive before they are ordered."))
            return redirect("production:order", pk=order.pk)
        expected = None if expected == order.expected_date else expected
        if (line.status, line.expected_date) != (status, expected):
            line.status, line.expected_date = status, expected
            line.save(update_fields=["status", "expected_date"])
            changed += 1
    messages.success(request, _("Lines updated: %(count)s.") % {"count": changed})
    return redirect("production:order", pk=order.pk)


@require_POST
@permission(view=VIEW, change=CHANGE + ("production.add_materialreceipt",))
def order_receive(request, pk):
    """What arrived, all or part: its material receipt (in UZS at the rate); the rest stays on the way."""
    order = _order(pk)
    if not order.is_open:
        messages.error(request, _("Only an open order can be received."))
        return redirect("production:order", pk=order.pk)
    form = ReceiveOrderForm(request.POST, order=order, lines=order.lines.all())
    if not form.is_valid():
        return _detail(request, order, form)
    rate = form.cleaned_data.get("rate") or Decimal(1)
    try:
        with transaction.atomic():
            receipt = MaterialReceipt.objects.create(
                supplier=order.supplier,
                date=form.cleaned_data["date"],
                number=form.cleaned_data["number"],
                note=str(_("By order № %(number)s") % {"number": order.number})[:255],
                order=order,
                currency=order.currency,
                rate=rate if order.currency != MaterialOrder.Currency.UZS else None,
                created_by=request.user,
            )
            lines = []
            for line, quantity in form.arrived():
                # The line's amount for the share that came: as ordered (its currency) and in UZS.
                share = (line.amount * quantity / line.quantity).quantize(Decimal("0.01"))
                lines.append(
                    MaterialReceiptLine(
                        receipt=receipt, material_id=line.material_id, order_line=line, quantity=quantity,
                        currency_amount=share, amount=(share * rate).quantize(Decimal("0.01")),
                        best_before=form.best_before(line),
                    )
                )  # fmt: skip
            MaterialReceiptLine.objects.bulk_create(lines)
            receipt.rebuild_moves()
            order.refresh_status()
    except IntegrityError:  # the same receipt number saved a moment ago
        form.add_error("number", _("This supplier's document № %(number)s is already entered.") % {
            "number": form.cleaned_data["number"]
        })  # fmt: skip
        return _detail(request, order, form)
    if order.status == MaterialOrder.Status.RECEIVED:
        text = _("Order № %(number)s received in full: the materials are in stock (receipt № %(receipt)s).")
    else:
        text = _("Part of order № %(number)s received (receipt № %(receipt)s); the rest is still on the way.")
    messages.success(request, text % {"number": order.number, "receipt": receipt.number})
    return redirect("production:order", pk=order.pk)
