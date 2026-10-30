"""The app's main menu, shown as the horizontal navigation bar.

This is the single place to add, rename or reorder menu entries. The header
search and the "coming soon" page titles are built from it too.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from django.http import HttpRequest
from django.urls import reverse
from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class NavItem:
    label: str
    url_name: str = ""
    icon: str = ""  # an Iconify icon name, e.g. "solar:wallet-money-line-duotone"
    children: tuple["NavItem", ...] = ()
    also_active_on: tuple[str, ...] = ()  # other views that highlight this item, e.g. its edit pages
    url_kwargs: tuple[tuple[str, str], ...] = ()  # e.g. (("desk", "bon"),); must also match to be active
    # Children built per request (e.g. one item per cash desk), shown before ``children``.
    children_from: Callable[[], tuple["NavItem", ...]] | None = None
    permission: str = ""  # shown only to users who have it, e.g. "cashbook.view_operation"

    def is_current(self, view_name: str, view_kwargs: dict) -> bool:
        names = (self.url_name, *self.also_active_on) if self.url_name else self.also_active_on
        return view_name in names and all(view_kwargs.get(key) == value for key, value in self.url_kwargs)


def cash_desk_items() -> tuple[NavItem, ...]:
    """One menu item per active cash desk."""
    from finance_saber.cashbook.models import CashDesk  # imported late: core shouldn't load other apps' models

    return tuple(
        NavItem(
            desk.name,
            "cashbook:operations",
            url_kwargs=(("desk", desk.slug),),
            also_active_on=("cashbook:operation_edit",),
            permission="cashbook.view_operation",
        )
        for desk in CashDesk.objects.filter(is_active=True)
    )


@dataclass
class MenuEntry:
    """A NavItem resolved for one request: its URL and whether it is the current page."""

    label: str
    url: str
    icon: str
    active: bool
    children: list["MenuEntry"] = field(default_factory=list)


# The money side of the menu (accounting, purchases, reports): for roles that see the cash desks.
MONEY = "cashbook.view_operation"

NAVIGATION = (
    NavItem(_("Dashboard"), "core:home", icon="solar:widget-2-line-duotone"),
    NavItem(
        _("Cash desk"),
        icon="solar:wallet-2-line-duotone",
        children_from=cash_desk_items,
        children=(
            NavItem(_("Upload Excel"), "cashbook:workbook_import", permission="cashbook.add_operation"),
            NavItem(
                _("Handbook"),
                "cashbook:handbook",
                also_active_on=(
                    "cashbook:article_add",
                    "cashbook:article_edit",
                    "cashbook:category_add",
                    "cashbook:category_edit",
                    "cashbook:handbook_import",
                ),
                permission="cashbook.view_article",
            ),
            NavItem(
                _("Counterparties"),
                "cashbook:counterparties",
                also_active_on=(
                    "cashbook:counterparty_add",
                    "cashbook:counterparty_edit",
                    "cashbook:region_add",
                    "cashbook:region_edit",
                ),
                permission="cashbook.view_counterparty",
            ),
            NavItem(
                _("Companies"),
                "cashbook:companies",
                also_active_on=("cashbook:company_add", "cashbook:company_edit"),
                permission="cashbook.view_company",
            ),
        ),
    ),
    NavItem(
        _("Production"),
        icon="solar:box-line-duotone",
        children=(
            NavItem(_("Daily production"), "production:daily", permission="production.view_productionrun"),
            NavItem(_("Production journal"), "production:journal", permission="production.view_productionrun"),
            NavItem(
                _("Raw materials"),
                "production:materials",
                also_active_on=(
                    "production:material_add",
                    "production:material_edit",
                    "production:material_write_off",
                    "production:material_import",
                ),
                permission="production.view_material",
            ),
            NavItem(
                _("Material receipts"),
                "production:receipts",
                also_active_on=("production:material_receipt", "production:receipt"),
                permission="production.view_materialreceipt",
            ),
            NavItem(
                _("Finished goods"),
                "production:products",
                also_active_on=("production:shipment",),
                permission="production.view_product",
            ),
            NavItem(
                _("Recipes and norms"),
                "production:recipes",
                also_active_on=("production:recipe_edit", "production:product_add", "production:recipe_import"),
                permission="production.view_recipeline",
            ),
            NavItem(
                _("Stock movements"),
                "production:moves",
                also_active_on=("production:stock_count",),
                permission="production.view_materialmove",
            ),
            NavItem(_("Import from Excel"), "production:import", permission="production.import_excel"),
        ),
    ),
    NavItem(
        _("Finance"),
        icon="solar:wallet-money-line-duotone",
        children=(
            NavItem(_("Weekly cash plan"), "planning:cash", permission="planning.view_cashplan"),
            NavItem(_("Collections by region"), "planning:collections", permission="planning.view_collectionplan"),
            NavItem(_("Exchange rates"), "planning:rates", permission="cashbook.view_exchangerate"),
            NavItem(_("Chart of accounts"), "core:accounts", permission=MONEY),
            NavItem(_("Journal entries"), "core:journal", permission=MONEY),
            NavItem(_("Bank & cash"), "core:banking", permission=MONEY),
        ),
    ),
    NavItem(
        _("Sales"),
        icon="solar:cart-large-2-line-duotone",
        children=(
            NavItem(
                _("Daily sales"),
                "sales:daily",
                also_active_on=("sales:sale_edit", "sales:import"),
                permission="sales.view_sale",
            ),
            NavItem(
                _("Debtors and creditors"),
                "sales:debts",
                also_active_on=("sales:statement",),
                permission="sales.view_client",
            ),
            NavItem(_("Sales plan"), "sales:plan", permission="sales.view_plan"),
            NavItem(
                _("Clients"), "sales:clients", also_active_on=("sales:client_edit",), permission="sales.view_client"
            ),
            NavItem(_("Invoices"), "core:invoices", permission="sales.view_sale"),
        ),
    ),
    NavItem(
        _("Purchases"),
        icon="solar:bag-4-line-duotone",
        children=(
            NavItem(_("Vendors"), "core:vendors", permission=MONEY),
            NavItem(_("Bills"), "core:bills", permission=MONEY),
            NavItem(_("Payments made"), "core:payments_made", permission=MONEY),
        ),
    ),
    NavItem(
        _("Reports"),
        icon="solar:chart-square-line-duotone",
        children=(
            NavItem(_("Profit & loss"), "core:profit_and_loss", permission=MONEY),
            NavItem(_("Balance sheet"), "core:balance_sheet", permission=MONEY),
            NavItem(_("Cash flow"), "core:cash_flow", permission=MONEY),
        ),
    ),
    NavItem(
        _("Administration"),
        icon="solar:shield-user-line-duotone",
        children=(
            NavItem(
                _("Users"),
                "users:list",
                also_active_on=("users:add", "users:edit"),
                permission="users.change_user",
            ),
            NavItem(
                _("Roles and permissions"),
                "users:roles",
                also_active_on=("users:role_edit",),
                permission="users.change_user",
            ),
            NavItem(_("Action log"), "audit:events", permission="audit.view_auditevent"),
        ),
    ),
)


def build_menu(request: HttpRequest) -> list[MenuEntry]:
    match = request.resolver_match
    current, current_kwargs = (match.view_name, match.kwargs) if match else ("", {})

    def allowed(item: NavItem) -> bool:
        return not item.permission or request.user.has_perm(item.permission)

    def resolve(item: NavItem) -> MenuEntry | None:
        child_items = (*item.children_from(), *item.children) if item.children_from else item.children
        children = [entry for child in child_items if allowed(child) and (entry := resolve(child))]
        if not item.url_name and child_items and not children:
            return None  # a section whose pages this user can't open
        return MenuEntry(
            label=item.label,
            url=reverse(item.url_name, kwargs=dict(item.url_kwargs)) if item.url_name else "",
            icon=item.icon,
            active=item.is_current(current, current_kwargs) or any(child.active for child in children),
            children=children,
        )

    return [entry for item in NAVIGATION if allowed(item) and (entry := resolve(item))]


def find(url_name: str) -> tuple[NavItem | None, NavItem | None]:
    """Return ``(section, item)`` for the menu item that links to ``url_name``."""
    for item in NAVIGATION:
        if item.url_name == url_name:
            return None, item
        for child in item.children:
            if child.url_name == url_name:
                return item, child
    return None, None
