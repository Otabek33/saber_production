import datetime

from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.core.paginator import Paginator
from django.db.models import Count, F, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, ListView, TemplateView, UpdateView

from finance_saber.core.permissions import PermissionMixin, permission

from . import bank, exports, handbooks, importer, workbook
from .forms import (
    ArticleForm,
    CategoryForm,
    CounterpartyForm,
    HandbookUploadForm,
    LedgerFilterForm,
    OperationForm,
    RegionForm,
    StatementUploadForm,
    WorkbookImportForm,
    WorkbookUploadForm,
)
from .ledger import build_ledger
from .models import (
    ACCOUNT_CURRENCY,
    ACCOUNT_DISPLAY,
    Article,
    ArticleKind,
    CashDesk,
    Category,
    Counterparty,
    Kind,
    Operation,
    Region,
)


def safe_next(request, url, fallback):
    """``url`` if it points back into this site, else ``fallback`` (avoids open redirects)."""
    allowed_hosts = {request.get_host()}
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts=allowed_hosts, require_https=request.is_secure()):
        return url
    return fallback


@permission(view="cashbook.view_operation")
def index(request):
    """The menu's "Cash desk" link: open the first active desk."""
    desk = CashDesk.objects.filter(is_active=True).first()
    if desk is None:
        messages.info(request, _("There is no cash desk yet. Add one in the admin."))
        return redirect("core:home")
    return redirect(desk)


class DeskMixin:
    @cached_property
    def desk(self):
        return get_object_or_404(CashDesk, slug=self.kwargs["desk"], is_active=True)

    def get_context_data(self, **kwargs):
        accounts = [
            {"value": value, "label": label, "currency": ACCOUNT_CURRENCY[value], **ACCOUNT_DISPLAY[value]}
            for value, label in self.desk.account_choices
        ]
        return super().get_context_data(
            desk=self.desk,
            desks=CashDesk.objects.filter(is_active=True),
            accounts=accounts,
            kinds=Kind,
            **kwargs,
        )


class OperationListView(PermissionMixin, DeskMixin, TemplateView):
    """The cash desk sheet: entry form on top, operations with running balances below.

    GET shows the ledger for the filtered period (this month by default). POST
    adds an operation and redirects back, keeping the filters and the entered
    date and type, so several operations can be entered in a row.
    """

    template_name = "cashbook/operation_list.html"
    view_permission = "cashbook.view_operation"
    permission_required = "cashbook.add_operation"
    PAGE_SIZE = 20

    def default_filters(self):
        today = timezone.localdate()
        return {"date_from": today.replace(day=1), "date_to": today}

    def get_filters(self):
        defaults = self.default_filters()
        form = LedgerFilterForm({**defaults, **self.request.GET.dict()}, desk=self.desk)
        if not form.is_valid():
            return form, defaults
        return form, form.cleaned_data

    def count_active_filters(self, filters) -> int:
        """Filters changed from the defaults (this month, everything shown), for the toolbar badge."""
        defaults = self.default_filters()
        changed_dates = any(filters.get(name) != value for name, value in defaults.items())
        return changed_dates + sum(bool(filters.get(name)) for name in ("kind", "article", "account", "region", "q"))

    def get_entry_form(self, data=None):
        last = self.desk.operations.order_by("-id").first()
        initial = {
            "kind": self.request.GET.get("entry_kind") or Kind.EXPENSE,
            "date": self.request.GET.get("entry_date") or timezone.localdate(),
            "account": self.desk.accounts[0],
            "rate": last.rate if last else None,
        }
        return OperationForm(data, initial=initial, desk=self.desk)

    def get(self, request, *args, **kwargs):
        return self.render_to_response(self.get_context_data(entry_form=self.get_entry_form()))

    def post(self, request, *args, **kwargs):
        form = self.get_entry_form(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(entry_form=form), status=400)
        operation = form.save(commit=False)
        operation.created_by = request.user
        operation.save()
        messages.success(request, _("Operation added."))
        # Keep the filters, prefill the next entry, and open the last page, where the new row is.
        query = request.GET.copy()
        query.pop("page", None)
        query["entry_date"] = operation.date.isoformat()
        query["entry_kind"] = operation.kind
        return redirect(f"{request.path}?{query.urlencode()}#entry")

    def get_context_data(self, **kwargs):
        filter_form, filters = self.get_filters()
        desk_operations = self.desk.operations.all()
        shown = desk_operations.filter(date__gte=filters["date_from"], date__lte=filters["date_to"])
        if filters.get("kind"):
            shown = shown.filter(kind=filters["kind"])
        if filters.get("article"):
            shown = shown.filter(category__article=filters["article"])
        if filters.get("account"):
            shown = shown.filter(Q(account=filters["account"]) | Q(to_account=filters["account"]))
        if filters.get("region"):
            shown = shown.filter(counterparty__region=filters["region"])
        if filters.get("q"):
            query = filters["q"]
            shown = shown.filter(
                Q(note__icontains=query)
                | Q(category__name__icontains=query)
                | Q(counterparty__name__icontains=query)
                | Q(counterparty__inn__icontains=query)
                | Q(document_number__icontains=query)
            )
        ledger = build_ledger(desk_operations, filters["date_from"], filters["date_to"], shown)
        if self.desk.is_bank:
            kwargs.setdefault("upload_form", StatementUploadForm())
            kwargs["unclassified_count"] = bank.unclassified_counterparties().count()

        # Pages of PAGE_SIZE rows, opening on the last (newest) one. Each page starts
        # with the balance carried over from the rows before it.
        paginator = Paginator(ledger.rows, self.PAGE_SIZE)
        page = paginator.get_page(self.request.GET.get("page") or paginator.num_pages)
        page_opening = page.object_list[0].balance_before if page.object_list else ledger.opening

        # Toolbar panels, the same on every desk: the entry form stays closed until its button
        # is pressed, and opens again only after an operation was just added or the form has errors.
        entry_form = kwargs.get("entry_form")
        active_filters = self.count_active_filters(filters)
        return super().get_context_data(
            filter_form=filter_form,
            filters=filters,
            ledger=ledger,
            page=page,
            page_range=paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1),
            page_opening=page_opening,
            entry_open="entry_kind" in self.request.GET or bool(entry_form and entry_form.is_bound),
            active_filters=active_filters,
            filters_open=bool(active_filters) or not filter_form.is_valid(),
            **kwargs,
        )


