"""Raw materials: 20 at a time, «Show 20 more» adds the next ones (partials/_load_more.html)."""

import pytest
from django.urls import reverse

from finance_saber.production.models import Material

PAGE = reverse("production:materials")


@pytest.fixture
def materials(db):
    return Material.objects.bulk_create(Material(name=f"Материал {number:02d}", unit="кг") for number in range(45))


def test_the_first_20_and_a_link_to_the_next(admin_client, materials):
    page = admin_client.get(PAGE).content.decode()
    assert page.count("Материал ") == 20
    assert 'data-load-more="#material-rows"' in page
    assert 'href="?page=2"' in page
    assert 'class="pagination' not in page  # no page numbers any more


def test_the_next_page_has_the_rows_and_a_smaller_last_step(admin_client, materials):
    second = admin_client.get(PAGE, {"page": 2}).content.decode()
    assert 'id="material-rows"' in second and 'href="?page=3"' in second
    last = admin_client.get(PAGE, {"page": 3}).content.decode()
    assert "data-load-more=" not in last  # 45 = 20 + 20 + 5: nothing after the third
    assert last.count("Материал ") == 5


def test_the_search_is_kept_in_the_link(admin_client, materials):
    page = admin_client.get(PAGE, {"q": "Материал"}).content.decode()
    assert "?q=%D0%9C%D0%B0%D1%82%D0%B5%D1%80%D0%B8%D0%B0%D0%BB&amp;page=2" in page
