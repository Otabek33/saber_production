"""ASGI entry point, used by servers such as uvicorn: ``uvicorn config.asgi:application``."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

application = get_asgi_application()
