from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class PlanningConfig(AppConfig):
    name = "finance_saber.planning"
    verbose_name = _("Planning")