class OperationUpdateView(PermissionMixin, DeskMixin, SuccessMessageMixin, UpdateView):
    permission_required = "cashbook.change_operation"
    template_name = "cashbook/operation_form.html"
    form_class = OperationForm
    success_message = _("Operation updated.")

    def get_queryset(self):
        return self.desk.operations.all()

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "desk": self.desk}

    def get_success_url(self):
        return safe_next(self.request, self.request.GET.get("next"), self.desk.get_absolute_url())

    def get_context_data(self, **kwargs):
        return super().get_context_data(back_url=self.get_success_url(), **kwargs)


@require_POST
@permission(change="cashbook.add_operation")
def upload_statement(request, desk):
    """Record a bank statement (.xlsx) on a bank desk; lines recorded before are skipped."""
    desk = get_object_or_404(CashDesk, slug=desk, is_active=True, kind="bank")
    form = StatementUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        for error in form.errors.get("file", []):
            messages.error(request, error)
        return redirect(desk)
    try:
        lines = bank.read_statement(form.cleaned_data["file"])
    except bank.StatementError as error:
        messages.error(request, _("Couldn't read the statement: %(error)s") % {"error": error})
        return redirect(desk)
    report = bank.record_lines(desk, lines, user=request.user)
    messages.success(
        request,
        _("Statement uploaded: %(created)s lines added, %(duplicates)s already recorded.")
        % {"created": report.created, "duplicates": report.duplicates},
    )
    if report.new_counterparties or report.unclassified:
        messages.warning(
            request,
            _("%(count)s new counterparties and %(lines)s lines need an article: see Counterparties.")
            % {"count": len(report.new_counterparties), "lines": report.unclassified},
        )
    # Show the period the statement covers.
    if lines:
        dates = [line.posted_at.date() for line in lines]
        query = f"?date_from={min(dates).isoformat()}&date_to={max(dates).isoformat()}"
        return redirect(desk.get_absolute_url() + query)
    return redirect(desk)


# Session key holding the token of the workbook checked on the import page.
WORKBOOK_SESSION_KEY = "cashbook_workbook"


