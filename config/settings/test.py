"""Settings for the test suite (pytest)."""

from .base import *  # noqa: F403
from .base import env

SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-test-only")
ALLOWED_HOSTS = ["testserver"]

# In-memory SQLite keeps tests fast and independent of your local database.
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

# Fast (insecure) hashing: test speed matters more than strength here.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Don't require collectstatic before running tests.
WHITENOISE_AUTOREFRESH = True
