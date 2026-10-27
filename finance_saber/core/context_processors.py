from django.conf import settings
from django.http import HttpRequest

from .navigation import build_menu


def site(request: HttpRequest) -> dict:
    """Expose the brand (settings.SITE_NAME) as ``company_name``.

    Not ``site_name``: Django's auth views put their own ``site_name`` (the host name) in
    the context, which would override it on the sign-in and password reset pages.
    """
    bot = settings.TELEGRAM_BOT_USERNAME
    return {
        "company_name": settings.SITE_NAME,
        # The password-reset bot: users press Start in it once so it can send them codes.
        "telegram_bot": bot,
        "telegram_bot_url": f"https://t.me/{bot}" if bot else "",
    }


def navigation(request: HttpRequest) -> dict:
    """Expose the main menu, resolved for the current page, as ``nav_menu``, and the user's role."""
    from finance_saber.users.roles import role_label, user_role  # late: core shouldn't load other apps early

    if not request.user.is_authenticated:
        return {}
    role = user_role(request.user)
    return {"nav_menu": build_menu(request), "user_role": role, "user_role_label": role_label(role)}
