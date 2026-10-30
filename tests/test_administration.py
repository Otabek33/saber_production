"""Administration: users, their roles, and what each role may do."""

import importlib

import pytest
from django.apps import apps
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import translation

from finance_saber.users.models import User
from finance_saber.users.roles import EDIT, NONE, VIEW, Role, access_matrix, user_role

USERS = reverse("users:list")
ADD = reverse("users:add")
ROLES = reverse("users:roles")


# --- What each role may do ---------------------------------------------------------


@pytest.mark.parametrize(
    ("role_fixture", "allowed", "denied"),
    [
        (
            "finance",
            ["cashbook.add_operation", "cashbook.change_counterparty", "sales.add_sale", "sales.change_client",
             "planning.change_cashplan", "cashbook.change_exchangerate", "production.view_productionrun"],
            ["production.add_productionrun", "production.change_recipeline", "users.change_user"],
        ),
        (
            "planner",  # Production only, like the technologist
            ["production.add_productionrun", "production.change_productionrun", "production.add_materialreceipt",
             "production.change_recipeline", "production.add_productmove", "production.view_materialmove"],
            ["cashbook.view_operation", "sales.view_sale", "sales.view_plan", "planning.view_cashplan",
             "production.import_excel", "users.change_user"],
        ),
        (
            "technologist",
            ["production.change_recipeline", "production.delete_recipeline", "production.add_productionrun",
             "production.change_productionrun", "production.add_materialmove", "production.add_materialreceipt",
             "production.change_materialreceipt", "production.add_productmove"],
            ["cashbook.view_operation", "sales.view_sale", "sales.view_plan", "planning.view_cashplan",
             "production.import_excel", "users.change_user"],
        ),
        (
            "operator",
            ["sales.view_sale", "sales.add_sale", "sales.change_sale", "sales.import_sales", "sales.view_client",
             "sales.view_channel"],
            ["sales.change_client", "sales.view_plan", "cashbook.view_operation", "production.view_productionrun",
             "planning.view_cashplan", "users.change_user"],
        ),
        (
            "user",  # the Director (Manager group): every business section
            ["cashbook.add_operation", "sales.add_sale", "production.add_productionrun", "planning.change_cashplan"],
            ["users.change_user", "users.add_user"],
        ),
    ],
)  # fmt: skip
def test_roles_grant_their_sections_only(request, role_fixture, allowed, denied):
    person = request.getfixturevalue(role_fixture)
    for perm in allowed:
        assert person.has_perm(perm), perm
    for perm in denied:
        assert not person.has_perm(perm), perm


def test_access_matrix_shows_who_works_and_who_views(db):
    with translation.override("en"):
        rows = {str(row["label"]): [cell["level"] for cell in row["cells"]] for row in access_matrix()}
    order = [*Role, "superuser"]
    recipes = dict(zip(order, rows["Recipes and norms"], strict=True))
    assert recipes[Role.TECHNOLOGIST] == EDIT
    assert recipes[Role.FINANCE] == VIEW
    cash = dict(zip(order, rows["Cash desks and bank"], strict=True))
    assert (cash[Role.FINANCE], cash[Role.PLANNER], cash[Role.TECHNOLOGIST]) == (EDIT, NONE, NONE)
    materials = dict(zip(order, rows["Raw materials: receipts, write-offs, counts"], strict=True))
    assert (materials[Role.PLANNER], materials[Role.TECHNOLOGIST], materials[Role.FINANCE]) == (EDIT, EDIT, VIEW)
    users = dict(zip(order, rows["Users, roles and permissions"], strict=True))
    assert [users[role] for role in Role] == [NONE] * len(Role)
    assert users["superuser"] == EDIT


# --- The Administration section ------------------------------------------------------


def test_only_the_superuser_sees_administration(client, superuser, finance, technologist):
    client.force_login(superuser)
    page = client.get(reverse("core:home")).content.decode()
    assert "Администрирование" in page
    assert ROLES in page
    for person in (finance, technologist):
        client.force_login(person)
        assert "Администрирование" not in client.get(reverse("core:home")).content.decode()
        for url in (USERS, ADD, ROLES, reverse("users:edit", args=[person.pk])):
            assert client.get(url).status_code == 403


def test_superuser_creates_a_user_with_a_role(client, superuser):
    client.force_login(superuser)
    response = client.post(
        ADD,
        {
            "username": "timur",
            "first_name": "Тимур",
            "last_name": "Каримов",
            "email": "",
            "telegram_username": "@timur_tech",
            "role": Role.TECHNOLOGIST,
            "password1": "Rec1pes-and-n0rms",
            "password2": "Rec1pes-and-n0rms",
        },
    )
    assert response.status_code == 302
    timur = User.objects.get(username="timur")
    assert (user_role(timur), timur.telegram_username, timur.get_full_name()) == (
        Role.TECHNOLOGIST, "timur_tech", "Тимур Каримов",
    )  # fmt: skip
    assert timur.has_perm("production.change_recipeline")
    client.logout()
    assert client.login(username="timur", password="Rec1pes-and-n0rms")


