"""Roles: what a superuser, manager, planner, finance and a user without a role may see and do."""

import datetime
from decimal import Decimal

import pytest
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.urls import reverse

from finance_saber.cashbook.models import CashDesk
from finance_saber.production.models import Material, MaterialMove, Product
from finance_saber.users.roles import (
    AREAS,
    STANDARD_ACCESS,
    SUPERUSER,
    Role,
    permissions_for,
    set_role,
    sync_roles,
    user_role,
)

# Pages and who may open them: (url name, kwargs, manager, planner). The planner works in
# Production only: nothing outside it, and not the production import (the Director's).
PAGES = [
    ("cashbook:operations", {"desk": "bon"}, True, False),
    ("cashbook:handbook", {}, True, False),
    ("cashbook:counterparties", {}, True, False),
    ("cashbook:article_add", {}, True, False),
    ("sales:daily", {}, True, False),
    ("sales:plan", {}, True, False),
    ("production:daily", {}, True, True),
    ("production:journal", {}, True, True),
    ("production:materials", {}, True, True),
    ("production:receipts", {}, True, True),
    ("production:products", {}, True, True),
    ("production:recipes", {}, True, True),
    ("production:moves", {}, True, True),
    ("production:material_receipt", {}, True, True),
    ("production:shipment", {}, True, True),
    ("production:material_add", {}, True, True),
    ("production:import", {}, True, False),
    ("users:list", {}, False, False),
]


def test_role_groups_are_created_by_migrate(db):
    assert set(Group.objects.values_list("name", flat=True)) >= set(Role.values)
    for role, levels in STANDARD_ACCESS.items():
        wanted = permissions_for(levels)
        group = Group.objects.get(name=role)
        got = {f"{p.content_type.app_label}.{p.codename}" for p in group.permissions.select_related("content_type")}
        assert got == wanted


def test_every_role_permission_exists(db):
    assert sync_roles() == []


def test_nobody_but_the_superuser_deletes(db):
    # Whatever access the superuser gives a role, no section grants deleting records.
    # Exceptions: recipe lines (editing a recipe) and plans (drafts, cleared by emptying a cell).
    allowed = {"production.delete_recipeline", "planning.delete_cashplan", "planning.delete_collectionplan"}
    grantable = {perm for area in AREAS if area.editable for perm in area.permissions("edit")}
    assert not {perm for perm in grantable if ".delete_" in perm} - allowed


@pytest.mark.parametrize(("url_name", "kwargs", "manager_ok", "planner_ok"), PAGES)
def test_who_can_open_what(client, user, planner, no_role, superuser, url_name, kwargs, manager_ok, planner_ok):
    url = reverse(url_name, kwargs=kwargs)
    for account, allowed in ((user, manager_ok), (planner, planner_ok), (no_role, False), (superuser, True)):
        client.force_login(account)
        assert (client.get(url).status_code == 200) is allowed, (account.username, url)


def test_finance_cannot_record_production(client, finance):
    product = Product.objects.create(name="Чай", volume=Decimal("0.45"))
    client.force_login(finance)
    response = client.post(reverse("production:daily"), {f"product_{product.pk}": "10"})
    assert response.status_code == 403
    assert not product.runs.exists()


def test_finance_sees_production_read_only(client, finance):
    Product.objects.create(name="Чай", volume=Decimal("0.45"))
    client.force_login(finance)
    response = client.get(reverse("production:daily"))
    assert not response.context["can_edit"]
    assert all(field.field.disabled for _product, field, _possible in response.context["rows"])
    assert "Сохранить" not in response.content.decode()


def test_finance_can_add_a_cash_operation(client, finance):
    client.force_login(finance)
    assert CashDesk.objects.filter(slug="bon").exists()
    response = client.post(reverse("cashbook:operations", kwargs={"desk": "bon"}), {"kind": "income"})
    assert response.status_code == 400  # let through: the incomplete form is what's rejected


def test_finance_cannot_cancel_a_stock_movement(client, finance):
    material = Material.objects.create(name="Сахар", unit="кг")
    move = MaterialMove.objects.create(
        material=material, date=datetime.date(2026, 10, 22), kind=MaterialMove.Kind.OPENING, quantity=1
    )
    client.force_login(finance)
    assert client.post(reverse("production:move_cancel", args=["materials", move.pk])).status_code == 403
    move.refresh_from_db()
    assert not move.is_cancelled


def test_menu_shows_only_allowed_pages(client, planner, no_role):
    client.force_login(planner)
    menu = client.get(reverse("core:home")).context["nav_menu"]
    production = next(entry for entry in menu if entry.label == "Производство")
    assert reverse("production:import") not in [child.url for child in production.children]

    client.force_login(no_role)
    response = client.get(reverse("core:home"))
    labels = [entry.label for entry in response.context["nav_menu"]]
    assert "Производство" not in labels
    assert "Касса" not in labels
    assert "ожидает назначения роли" in response.content.decode()


def test_superuser_assigns_a_role(client, superuser, no_role):
    client.force_login(superuser)
    response = client.post(
        reverse("users:access", args=[no_role.pk]),
        {f"{no_role.pk}-role": Role.PLANNER, f"{no_role.pk}-is_active": "on"},
    )
    assert response.status_code == 302
    no_role.refresh_from_db()
    assert user_role(no_role) == Role.PLANNER
    assert no_role.has_perm("production.add_productionrun")
    assert not no_role.has_perm("cashbook.view_operation")  # Production only
    assert not no_role.has_perm("planning.change_cashplan")


def test_changing_a_role_replaces_the_old_one(user):
    set_role(user, Role.PLANNER)
    assert list(user.groups.values_list("name", flat=True)) == [Role.PLANNER]
    set_role(user, SUPERUSER)
    assert user.is_superuser and user.is_staff and not user.groups.exists()
    set_role(user, "")
    assert not user.is_superuser and user_role(user) == ""


def test_superuser_cannot_lock_themselves_out(client, superuser):
    client.force_login(superuser)
    client.post(reverse("users:access", args=[superuser.pk]), {f"{superuser.pk}-role": ""})
    superuser.refresh_from_db()
    assert superuser.is_superuser


def test_access_redirect_stays_on_this_site(client, superuser, no_role):
    client.force_login(superuser)
    response = client.post(
        reverse("users:access", args=[no_role.pk]), {f"{no_role.pk}-role": "", "next": "//evil.example/"}
    )
    assert response.url == reverse("users:list")


def test_manager_cannot_manage_users(client, user, no_role):
    client.force_login(user)
    response = client.post(reverse("users:access", args=[no_role.pk]), {f"{no_role.pk}-role": Role.MANAGER})
    assert response.status_code == 403


def test_set_role_command(no_role):
    call_command("set_role", "newbie", "manager")
    assert user_role(no_role) == Role.MANAGER
