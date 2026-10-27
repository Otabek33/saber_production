"""Russian by default; English only when chosen in the switcher."""

from django.conf import settings
from django.urls import reverse


def test_russian_is_the_default(client, db):
    response = client.get(reverse("users:login"))
    assert response.headers["Content-Language"] == "ru"
    assert "Запомнить это устройство" in response.content.decode()


def test_browser_language_is_ignored(client, db):
    response = client.get(reverse("users:login"), headers={"accept-language": "en-US,en;q=0.9"})
    assert response.headers["Content-Language"] == "ru"


def test_switcher_changes_language(client, db):
    response = client.post(reverse("set_language"), {"language": "en", "next": reverse("users:login")})
    assert response.status_code == 302
    assert response.cookies[settings.LANGUAGE_COOKIE_NAME].value == "en"

    response = client.get(reverse("users:login"))
    assert response.headers["Content-Language"] == "en"
    assert "Remember this device" in response.content.decode()


def test_unknown_language_cookie_falls_back_to_russian(client, db):
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "xx"
    response = client.get(reverse("users:login"))
    assert response.headers["Content-Language"] == "ru"


def test_menu_is_translated(client, user):
    client.force_login(user)
    content = client.get(reverse("core:home")).content.decode()
    assert "Продажи" in content
    assert "Сменить пароль" in content
