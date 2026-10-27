"""The daily dashboard report the Telegram bot sends."""

import datetime
import json
from decimal import Decimal

import pytest
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone, translation

from finance_saber.core import daily_report
from finance_saber.users import telegram
from finance_saber.users.models import User

TASHKENT = datetime.timezone(datetime.timedelta(hours=5))


@pytest.fixture
def sent(monkeypatch):
    messages = []

    def fake_send(chat_id, text, parse_mode=""):
        messages.append((chat_id, text, parse_mode))
        return True

    monkeypatch.setattr(telegram, "send_message", fake_send)
    monkeypatch.setattr(telegram.settings, "TELEGRAM_WEBHOOK_SECRET", "s", raising=False)
    return messages


@pytest.fixture
def boss(db):
    user = User.objects.create_superuser("boss", "boss@example.com", "x-long-Passphrase-1")
    user.telegram_chat_id = 100
    user.daily_report = True
    user.save()
    return user


def at(hour, minute, day=datetime.date(2026, 10, 22)):
    return datetime.datetime.combine(day, datetime.time(hour, minute), tzinfo=TASHKENT)


def test_time_zone_is_tashkent(settings):
    assert settings.TIME_ZONE == "Asia/Tashkent"


def test_report_has_the_main_dashboard_parts(boss):
    with translation.override("ru"):
        text = daily_report.render(boss, datetime.date(2026, 10, 22))
    for part in ("Ежедневная сводка", "22.10.2026", "Деньги на конец дня", "Поступления", "Расходы", "Отгружено"):
        assert part in text


def test_a_role_without_data_gets_a_short_note(db):
    user = User.objects.create_user("plain", password="x-long-Passphrase-1")
    with translation.override("en"):
        text = daily_report.render(user, datetime.date(2026, 10, 22))
    assert "no dashboard data" in text and "Money at day end" not in text


def test_role_limits_the_blocks(db):
    from finance_saber.users.roles import Role, set_role

    user = User.objects.create_user("mgr", password="x-long-Passphrase-1")
    set_role(user, Role.MANAGER)
    user = User.objects.get(pk=user.pk)  # fresh permission cache
    with translation.override("en"):
        text = daily_report.render(user, datetime.date(2026, 10, 22))
    assert Group.objects.filter(user=user).exists()
    assert ("Money at day end" in text) == user.has_perm("cashbook.view_operation")
    assert ("Shipped" in text) == user.has_perm("sales.view_sale")


def test_money_is_short_and_uses_a_real_minus():
    with translation.override("ru"):
        assert daily_report.money(Decimal("-57500000")) == "−57,5 млн"
        assert daily_report.money(Decimal("7570000000")) == "7,57 млрд"
        assert daily_report.money(Decimal("950000")) == "950\xa0000"


def test_names_are_escaped_for_telegram_html(boss, monkeypatch):
    from finance_saber.core import dashboard

    original = dashboard.build

    def with_debtor(user, period):
        data = original(user, period)
        row = type("Row", (), {"name": "<A&B>", "balance": Decimal(5)})()
        data["debts"] = {"receivable": 5, "payable": 0, "debtor_count": 1, "top": [row]}
        return data

    monkeypatch.setattr(dashboard, "build", with_debtor)
    assert "&lt;A&amp;B&gt;" in daily_report.render(boss, datetime.date(2026, 10, 22))


@pytest.fixture
def desk_day(db):
    from finance_saber.cashbook.models import Account, Article, ArticleKind, CashDesk, Category, Kind, Operation

    desk = CashDesk.objects.create(name="Касса <БОН>", slug="bon2", accounts=[Account.CASH_UZS, Account.CARD])
    sales = Category.objects.create(
        article=Article.objects.create(name="Тест продажа", kind=ArticleKind.INCOME), name="Тест продажа"
    )
    food = Category.objects.create(
        article=Article.objects.create(name="Тест питание", kind=ArticleKind.EXPENSE), name="Офис"
    )
    day = datetime.date(2026, 10, 20)
    common = {"desk": desk, "date": day}
    Operation.objects.create(**common, kind=Kind.INCOME, category=sales, account=Account.CARD, amount=118_000_000)
    for amount in range(1, 16):
        Operation.objects.create(
            **common, kind=Kind.EXPENSE, category=food, account=Account.CASH_UZS, amount=amount * 1000, note="обед"
        )
    Operation.objects.create(
        **common,
        kind=Kind.EXPENSE,
        category=food,
        account=Account.CASH_UZS,
        amount=999,
        is_cancelled=True,
    )
    return desk, day