@permission(view="cashbook.add_operation")
def workbook_import(request):
    """Import the finance workbook (Situation.xlsx) into every cash desk at once.

    "Check" reads the uploaded workbook and shows, desk by desk, what would be
    added, without saving; the file waits in the temp folder. "Import" then
    imports that same file: desks without errors get their new rows and lines,
    and the user decides whether bank counterparties not in the app yet are added.
    """
    can_add = request.user.has_perm("cashbook.add_counterparty")
    upload_form = WorkbookUploadForm()
    confirm_form = WorkbookImportForm(can_add_counterparties=can_add)
    preview = result = None

    if request.method == "POST" and "import" in request.POST:
        token = request.session.get(WORKBOOK_SESSION_KEY)
        data = workbook.load(token)
        if data is None:
            messages.error(request, _("The checked workbook is no longer here. Upload it and check it again."))
            return redirect("cashbook:workbook_import")
        confirm_form = WorkbookImportForm(request.POST, can_add_counterparties=can_add)
        if confirm_form.is_valid():
            result = workbook.import_workbook(
                data,
                request.user,
                add_counterparties=confirm_form.add_counterparties,
                update_changed=confirm_form.cleaned_data["update_changed"],
            )
            workbook.discard(token)
            del request.session[WORKBOOK_SESSION_KEY]
            if result.has_errors:
                messages.warning(request, _("Imported, except the sections with errors below."))
            else:
                messages.success(request, _("Workbook imported."))
    elif request.method == "POST":
        upload_form = WorkbookUploadForm(request.POST, request.FILES)
        if upload_form.is_valid():
            data = upload_form.cleaned_data["file"].read()
            workbook.discard(request.session.pop(WORKBOOK_SESSION_KEY, None))
            preview = workbook.import_workbook(data, request.user, dry_run=True)
            if preview.has_new:  # keep the file for the "Import" click; otherwise there is nothing to import
                request.session[WORKBOOK_SESSION_KEY] = workbook.keep(data)

    return render(
        request,
        "cashbook/workbook_import.html",
        {
            "upload_form": upload_form,
            "confirm_form": confirm_form,
            "preview": preview,
            "result": result,
            "report": result or preview,
            "sheets": [*importer.SHEETS_BY_DESK.values(), bank.WORKBOOK_SHEET],
        },
    )


@require_POST
@permission(change="cashbook.change_operation")
def toggle_cancelled(request, desk, pk):
    """Cancel an operation (or restore a cancelled one). Operations are never deleted."""
    operation = get_object_or_404(Operation, desk__slug=desk, pk=pk)
    operation.is_cancelled = not operation.is_cancelled
    operation.save(update_fields=["is_cancelled", "updated_at"])
    if operation.is_cancelled:
        messages.success(request, _("Operation cancelled. It no longer counts towards the balance."))
    else:
        messages.success(request, _("Operation restored."))
    return redirect(safe_next(request, request.POST.get("next"), operation.desk.get_absolute_url()))


class HandbookView(PermissionMixin, ListView):
    """Articles grouped by type, each with its categories."""

    template_name = "cashbook/handbook.html"
    permission_required = "cashbook.view_article"
    context_object_name = "articles"

    def get_queryset(self):
        return Article.objects.prefetch_related(
            Prefetch("categories", queryset=Category.objects.order_by("name")), "desks"
        )

    def get_context_data(self, **kwargs):
        groups = {kind: [] for kind in ArticleKind}
        for article in self.object_list:
            groups[ArticleKind(article.kind)].append(article)
        return super().get_context_data(groups=groups.items(), **kwargs)


class HandbookFormMixin(PermissionMixin, SuccessMessageMixin):
    template_name = "cashbook/handbook_form.html"
    success_url = reverse_lazy("cashbook:handbook")
    success_message = _("Saved.")


class ArticleCreateView(HandbookFormMixin, CreateView):
    permission_required = "cashbook.add_article"
    model = Article
    form_class = ArticleForm
    extra_context = {"title": _("New article")}


class ArticleUpdateView(HandbookFormMixin, UpdateView):
    permission_required = "cashbook.change_article"
    model = Article
    form_class = ArticleForm
    extra_context = {"title": _("Edit article")}


