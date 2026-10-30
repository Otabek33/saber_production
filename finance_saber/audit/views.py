from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _

from finance_saber.cashbook import exports
from finance_saber.core.permissions import permission

from .models import AuditEvent

PAGE_SIZE = 50


def section_name(label: str) -> str:
    """ "production.materialreceipt" -> "Поступления сырья"; a removed model keeps its label."""
    try:
        return str(apps.get_model(label)._meta.verbose_name_plural).capitalize()
    except (LookupError, ValueError):
        return label


def field_name(label: str, name: str) -> str:
    if name == "groups":
        return _("Role")
    try:
        return str(apps.get_model(label)._meta.get_field(name).verbose_name).capitalize()
    except (LookupError, ValueError, FieldDoesNotExist):
        return name


def filter_events(params):
    """The log's filters (user, action, section, record, dates, text), shared by the page and its Excel file."""
    events = AuditEvent.objects.select_related("user")
    if params.get("user", "").isdigit():
        events = events.filter(user_id=params["user"])
    if params.get("action") in AuditEvent.Action.values:
        events = events.filter(action=params["action"])
    if section := params.get("section", ""):
        events = events.filter(model=section)
    if record := params.get("record", ""):  # a record's history: "<section>:<id>"
        section, _sep, pk = record.rpartition(":")
        events = events.filter(model=section, object_id=pk)
    date_from, date_to = parse_date(params.get("from") or ""), parse_date(params.get("to") or "")
    if date_from:
        events = events.filter(created_at__date__gte=date_from)
    if date_to:
        events = events.filter(created_at__date__lte=date_to)
    if query := params.get("q", "").strip():
        events = events.filter(Q(object_repr__icontains=query) | Q(username__icontains=query) | Q(ip__startswith=query))
    return events.order_by("-created_at", "-id")


def rows(event: AuditEvent) -> list[tuple[str, str, str]]:
    return [(field_name(event.model, name), before, after) for name, (before, after) in event.changes.items()]


@permission(view="audit.view_auditevent")
def events(request):
    """Administration › Action log: who signed in, and who created, changed or deleted what, and when."""
    page = Paginator(filter_events(request.GET), PAGE_SIZE).get_page(request.GET.get("page"))
    for event in page:
        event.section = section_name(event.model) if event.model else ""
        event.rows = rows(event)
    sections = sorted(
        (
            (label, section_name(label))
            for label in AuditEvent.objects.exclude(model="").values_list("model", flat=True).distinct()
        ),
        key=lambda item: item[1],
    )
    return render(
        request,
        "audit/events.html",
        {
            "page_obj": page,
            "people": get_user_model()._default_manager.order_by("username"),
            "actions": AuditEvent.Action.choices,
            "sections": sections,
            "record": request.GET.get("record", ""),
        },
    )


@permission(view="audit.view_auditevent")
def export(request):
    """The filtered log as an .xlsx file: one row per changed field (one per event without changes)."""
    table = []
    for event in filter_events(request.GET).iterator(chunk_size=2000):
        start = [
            timezone.localtime(event.created_at).replace(tzinfo=None),
            event.username,
            event.get_action_display(),
            section_name(event.model) if event.model else "",
            event.object_repr,
            event.object_id,
        ]
        end = [event.ip or "", event.path]
        table.extend([*start, field, before, after, *end] for field, before, after in rows(event) or [("", "", "")])
    headers = [
        _("Time"), _("User"), _("Action"), _("Section"), _("Record"), _("ID"),
        _("Field"), _("Before"), _("After"), _("IP address"), _("Page"),
    ]  # fmt: skip
    sheet = exports.Sheet(_("Action log"), headers, table)
    return exports.xlsx_response(exports.dated_filename(_("Action log")), [sheet])
