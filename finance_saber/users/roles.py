"""User roles: what each kind of user may see and do.

A role is a Django group. What it may do is set per section of the app (AREAS):
no access, view, or work (view and enter or change data). Each level of a
section stands for a fixed set of permissions, so a role can never get half of
what a page needs.

Every role has a standard access defined here (STANDARD_ACCESS). The superuser
can change a role's access section by section under Administration › Roles and
permissions; those changes are stored (users.RoleAccess), logged
(users.RoleAccessChange) and kept by later updates. After every ``migrate`` (see
apps.py) and every change, each group gets exactly the permissions of its
standard access plus the stored changes. "Reset to standard" removes a role's
changes.

* Superuser — everything, including users, roles and the admin site.
  Not a group: Django's ``is_superuser`` flag, and not editable.
* Finance — the money side: cash desks, bank, the handbook and counterparties,
  exchange rates, the cash plan and collections, and sales (sales, clients and
  their payers, plans, imports); production and stock read-only.
* Planner — the plans: the sales and production plan, the cash plan and
  collections by region; cash desks, sales, production and stock read-only.
* Technologist — production: recipes and norms, daily production and its
  calculation, raw materials (receipts, write-offs, stock counts), finished
  goods, the production Excel import and the production plan; sales read-only.
* Operator — sales entry: the day's sales (entering, correcting, cancelling)
  and the sales import; clients, debts and sales channels read-only.
* Manager (Director) — every business section, but not users and roles.

Nobody but the superuser deletes records: they are cancelled instead, so the
history stays complete. (Plans are drafts: clearing a plan cell removes it.)
Users who sign up themselves get no role and see nothing until a superuser
gives them one.
"""

from dataclasses import dataclass, field

from django.contrib.auth.models import Group, Permission
from django.db import transaction
from django.db.models import Q, TextChoices
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy


class Role(TextChoices):
    # The value is the group's name in the database; keep it stable. Labels have their own
    # translation context: "Finance" the role (Финансист) is not "Finance" the menu section.
    FINANCE = "Finance", pgettext_lazy("role", "Finance")
    PLANNER = "Planner", pgettext_lazy("role", "Planner")
    TECHNOLOGIST = "Technologist", pgettext_lazy("role", "Technologist")
    OPERATOR = "Operator", pgettext_lazy("role", "Operator")
    MANAGER = "Manager", pgettext_lazy("role", "Director")


SUPERUSER = "superuser"  # not a group, but offered alongside the roles on the Users page

# One line per role (its standard access) for the Users and Roles pages.
DESCRIPTIONS = {
    SUPERUSER: _("Everything, including users, roles and permissions, cash desk setup and the admin site."),
    Role.FINANCE: _(
        "Cash desks and bank, the handbook and counterparties, exchange rates, the cash plan and collections, "
        "sales and clients. Production and stock: view only."
    ),
    Role.PLANNER: _(
        "The sales and production plan, the cash plan and collections. Cash desks, sales, production and stock: "
        "view only."
    ),
    Role.TECHNOLOGIST: _(
        "Recipes and norms, daily production and its calculation, raw materials and stock, finished goods, "
        "the production import and the production plan. Sales: view only."
    ),
    Role.OPERATOR: _("Enters the day's sales and the sales import, and corrects them. Clients and debts: view only."),
    Role.MANAGER: _("Every business section, without users and roles."),
    "": _("Can sign in but sees no data. People who sign up themselves start here."),
}


def role_choices():
    """(value, label) for every role, the superuser last, for role pickers."""
    return [("", _("No role")), *Role.choices, (SUPERUSER, _("Superuser"))]


# --- Sections and access levels ---------------------------------------------------------

EDIT, VIEW, NONE = "edit", "view", ""
LEVEL_LABELS = {NONE: _("No access"), VIEW: _("Views"), EDIT: _("Works")}


def _perms(app: str, *names: str) -> frozenset[str]:
    return frozenset(f"{app}.{name}" for name in names)


@dataclass(frozen=True)
class Area:
    """A section of the app and the permissions behind its access levels.

    ``view`` opens the section's pages; ``edit`` (on top of ``view``) enters and
    changes their data. A section without ``view`` permissions has no "view"
    level: it is either not available or fully available (e.g. an import).
    """

    key: str  # stored in users.RoleAccess; keep it stable
    label: str
    view: frozenset[str] = field(default_factory=frozenset)
    edit: frozenset[str] = field(default_factory=frozenset)
    editable: bool = True  # the superuser's sections can't be given to a role

    @property
    def levels(self) -> list[str]:
        return [NONE, VIEW, EDIT] if self.view else [NONE, EDIT]

    def permissions(self, level: str) -> frozenset[str]:
        if level == EDIT:
            return self.view | self.edit
        if level == VIEW:
            return self.view
        return frozenset()


