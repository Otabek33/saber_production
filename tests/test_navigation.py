"""The main menu and the pages it links to."""

import pytest
from django.urls import reverse

from finance_saber.core.navigation import NAVIGATION

MENU_URL_NAMES = [child.url_name for item in NAVIGATION for child in item.children] + [
    item.url_name for item in NAVIGATION if item.url_name
]


@pytest.mark.parametrize("url_name", MENU_URL_NAMES)
def test_every_menu_page_renders(client, superuser, url_name):
    client.force_login(superuser)  # the one user who may open every page in the menu
    response = client.get(reverse(url_name), follow=True)
    assert response.status_code == 200


def test_current_page_is_marked_active(client, user):
    client.force_login(user)
    response = client.get(reverse("core:invoices"))
    active = [entry for entry in response.context["nav_menu"] if entry.active]
    assert [child.url for entry in active for child in entry.children if child.active] == [reverse("core:invoices")]
    assert len(active) == 1
    assert b'aria-current="page"' in response.content


@pytest.mark.parametrize("desk", ["bon", "inkassa"])
def test_each_cash_desk_has_its_own_menu_item(client, user, desk):
    client.force_login(user)
    url = reverse("cashbook:operations", kwargs={"desk": desk})
    response = client.get(url)
    cash_section = next(entry for entry in response.context["nav_menu"] if entry.url == "" and entry.active)
    assert [child.url for child in cash_section.children] == [
        "/cash/bon/",
        "/cash/inkassa/",
        "/cash/bank/",
        "/cash/import/",
        "/cash/handbook/",
        "/cash/counterparties/",
        "/cash/companies/",
    ]
    assert [child.url for child in cash_section.children if child.active] == [url]


def test_sales_pages_are_in_the_sales_section(client, user):
    client.force_login(user)
    response = client.get(reverse("sales:debts"))
    active = [entry for entry in response.context["nav_menu"] if entry.active]
    assert [entry.label for entry in active] == ["Продажи"]
    assert [child.url for child in active[0].children][:4] == [
        reverse("sales:daily"),
        reverse("sales:debts"),
        reverse("sales:plan"),
        reverse("sales:clients"),
    ]
    production = next(entry for entry in response.context["nav_menu"] if entry.label == "Производство")
    assert not any(child.url.startswith("/sales/") for child in production.children)
