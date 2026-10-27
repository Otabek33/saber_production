"""Permission checks for views: reading a page and changing data can need different permissions.

Roles (finance_saber.users.roles) grant model permissions such as
``cashbook.view_operation``; views ask for them here. A user without the
permission gets a 403 page rather than a sign-in redirect, as they are signed in.
"""

from functools import wraps

from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def _as_tuple(perms) -> tuple[str, ...]:
    return (perms,) if isinstance(perms, str) else tuple(perms)


class PermissionMixin(PermissionRequiredMixin):
    """``view_permission`` to open the page; ``permission_required`` to submit it.

    Without ``view_permission``, ``permission_required`` is needed for both (edit pages).
    """

    raise_exception = True
    view_permission: str | tuple[str, ...] = ()

    def get_permission_required(self):
        if self.request.method in SAFE_METHODS and self.view_permission:
            return _as_tuple(self.view_permission)
        return super().get_permission_required()


def permission(view=(), change=()):
    """Decorator for function views: ``view`` perms for GET, ``change`` perms (plus ``view``) for POST."""

    def decorator(func):
        @wraps(func)
        def wrapped(request, *args, **kwargs):
            needed = _as_tuple(view)
            if request.method not in SAFE_METHODS:
                needed += _as_tuple(change)
            if not request.user.has_perms(needed):
                raise PermissionDenied
            return func(request, *args, **kwargs)

        return wrapped

    return decorator
