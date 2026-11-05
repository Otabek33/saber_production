"""The app's main menu, shown as the horizontal navigation bar.

This is the single place to add, rename or reorder menu entries. The header
search and the "coming soon" page titles are built from it too.

A long section can be split into groups: a NavItem with children and no URL inside a
section is a group, shown as a headed column of the dropdown (a headed block in the drawer).
"""

import datetime
from collections.abc import Callable
from dataclasses import dataclass, field

from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy


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

    def pages(self) -> tuple["NavItem", ...]:
        """The section's page items, with those inside its groups (the groups themselves left out)."""
        return tuple(page for child in self.children for page in (child.pages() if child.children else (child,)))


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
    new: bool = False  # a new page: a «Новое» badge until the person opens it (js/app.js)

    @property
    def has_new(self) -> bool:
        """A section with a new page somewhere in it: a dot on the section."""
        return any(child.new or child.has_new for child in self.children)

    @property
    def grouped(self) -> bool:
        """A section split into groups (its children are groups with their own children)."""
        return any(child.children for child in self.children)

    @property
    def links(self) -> list["MenuEntry"]:
        """The section's pages, with those inside its groups."""
        return [page for child in self.children for page in (child.links if child.children else [child])]


# New pages get a «Новое» badge in the menu (and their section a dot) until NEW_UNTIL, and for each
# person only until they open the page. Add a page's url name here when it is added; move the date on.
NEW_UNTIL = datetime.date(2026, 12, 4)
NEW_PAGES = frozenset({
    "production:index", "production:orders", "production:material_analysis", "production:suppliers",
    "production:costs", "production:expiry", "production:losses", "production:close", "production:margin",
    "settlements:balances", "purchases:vendors", "purchases:bills", "purchases:payments",
})  # fmt: skip

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
        # Twelve pages in four groups: four short columns in the dropdown, not one long list.
        children=(
            NavItem(
                pgettext_lazy("menu group", "Output"),
                children=(
                    NavItem(_("Overview"), "production:index", permission="production.view_material"),
                    NavItem(_("Daily production"), "production:daily", permission="production.view_productionrun"),
                    NavItem(_("Production journal"), "production:journal", permission="production.view_productionrun"),
                    NavItem(
                        _("Production batches"),
                        "production:batches",
                        also_active_on=("production:batch",),
                        permission="production.view_productionrun",
                    ),
                ),
            ),
            NavItem(
                pgettext_lazy("menu group", "Raw materials"),
                children=(
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
                        _("Goods in transit"),
                        "production:orders",
                        also_active_on=("production:order_new", "production:order", "production:order_edit"),
                        permission="production.view_materialorder",
                    ),
                    NavItem(
                        _("Suppliers"),
                        "production:suppliers",
                        also_active_on=("production:supplier",),
                        permission="production.view_materialreceipt",
                    ),
                    NavItem(
                        _("Material analysis"),
                        "production:material_analysis",
                        permission="production.view_recipeline",
                    ),
                ),
            ),
            NavItem(
                pgettext_lazy("menu group", "Finished goods"),
                children=(
                    NavItem(_("Finished goods"), "production:products", permission="production.view_product"),
                    NavItem(_("Production cost"), "production:costs", permission="production.view_costs"),
                    NavItem(
                        pgettext_lazy("stock page", "Shelf life"),
                        "production:expiry",
                        permission="production.view_product",
                    ),
                    NavItem(
                        _("Shipments"),
                        "production:shipments",
                        also_active_on=("production:shipment", "production:shipment_detail"),
                        permission="production.view_productshipment",
                    ),
                ),
            ),
            NavItem(
                pgettext_lazy("menu group", "Norms and records"),
                children=(
                    NavItem(
                        _("Recipes and norms"),
                        "production:recipes",
                        also_active_on=(
                            "production:recipe_edit",
                            "production:product_add",
                            "production:recipe_import",
                        ),
                        permission="production.view_recipeline",
                    ),
                    NavItem(_("Losses against norms"), "production:losses", permission="production.view_materialmove"),
                    NavItem(
                        _("Stock movements"),
                        "production:moves",
                        also_active_on=("production:stock_count",),
                        permission="production.view_materialmove",
                    ),
                    NavItem(_("Closing a period"), "production:close", permission="production.view_closedperiod"),
                    NavItem(_("Import from Excel"), "production:import", permission="production.import_excel"),
                ),
            ),
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
                _("Settlements"),
                "settlements:balances",
                also_active_on=("settlements:statement",),
                permission="settlements.view_openingbalance",
            ),
            NavItem(_("Margin"), "production:margin", permission="production.view_costs"),
            NavItem(_("Sales plan"), "sales:plan", also_active_on=("sales:import",), permission="sales.view_plan"),
            NavItem(_("Invoices"), "core:invoices", permission=MONEY),
        ),
    ),
    NavItem(
        _("Purchases"),
        icon="solar:bag-4-line-duotone",
        children=(
            NavItem(_("Vendors"), "purchases:vendors", permission="purchases.view_bill"),
            NavItem(
                _("Bills"), "purchases:bills", also_active_on=("purchases:bill_new",), permission="purchases.view_bill"
            ),
            NavItem(_("Payments made"), "purchases:payments", permission="purchases.view_bill"),
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

    showing_new = timezone.localdate() <= NEW_UNTIL

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
            new=showing_new and item.url_name in NEW_PAGES,
        )

    return [entry for item in NAVIGATION if allowed(item) and (entry := resolve(item))]


def find(url_name: str) -> tuple[NavItem | None, NavItem | None]:
    """Return ``(section, item)`` for the menu item that links to ``url_name``."""
    for item in NAVIGATION:
        if item.url_name == url_name:
            return None, item
        for child in item.pages():
            if child.url_name == url_name:
                return item, child
    return None, None