def test_each_cash_desk_gets_its_day(boss, desk_day):
    desk, day = desk_day
    with translation.override("ru"):
        text = next(text for text in daily_report.desk_texts(boss, day) if "БОН" in text)
    assert "Касса &lt;БОН&gt;" in text
    assert "+118,0 млн</b> — Тест продажа\n" in text  # article name not repeated
    assert "Тест питание › Офис" in text
    assert "Расходы: −120\xa0000" in text and "(15 опер.)" in text  # the cancelled one is left out
    assert text.count("обед") == daily_report.OPERATIONS_LIMIT
    assert "и ещё 3 опер. на −6\xa0000" in text


def test_desk_days_need_the_cash_book_permission(db, desk_day):
    user = User.objects.create_user("plain", password="x-long-Passphrase-1")
    assert daily_report.desk_texts(user, desk_day[1]) == []


def test_desks_come_as_separate_messages(boss, desk_day, sent):
    daily_report.send(boss, desk_day[1])
    assert len(sent) == 1 + len(daily_report.desk_texts(boss, desk_day[1]))


def test_long_reports_are_split_at_line_ends():
    text = "\n".join("x" * 100 for _ in range(100))
    chunks = daily_report.split(text, limit=1000)
    assert all(len(chunk) <= 1000 for chunk in chunks)
    assert "\n".join(chunks) == text


def test_sent_once_after_the_time(boss, sent):
    assert daily_report.send_due(at(17, 29)) == 0
    assert daily_report.send_due(at(17, 30)) == 1
    assert daily_report.send_due(at(18, 0)) == 0  # already had today's
    assert sent[0][0] == 100 and sent[0][2] == "HTML"
    assert daily_report.send_due(at(17, 31, datetime.date(2026, 10, 23))) == 1  # and again tomorrow


def test_not_sent_when_off_unlinked_or_inactive(boss, sent):
    for change in ({"daily_report": False}, {"telegram_chat_id": None}, {"is_active": False}):
        User.objects.filter(pk=boss.pk).update(
            **{"daily_report": True, "telegram_chat_id": 100, "is_active": True, **change}
        )
        assert daily_report.send_due(at(18, 0)) == 0
    assert sent == []


def test_a_refused_message_is_tried_again(boss, monkeypatch):
    monkeypatch.setattr(telegram, "send_message", lambda *args, **kwargs: False)
    assert daily_report.send_due(at(17, 30)) == 0
    boss.refresh_from_db()
    assert boss.daily_report_sent_on is None


def test_own_time_is_respected(boss, sent):
    User.objects.filter(pk=boss.pk).update(daily_report_time=datetime.time(9, 0))
    assert daily_report.send_due(at(9, 5)) == 1


def test_report_command_in_the_bot(client, boss, sent):
    body = json.dumps({"message": {"chat": {"id": 100}, "text": "/report"}})
    client.post(reverse("users:telegram_webhook", args=["s"]), body, content_type="application/json")
    assert sent and sent[0][0] == 100 and sent[0][2] == "HTML"


def test_report_command_needs_a_linked_account(client, db, sent):
    body = json.dumps({"message": {"chat": {"id": 999}, "text": "/report"}})
    client.post(reverse("users:telegram_webhook", args=["s"]), body, content_type="application/json")
    assert sent[0][0] == 999 and "/start" in sent[0][1]


def test_profile_turns_it_on_and_sets_the_time(client, boss, sent):
    client.force_login(boss)
    client.post(reverse("users:telegram_report"), {"daily_report": "on", "daily_report_time": "08:15"})
    boss.refresh_from_db()
    assert boss.daily_report and boss.daily_report_time == datetime.time(8, 15)
    client.post(reverse("users:telegram_report"), {"daily_report_time": "08:15"})
    boss.refresh_from_db()
    assert not boss.daily_report


def test_profile_send_now(client, boss, sent):
    client.force_login(boss)
    client.post(reverse("users:telegram_report"), {"daily_report": "on", "daily_report_time": "17:30", "send_now": "1"})
    assert sent and sent[0][0] == 100


def test_profile_page_shows_the_report_form(client, boss):
    client.force_login(boss)
    assert "daily_report_time" in client.get(reverse("users:profile")).content.decode()


def test_command_sends_to_one_user(boss, sent):
    call_command("send_daily_reports", user="boss")
    assert sent[0][0] == 100


def test_command_sends_whoever_is_due(boss, sent, monkeypatch):
    monkeypatch.setattr(timezone, "now", lambda: at(17, 45))
    call_command("send_daily_reports")
    assert sent and "Ежедневная сводка" in sent[0][1]
    call_command("send_daily_reports")  # not again today
    assert sum("Ежедневная сводка" in text for _chat, text, _mode in sent) == 1