class CategoryCreateView(HandbookFormMixin, CreateView):
    permission_required = "cashbook.add_category"
    model = Category
    form_class = CategoryForm
    extra_context = {"title": _("New category")}

    def get_initial(self):
        return {"article": self.request.GET.get("article")}


class CategoryUpdateView(HandbookFormMixin, UpdateView):
    permission_required = "cashbook.change_category"
    model = Category
    form_class = CategoryForm
    extra_context = {"title": _("Edit category")}


HANDBOOK_PERMISSIONS = (
    "cashbook.add_article",
    "cashbook.change_article",
    "cashbook.add_category",
    "cashbook.change_category",
    "cashbook.add_region",
    "cashbook.change_region",
    "cashbook.add_counterparty",
    "cashbook.change_counterparty",
)


@permission(view=HANDBOOK_PERMISSIONS)
def handbook_import(request):
    """Load articles, categories, regions and counterparties from Excel, e.g. into a new server's empty database."""
    seed_files = [
        {"kind": kind, "path": path, "name": _(name), "size": path.stat().st_size,
         "modified": datetime.datetime.fromtimestamp(path.stat().st_mtime, tz=datetime.UTC)}
        for kind, (_file, name) in exports.SEED_FILES.items()
        if (path := exports.seed_path(kind)).is_file()
    ]  # fmt: skip
    using_seed = request.method == "POST" and "use_seed" in request.POST
    form = HandbookUploadForm(None if using_seed else request.POST or None, request.FILES or None)
    report = None
    try:
        if using_seed:
            # The project's files: the handbook, then the counterparties that refer to its articles.
            report = handbooks.load_files([file["path"] for file in seed_files], dry_run="dry_run" in request.POST)
        elif request.method == "POST" and form.is_valid():
            report = handbooks.load_handbooks(form.cleaned_data["file"], dry_run=form.cleaned_data["dry_run"])
    except handbooks.HandbookError as error:
        if using_seed:
            messages.error(request, str(error))
        else:
            form.add_error("file", str(error))
    if report is not None:
        if report.errors:
            messages.error(request, _("Nothing was saved: correct the rows listed below and upload again."))
        elif report.saved:
            messages.success(request, _("Handbooks loaded."))
        else:
            messages.info(request, _("Checked: nothing was saved. Untick “Only check” to load."))
    return render(
        request,
        "cashbook/handbook_import.html",
        {
            "form": form,
            "report": report,
            "seed_files": seed_files,
            "seed_dry_run": not using_seed or "dry_run" in request.POST,
        },
    )


def missing_seed(request, back):
    messages.error(request, _("The project has no such file yet. Write it with: manage.py export_handbooks --seed"))
    return redirect(back)


@permission(view="cashbook.view_article")
def handbook_seed(request):
    """The project's handbook file (cashbook/seed/), e.g. to load into a new server."""
    return exports.seed_response("handbook") or missing_seed(request, "cashbook:handbook")


@permission(view="cashbook.view_counterparty")
def counterparty_seed(request):
    """The project's counterparties file (cashbook/seed/), e.g. to load into a new server."""
    return exports.seed_response("counterparties") or missing_seed(request, "cashbook:counterparties")


def filter_counterparties(counterparties, params):
    """The counterparties list's filters (search, status, region), shared by the page and its Excel download."""
    query = params.get("q", "").strip()
    if query:
        counterparties = counterparties.filter(
            Q(name__icontains=query) | Q(inn__icontains=query) | Q(account_number__icontains=query)
        )
    if params.get("status") == "unclassified":
        counterparties = counterparties.filter(article__isnull=True)
    if params.get("region", "").isdigit():
        counterparties = counterparties.filter(region_id=params["region"])
    return counterparties.order_by(F("article").asc(nulls_first=True), "name")


