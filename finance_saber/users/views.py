import hmac
import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.contrib.messages.views import SuccessMessageMixin
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, FormView, ListView, UpdateView

from finance_saber.core.permissions import PermissionMixin, permission

from . import reset, telegram
from .forms import (
    DailyReportForm,
    LoginForm,
    ProfileForm,
    ResetCodeForm,
    ResetRequestForm,
    RoleAccessForm,
    SignUpForm,
    StyledPasswordChangeForm,
    StyledSetPasswordForm,
    UserAccessForm,
    UserCreateForm,
    UserEditForm,
)
from .models import RoleAccessChange, User
from .roles import (
    AREAS,
    AREAS_BY_KEY,
    DESCRIPTIONS,
    LEVEL_LABELS,
    SUPERUSER,
    Role,
    access_matrix,
    change_access,
    changed_levels,
    reset_access,
    role_choices,
    role_label,
    role_levels,
    set_role,
    standard_level,
    user_role,
)


class LoginView(auth_views.LoginView):
    template_name = "users/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        if not form.cleaned_data["remember_me"]:
            # End the session when the browser closes instead of after SESSION_COOKIE_AGE.
            self.request.session.set_expiry(0)
        return response


@method_decorator(login_not_required, name="dispatch")
class SignUpView(CreateView):
    template_name = "users/register.html"
    form_class = SignUpForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(settings.LOGIN_REDIRECT_URL)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()
        login(self.request, user)
        messages.success(
            self.request,
            _("Welcome! Your account has been created. Open the Telegram bot and press Start to receive reset codes."),
        )
        return redirect(settings.LOGIN_REDIRECT_URL)


class ProfileView(SuccessMessageMixin, UpdateView):
    template_name = "users/profile.html"
    form_class = ProfileForm
    success_url = reverse_lazy("users:profile")
    success_message = _("Your profile has been updated.")

    def get_object(self, queryset=None):
        return self.request.user

    def get_context_data(self, **kwargs):
        # The link is shown once, right after it was made (it is not kept in the page).
        return super().get_context_data(
            telegram_link=self.request.session.pop("telegram_link", None),
            report_form=DailyReportForm(instance=User.objects.get(pk=self.request.user.pk)),
            **kwargs,
        )


class PasswordChangeView(SuccessMessageMixin, auth_views.PasswordChangeView):
    template_name = "users/password_change.html"
    form_class = StyledPasswordChangeForm
    success_url = reverse_lazy("users:profile")
    success_message = _("Your password has been changed.")


@method_decorator(login_not_required, name="dispatch")
class ResetRequestView(FormView):
    """Step 1 of a password reset: a site username or a linked Telegram username. Same answer either way."""

    template_name = "users/password_reset.html"
    form_class = ResetRequestForm
    success_url = reverse_lazy("users:password_reset_code")

    def form_valid(self, form):
        user = reset.find_account(form.cleaned_data["identifier"])
        # The same steps and the same message whether or not the account exists or is linked.
        if user is not None and reset.send_code(user):
            self.request.session["reset_pk"] = user.pk
        else:
            self.request.session.pop("reset_pk", None)
        self.request.session["reset_requested"] = True
        self.request.session["reset_verified"] = False
        messages.info(self.request, _("If the account is linked to Telegram, a code has been sent there."))
        return super().form_valid(form)


@method_decorator(login_not_required, name="dispatch")
class ResetCodeView(FormView):
    """Step 2: the 6-digit code from Telegram. Right code marks the session as verified for step 3."""

    template_name = "users/password_reset_code.html"
    form_class = ResetCodeForm
    success_url = reverse_lazy("users:password_reset_new")

    def get(self, request, *args, **kwargs):
        if not request.session.get("reset_requested"):
            messages.error(request, _("Start again: enter your username or Telegram username."))
            return redirect("users:password_reset")
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        user = User.objects.filter(pk=self.request.session.get("reset_pk")).first()
        if user is None or not reset.check_code(user, form.cleaned_data["code"]):
            form.add_error("code", _("The code is wrong or has expired. Check Telegram, or start again."))
            return self.form_invalid(form)
        self.request.session["reset_verified"] = True
        return super().form_valid(form)


