"""The main menu and the pages it links to."""

import pytest
from django.urls import reverse

from finance_saber.core.navigation import NAVIGATION

MENU_URL_NAMES = [child.url_name for item in NAVIGATION for child in item.pages()] + [
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
    assert [child.url for entry in active for child in entry.links if child.active] == [reverse("core:invoices")]
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
    response = client.get(reverse("settlements:balances"))
    active = [entry for entry in response.context["nav_menu"] if entry.active]
    assert [entry.label for entry in active] == ["Продажи"]
    assert [child.url for child in active[0].children][:3] == [
        reverse("settlements:balances"),
        reverse("production:margin"),
        reverse("sales:plan"),
    ]
    production = next(entry for entry in response.context["nav_menu"] if entry.label == "Производство")
    assert not any(child.url.startswith("/sales/") for child in production.links)


def test_production_is_split_into_four_groups(client, superuser):
    client.force_login(superuser)
    response = client.get(reverse("production:orders"))
    production = next(entry for entry in response.context["nav_menu"] if entry.label == "Производство")
    assert production.grouped and production.active
    groups = {str(group.label): [str(page.label) for page in group.children] for group in production.children}
    assert groups == {
        "Выпуск": ["Обзор", "Выпуск за день", "Журнал выпуска", "Партии выпуска"],
        "Сырьё": ["Сырьё на складе", "Поступления сырья", "Товары в пути", "Поставщики", "Анализ сырья"],
        "Продукция": ["Готовая продукция", "Себестоимость", "Сроки годности", "Отгрузки"],
        "Нормы и учёт": [
            "Рецептуры и нормы",
            "Нормы и потери",
            "Движения склада",
            "Закрытие периода",
            "Импорт из Excel",
        ],
    }
    assert [page.url for page in production.links if page.active] == [reverse("production:orders")]
    content = response.content.decode()
    assert 'class="collapse first-level nav-groups"' in content
    assert content.count('class="nav-group-title"') == 4


def test_a_group_the_user_cannot_open_is_left_out(client, operator):
    client.force_login(operator)
    menu = client.get(reverse("core:home")).context["nav_menu"]
    production = next(entry for entry in menu if entry.label == "Производство")
    assert [str(group.label) for group in production.children] == ["Продукция"]


def test_new_pages_have_a_badge_and_their_section_a_dot(client, superuser, monkeypatch):
    import datetime

    from django.utils import timezone

    from finance_saber.core import navigation

    client.force_login(superuser)
    monkeypatch.setattr(timezone, "localdate", lambda *args: navigation.NEW_UNTIL)
    response = client.get(reverse("core:home"))
    production = next(entry for entry in response.context["nav_menu"] if entry.label == "Производство")
    assert production.has_new
    assert {page.url for page in production.links if page.new} >= {reverse("production:costs")}
    content = response.content.decode()
    assert f'data-new="{reverse("production:costs")}"' in content and "data-new-dot" in content
    # After the date, no badges.
    monkeypatch.setattr(timezone, "localdate", lambda *args: navigation.NEW_UNTIL + datetime.timedelta(days=1))
    response = client.get(reverse("core:home"))
    assert "data-new=" not in response.content.decode()


def test_every_new_page_is_in_the_menu():
    pages = {page.url_name for item in NAVIGATION for page in item.pages()}
    from finance_saber.core.navigation import NEW_PAGES

    assert pages >= NEW_PAGES
