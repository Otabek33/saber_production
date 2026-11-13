import logging

from django.http import HttpRequest

logger = logging.getLogger(__name__)
SHOWN = 6  # notifications listed in the bell


def notifications(request: HttpRequest) -> dict:
    """``bell`` for the header: the user's latest notifications and how many are unread. Worked out
    only when a page shows the header (templates call it)."""
    if not request.user.is_authenticated:
        return {}
    return {"bell": lambda: bell(request.user)}


def bell(user) -> dict:
    from finance_saber.production import expiry
    from finance_saber.production import notifications as expiry_notifications

    try:  # today's morning notification, if the timer hasn't made it yet
        expiry_notifications.create_due(user=user)
    except Exception:  # a page must never fail because of the bell
        logger.exception("Could not make the expiry notification for %s", user)
    items = list(user.notifications.all()[:SHOWN])
    for item in items:
        if item.kind == item.Kind.EXPIRY:
            counts = item.data.get("levels", {})
            item.levels = [
                {"label": label, "tone": tone, "count": counts.get(key, 0)}
                for key, label, tone in expiry.ALERT_LEVELS
                if counts.get(key)
            ]
    return {"items": items, "unread": user.notifications.filter(read_at__isnull=True).count()}