def test_weak_or_mismatched_passwords_are_refused(client, superuser):
    client.force_login(superuser)
    response = client.post(ADD, {"username": "x", "role": "", "password1": "123", "password2": "124"})
    assert response.status_code == 200
    assert not User.objects.filter(username="x").exists()


def test_superuser_edits_a_user_and_sets_a_password(client, superuser, planner):
    client.force_login(superuser)
    url = reverse("users:edit", args=[planner.pk])
    response = client.post(
        url,
        {"first_name": "Павел", "last_name": "", "email": "pavel@example.com", "telegram_username": "",
         "role": Role.FINANCE},
    )  # fmt: skip
    assert response.status_code == 302
    planner.refresh_from_db()
    assert (user_role(planner), planner.is_active, planner.first_name) == (Role.FINANCE, False, "Павел")

    response = client.post(
        url, {"set_password": "1", "new_password1": "N3w-strong-pass", "new_password2": "N3w-strong-pass"}
    )
    assert response.status_code == 302
    planner.refresh_from_db()
    assert planner.check_password("N3w-strong-pass")


def test_superuser_cannot_remove_their_own_access(client, superuser):
    client.force_login(superuser)
    response = client.post(
        reverse("users:edit", args=[superuser.pk]),
        {"first_name": "Root", "last_name": "", "email": "", "telegram_username": "", "role": Role.PLANNER},
    )
    assert response.status_code == 302
    superuser.refresh_from_db()
    assert (user_role(superuser), superuser.is_active, superuser.first_name) == ("superuser", True, "Root")


def test_users_list_filters_by_role(client, superuser, finance, technologist, no_role):
    client.force_login(superuser)
    listed = lambda role: {row["user"] for row in client.get(USERS, {"role": role}).context["rows"]}  # noqa: E731
    assert listed(Role.TECHNOLOGIST) == {technologist}
    assert listed("none") == {no_role}
    assert listed("superuser") == {superuser}
    page = client.get(USERS).content.decode()
    assert "Технолог" in page and "Финансист" in page and "Руководитель" in page


def test_roles_page(client, superuser, technologist):
    client.force_login(superuser)
    page = client.get(ROLES).content.decode()
    for text in ("Роли и права", "Рецептуры и нормы", "Работает", "Смотрит", "Технолог", "Планировщик"):
        assert text in page


# --- The old role --------------------------------------------------------------------


def test_finance_planner_group_is_renamed_to_planner(db):
    migration = importlib.import_module("finance_saber.users.migrations.0005_rename_finance_planner_role")
    Group.objects.filter(name=Role.PLANNER).delete()
    old = Group.objects.create(name="Finance planner")
    person = User.objects.create_user(username="old_planner")
    person.groups.add(old)

    migration.rename_groups(apps, None)
    assert not Group.objects.filter(name="Finance planner").exists()
    assert list(person.groups.values_list("name", flat=True)) == [Role.PLANNER]


@pytest.mark.parametrize("role_fixture", ["technologist", "planner"])
def test_production_roles_see_only_production(request, client, role_fixture):
    client.force_login(request.getfixturevalue(role_fixture))
    menu = client.get(reverse("core:home")).context["nav_menu"]
    sections = {str(entry.label) for entry in menu if entry.children}
    assert sections == {"Производство"}
    production = next(entry for entry in menu if str(entry.label) == "Производство")
    assert [str(child.label) for child in production.children] == [
        "Выпуск за день", "Журнал выпуска", "Сырьё на складе", "Поступления сырья",
        "Готовая продукция", "Рецептуры и нормы", "Движения склада",
    ]  # fmt: skip


def test_finance_menu_has_the_money_sections(client, finance):
    client.force_login(finance)
    sections = {str(entry.label) for entry in client.get(reverse("core:home")).context["nav_menu"]}
    assert {"Касса", "Финансы", "Продажи", "Закупки", "Отчёты"} <= sections
    assert "Администрирование" not in sections


def test_operator_enters_sales_and_sees_nothing_else(client, operator):
    client.force_login(operator)
    menu = client.get(reverse("core:home")).context["nav_menu"]
    sections = {str(entry.label): [str(child.label) for child in entry.children] for entry in menu}
    assert set(sections) == {"Главная", "Продажи"}
    assert "Продажи за день" in sections["Продажи"]
    assert "План продаж" not in sections["Продажи"]
    assert client.get(reverse("sales:daily")).status_code == 200
    assert client.get(reverse("sales:import")).status_code == 200
    assert client.get(reverse("sales:debts")).status_code == 200
    for url in (reverse("sales:plan"), reverse("cashbook:index"), reverse("production:daily"), USERS):
        assert client.get(url).status_code == 403, url