@method_decorator(login_not_required, name="dispatch")
class ResetPasswordView(FormView):
    """Step 3: a new password, only after a verified code. Signs nobody in; they sign in afterwards."""

    template_name = "users/password_reset_new.html"
    form_class = StyledSetPasswordForm
    success_url = reverse_lazy("users:login")

    def dispatch(self, request, *args, **kwargs):
        user_pk = request.session.get("reset_pk")
        if not (user_pk and request.session.get("reset_verified")):
            messages.error(request, _("Enter the code from Telegram first."))
            return redirect("users:password_reset")
        self.reset_user = User.objects.filter(pk=user_pk).first()
        if self.reset_user is None:
            return redirect("users:password_reset")
        return super().dispatch(request, *args, **kwargs)

    def get_form(self, form_class=None):
        return StyledSetPasswordForm(self.reset_user, **self.get_form_kwargs())

    def form_valid(self, form):
        form.save()
        for key in ("reset_pk", "reset_verified"):
            self.request.session.pop(key, None)
        messages.success(self.request, _("Your password has been changed. Sign in with the new password."))
        return super().form_valid(form)


@login_not_required
@csrf_exempt
@require_POST
def telegram_webhook(request, secret):
    """Telegram posts the bot's messages here; the secret in the URL keeps strangers out."""
    if not settings.TELEGRAM_WEBHOOK_SECRET or not hmac.compare_digest(secret, settings.TELEGRAM_WEBHOOK_SECRET):
        raise Http404
    try:
        update = json.loads(request.body or b"{}")
    except ValueError:
        return HttpResponseBadRequest()
    telegram.handle_update(update)
    return JsonResponse({"ok": True})


@require_POST
def telegram_link(request):
    """Give the signed-in user a link to open in the bot."""
    if not telegram.configured():
        messages.error(request, _("Telegram is not set up yet. Ask an administrator."))
        return redirect("users:profile")
    request.session["telegram_link"] = telegram.new_link(request.user)
    return redirect("users:profile")


@require_POST
def telegram_unlink(request):
    request.user.telegram_chat_id = None
    request.user.telegram_username = ""
    request.user.save(update_fields=["telegram_chat_id", "telegram_username"])
    messages.success(request, _("Telegram is unlinked. Password reset will be unavailable until you link it again."))
    return redirect("users:profile")


@require_POST
def telegram_report(request):
    """Turn the daily Telegram report on or off and set its time; "send now" sends today's at once."""
    from finance_saber.core import daily_report  # late: core's report loads the dashboard and other apps

    form = DailyReportForm(request.POST, instance=request.user)
    if not form.is_valid():
        messages.error(request, _("Enter the time as HH:MM, e.g. 17:30."))
        return redirect("users:profile")
    form.save()
    if "send_now" in request.POST:
        if daily_report.send(request.user):
            messages.success(request, _("Today's report has been sent to your Telegram."))
        else:
            messages.error(request, _("The report could not be sent. Check that Telegram is linked."))
    elif request.user.daily_report:
        messages.success(
            request,
            _("The daily report will come to Telegram at %(time)s.")
            % {"time": request.user.daily_report_time.strftime("%H:%M")},
        )
    else:
        messages.success(request, _("The daily report is turned off."))
    return redirect("users:profile")


