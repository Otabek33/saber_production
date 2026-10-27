"""Changing what a role may do, section by section, from the Roles and permissions page."""

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from finance_saber.users.models import RoleAccess, RoleAccessChange
from finance_saber.users.roles import (
    AREAS,
    AREAS_BY_KEY,
    EDIT,
    NONE,
    STANDARD_ACCESS,
    VIEW,
    Role,
    access_matrix,
    permissions_for,
    role_levels,
    sync_roles,
)

TECH = Role.TECHNOLOGIST


def url(role=TECH):
    return reverse("users:role_edit", kwargs={"role": role})


def group_permissions(role) -> set[str]:
    group = Group.objects.get(name=role)
    return {f"{p.content_type.app_label}.{p.codename}" for p in group.permissions.select_related("content_type")}


def form_data(role, **changes):
    """The edit form as the page posts it: every editable section at its current level, with ``changes``."""
    levels = role_levels(role)
    return {area.key: changes.get(area.key, levels[area.key]) for area in AREAS if area.editable}


# --- Sections and the standard ---------------------------------------------------------


def test_no_permission_belongs_to_two_sections():
    seen = {}
    for area in AREAS:
        for perm in area.view | area.edit:
            assert perm not in seen, f"{perm} is in {seen.get(perm)} and {area.key}"
            seen[perm] = area.key


def test_every_permission_exists_and_groups_have_the_standard(db):
    assert sync_roles() == []  # every permission named in AREAS exists
    for role in Role:
        assert group_permissions(role) == permissions_for(STANDARD_ACCESS[role])


# --- The page ---------------------------------------------------------------------------


def test_superuser_gives_a_role_a_section(client, superuser, technologist):
    assert not technologist.has_perm("cashbook.view_operation")
    client.force_login(superuser)
    page = client.get(url()).content.decode()
    assert "Стандартный доступ" in page and "Только суперпользователь: ролям не выдаётся" in page

    response = client.post(url(), form_data(TECH, cash=VIEW, production_import=NONE))
    assert response.status_code == 302
    technologist = type(technologist).objects.get(pk=technologist.pk)  # fresh: permissions are cached per object
    assert technologist.has_perm("cashbook.view_operation")
    assert not technologist.has_perm("cashbook.add_operation")
    assert not technologist.has_perm("production.import_excel")
    assert {(a.area, a.level) for a in RoleAccess.objects.filter(role=TECH)} == {
        ("cash", VIEW),
        ("production_import", NONE),
    }
    change = RoleAccessChange.objects.get(role=TECH, area="cash")
    assert (change.old_level, change.new_level, change.changed_by) == (NONE, VIEW, superuser)

    page = client.get(url()).content.decode()
    assert "2 раздела изменены относительно стандарта" in page
    assert "Кассы и банк: Нет доступа → <strong>Смотрит</strong>" in page
    cells = {str(row["label"]): row["cells"] for row in access_matrix()}
    tech = list(Role).index(TECH)
    assert cells["Кассы и банк"][tech] == {"level": VIEW, "changed": True}


def test_changes_survive_migrate(client, superuser, technologist):
    client.force_login(superuser)
    client.post(url(), form_data(TECH, cash=EDIT))
    sync_roles()  # what every migrate runs
    assert "cashbook.add_operation" in group_permissions(TECH)


def test_setting_the_standard_again_removes_the_change(client, superuser):
    client.force_login(superuser)
    client.post(url(), form_data(TECH, recipes=VIEW))
    client.post(url(), form_data(TECH, recipes=EDIT))  # back to the technologist's standard
    assert not RoleAccess.objects.filter(role=TECH).exists()
    assert RoleAccessChange.objects.filter(role=TECH).count() == 2
    assert group_permissions(TECH) == permissions_for(STANDARD_ACCESS[TECH])


def test_reset_to_standard(client, superuser):
    client.force_login(superuser)
    client.post(url(Role.OPERATOR), form_data(Role.OPERATOR, plan=VIEW, clients=EDIT))
    response = client.post(url(Role.OPERATOR), {"reset": "1"})
    assert response.status_code == 302
    assert not RoleAccess.objects.filter(role=Role.OPERATOR).exists()
    assert group_permissions(Role.OPERATOR) == permissions_for(STANDARD_ACCESS[Role.OPERATOR])
    assert RoleAccessChange.objects.filter(role=Role.OPERATOR).count() == 4  # two changes, two undone


def test_user_management_cannot_be_given_to_a_role(client, superuser, technologist):
    client.force_login(superuser)
    client.post(url(), {**form_data(TECH), "users": EDIT})
    assert not AREAS_BY_KEY["users"].editable
    assert not type(technologist).objects.get(pk=technologist.pk).has_perm("users.change_user")
    assert not RoleAccess.objects.filter(area="users").exists()


def test_two_state_sections_have_no_view_level(client, superuser):
    client.force_login(superuser)
    response = client.post(url(), form_data(TECH, production_import=VIEW))
    assert response.status_code == 200  # rejected: an import is either available or not
    assert not RoleAccess.objects.exists()


@pytest.mark.parametrize("person", ["technologist", "finance"])
def test_only_the_superuser_edits_roles(client, request, person):
    client.force_login(request.getfixturevalue(person))
    assert client.get(url()).status_code == 403
    assert client.post(url(), {"cash": EDIT}).status_code == 403


def test_unknown_role(client, superuser):
    client.force_login(superuser)
    assert client.get(url("Hacker")).status_code == 404


def test_roles_page_links_to_editing_and_marks_changes(client, superuser):
    client.force_login(superuser)
    client.post(url(), form_data(TECH, cash=VIEW))
    page = client.get(reverse("users:roles")).content.decode()
    assert url() in page
    assert "1 изменение" in page
    assert "Изменено относительно стандарта" in page
