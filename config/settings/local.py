"""Settings for local development."""

from .base import *  # noqa: F403
from .base import INSTALLED_APPS, MIDDLEWARE, env

# On a Windows development machine behind a corporate proxy, Python doesn't trust the proxy's
# certificate, so HTTPS calls (e.g. to Telegram) fail. truststore makes Python use the same
# certificates as the browser. Only the local settings do this.
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:  # not installed: normal certificate checks apply
    pass

# GENERAL
# ------------------------------------------------------------------------------
DEBUG = env.bool("DJANGO_DEBUG", default=True)
SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-local-only-change-me")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "[::1]"])

# EMAIL
# ------------------------------------------------------------------------------
# Emails are printed to the runserver console instead of being sent.
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}}

# CACHES
# ------------------------------------------------------------------------------
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
}

# WHITENOISE
# ------------------------------------------------------------------------------
# Let runserver serve static files through WhiteNoise too, so local matches production.
INSTALLED_APPS = ["whitenoise.runserver_nostatic", *INSTALLED_APPS]

# In development static files aren't hashed, so WhiteNoise tells browsers to re-check
# every file on every page (max-age=0). The theme and vendored libraries never change
# while you work, so let the browser keep those for a day; the app's own CSS/JS stay
# uncached, so edits show on the next reload.
LONG_CACHED_STATIC = ("/static/matdash/", "/static/vendor/")


def _cache_vendor_static(headers, path, url):
    if url.startswith(LONG_CACHED_STATIC):
        headers["Cache-Control"] = "public, max-age=86400"


WHITENOISE_ADD_HEADERS_FUNCTION = _cache_vendor_static

# DJANGO DEBUG TOOLBAR
# ------------------------------------------------------------------------------
# https://django-debug-toolbar.readthedocs.io/
# Off by default: it instruments every request and makes pages several times slower.
# Turn it on for a profiling session with DJANGO_DEBUG_TOOLBAR=True in .env/local.env.
USE_DEBUG_TOOLBAR = DEBUG and env.bool("DJANGO_DEBUG_TOOLBAR", default=False)
if USE_DEBUG_TOOLBAR:
    INSTALLED_APPS += ["debug_toolbar"]
    MIDDLEWARE = ["debug_toolbar.middleware.DebugToolbarMiddleware", *MIDDLEWARE]
    INTERNAL_IPS = ["127.0.0.1", "::1"]

    def _show_toolbar(request):
        # Pages only: by default the toolbar also wraps every static file request (~130 ms each).
        return request.META.get("REMOTE_ADDR") in INTERNAL_IPS and not request.path.startswith("/static/")

    DEBUG_TOOLBAR_CONFIG = {"SHOW_TOOLBAR_CALLBACK": _show_toolbar}
