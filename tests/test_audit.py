"""The action log: sign-ins, and who created, changed or deleted what, and when (finance_saber/audit)."""

import datetime
import json
from io import BytesIO, StringIO

import openpyxl
import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from finance_saber.audit.models import AuditEvent
from finance_saber.cashbook.models import Company
from finance_saber.production.models import Material, MaterialMove, MaterialReceipt
from finance_saber.users.models import PasswordResetCode
from finance_saber.users.roles import Role

LOGIN = reverse("users:login")
LOG = reverse("audit:events")
Action = AuditEvent.Action


def events(**filters):
    return list(AuditEvent.objects.filter(**filters).order_by("id"))


# --- Signing in and out ------------------------------------------------------------------


def test_signing_in_is_logged_with_the_address_and_browser(client, finance):
    client.post(LOGIN, {"username": "farida", "password": "s3cret-pass"}, HTTP_USER_AGENT="Firefox/140")
    [event] = events(action=Action.LOGIN)
    assert (event.user, event.username, event.ip, event.user_agent) == (finance, "farida", "127.0.0.1", "Firefox/140")


def test_a_failed_sign_in_keeps_the_name_typed_but_never_the_password(client, finance):
    client.post(LOGIN, {"username": "farida", "password": "wrong-guess-123"})
    client.post(LOGIN, {"username": "nobody", "password": "wrong-guess-456"})
    known, unknown = events(action=Action.LOGIN_FAILED)
    assert (known.user, known.username) == (finance, "farida")  # someone tried farida's account
    assert (unknown.user, unknown.username) == (None, "nobody")
    stored = json.dumps([[e.changes, e.object_repr, e.path] for e in AuditEvent.objects.all()])
    assert "wrong-guess" not in stored


def test_signing_out_is_logged(client, finance):
    client.force_login(finance)
    client.post(reverse("users:logout"))
    assert [(e.action, e.user) for e in events(action=Action.LOGOUT)] == [(Action.LOGOUT, finance)]


# --- Records created, changed, deleted -----------------------------------------------------------


def test_a_new_record_is_logged_with_who_and_its_values(client, finance):
    client.force_login(finance)
    client.post(reverse("cashbook:company_add"), {"name": "ООО Новая", "inn": "123456789"})
    [event] = events(action=Action.CREATE, model="cashbook.company")
    company = Company.objects.get(inn="123456789")
    assert (event.user, event.object_id, event.object_repr) == (finance, str(company.pk), "ООО Новая")
    assert event.changes["name"] == ["", "ООО Новая"]
    assert event.changes["inn"] == ["", "123456789"]
    assert event.path == reverse("cashbook:company_add")


def test_a_change_logs_only_the_fields_that_changed(client, finance):
    company = Company.objects.create(name="ООО Старая", inn="123456789")
    client.force_login(finance)
    url = reverse("cashbook:company_edit", args=[company.pk])
    client.post(url, {"name": "ООО Новая", "inn": "123456789"})
    client.post(url, {"name": "ООО Новая", "inn": "123456789"})  # saved again unchanged: nothing to log
    [event] = events(action=Action.UPDATE, model="cashbook.company")
    assert event.changes == {"name": ["ООО Старая", "ООО Новая"]}
    assert event.user == finance


def test_related_records_choices_and_flags_read_as_people_see_them(client, finance):
    client.force_login(finance)
    company = Company.objects.create(name="ООО Своя", inn="111")
    client.post(reverse("cashbook:company_edit", args=[company.pk]), {"name": "ООО Своя", "inn": "111", "is_own": "on"})
    [event] = events(action=Action.UPDATE, model="cashbook.company")
    assert event.changes == {"is_own": ["нет", "да"]}


def test_deleting_is_logged_with_what_was_deleted(db):
    company = Company.objects.create(name="ООО Удалённая", inn="999")
    company.delete()  # outside a request (e.g. manage.py): logged without a user
    [event] = events(action=Action.DELETE)
    assert (event.user, event.object_repr, event.changes["inn"]) == (None, "ООО Удалённая", ["999", ""])


def test_a_new_password_shows_only_that_it_changed(client, superuser, planner):
    client.force_login(superuser)
    client.post(
        reverse("users:edit", args=[planner.pk]),
        {"set_password": "1", "new_password1": "N3w-strong-pass", "new_password2": "N3w-strong-pass"},
    )
    [event] = events(action=Action.UPDATE, model="users.user", object_id=str(planner.pk), user=superuser)
    assert event.changes == {"password": ["••••••", "••••••"]}
    planner.refresh_from_db()
    assert planner.password not in json.dumps(event.changes)


def test_a_role_change_is_one_event(client, superuser, planner):
    client.force_login(superuser)
    client.post(
        reverse("users:access", args=[planner.pk]),
        {f"{planner.pk}-role": Role.TECHNOLOGIST, f"{planner.pk}-is_active": "on"},
    )
    # (the fixture giving the planner a role, outside a request, is an event of its own)
    mine = events(model="users.user", object_id=str(planner.pk), user=superuser)
    role_events = [e for e in mine if "groups" in e.changes]
    assert len(role_events) == 1
    assert role_events[0].changes["groups"] == ["Планировщик", "Технолог"]
    assert role_events[0].user == superuser


