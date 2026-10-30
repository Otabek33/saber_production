"""Who is acting right now: the current request, for the recorder's signal handlers.

A context variable, not a thread-local: each request (thread or async task) sees
only its own. Outside a request (manage.py commands, the Telegram bot's jobs)
there is none, and events are recorded without a user.
"""

from contextvars import ContextVar
from dataclasses import dataclass, field

_current: ContextVar["Actor | None"] = ContextVar("audit_actor", default=None)


@dataclass
class Actor:
    request: object
    ip: str | None = None
    user_agent: str = ""
    path: str = ""
    # Within one request, one user's role change is one event (groups: removed, then added).
    group_events: dict = field(default_factory=dict)

    @property
    def user(self):
        user = getattr(self.request, "user", None)
        return user if user is not None and user.is_authenticated else None


def current() -> Actor | None:
    return _current.get()


def client_ip(request) -> str | None:
    """The client's address. Behind a proxy, set AUDIT_TRUSTED_PROXY_HEADER to the header it sets."""
    from django.conf import settings

    header = getattr(settings, "AUDIT_TRUSTED_PROXY_HEADER", "")
    if header and (forwarded := request.META.get(header)):
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


class AuditMiddleware:
    """Makes the request known to the recorder for as long as it is being handled."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        actor = Actor(
            request=request,
            ip=client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
            path=request.get_full_path()[:255],
        )
        token = _current.set(actor)
        try:
            return self.get_response(request)
        finally:
            _current.reset(token)
