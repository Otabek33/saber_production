from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ProductionConfig(AppConfig):
    name = "finance_saber.production"
    verbose_name = _("Production")

    def ready(self):
        # A closed period's records can't be saved or deleted (periods.py).
        from . import periods

        periods.connect()
