import calendar
import datetime
from decimal import Decimal

from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
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
from finance_saber.core.permissions import PermissionMixin, permission

from . import importer, recipe_excel, services
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
    RecipeFormSet,
    RecipeUploadForm,
    ShipmentForm,
    StockCountForm,
)
from .models import Material, MaterialMove, Product, ProductionRun, ProductMove, material_stock, product_stock

PAGE_SIZE = 20


def selected_date(request) -> datetime.date:
    return parse_date(request.GET.get("date") or "") or timezone.localdate()


@permission(
    view="production.view_productionrun",
    change=("production.add_productionrun", "production.change_productionrun"),
)
def daily(request):
    """Fill in one day's production: a quantity per product, materials written off by recipe.

    GET shows the day (today by default). POST first shows what materials the
    quantities use and warns about any that would go below zero; the user then
    confirms, or saves straight away when nothing runs short.
    """
    date = selected_date(request)
    products = Product.objects.filter(is_active=True).prefetch_related("recipe")
    runs = ProductionRun.objects.filter(date=date, is_cancelled=False)
    initial = {run.product_id: run.quantity for run in runs}
    form = DailyProductionForm(request.POST or None, products=products, initial_quantities=initial)
    can_edit = request.user.has_perms(("production.add_productionrun", "production.change_productionrun"))
    if not can_edit:  # read-only: the day's figures are shown, not offered for editing
        for field in form.fields.values():
            field.disabled = True

    needs = []
    if request.method == "POST" and form.is_valid():
        quantities = form.quantities()
        needs = services.material_needs(quantities, date=date)
        if not any(need.is_short for need in needs) or "confirm" in request.POST:
            changed = services.save_day(date, quantities, user=request.user)
            messages.success(
                request,
                _("Production for %(date)s saved (%(count)s products changed).")
                % {"date": date.strftime("%d.%m.%Y"), "count": changed},
            )
            return redirect(f"{reverse('production:daily')}?date={date.isoformat()}")
        messages.warning(request, _("Some materials would go below zero. Check them, then save anyway or correct."))
    elif request.method == "GET":
        # What the saved day used; stock already includes it, so pass the date to add it back first.
        needs = services.material_needs(
            {run.product: run.quantity for run in runs.select_related("product")}, date=date
        )

    total = sum(initial.values())
    possible = services.units_possible(products)
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
        run, recipe_updated = services.save_calculation(
            day,
            product,
            form.cleaned_data["quantity"],
            form.cleaned_data["batch_quantity"],
            formset.lines(),
            user=request.user,
            update_recipe=can_change_recipe,
        )
        messages.success(
            request,
            _("Production saved: %(quantity)s units of %(product)s on %(date)s.")
            % {"quantity": f"{run.quantity:,}".replace(",", " "), "product": product.name,
               "date": day.strftime("%d.%m.%Y")},
        )  # fmt: skip
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


@permission(view="production.view_productionrun")
def journal(request):
    """A month of production as products × days, like the workbook's Warehouse sheet."""
    today = timezone.localdate()
    month = parse_date((request.GET.get("month") or "") + "-01") or today.replace(day=1)
    days = [month.replace(day=day) for day in range(1, calendar.monthrange(month.year, month.month)[1] + 1)]
    runs = ProductionRun.objects.filter(date__year=month.year, date__month=month.month, is_cancelled=False)
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
                "shipped": -(month.get(product.pk, {}).get(ProductMove.Kind.SHIPMENT) or 0),
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


@permission(view="production.add_materialmove")
def material_receipt(request):
    return move_form(
        request, MaterialReceiptForm, _("Material receipt"), reverse("production:materials"), _("Receipt recorded.")
    )


@permission(view="production.add_materialmove")
def material_write_off(request):
    return move_form(
        request, MaterialWriteOffForm, _("Material write-off"), reverse("production:materials"),
        _("Write-off recorded."),
    )  # fmt: skip


@permission(view="production.add_productmove")
def shipment(request):
    return move_form(request, ShipmentForm, _("Shipment"), reverse("production:products"), _("Shipment recorded."))


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
