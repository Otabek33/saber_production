from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CashbookConfig(AppConfig):
    name = "finance_saber.cashbook"
    verbose_name = _("Cash desk")