def test_a_receipt_logs_the_document_not_the_stock_movements_it_makes(client, technologist):
    supplier = Company.objects.create(name="ООО Ромашка", inn="770000001")
    sugar = Material.objects.create(name="Сахар", unit="кг")
    client.force_login(technologist)
    data = {
        "supplier": supplier.pk, "number": "НК-1", "date": "2026-10-22", "note": "",
        "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "1", "lines-MAX_NUM_FORMS": "1000",
        "lines-0-material": sugar.pk, "lines-0-quantity": "1 250,5",
    }  # fmt: skip
    assert client.post(reverse("production:material_receipt"), data).status_code == 302
    assert MaterialMove.objects.filter(material=sugar).exists()
    logged = {e.model for e in events(action=Action.CREATE)}
    assert {"production.materialreceipt", "production.materialreceiptline"} <= logged
    assert "production.materialmove" not in logged
    line = events(model="production.materialreceiptline")[0]
    assert line.changes["quantity"] == ["", "1250.5"]
    assert line.changes["material"] == ["", "Сахар"]

    receipt = MaterialReceipt.objects.get()
    client.post(reverse("production:receipt_cancel", args=[receipt.pk]))
    [cancelled] = events(action=Action.UPDATE, model="production.materialreceipt")
    assert cancelled.changes == {"is_cancelled": ["нет", "да"]}


def test_secrets_are_not_logged(planner):
    PasswordResetCode.objects.create(user=planner, code_hash="x" * 20, expires_at=timezone.now())
    assert not events(model="users.passwordresetcode")


# --- The Action log page -------------------------------------------------------------------------


@pytest.fixture
def log(superuser, finance, planner):
    Company.objects.create(name="ООО Первая", inn="1")
    second = Company.objects.create(name="ООО Вторая", inn="2")
    AuditEvent.objects.filter(model="cashbook.company", object_id=str(second.pk)).update(
        user=finance, username="farida"
    )
    second.name = "ООО Вторая (новое имя)"
    second.save()
    return second


def listed(client, **params):
    response = client.get(LOG, params)
    assert response.status_code == 200
    return [(e.action, e.object_repr) for e in response.context["page_obj"]]


def test_only_the_superuser_opens_the_log(client, superuser, finance, planner, user):
    for person in (finance, planner, user):  # even the Director
        client.force_login(person)
        assert client.get(LOG).status_code == 403
        assert client.get(reverse("audit:export")).status_code == 403
        menu = client.get(reverse("core:home")).context["nav_menu"]
        assert LOG not in [child.url for entry in menu for child in entry.children]
    client.force_login(superuser)
    assert client.get(LOG).status_code == 200
    menu = client.get(reverse("core:home")).context["nav_menu"]
    assert LOG in [child.url for entry in menu for child in entry.children]


def test_log_filters(client, superuser, finance, log):
    client.force_login(superuser)
    companies = {"section": "cashbook.company"}
    assert listed(client, **companies) == [
        (Action.UPDATE, "ООО Вторая (новое имя)"), (Action.CREATE, "ООО Вторая"), (Action.CREATE, "ООО Первая"),
    ]  # fmt: skip
    assert listed(client, action=Action.UPDATE, **companies) == [(Action.UPDATE, "ООО Вторая (новое имя)")]
    assert listed(client, user=finance.pk) == [(Action.CREATE, "ООО Вторая")]
    assert listed(client, q="Первая") == [(Action.CREATE, "ООО Первая")]
    history = listed(client, record=f"cashbook.company:{log.pk}")
    assert [action for action, _repr in history] == [Action.UPDATE, Action.CREATE]
    tomorrow = (timezone.localdate() + datetime.timedelta(days=1)).isoformat()
    assert listed(client, **{"from": tomorrow}) == []


def test_log_page_shows_changes_as_field_before_after(client, superuser, log):
    client.force_login(superuser)
    page = client.get(LOG, {"action": Action.UPDATE, "section": "cashbook.company"}).content.decode()
    assert "Название:" in page
    assert '<s class="text-muted">ООО Вторая</s> → ООО Вторая (новое имя)' in page


def test_log_excel_has_a_row_per_changed_field(client, superuser, log):
    client.force_login(superuser)
    response = client.get(reverse("audit:export"), {"action": Action.UPDATE, "section": "cashbook.company"})
    sheet = openpyxl.load_workbook(BytesIO(response.content)).active
    header = [cell.value for cell in sheet[1]]
    rows = [dict(zip(header, row, strict=True)) for row in sheet.iter_rows(min_row=2, values_only=True)]
    assert [(r["Поле"], r["Было"], r["Стало"]) for r in rows] == [("Название", "ООО Вторая", "ООО Вторая (новое имя)")]


# --- Keeping the log ---------------------------------------------------------------------------


def test_prune_deletes_only_old_entries(db):
    Company.objects.create(name="ООО Новая", inn="1")
    old = Company.objects.create(name="ООО Старая", inn="2")
    AuditEvent.objects.filter(object_id=str(old.pk)).update(created_at=timezone.now() - datetime.timedelta(days=400))
    out = StringIO()
    call_command("prune_audit", "--days", "365", stdout=out)
    assert "Deleted 1" in out.getvalue()
    assert [e.object_repr for e in AuditEvent.objects.all()] == ["ООО Новая"]


def test_prune_keeps_at_least_a_month(db):
    with pytest.raises(Exception, match="30 days"):
        call_command("prune_audit", "--days", "7")
