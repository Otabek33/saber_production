import re

from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    SetPasswordForm,
    UserCreationForm,
)
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from finance_saber.core.forms import BootstrapFormMixin

from .models import User


class EmailUniqueMixin:
    """Reject an email address another user already has (case-insensitively)."""

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data["email"])
        if not email:
            return email
        others = User.objects.filter(email__iexact=email)
        if self.instance.pk:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise ValidationError(_("A user with that email address already exists."))
        return email


TELEGRAM_USERNAME = re.compile(r"^[A-Za-z0-9_]{5,32}$")


def normalize_telegram_username(value: str) -> str:
    """ "@Otabek33 " -> "Otabek33": stored without the @."""
    return (value or "").strip().lstrip("@")


class TelegramUsernameMixin:
    """A Telegram username: 5–32 letters, digits or _, typed with or without @, one account per username.

    The bot links a chat to the account whose username matches whoever presses Start, so
    two accounts can't claim the same Telegram username.
    """

    def clean_telegram_username(self):
        value = normalize_telegram_username(self.cleaned_data.get("telegram_username"))
        if not value:
            return value
        if not TELEGRAM_USERNAME.match(value):
            raise ValidationError(_("A Telegram username has 5–32 letters, digits or underscores, e.g. @Otabek33."))
        others = User.objects.filter(telegram_username__iexact=value)
        if self.instance.pk:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise ValidationError(_("This Telegram username is already used by another account."))
        return value


class LoginForm(BootstrapFormMixin, AuthenticationForm):
    remember_me = forms.BooleanField(label=_("Remember this device"), required=False, initial=True)


TELEGRAM_HELP = _("Password reset codes come to this Telegram account, e.g. @Otabek33.")


class SignUpForm(BootstrapFormMixin, EmailUniqueMixin, TelegramUsernameMixin, UserCreationForm):
    email = forms.EmailField(label=_("Email address"))
    telegram_username = forms.CharField(label=_("Telegram username"), max_length=33, help_text=TELEGRAM_HELP)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "telegram_username")


class ProfileForm(BootstrapFormMixin, EmailUniqueMixin, TelegramUsernameMixin, forms.ModelForm):
    telegram_username = forms.CharField(label=_("Telegram username"), max_length=33, help_text=TELEGRAM_HELP)

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "telegram_username")

    def save(self, commit=True):
        # A different Telegram account: the old chat no longer belongs to this user.
        if "telegram_username" in self.changed_data:
            self.instance.telegram_chat_id = None
        return super().save(commit=commit)


class DailyReportForm(BootstrapFormMixin, forms.ModelForm):
    """The daily Telegram report: on or off, and when it comes (the company's time zone)."""

    class Meta:
        model = User
        fields = ("daily_report", "daily_report_time")
        widgets = {"daily_report_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M")}


class StyledPasswordChangeForm(BootstrapFormMixin, PasswordChangeForm):
    pass


class ResetRequestForm(BootstrapFormMixin, forms.Form):
    identifier = forms.CharField(
        label=_("Username or Telegram username"),
        max_length=150,
        help_text=_("Your site username, or the Telegram username you linked (with or without @)."),
    )


class ResetCodeForm(BootstrapFormMixin, forms.Form):
    code = forms.CharField(
        label=_("Code from Telegram"),
        min_length=6,
        max_length=6,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "autofocus": True}),
    )


class StyledSetPasswordForm(BootstrapFormMixin, SetPasswordForm):
    pass


class UserAccessForm(BootstrapFormMixin, forms.Form):
    """A user's role and whether they may sign in; set on the Users page."""

    role = forms.ChoiceField(label=_("Role"), required=False)
    is_active = forms.BooleanField(label=_("Can sign in"), required=False)

    def __init__(self, *args, **kwargs):
        from .roles import role_choices

        super().__init__(*args, **kwargs)
        self.fields["role"].choices = role_choices()


class RoleFieldMixin:
    """A role picker on a user form; the role is saved after the user (see roles.set_role)."""

    def add_role_field(self, initial=""):
        from .roles import role_choices

        self.fields["role"] = forms.ChoiceField(
            label=_("Role"), choices=role_choices(), required=False, initial=initial,
            help_text=_("What the user may see and do: see Administration › Roles and permissions."),
        )  # fmt: skip


class UserCreateForm(BootstrapFormMixin, EmailUniqueMixin, TelegramUsernameMixin, RoleFieldMixin, UserCreationForm):
    """A new user, created by the superuser with a role and a first password."""

    email = forms.EmailField(label=_("Email address"), required=False)
    telegram_username = forms.CharField(
        label=_("Telegram username"), max_length=33, required=False, help_text=TELEGRAM_HELP
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email", "telegram_username")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_role_field()
        self.fields["password1"].help_text = _(
            "Give it to the user; they can change it under Profile › Change password."
        )


class UserEditForm(BootstrapFormMixin, EmailUniqueMixin, TelegramUsernameMixin, RoleFieldMixin, forms.ModelForm):
    """A user's details, role and sign-in access, edited by the superuser."""

    telegram_username = forms.CharField(
        label=_("Telegram username"), max_length=33, required=False, help_text=TELEGRAM_HELP
    )

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "telegram_username", "is_active")
        labels = {"is_active": _("Can sign in")}
        help_texts = {"is_active": _("Untick instead of deleting: the user's records keep their author.")}

    def __init__(self, *args, editing_self=False, **kwargs):
        from .roles import user_role

        super().__init__(*args, **kwargs)
        self.add_role_field(initial=user_role(self.instance))
        if editing_self:  # so nobody locks themselves out
            self.fields["role"].disabled = True
            self.fields["is_active"].disabled = True
            self.fields["role"].help_text = _("You can't change your own role or access.")

    def save(self, commit=True):
        # A different Telegram account: the old chat no longer belongs to this user.
        if "telegram_username" in self.changed_data:
            self.instance.telegram_chat_id = None
        return super().save(commit=commit)


class RoleAccessForm(forms.Form):
    """A role's access, section by section: no access, view, or work (see users/roles.py)."""

    def __init__(self, *args, **kwargs):
        from .roles import AREAS, LEVEL_LABELS

        super().__init__(*args, **kwargs)
        for area in AREAS:
            if area.editable:
                self.fields[area.key] = forms.ChoiceField(
                    label=area.label,
                    choices=[(level, LEVEL_LABELS[level]) for level in area.levels],
                    widget=forms.RadioSelect,
                    required=False,
                )