AREAS = [
    Area(
        "cash",
        _("Cash desks and bank"),
        _perms("cashbook", "view_operation", "view_cashdesk"),
        _perms("cashbook", "add_operation", "change_operation"),
    ),
    Area(
        "handbook",
        _("Handbook: articles and categories"),
        _perms("cashbook", "view_article", "view_category"),
        _perms("cashbook", "add_article", "change_article", "add_category", "change_category"),
    ),
    Area(
        "counterparties",
        _("Counterparties and regions"),
        _perms("cashbook", "view_counterparty", "view_region"),
        _perms("cashbook", "add_counterparty", "change_counterparty", "add_region", "change_region"),
    ),
    Area(
        "rates",
        _("Exchange rates"),
        _perms("cashbook", "view_exchangerate"),
        _perms("cashbook", "add_exchangerate", "change_exchangerate"),
    ),
    Area(
        "cash_plan",
        _("Weekly cash plan"),
        _perms("planning", "view_cashplan"),
        # Plans are drafts, so emptying a cell may delete it.
        _perms("planning", "add_cashplan", "change_cashplan", "delete_cashplan"),
    ),
    Area(
        "collections",
        _("Collections by region"),
        _perms("planning", "view_collectionplan"),
        _perms("planning", "add_collectionplan", "change_collectionplan", "delete_collectionplan"),
    ),
    Area(
        "sales",
        _("Daily sales and imports"),
        _perms("sales", "view_sale"),
        # New clients are created by the entry itself.
        _perms("sales", "add_sale", "change_sale", "import_sales"),
    ),
    Area(
        "clients",
        _("Clients, debtors and creditors"),
        _perms("sales", "view_client", "view_channel"),
        _perms("sales", "add_client", "change_client", "add_channel", "change_channel"),
    ),
    Area(
        "plan",
        _("Sales and production plan"),
        _perms("sales", "view_plan"),
        _perms("sales", "add_plan", "change_plan"),
    ),
    Area(
        "recipes",
        _("Recipes and norms"),
        _perms("production", "view_recipeline"),
        # Editing a recipe removes lines; products and their other names are kept with recipes.
        _perms(
            "production",
            "add_recipeline",
            "change_recipeline",
            "delete_recipeline",
            "add_product",
            "change_product",
            "view_productalias",
            "add_productalias",
            "change_productalias",
        ),
    ),
    Area(
        "production",
        _("Daily production and calculation"),
        _perms("production", "view_productionrun"),
        _perms("production", "add_productionrun", "change_productionrun"),
    ),
    Area(
        "materials",
        _("Raw materials: receipts, write-offs, counts"),
        _perms("production", "view_material", "view_materialmove"),
        _perms("production", "add_material", "change_material", "add_materialmove", "change_materialmove"),
    ),
    Area(
        "goods",
        _("Finished goods and shipments"),
        _perms("production", "view_product", "view_productmove"),
        _perms("production", "add_productmove", "change_productmove"),
    ),
    Area("production_import", _("Production import from Excel"), edit=_perms("production", "import_excel")),
    Area(
        "users",
        _("Users, roles and permissions"),
        _perms("users", "view_user"),
        _perms("users", "add_user", "change_user"),
        editable=False,
    ),
]
AREAS_BY_KEY = {area.key: area for area in AREAS}

# Each role's standard access: {section: level}; sections not listed have no access.
STANDARD_ACCESS: dict[str, dict[str, str]] = {
    Role.FINANCE: {
        "cash": EDIT, "handbook": EDIT, "counterparties": EDIT, "rates": EDIT, "cash_plan": EDIT,
        "collections": EDIT, "sales": EDIT, "clients": EDIT, "plan": EDIT,
        "recipes": VIEW, "production": VIEW, "materials": VIEW, "goods": VIEW,
    },
    Role.PLANNER: {
        "cash": VIEW, "handbook": VIEW, "counterparties": VIEW, "rates": VIEW, "cash_plan": EDIT,
        "collections": EDIT, "sales": VIEW, "clients": VIEW, "plan": EDIT,
        "recipes": VIEW, "production": VIEW, "materials": VIEW, "goods": VIEW,
    },
    Role.TECHNOLOGIST: {
        "sales": VIEW, "clients": VIEW, "plan": EDIT,
        "recipes": EDIT, "production": EDIT, "materials": EDIT, "goods": EDIT, "production_import": EDIT,
    },
    Role.OPERATOR: {"sales": EDIT, "clients": VIEW},
    Role.MANAGER: {
        "cash": EDIT, "handbook": EDIT, "counterparties": EDIT, "rates": EDIT, "cash_plan": EDIT,
        "collections": EDIT, "sales": EDIT, "clients": EDIT, "plan": EDIT,
        "recipes": EDIT, "production": EDIT, "materials": EDIT, "goods": EDIT, "production_import": EDIT,
    },
}  # fmt: skip


