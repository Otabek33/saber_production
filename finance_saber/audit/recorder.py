"""Records the action log (AuditEvent) from Django's signals.

* Signing in, signing out and failed sign-ins (django.contrib.auth signals).
* Records created, changed and deleted in the business apps (post_save / post_delete):
  for a change, only the fields that changed, before and after, as readable text.
* A user's role: its group, as one event per request (Planner → Technologist).

Not recorded: secrets (reset codes, Telegram link tokens), the role-access tables
(they keep their own change log, users.RoleAccessChange), and stock movements a
document or a production day makes (the document or day is recorded instead).
Queryset .update() and bulk_create() send no signals, so they aren't recorded;
the app uses them only for such derived rows.
"""

import datetime
from decimal import Decimal

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
from django.utils import timezone
from django.utils.translation import gettext as _

from .context import client_ip, current

TRACKED_APPS = {"cashbook", "production", "sales", "planning", "users"}
SKIPPED_MODELS = {"users.telegramlinktoken", "users.passwordresetcode", "users.roleaccess", "users.roleaccesschange"}
DERIVED_MOVES = {"production.materialmove", "production.productmove"}
IGNORED_FIELDS = {"id", "last_login", "created_at", "updated_at"}
SECRET_WORDS = ("password", "hash", "token", "secret")
HIDDEN = "••••••"


# --- What is recorded --------------------------------------------------------------------


def tracked(sender, instance) -> bool:
    meta = sender._meta
    if meta.app_label not in TRACKED_APPS or meta.label_lower in SKIPPED_MODELS:
        return False
    try:
        if apps.get_model(meta.label_lower) is not sender:  # a migration's historical model
            return False
    except LookupError:
        return False
    if meta.label_lower in DERIVED_MOVES and (instance.run_id or instance.source):
        return False
    return True


def is_secret(field) -> bool:
    return any(word in field.name for word in SECRET_WORDS)


def text(instance, field) -> str:
    """A field's value as people read it: a related record's name, a choice's label, 1250.5, 22.10.2026."""
    value = getattr(instance, field.attname)
    if value is None or value == "":
        return ""
    if field.is_relation:
        try:
            return str(getattr(instance, field.name))[:200]
        except Exception:  # the related record is gone
            return str(value)
    if field.choices:
        return str(dict(field.flatchoices).get(value, value))
    if isinstance(value, bool):
        return _("yes") if value else _("no")
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")  # 1250.5000 -> "1250.5", 1000.0000 -> "1000"
    if isinstance(value, datetime.datetime):
        return timezone.localtime(value).strftime("%d.%m.%Y %H:%M") if timezone.is_aware(value) else str(value)
    if isinstance(value, datetime.date):
        return value.strftime("%d.%m.%Y")
    return str(value)[:500]


def snapshot(instance) -> dict:
    """{field: text} of a record; a secret field holds a fingerprint, so a change is seen but never its value."""
    values = {}
    for field in instance._meta.concrete_fields:
        if field.name in IGNORED_FIELDS:
            continue
        if is_secret(field):
            raw = getattr(instance, field.attname)
            values[field.name] = f"{HIDDEN}{hash(raw)}" if raw else ""
        else:
            values[field.name] = text(instance, field)
    return values


def shown(value: str) -> str:
    return HIDDEN if value.startswith(HIDDEN) else value


# --- Writing events ------------------------------------------------------------------------


def record(action, instance=None, changes=None, user=None, username="", request=None):
    from .models import AuditEvent

    actor = current()
    if user is None and actor is not None:
        user = actor.user
    if request is not None:
        ip, agent, path = client_ip(request), request.META.get("HTTP_USER_AGENT", "")[:255], request.get_full_path()
    elif actor is not None:
        ip, agent, path = actor.ip, actor.user_agent, actor.path
    else:
        ip, agent, path = None, "", ""
    return AuditEvent.objects.create(
        user=user,
        username=(username or (user.get_username() if user else ""))[:150],
        action=action,
        model=instance._meta.label_lower if instance is not None else "",
        object_id=str(instance.pk) if instance is not None else "",
        object_repr=str(instance)[:255] if instance is not None else "",
        changes=changes or {},
        ip=ip,
        user_agent=agent,
        path=path[:255],
    )