class UserListView(PermissionMixin, ListView):
    """Everyone's role and sign-in access, for the superuser; users waiting for a role first."""

    permission_required = "users.change_user"
    template_name = "users/user_list.html"
    context_object_name = "users"
    paginate_by = 20

    def get_queryset(self):
        users = User.objects.prefetch_related("groups").order_by("-is_active", "username")
        role = self.request.GET.get("role", "")
        if role == SUPERUSER:
            users = users.filter(is_superuser=True)
        elif role in Role.values:
            users = users.filter(groups__name=role, is_superuser=False)
        elif role == "none":
            users = users.filter(is_superuser=False).exclude(groups__name__in=Role.values)
        query = self.request.GET.get("q", "").strip()
        if query:
            users = users.filter(
                Q(username__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(email__icontains=query)
            )
        return users

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["rows"] = [
            {
                "user": user,
                "role": role_label(user_role(user)),
                "form": UserAccessForm(initial={"role": user_role(user), "is_active": user.is_active}, prefix=user.pk),
            }
            for user in context["users"]
        ]
        context["role_filter"] = [("none" if value == "" else value, label) for value, label in role_choices()]
        context["descriptions"] = [(label, DESCRIPTIONS[value]) for value, label in role_choices()]
        return context


@permission(view=("users.add_user", "users.change_user"))
def user_create(request):
    """A new user with a role and a first password, created by the superuser."""
    form = UserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save()
            set_role(user, form.cleaned_data["role"])
        messages.success(
            request,
            _("User %(user)s created: %(role)s. Give them the password; they can change it in their profile.")
            % {"user": user.get_username(), "role": role_label(form.cleaned_data["role"])},
        )
        return redirect("users:list")
    return render(request, "users/user_form.html", {"form": form, "title": _("New user")})


@permission(view="users.change_user")
def user_edit(request, pk):
    """A user's details, role and access; and a new password for someone who lost theirs."""
    person = get_object_or_404(User, pk=pk)
    editing_self = person == request.user
    form = UserEditForm(instance=person, editing_self=editing_self)
    password_form = StyledSetPasswordForm(person)
    if request.method == "POST" and "set_password" in request.POST:
        password_form = StyledSetPasswordForm(person, request.POST)
        if password_form.is_valid():
            password_form.save()
            messages.success(request, _("New password set for %(user)s.") % {"user": person.get_username()})
            return redirect("users:edit", pk=person.pk)
    elif request.method == "POST":
        form = UserEditForm(request.POST, instance=person, editing_self=editing_self)
        if form.is_valid():
            with transaction.atomic():
                form.save()
                if not editing_self:
                    set_role(person, form.cleaned_data["role"])
            messages.success(request, _("%(user)s saved.") % {"user": person.get_username()})
            return redirect("users:list")
    return render(
        request,
        "users/user_form.html",
        {
            "form": form,
            "password_form": password_form,
            "person": person,
            "title": person.get_full_name() or person.get_username(),
        },
    )


@permission(view="users.change_user")
def roles(request):
    """What each role may see and change, section by section, as defined in users/roles.py."""
    columns = [
        {
            "value": role.value,
            "label": role.label,
            "description": DESCRIPTIONS[role],
            "count": User.objects.filter(groups__name=role.value, is_superuser=False).count(),
            "changed": len(changed_levels(role.value)),
        }
        for role in Role
    ]
    columns.append(
        {
            "value": SUPERUSER,
            "label": _("Superuser"),
            "description": DESCRIPTIONS[SUPERUSER],
            "count": User.objects.filter(is_superuser=True).count(),
            "changed": 0,
        }
    )
    return render(request, "users/roles.html", {"columns": columns, "rows": access_matrix()})


@permission(view="users.change_user")
def role_edit(request, role):
    """Change what a role may do, section by section; "Reset" returns it to its standard access.

    Changes apply to the role's users at once, are kept by later updates and are
    logged (see users/roles.py).
    """
    if role not in Role.values:
        raise Http404
    label = Role(role).label
    current = role_levels(role)
    form = RoleAccessForm(request.POST or None, initial=current)
    if request.method == "POST" and ("reset" in request.POST or form.is_valid()):
        if "reset" in request.POST:
            changes = reset_access(role, request.user)
        else:
            changes = change_access(role, form.cleaned_data, request.user)
        if changes:
            messages.success(
                request,
                _("%(role)s: access changed in %(count)s sections. Users with this role have it right away.")
                % {"role": label, "count": len(changes)},
            )
        else:
            messages.info(request, _("Nothing changed."))
        return redirect("users:role_edit", role=role)

    rows = []
    for area in AREAS:
        standard = standard_level(role, area)
        rows.append(
            {
                "area": area,
                "field": form[area.key] if area.editable else None,
                "level": current[area.key],
                "standard": LEVEL_LABELS[standard],
                "changed": current[area.key] != standard,
            }
        )
    history = [
        {
            "change": change,
            "area": AREAS_BY_KEY[change.area].label if change.area in AREAS_BY_KEY else change.area,
            "old": LEVEL_LABELS.get(change.old_level, change.old_level),
            "new": LEVEL_LABELS.get(change.new_level, change.new_level),
        }
        for change in RoleAccessChange.objects.filter(role=role).select_related("changed_by")[:30]
    ]
    return render(
        request,
        "users/role_edit.html",
        {
            "role": role,
            "label": label,
            "description": DESCRIPTIONS[role],
            "rows": rows,
            "changed": sum(row["changed"] for row in rows),
            "history": history,
            "users": User.objects.filter(groups__name=role, is_superuser=False).order_by("username"),
            "level_labels": LEVEL_LABELS,
        },
    )


@require_POST
@permission(change="users.change_user")
def user_access(request, pk):
    """Save one user's role and sign-in access. Your own can't be changed here, so you can't lock yourself out."""
    user = get_object_or_404(User, pk=pk)
    back = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        back = reverse("users:list")
    if user == request.user:
        messages.error(request, _("You can't change your own role or access."))
        return redirect(back)
    form = UserAccessForm(request.POST, prefix=user.pk)
    if form.is_valid():
        set_role(user, form.cleaned_data["role"])
        user.is_active = form.cleaned_data["is_active"]
        user.save(update_fields=["is_active"])
        messages.success(
            request,
            _("%(user)s: %(role)s.") % {"user": user.get_username(), "role": role_label(form.cleaned_data["role"])},
        )
    return redirect(back)
