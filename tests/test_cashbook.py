"""Cash desk: handbook, operations, balances and the ledger page."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from finance_saber.cashbook.forms import MoneyField
from finance_saber.cashbook.ledger import balances, build_ledger
from finance_saber.cashbook.models import Account, Article, CashDesk, Category, Kind, Operation

D = Decimal
DAY = datetime.date(2026, 10, 20)


@pytest.fixture
def desk(db):
    return CashDesk.objects.get(slug="bon")  # seeded by migration 0002


@pytest.fixture
def salary(db):
    return Category.objects.get(article__name="Взаиморасчеты с персоналом", name="ЗП")


@pytest.fixture
def sale(db):
    return Category.objects.get(article__name="Продажа БОН", name="Продажа БОН")


@pytest.fixture
def to_card(db):
    return Category.objects.get(article__name="Переброска на карту")


@pytest.fixture
def exchange(db):
    return Category.objects.get(article__name="Обмена валюты")


def make(desk, category, kind, account, amount, date=DAY, **extra):
    operation = Operation(
        desk=desk, category=category, kind=kind, account=account, amount=D(amount), date=date, **extra
    )
    operation.full_clean()
    operation.save()
    return operation


# --- Amount input --------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("1850000", "1850000"),
        ("1 850 000", "1850000"),
        ("1 850 000,50", "1850000.50"),
        ("1,850,000.00", "1850000.00"),
        ("1.850.000,25", "1850000.25"),
        ("1850000,5", "1850000.5"),
        ("353439.00", "353439.00"),
    ],
)
def test_money_field_reads_common_formats(typed, expected):
    assert MoneyField().clean(typed) == D(expected)


def test_money_field_rejects_zero():
    with pytest.raises(ValidationError):
        MoneyField().clean("0")


# --- Validation ----------------------------------------------------------------


def test_category_must_match_operation_type(desk, salary):
    with pytest.raises(ValidationError) as error:
        make(desk, salary, Kind.INCOME, Account.CASH_UZS, "100")
    assert "category" in error.value.message_dict


def test_transfer_needs_a_different_target_account(desk, to_card):
    with pytest.raises(ValidationError) as error:
        make(desk, to_card, Kind.TRANSFER, Account.CASH_UZS, "100", to_account=Account.CASH_UZS)
    assert "to_account" in error.value.message_dict


def test_same_currency_transfer_receives_the_same_amount(desk, to_card):
    operation = make(desk, to_card, Kind.TRANSFER, Account.CASH_UZS, "1000000", to_account=Account.CARD)
    assert operation.to_amount == D("1000000")


def test_exchange_needs_the_received_amount(desk, exchange):
    with pytest.raises(ValidationError) as error:
        make(desk, exchange, Kind.TRANSFER, Account.CASH_USD, "100", to_account=Account.CASH_UZS)
    assert "to_amount" in error.value.message_dict


def test_non_transfer_drops_transfer_fields(desk, salary):
    operation = make(desk, salary, Kind.EXPENSE, Account.CARD, "100", to_account=Account.CASH_UZS, to_amount=D("5"))
    assert operation.to_account == ""
    assert operation.to_amount is None


# --- Balances and ledger -------------------------------------------------------


def test_balances_follow_income_expense_transfer_and_exchange(desk, sale, salary, to_card, exchange):
    make(desk, sale, Kind.INCOME, Account.CASH_UZS, "5000000")
    make(desk, salary, Kind.EXPENSE, Account.CASH_UZS, "1000000")
    make(desk, to_card, Kind.TRANSFER, Account.CASH_UZS, "2000000", to_account=Account.CARD)
    make(desk, exchange, Kind.TRANSFER, Account.CASH_UZS, "1200000", to_account=Account.CASH_USD, to_amount=D("100"))
    assert balances(desk.operations.all()) == {
        Account.CASH_UZS: D("800000"),
        Account.CASH_USD: D("100"),
        Account.CARD: D("2000000"),
        Account.BANK: 0,
    }


def test_cancelled_operations_do_not_count(desk, sale):
    make(desk, sale, Kind.INCOME, Account.CARD, "500", is_cancelled=True)
    assert balances(desk.operations.all())[Account.CARD] == 0


def test_ledger_running_balance_ignores_filters(desk, sale, salary):
    make(desk, sale, Kind.INCOME, Account.CASH_UZS, "100", date=DAY - datetime.timedelta(days=1))
    make(desk, sale, Kind.INCOME, Account.CASH_UZS, "50")
    expense = make(desk, salary, Kind.EXPENSE, Account.CASH_UZS, "30")

    only_expenses = desk.operations.filter(kind=Kind.EXPENSE)
    ledger = build_ledger(desk.operations.all(), DAY, DAY, only_expenses)

    assert ledger.opening[Account.CASH_UZS] == D("100")
    assert [row.operation for row in ledger.rows] == [expense]
    # The hidden income of 50 still counts: 100 + 50 - 30.
    assert ledger.rows[0].balance[Account.CASH_UZS] == D("120")
    assert ledger.expense_total[Account.CASH_UZS] == D("30")
    assert ledger.income_total[Account.CASH_UZS] == 0


# --- Pages ---------------------------------------------------------------------


@pytest.fixture
def signed_in(client, user):
    client.force_login(user)
    return client


def desk_url(desk, **query):
    url = reverse("cashbook:operations", kwargs={"desk": desk.slug})
    return url + ("?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else "")


def test_menu_link_opens_the_first_desk(signed_in, desk):
    response = signed_in.get(reverse("cashbook:index"))
    assert response.url == desk_url(desk)


def test_ledger_page_shows_operations_and_balances(signed_in, desk, sale):
    make(desk, sale, Kind.INCOME, Account.CARD, "120000000", note="пополнение карт")
    response = signed_in.get(desk_url(desk, date_from="2026-10-01", date_to="2026-10-31"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "пополнение карт" in content
    assert response.context["ledger"].closing[Account.CARD] == D("120000000")


def test_add_operation_keeps_date_and_type_for_the_next_entry(signed_in, desk, salary, user):
    response = signed_in.post(
        desk_url(desk),
        {
            "kind": "expense",
            "date": "2026-10-20",
            "category": salary.pk,
            "account": "card",
            "amount": "2 000 000",
            "note": "Shuxratov Abdulaziz аванс опер",
        },
    )
    assert response.status_code == 302
    assert "entry_date=2026-10-20" in response.url
    assert "entry_kind=expense" in response.url
    operation = Operation.objects.get()
    assert operation.amount == D("2000000")
    assert operation.created_by == user


def test_add_transfer(signed_in, desk, to_card):
    signed_in.post(
        desk_url(desk),
        {
            "kind": "transfer",
            "date": "2026-10-20",
            "category": to_card.pk,
            "account": "cash_uzs",
            "amount": "1000000",
            "to_account": "card",
        },
    )
    operation = Operation.objects.get()
    assert (operation.to_account, operation.to_amount) == (Account.CARD, D("1000000"))


def test_invalid_operation_shows_errors(signed_in, desk, salary):
    response = signed_in.post(desk_url(desk), {"kind": "income", "date": "2026-10-20", "category": salary.pk})
    assert response.status_code == 400
    assert {"category", "account", "amount"} <= set(response.context["entry_form"].errors)
    assert not Operation.objects.exists()


def test_edit_operation(signed_in, desk, salary):
    operation = make(desk, salary, Kind.EXPENSE, Account.CARD, "100")
    url = reverse("cashbook:operation_edit", kwargs={"desk": desk.slug, "pk": operation.pk})
    response = signed_in.post(
        url,
        {"kind": "expense", "date": "2026-10-21", "category": salary.pk, "account": "card", "amount": "150"},
    )
    assert response.status_code == 302
    operation.refresh_from_db()
    assert (operation.amount, operation.date) == (D("150"), datetime.date(2026, 10, 21))


def test_cancel_and_restore_operation(signed_in, desk, salary):
    operation = make(desk, salary, Kind.EXPENSE, Account.CARD, "100")
    url = reverse("cashbook:operation_cancel", kwargs={"desk": desk.slug, "pk": operation.pk})
    assert signed_in.get(url).status_code == 405
    signed_in.post(url)
    operation.refresh_from_db()
    assert operation.is_cancelled
    signed_in.post(url)
    operation.refresh_from_db()
    assert not operation.is_cancelled


def test_cancel_does_not_redirect_off_site(signed_in, desk, salary):
    operation = make(desk, salary, Kind.EXPENSE, Account.CARD, "100")
    url = reverse("cashbook:operation_cancel", kwargs={"desk": desk.slug, "pk": operation.pk})
    response = signed_in.post(url, {"next": "//evil.example/"})
    assert response.url == desk_url(desk)


def test_handbook_lists_seeded_articles(signed_in):
    response = signed_in.get(reverse("cashbook:handbook"))
    assert response.status_code == 200
    assert "Взаиморасчеты с персоналом" in response.content.decode()


def test_add_article_and_category(signed_in):
    signed_in.post(
        reverse("cashbook:article_add"), {"name": "Налоги", "kind": "expense", "position": 30, "is_active": "on"}
    )
    article = Article.objects.get(name="Налоги")
    signed_in.post(reverse("cashbook:category_add"), {"article": article.pk, "name": "НДС", "is_active": "on"})
    assert article.categories.filter(name="НДС").exists()


def test_inactive_category_is_not_offered_for_new_operations(signed_in, desk, salary):
    salary.is_active = False
    salary.save()
    response = signed_in.get(desk_url(desk))
    assert salary not in response.context["entry_form"].fields["category"].queryset


def test_operations_cannot_be_deleted_in_admin(client, desk, salary, django_user_model):
    admin = django_user_model.objects.create_superuser("root", password="x-pass-123456")
    client.force_login(admin)
    operation = make(desk, salary, Kind.EXPENSE, Account.CARD, "100")
    response = client.post(reverse("admin:cashbook_operation_delete", args=[operation.pk]), {"post": "yes"})
    assert response.status_code == 403
    assert Operation.objects.filter(pk=operation.pk).exists()


def test_ledger_is_paginated_by_20_and_opens_on_the_newest_page(signed_in, desk, sale):
    for day in range(1, 26):  # 25 operations of 10 each
        make(desk, sale, Kind.INCOME, Account.CASH_UZS, "10", date=datetime.date(2026, 10, day))
    url = desk_url(desk, date_from="2026-10-01", date_to="2026-10-31")

    last = signed_in.get(url).context
    assert (last["page"].number, len(last["page"].object_list)) == (2, 5)
    # The page starts from the balance after the 20 rows on page 1.
    assert last["page_opening"][Account.CASH_UZS] == D("200")
    assert last["ledger"].closing[Account.CASH_UZS] == D("250")

    first = signed_in.get(url + "&page=1").context
    assert len(first["page"].object_list) == 20
    assert first["page_opening"][Account.CASH_UZS] == 0


def test_adding_an_operation_does_not_filter_the_list(signed_in, desk, salary, sale):
    make(desk, sale, Kind.INCOME, Account.CASH_UZS, "100")
    response = signed_in.post(
        desk_url(desk),
        {"kind": "expense", "date": "2026-10-20", "category": salary.pk, "account": "cash_uzs", "amount": "10"},
    )
    follow = signed_in.get(response.url.replace("#entry", "") + "&date_from=2026-10-01&date_to=2026-10-31")
    kinds = {row.operation.kind for row in follow.context["page"].object_list}
    assert kinds == {Kind.INCOME, Kind.EXPENSE}
    assert follow.context["entry_form"].initial["kind"] == "expense"


def test_brand_is_shown(signed_in, desk):
    content = signed_in.get(desk_url(desk)).content.decode()
    assert '<span class="brand-main">BON NATURAL</span>' in content
    assert "7Saber" not in content
    assert "matdash/images/logos/logo.svg" not in content
