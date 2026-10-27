import datetime

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    """Project user model.

    Defined up front so fields can be added later without a painful
    AUTH_USER_MODEL migration.
    """

    # Telegram account linked through the bot (see users/telegram.py). Password reset codes
    # are sent to this chat; the bot can only message a person who has pressed Start in it.
    telegram_chat_id = models.BigIntegerField(_("Telegram chat"), null=True, blank=True, unique=True)
    telegram_username = models.CharField(_("Telegram username"), max_length=64, blank=True, db_index=True)
    # The dashboard summary the bot sends every day (see core/daily_report.py).
    daily_report = models.BooleanField(_("Daily report in Telegram"), default=False)
    daily_report_time = models.TimeField(_("Send at"), default=datetime.time(17, 30))
    daily_report_sent_on = models.DateField(null=True, blank=True, editable=False)


class TelegramLinkToken(models.Model):
    """A one-time code the user opens in the bot (t.me/<bot>?start=<token>) to link their account."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, models.CASCADE, related_name="telegram_link_tokens")
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user} link"

    def is_valid(self) -> bool:
        return self.expires_at > timezone.now()


class PasswordResetCode(models.Model):
    """A 6-digit code sent to the user's Telegram. Stored hashed; a few wrong tries end it."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, models.CASCADE, related_name="password_reset_codes")
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} reset code"


class RoleAccess(models.Model):
    """A role's access to a section, changed by the superuser from the role's standard (see users/roles.py)."""

    role = models.CharField(_("role"), max_length=50)
    area = models.CharField(_("section"), max_length=50)
    level = models.CharField(_("access"), max_length=10, blank=True)  # "", "view" or "edit"
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("changed by")
    )
    changed_at = models.DateTimeField(_("changed"), auto_now=True)

    class Meta:
        verbose_name = _("role access")
        verbose_name_plural = _("role access")
        constraints = [models.UniqueConstraint(fields=["role", "area"], name="one_access_per_role_and_section")]

    def __str__(self):
        return f"{self.role}: {self.area} = {self.level or '—'}"


class RoleAccessChange(models.Model):
    """The history of role access changes: who gave or took what, and when."""

    role = models.CharField(_("role"), max_length=50, db_index=True)
    area = models.CharField(_("section"), max_length=50)
    old_level = models.CharField(_("was"), max_length=10, blank=True)
    new_level = models.CharField(_("now"), max_length=10, blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("changed by")
    )
    changed_at = models.DateTimeField(_("changed"), auto_now_add=True)

    class Meta:
        ordering = ["-changed_at", "-id"]
        verbose_name = _("role access change")
        verbose_name_plural = _("role access changes")

    def __str__(self):
        old, new = self.old_level or "—", self.new_level or "—"
        return f"{self.changed_at:%Y-%m-%d %H:%M} {self.role}: {self.area} {old} → {new}"
