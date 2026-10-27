"""Sign in, sign up, sign out, profile and password reset."""

import pytest
from django.urls import reverse

from finance_saber.users.models import User


@pytest.mark.parametrize("url_name", ["users:login", "users:register", "users:password_reset"])
def test_auth_pages_are_public(client, db, url_name):
    response = client.get(reverse(url_name))
    assert response.status_code == 200


@pytest.mark.parametrize("url_name", ["users:login", "users:password_reset"])
def test_auth_pages_show_the_brand(client, db, url_name):
    content = client.get(reverse(url_name)).content.decode()
    assert '<span class="brand-main">BON NATURAL</span>' in content
    assert "7Saber" not in content
    assert "| BON NATURAL</title>" in content


def test_login(client, user):
    response = client.post(
        reverse("users:login"),
        {"username": "alice", "password": "s3cret-pass", "remember_me": "on"},
    )
    assert response.status_code == 302
    assert response.url == reverse("core:home")
    assert client.session.get_expire_at_browser_close() is False


def test_login_without_remember_me_ends_with_browser_session(client, user):
    client.post(reverse("users:login"), {"username": "alice", "password": "s3cret-pass"})
    assert client.session.get_expire_at_browser_close() is True


def test_login_rejects_wrong_password(client, user):
    response = client.post(reverse("users:login"), {"username": "alice", "password": "wrong"})
    assert response.status_code == 200
    assert b"alert-danger" in response.content


def test_login_redirects_signed_in_user(client, user):
    client.force_login(user)
    response = client.get(reverse("users:login"))
    assert response.status_code == 302


def test_register_creates_and_signs_in_user(client, db):
    response = client.post(
        reverse("users:register"),
        {
            "username": "bob",
            "email": "bob@example.com",
            "telegram_username": "@bob_tg",
            "password1": "a-long-Passphrase-42",
            "password2": "a-long-Passphrase-42",
        },
    )
    assert response.status_code == 302
    assert response.url == reverse("core:home")
    bob = User.objects.get(username="bob")
    assert (bob.email, bob.telegram_username) == ("bob@example.com", "bob_tg")  # stored without the @
    assert client.get(reverse("core:home")).status_code == 200


def test_register_rejects_duplicate_email(client, user):
    user.email = "alice@example.com"
    user.save()
    response = client.post(
        reverse("users:register"),
        {
            "username": "alice2",
            "email": "ALICE@example.com",
            "telegram_username": "alice2_tg",
            "password1": "a-long-Passphrase-42",
            "password2": "a-long-Passphrase-42",
        },
    )
    assert response.status_code == 200
    assert "email" in response.context["form"].errors
    assert not User.objects.filter(username="alice2").exists()


def test_logout_requires_post(client, user):
    client.force_login(user)
    assert client.get(reverse("users:logout")).status_code == 405
    response = client.post(reverse("users:logout"))
    assert response.url == reverse("users:login")
    assert client.get(reverse("core:home")).status_code == 302


def test_profile_update(client, user):
    client.force_login(user)
    response = client.post(
        reverse("users:profile"),
        {"first_name": "Alice", "last_name": "Smith", "email": "alice@example.com", "telegram_username": "alice_tg"},
    )
    assert response.status_code == 302
    user.refresh_from_db()
    assert user.get_full_name() == "Alice Smith"


def register(client, **data):
    form = {"username": "bob", "email": "bob@example.com", "password1": "a-long-Passphrase-42",
            "password2": "a-long-Passphrase-42"} | data  # fmt: skip
    return client.post(reverse("users:register"), form)


def test_register_requires_a_telegram_username(client, db):
    response = register(client)
    assert "telegram_username" in response.context["form"].errors
    assert not User.objects.filter(username="bob").exists()


@pytest.mark.parametrize("bad", ["ab", "has space", "bad-dash", "x" * 33])
def test_register_rejects_an_invalid_telegram_username(client, db, bad):
    assert "telegram_username" in register(client, telegram_username=bad).context["form"].errors


def test_telegram_username_is_unique(client, user):
    user.telegram_username = "Taken_Name"
    user.save()
    response = register(client, telegram_username="@taken_name")
    assert "telegram_username" in response.context["form"].errors


def test_changing_the_telegram_username_unlinks_the_old_chat(client, user):
    user.telegram_username, user.telegram_chat_id = "old_name", 55
    user.save()
    client.force_login(user)
    client.post(reverse("users:profile"), {"email": "", "telegram_username": "new_name"})
    user.refresh_from_db()
    assert (user.telegram_username, user.telegram_chat_id) == ("new_name", None)
