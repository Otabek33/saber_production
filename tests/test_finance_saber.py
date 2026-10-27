"""Smoke tests for the Django project."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse


def test_custom_user_model_is_active():
    assert settings.AUTH_USER_MODEL == "users.User"
    assert get_user_model()._meta.label == "users.User"


def test_home_requires_login(client):
    response = client.get(reverse("core:home"))
    assert response.status_code == 302
    assert response.url == f"{reverse('users:login')}?next=/"


def test_home(client, user):
    client.force_login(user)
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    assert user.username in response.content.decode()


def test_health(client, db):
    response = client.get(reverse("core:health"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_admin_requires_login(client):
    response = client.get(reverse("admin:index"))
    assert response.status_code == 302


def test_user_fixture(user):
    assert user.check_password("s3cret-pass")