def standard_level(role: str, area: Area) -> str:
    return STANDARD_ACCESS[role].get(area.key, NONE)


def changed_levels(role: str) -> dict[str, str]:
    """The superuser's changes to ``role``: {section key: level}, for sections that still exist and may change."""
    from .models import RoleAccess

    return {
        access.area: access.level
        for access in RoleAccess.objects.filter(role=role)
        if access.area in AREAS_BY_KEY
        and AREAS_BY_KEY[access.area].editable
        and access.level in AREAS_BY_KEY[access.area].levels
    }


def role_levels(role: str) -> dict[str, str]:
    """{section key: level} the role has now: its standard access with the superuser's changes."""
    changed = changed_levels(role)
    return {area.key: changed.get(area.key, standard_level(role, area)) for area in AREAS}


def permissions_for(levels: dict[str, str]) -> set[str]:
    return {perm for area in AREAS for perm in area.permissions(levels.get(area.key, NONE))}


def access_matrix() -> list[dict]:
    """Rows of (section, access per role, whether it differs from the standard) for the Roles page."""
    current = {role: role_levels(role) for role in Role}
    rows = []
    for area in AREAS:
        cells = [
            {"level": current[role][area.key], "changed": current[role][area.key] != standard_level(role, area)}
            for role in Role
        ]
        rows.append({"label": area.label, "cells": [*cells, {"level": EDIT, "changed": False}]})  # the superuser
    return rows


# --- Users and their role ---------------------------------------------------------------


def user_role(user) -> str:
    """The user's role value: SUPERUSER, a Role, or "" for none."""
    if user.is_superuser:
        return SUPERUSER
    names = {group.name for group in user.groups.all()}
    return next((role.value for role in Role if role.value in names), "")


def role_label(value: str) -> str:
    if value == SUPERUSER:
        return str(_("Superuser"))
    return str(Role(value).label) if value else str(_("No role"))


def set_role(user, value: str) -> None:
    """Give ``user`` exactly one role (or none); other, non-role groups are kept."""
    user.is_superuser = user.is_staff = value == SUPERUSER
    user.save(update_fields=["is_superuser", "is_staff"])
    user.groups.remove(*Group.objects.filter(name__in=Role.values))
    if value in Role.values:
        user.groups.add(Group.objects.get(name=value))


# --- Writing roles to the database ------------------------------------------------------


def _sync_group(role: str) -> list[str]:
    """Give the role's group exactly its current permissions. Returns permissions not found."""
    wanted = permissions_for(role_levels(role))
    query = Q(pk__in=[])
    for name in wanted:
        app_label, codename = name.split(".")
        query |= Q(content_type__app_label=app_label, codename=codename)
    permissions = list(Permission.objects.filter(query).select_related("content_type"))
    found = {f"{p.content_type.app_label}.{p.codename}" for p in permissions}
    group, _created = Group.objects.get_or_create(name=role)
    group.permissions.set(permissions)
    return sorted(wanted - found)


def sync_roles() -> list[str]:
    """Create the role groups and set their permissions exactly. Returns permissions not found."""
    missing = []
    for role in Role:
        missing += _sync_group(role.value)
    return missing


@transaction.atomic
def change_access(role: str, levels: dict[str, str], user=None) -> list[tuple[Area, str, str]]:
    """Set ``role``'s access to the given sections; returns (section, old, new) for each change.

    A level equal to the standard is stored as no change at all, so the role
    follows the standard again; every change is logged.
    """
    from .models import RoleAccess, RoleAccessChange

    current = role_levels(role)
    changes = []
    for key, level in levels.items():
        area = AREAS_BY_KEY.get(key)
        if area is None or not area.editable or level not in area.levels or current[key] == level:
            continue
        if level == standard_level(role, area):
            RoleAccess.objects.filter(role=role, area=key).delete()
        else:
            RoleAccess.objects.update_or_create(role=role, area=key, defaults={"level": level, "changed_by": user})
        RoleAccessChange.objects.create(role=role, area=key, old_level=current[key], new_level=level, changed_by=user)
        changes.append((area, current[key], level))
    if changes:
        _sync_group(role)
    return changes


def reset_access(role: str, user=None) -> list[tuple[Area, str, str]]:
    """Put ``role`` back to its standard access."""
    return change_access(role, {area.key: standard_level(role, area) for area in AREAS if area.editable}, user)
