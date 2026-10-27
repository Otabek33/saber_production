"""Settings shared by every environment.

Values that differ between environments, or that are secret, are read from
environment variables, or from ``.env/<settings name>.env`` in the project root
(``.env/local.env`` for local, ``.env/production.env`` for production). See the
``.env/*.env.example`` files for the full list.
"""

import os
from pathlib import Path

import environ
from django.contrib.messages import constants as messages
from django.utils.translation import gettext_lazy as _

# finance_saber/ project root (the directory that contains manage.py)
BASE_DIR = Path(__file__).resolve().parent.parent.parent
APPS_DIR = BASE_DIR / "finance_saber"

env = environ.Env()

# Load the env file matching the active settings module when present, e.g.
# config.settings.local -> .env/local.env. Real environment variables always
# take precedence.
READ_DOT_ENV_FILE = env.bool("DJANGO_READ_DOT_ENV_FILE", default=True)
SETTINGS_NAME = os.environ.get("DJANGO_SETTINGS_MODULE", "").rpartition(".")[2]
ENV_FILE = BASE_DIR / ".env" / f"{SETTINGS_NAME}.env"
if READ_DOT_ENV_FILE and ENV_FILE.is_file():
    env.read_env(ENV_FILE)

# GENERAL
# ------------------------------------------------------------------------------
DEBUG = False
# Brand shown in the header, sign-in pages, page titles and emails.
SITE_NAME = "BON NATURAL"
# SECRET_KEY and ALLOWED_HOSTS are set per environment (local / production / test).

# The company's clock: "today" on the dashboard and the daily Telegram report's 17:30.
TIME_ZONE = env("DJANGO_TIME_ZONE", default="Asia/Tashkent")
# Public address of the site (https://finance.example.uz), for links in Telegram messages. Optional.
SITE_URL = env("DJANGO_SITE_URL", default="").rstrip("/")
# Russian for everyone unless they pick another language in the header switcher
# (stored in a cookie). The browser's language is deliberately not used; see
# finance_saber.core.middleware.LocaleMiddleware.
LANGUAGE_CODE = "ru"
LANGUAGES = [
    ("ru", _("Russian")),
    ("en", _("English")),
]
USE_I18N = True
USE_TZ = True
LOCALE_PATHS = [BASE_DIR / "locale"]

# DATABASES
# ------------------------------------------------------------------------------
# A single DATABASE_URL drives the database. SQLite by default; switch to
# PostgreSQL later just by changing the URL, e.g.
#   DATABASE_URL=postgres://user:password@localhost:5432/finance_saber
DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}
DATABASES["default"]["ATOMIC_REQUESTS"] = True
# Keep database connections open between requests: opening one costs ~100 ms
# (authentication), more than most pages take to build. Health checks replace a
# connection the server dropped.
DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# URLS
# ------------------------------------------------------------------------------
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# APPS
# ------------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]
THIRD_PARTY_APPS: list[str] = []
LOCAL_APPS = [
    "finance_saber.core",
    "finance_saber.users",
    "finance_saber.cashbook",
    "finance_saber.production",
    "finance_saber.sales",
    "finance_saber.planning",
]
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# AUTHENTICATION
# ------------------------------------------------------------------------------
# Custom user model from day one: swapping it after the first migration is painful.
AUTH_USER_MODEL = "users.User"

# TELEGRAM
# ------------------------------------------------------------------------------
# A bot creates password reset codes and sends them. Create it with @BotFather, then set
# the token and the bot's username (without @). The webhook secret is any long random
# string; the webhook URL includes it, so only Telegram can call it.
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="")
TELEGRAM_BOT_USERNAME = env("TELEGRAM_BOT_USERNAME", default="")
TELEGRAM_WEBHOOK_SECRET = env("TELEGRAM_WEBHOOK_SECRET", default="")

# Every page requires sign-in (LoginRequiredMiddleware below); mark public views
# with @login_not_required.
LOGIN_URL = "users:login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "users:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# MIDDLEWARE
# ------------------------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "finance_saber.core.middleware.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# STATIC & MEDIA
# ------------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [APPS_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# TEMPLATES
# ------------------------------------------------------------------------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [APPS_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.template.context_processors.static",
                "django.template.context_processors.tz",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "finance_saber.core.context_processors.site",
                "finance_saber.core.context_processors.navigation",
            ],
        },
    },
]

# MESSAGES
# ------------------------------------------------------------------------------
# Match Bootstrap's alert classes (alert-danger, not alert-error).
MESSAGE_TAGS = {messages.ERROR: "danger"}

# SECURITY
# ------------------------------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True

# ADMIN
# ------------------------------------------------------------------------------
# Override in production to move the admin off the well-known /admin/ path.
ADMIN_URL = env("DJANGO_ADMIN_URL", default="admin/")

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
    "root": {"handlers": ["console"], "level": "INFO"},
}
