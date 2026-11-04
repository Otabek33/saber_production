"""Slow down password guessing: sign-in and reset-code limits.

Failed sign-ins are already in the action log (audit, with the username and IP), so the limits
are counted from there: nothing extra to store, and they hold across server processes.

- One username: after ``USER_FAILURES`` failed sign-ins in ``WINDOW`` (since its last successful
  one), it can't sign in until the window passes. The right password doesn't help meanwhile.
- One IP address: after ``IP_FAILURES`` failures in ``WINDOW``, whatever the usernames tried.
- Reset codes: at most ``CODES_PER_HOUR`` sent to one account an hour (each code allows 5 tries).
"""

from django.utils import timezone

from finance_saber.audit.context import client_ip

WINDOW = timezone.timedelta(minutes=15)
USER_FAILURES = 5
IP_FAILURES = 20
CODES_PER_HOUR = 3


def login_blocked(username: str, request) -> bool:
    """True while ``username`` or the request's IP has failed to sign in too often lately."""
    from finance_saber.audit.models import AuditEvent

    since = timezone.now() - WINDOW
    events = AuditEvent.objects.filter(created_at__gte=since)
    if username:
        last_login = (
            events.filter(action=AuditEvent.Action.LOGIN, username__iexact=username)
            .order_by("-created_at")
            .values_list("created_at", flat=True)
            .first()
        )
        failed = events.filter(action=AuditEvent.Action.LOGIN_FAILED, username__iexact=username)
        if last_login:
            failed = failed.filter(created_at__gt=last_login)
        if failed.count() >= USER_FAILURES:
            return True
    ip = client_ip(request) if request is not None else None
    if ip:
        return events.filter(action=AuditEvent.Action.LOGIN_FAILED, ip=ip).count() >= IP_FAILURES
    return False


def codes_exhausted(user) -> bool:
    """True when ``user`` has had as many reset codes as allowed this hour."""
    from .models import PasswordResetCode

    since = timezone.now() - timezone.timedelta(hours=1)
    return PasswordResetCode.objects.filter(user=user, created_at__gte=since).count() >= CODES_PER_HOUR
