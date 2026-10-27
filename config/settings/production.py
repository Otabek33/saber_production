"""Settings for production.

Every secret and host-specific value must come from the environment; nothing
here has an insecure fallback. Run ``python manage.py check --deploy`` against
these settings before each release.
"""

from .base import *  # noqa: F403
from .base import DATABASES, STORAGES, env

# GENERAL
# ------------------------------------------------------------------------------
DEBUG = False
SECRET_KEY = env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")
# Needed when the site is served over HTTPS behind a domain, e.g. https://example.com
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

# DATABASES
# ------------------------------------------------------------------------------
# No SQLite fallback on a server: a missing DATABASE_URL must stop the app, not
# silently start an empty database next to the code.
DATABASES["default"] = {
    **env.db("DATABASE_URL"),
    "ATOMIC_REQUESTS": True,
    "CONN_MAX_AGE": env.int("CONN_MAX_AGE", default=60),
    "CONN_HEALTH_CHECKS": True,
}

# SECURITY
# ------------------------------------------------------------------------------
# Trust the X-Forwarded-Proto header set by the reverse proxy (nginx, a PaaS router, ...).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
# Let uptime monitors on the server itself call http://127.0.0.1:8000/health/ without a redirect.
SECURE_REDIRECT_EXEMPT = [r"^health/$"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# Start low and raise to 31536000 (1 year) once HTTPS is confirmed working everywhere.
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=60)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True)
SECURE_HSTS_PRELOAD = env.bool("DJANGO_SECURE_HSTS_PRELOAD", default=True)
SECURE_REFERRER_POLICY = "same-origin"

# STATIC
# ------------------------------------------------------------------------------
# Compressed, cache-busting static files served by WhiteNoise (tolerant of the
# vendored MatDash theme's references to files it doesn't ship).
STORAGES["staticfiles"] = {"BACKEND": "finance_saber.core.storage.StaticFilesStorage"}

# EMAIL
# ------------------------------------------------------------------------------
DEFAULT_FROM_EMAIL = env("DJANGO_DEFAULT_FROM_EMAIL", default="finance_saber <noreply@example.com>")
SERVER_EMAIL = env("DJANGO_SERVER_EMAIL", default=DEFAULT_FROM_EMAIL)
EMAIL_SUBJECT_PREFIX = env("DJANGO_EMAIL_SUBJECT_PREFIX", default="[finance_saber] ")
# https://docs.djangoproject.com/en/6.1/topics/email/#topic-email-configuration
MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": env("EMAIL_HOST", default="localhost"),
            "port": env.int("EMAIL_PORT", default=587),
            "username": env("EMAIL_HOST_USER", default=""),
            "password": env("EMAIL_HOST_PASSWORD", default=""),
            "use_tls": env.bool("EMAIL_USE_TLS", default=True),
            "timeout": env.int("EMAIL_TIMEOUT", default=10),
        },
    },
}

# LOGGING
# ------------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "%(levelname)s %(asctime)s %(name)s %(process)d %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", default="INFO")},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        "django.security.DisallowedHost": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}