@require_POST
@permission(change=HANDBOOK_PERMISSIONS)
def counterparty_import(request):
    """Create counterparties (with their article and region) from Excel, from the Counterparties page.

    Takes the finance workbook (its "Handbook" sheet) or a counterparties / handbook download, see handbooks.py.
    """
    form = HandbookUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        for error in form.errors.get("file", []):
            messages.error(request, error)
        return redirect("cashbook:counterparties")
    try:
        report = handbooks.load_handbooks(form.cleaned_data["file"], dry_run=form.cleaned_data["dry_run"])
    except handbooks.HandbookError as error:
        messages.error(request, str(error))
        return redirect("cashbook:counterparties")

    summary = "; ".join(
        _("%(label)s: %(created)s new, %(updated)s changed, %(unchanged)s unchanged")
        % {"label": label, "created": created, "updated": updated, "unchanged": unchanged}
        for label, created, updated, unchanged in report.rows()
    )
    if report.errors:
        shown = " ".join(report.errors[:10]) + (" …" if len(report.errors) > 10 else "")
        messages.error(request, _("Nothing was saved: %(errors)s") % {"errors": shown})
    elif report.saved:
        messages.success(request, _("Loaded from «%(sheets)s». %(summary)s.") % {
            "sheets": "», «".join(report.sheets), "summary": summary,
        })  # fmt: skip
    else:
        messages.info(request, _("Checked «%(sheets)s», nothing was saved. %(summary)s.") % {
            "sheets": "», «".join(report.sheets), "summary": summary,
        })  # fmt: skip
    for warning in report.warnings:
        messages.warning(request, warning)
    return redirect("cashbook:counterparties")


@permission(view="cashbook.view_counterparty")
def counterparty_export(request):
    """The counterparties list as an .xlsx file, with the filters currently applied on the page."""
    counterparties = filter_counterparties(Counterparty.objects.all(), request.GET)
    return exports.xlsx_response(
        exports.dated_filename(_("Counterparties")),
        [exports.counterparty_sheet(counterparties), exports.region_sheet()],
    )


@permission(view="cashbook.view_article")
def handbook_export(request):
    """The handbook (articles and their categories) as an .xlsx file."""
    return exports.xlsx_response(exports.dated_filename(_("Handbook")), exports.handbook_sheets())


class CounterpartyListView(PermissionMixin, ListView):
    """Bank counterparties with their article and region; unclassified ones first."""

    template_name = "cashbook/counterparty_list.html"
    permission_required = "cashbook.view_counterparty"
    context_object_name = "counterparties"
    paginate_by = 20

    def get_queryset(self):
        counterparties = Counterparty.objects.select_related("article", "region").annotate(lines=Count("operations"))
        return filter_counterparties(counterparties, self.request.GET)

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            # Explicit order: Meta.ordering doesn't apply to aggregate (GROUP BY) queries.
            regions=Region.objects.annotate(counterparty_count=Count("counterparties")).order_by("name"),
            unclassified_count=bank.unclassified_counterparties().count(),
            upload_form=HandbookUploadForm(initial={"dry_run": False}),
            **kwargs,
        )


class CounterpartyFormMixin(PermissionMixin, SuccessMessageMixin):
    model = Counterparty
    form_class = CounterpartyForm
    template_name = "cashbook/handbook_form.html"
    success_message = _("Saved.")

    def get_success_url(self):
        return safe_next(self.request, self.request.GET.get("next"), reverse("cashbook:counterparties"))

    def form_valid(self, form):
        response = super().form_valid(form)
        if form.cleaned_data.get("apply_to_existing"):
            changed = bank.reclassify(self.object)
            if changed:
                messages.info(self.request, _("%(count)s bank lines re-filed.") % {"count": changed})
        return response


class CounterpartyCreateView(CounterpartyFormMixin, CreateView):
    permission_required = "cashbook.add_counterparty"
    extra_context = {"title": _("New counterparty"), "section_url": reverse_lazy("cashbook:counterparties")}


class CounterpartyUpdateView(CounterpartyFormMixin, UpdateView):
    permission_required = "cashbook.change_counterparty"
    extra_context = {"title": _("Edit counterparty"), "section_url": reverse_lazy("cashbook:counterparties")}


class RegionFormMixin(PermissionMixin, SuccessMessageMixin):
    model = Region
    form_class = RegionForm
    template_name = "cashbook/handbook_form.html"
    success_url = reverse_lazy("cashbook:counterparties")
    success_message = _("Saved.")


class RegionCreateView(RegionFormMixin, CreateView):
    permission_required = "cashbook.add_region"
    extra_context = {"title": _("New region"), "section_url": reverse_lazy("cashbook:counterparties")}


class RegionUpdateView(RegionFormMixin, UpdateView):
    permission_required = "cashbook.change_region"
    extra_context = {"title": _("Edit region"), "section_url": reverse_lazy("cashbook:counterparties")}
