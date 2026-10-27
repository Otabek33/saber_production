"""Password reset by a code sent to Telegram, and linking the account through the bot."""

import json

import pytest
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from finance_saber.users import reset, telegram
from finance_saber.users.models import PasswordResetCode, TelegramLinkToken

SECRET = "secret-123"


@pytest.fixture
def sent(monkeypatch):
    """Record Telegram messages instead of calling Telegram."""
    messages = []
    monkeypatch.setattr(telegram, "send_message", lambda chat_id, text: messages.append((chat_id, text)) or True)
    monkeypatch.setattr(telegram, "configured", lambda: True)
    monkeypatch.setattr(telegram.settings, "TELEGRAM_BOT_USERNAME", "finance_saber_bot", raising=False)
    monkeypatch.setattr(telegram.settings, "TELEGRAM_WEBHOOK_SECRET", SECRET, raising=False)
    return messages


@pytest.fixture
def linked(db, user, sent):
    user.telegram_chat_id = 4242
    user.telegram_username = "alice_tg"
    user.save()
    return user


def code_from(sent_messages):
    return sent_messages[-1][1].split(": ")[1].split("\n")[0]


def webhook(client, message):
    body = json.dumps({"message": message})
    return client.post(reverse("users:telegram_webhook", args=[SECRET]), body, content_type="application/json")


def test_email_reset_pages_are_gone():
    for name in ("users:password_reset_done", "users:password_reset_confirm", "users:password_reset_complete"):
        with pytest.raises(NoReverseMatch):
            reverse(name)


def test_request_page_is_public(client):
    assert client.get(reverse("users:password_reset")).status_code == 200


def test_code_is_sent_to_the_linked_chat_by_username(client, linked, sent):
    client.post(reverse("users:password_reset"), {"identifier": "alice"})
    assert sent[0][0] == 4242
    assert len(code_from(sent)) == 6 and code_from(sent).isdigit()


def test_telegram_username_works_too(client, linked, sent):
    client.post(reverse("users:password_reset"), {"identifier": "@alice_tg"})
    assert sent and sent[0][0] == 4242


def test_unknown_or_unlinked_account_gives_the_same_answer(client, user, sent):
    response = client.post(reverse("users:password_reset"), {"identifier": "nobody"}, follow=True)
    assert response.redirect_chain[-1][0] == reverse("users:password_reset_code")
    response = client.post(reverse("users:password_reset"), {"identifier": "alice"}, follow=True)
    assert response.redirect_chain[-1][0] == reverse("users:password_reset_code")
    assert sent == []  # no Telegram linked: nothing sent, nothing revealed


def test_full_reset_with_the_right_code(client, linked, sent):
    client.post(reverse("users:password_reset"), {"identifier": "alice"})
    client.post(reverse("users:password_reset_code"), {"code": code_from(sent)})
    response = client.post(
        reverse("users:password_reset_new"),
        {"new_password1": "Brand-new-7731", "new_password2": "Brand-new-7731"},
    )
    assert response.status_code == 302
    linked.refresh_from_db()
    assert linked.check_password("Brand-new-7731")


def test_wrong_code_is_refused_and_counts(client, linked, sent):
    client.post(reverse("users:password_reset"), {"identifier": "alice"})
    response = client.post(reverse("users:password_reset_code"), {"code": "000000"})
    assert response.status_code == 200 and response.context["form"].errors
    assert PasswordResetCode.objects.get().attempts == 1


def test_code_stops_after_too_many_tries(client, linked, sent):
    client.post(reverse("users:password_reset"), {"identifier": "alice"})
    real = code_from(sent)
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(reset.MAX_ATTEMPTS):
        reset.check_code(linked, wrong)
    assert reset.check_code(linked, real) is False


def test_expired_code_is_refused(client, linked, sent):
    client.post(reverse("users:password_reset"), {"identifier": "alice"})
    PasswordResetCode.objects.update(expires_at=timezone.now() - timezone.timedelta(minutes=1))
    assert reset.check_code(linked, code_from(sent)) is False


def test_password_step_needs_a_verified_code(client, linked):
    response = client.get(reverse("users:password_reset_new"))
    assert response.status_code == 302 and response.url == reverse("users:password_reset")


def test_code_step_needs_a_request_first(client):
    assert client.get(reverse("users:password_reset_code")).status_code == 302


def make_link(client, user):
    client.force_login(user)
    client.post(reverse("users:telegram_link"))
    return client.get(reverse("users:profile")).context["telegram_link"].split("start=")[1]


def test_start_with_the_token_links_the_account(client, user, sent):
    token = make_link(client, user)
    client.logout()
    webhook(client, {"chat": {"id": 777}, "from": {"username": "alice_tg"}, "text": f"/start {token}"})
    user.refresh_from_db()
    assert (user.telegram_chat_id, user.telegram_username) == (777, "alice_tg")
    assert not TelegramLinkToken.objects.exists()  # used up


def test_a_fake_token_links_nothing(client, user, sent):
    make_link(client, user)
    client.logout()
    webhook(client, {"chat": {"id": 9}, "text": "/start not-a-real-token"})
    user.refresh_from_db()
    assert user.telegram_chat_id is None


def test_webhook_refuses_a_wrong_secret(client, sent):
    response = client.post(reverse("users:telegram_webhook", args=["wrong"]), "{}", content_type="application/json")
    assert response.status_code == 404


def test_expired_link_token_is_refused(user, sent):
    url = telegram.new_link(user)
    TelegramLinkToken.objects.update(expires_at=timezone.now() - timezone.timedelta(minutes=1))
    assert telegram.link_chat(url.split("start=")[1], 5, "x") is None


def test_unlink(client, linked):
    client.force_login(linked)
    client.post(reverse("users:telegram_unlink"))
    linked.refresh_from_db()
    assert linked.telegram_chat_id is None


def test_without_bot_configuration_nothing_is_sent(monkeypatch, user):
    monkeypatch.setattr(telegram.settings, "TELEGRAM_BOT_TOKEN", "", raising=False)
    user.telegram_chat_id = 1
    user.save()
    assert reset.send_code(user) is False


def test_start_links_by_telegram_username(client, user, sent):
    user.telegram_username = "Otabek33"
    user.save()
    webhook(client, {"chat": {"id": 555}, "from": {"username": "otabek33"}, "text": "/start"})
    user.refresh_from_db()
    assert user.telegram_chat_id == 555
    assert "linked" in sent[-1][1] or "привязана" in sent[-1][1]


def test_start_with_an_unknown_username_explains(client, db, sent):
    webhook(client, {"chat": {"id": 556}, "from": {"username": "stranger"}, "text": "/start"})
    assert sent[-1][0] == 556
    assert "stranger" in sent[-1][1]


def test_a_chat_moves_to_the_account_that_claims_it(client, user, sent):
    from finance_saber.users.models import User

    other = User.objects.create_user(username="other", password="x-long-Passphrase-1", telegram_chat_id=557)
    user.telegram_username = "alice_tg"
    user.save()
    webhook(client, {"chat": {"id": 557}, "from": {"username": "alice_tg"}, "text": "/start"})
    other.refresh_from_db()
    user.refresh_from_db()
    assert (user.telegram_chat_id, other.telegram_chat_id) == (557, None)