def before_save(sender, instance, raw=False, **kwargs):
    if raw or instance._state.adding or instance.pk is None or not tracked(sender, instance):
        return
    old = sender._base_manager.filter(pk=instance.pk).first()
    instance._audit_before = snapshot(old) if old is not None else None


def after_save(sender, instance, created, raw=False, **kwargs):
    from .models import AuditEvent

    if raw or not tracked(sender, instance):
        return
    after = snapshot(instance)
    before = instance.__dict__.pop("_audit_before", None)
    if created or before is None:
        changes = {name: ["", shown(value)] for name, value in after.items() if value}
        record(AuditEvent.Action.CREATE, instance, changes)
        return
    changes = {
        name: [shown(before.get(name, "")), shown(value)]
        for name, value in after.items()
        if before.get(name, "") != value
    }
    if changes:
        record(AuditEvent.Action.UPDATE, instance, changes)


def after_delete(sender, instance, **kwargs):
    from .models import AuditEvent

    if tracked(sender, instance):
        changes = {name: [shown(value), ""] for name, value in snapshot(instance).items() if value}
        record(AuditEvent.Action.DELETE, instance, changes)


# --- A user's role (groups) ------------------------------------------------------------------


def role_names(user) -> list[str]:
    from finance_saber.users.roles import Role

    labels = dict(Role.choices)
    return sorted(str(labels.get(name, name)) for name in user.groups.values_list("name", flat=True))


def groups_changed(sender, instance, action, reverse, **kwargs):
    from .models import AuditEvent

    if reverse or not isinstance(instance, get_user_model()):
        return
    actor = current()
    # In a request, the first change keeps the "before"; outside one, each change stands alone.
    pending = actor.group_events if actor is not None else {}
    if action.startswith("pre_"):
        pending.setdefault(instance.pk, {"before": role_names(instance), "event": None})
        instance._audit_groups = pending[instance.pk]
        return
    state = pending.get(instance.pk) or getattr(instance, "_audit_groups", None)
    if not state:
        return
    before, after = ", ".join(state["before"]), ", ".join(role_names(instance))
    if state["event"] is not None:  # the same request already recorded this user's role change
        state["event"].changes = {"groups": [before, after]}
        if before == after:
            state["event"].delete()
            state["event"] = None
        else:
            state["event"].save(update_fields=["changes"])
    elif before != after:
        state["event"] = record(AuditEvent.Action.UPDATE, instance, {"groups": [before, after]})


# --- Signing in and out ----------------------------------------------------------------------


def logged_in(sender, request, user, **kwargs):
    from .models import AuditEvent

    record(AuditEvent.Action.LOGIN, user=user, request=request)


def logged_out(sender, request, user, **kwargs):
    from .models import AuditEvent

    if user is not None:
        record(AuditEvent.Action.LOGOUT, user=user, request=request)


def login_failed(sender, credentials, request=None, **kwargs):
    from .models import AuditEvent

    username = str(credentials.get("username", ""))[:150]
    known = get_user_model()._default_manager.filter(username=username).first() if username else None
    record(AuditEvent.Action.LOGIN_FAILED, user=known, username=username, request=request)


def connect():
    pre_save.connect(before_save, dispatch_uid="audit.before_save")
    post_save.connect(after_save, dispatch_uid="audit.after_save")
    post_delete.connect(after_delete, dispatch_uid="audit.after_delete")
    m2m_changed.connect(groups_changed, sender=get_user_model().groups.through, dispatch_uid="audit.groups_changed")
    user_logged_in.connect(logged_in, dispatch_uid="audit.logged_in")
    user_logged_out.connect(logged_out, dispatch_uid="audit.logged_out")
    user_login_failed.connect(login_failed, dispatch_uid="audit.login_failed")
