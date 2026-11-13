from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ProductionConfig(AppConfig):
    name = "finance_saber.production"
    verbose_name = _("Production")

    def ready(self):
        # A closed period's records can't be saved or deleted (periods.py).
        from . import notifications, periods

        periods.connect()
        # Expiry alerts in the header are worked out again when product stock moves.
        notifications.connect()
