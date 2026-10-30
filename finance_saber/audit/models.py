"""The action log: who signed in, and who created, changed or deleted what, and when.

Events are written by audit/recorder.py and never changed afterwards: there is no
edit or delete in the app, and the admin site shows them read-only. Old events are
removed only by ``manage.py prune_audit``.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class AuditEvent(models.Model):
    class Action(models.TextChoices):
        LOGIN = "login", _("Signed in")
        LOGOUT = "logout", _("Signed out")
        LOGIN_FAILED = "login_failed", _("Failed sign-in")
        CREATE = "create", _("Created")
        UPDATE = "update", _("Changed")
        DELETE = "delete", _("Deleted")

    created_at = models.DateTimeField(_("time"), auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("user")
    )
    # Kept as typed: a failed sign-in has no user, and the name stays if the user is deleted.
    username = models.CharField(_("username"), max_length=150, blank=True)
    action = models.CharField(_("action"), max_length=12, choices=Action.choices, db_index=True)
    # What it was done to (empty for signing in and out): "production.materialreceipt", 42, "№ НК-101 …".
    model = models.CharField(_("section"), max_length=100, blank=True, db_index=True)
    object_id = models.CharField(_("record"), max_length=64, blank=True)
    object_repr = models.CharField(_("record"), max_length=255, blank=True)
    # {field name: [before, after]} as text; secrets show only that they changed.
    changes = models.JSONField(_("changes"), default=dict, blank=True)
    ip = models.GenericIPAddressField(_("IP address"), null=True, blank=True)
    user_agent = models.CharField(_("browser"), max_length=255, blank=True)
    path = models.CharField(_("page"), max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _("action log entry")
        verbose_name_plural = _("action log")
        indexes = [models.Index(fields=["model", "object_id"], name="audit_event_object")]

    def __str__(self):
        return f"{self.created_at:%d.%m.%Y %H:%M} {self.username or '—'} {self.get_action_display()} {self.object_repr}"
