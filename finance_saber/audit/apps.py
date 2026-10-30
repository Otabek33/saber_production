from django.apps import AppConfig


class AuditConfig(AppConfig):
    name = "finance_saber.audit"
    verbose_name = "Audit log"

    def ready(self):
        from . import recorder

        recorder.connect()
