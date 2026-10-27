"""Password reset by Telegram code: create a code, check a code."""

import secrets

from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone
from django.utils.translation import gettext as _

from . import telegram
from .models import PasswordResetCode, User

CODE_TTL = timezone.timedelta(minutes=10)
MAX_ATTEMPTS = 5


def find_account(identifier: str) -> User | None:
    """The account for a site username or a linked Telegram username (with or without @)."""
    identifier = identifier.strip()
    if not identifier:
        return None
    user = User.objects.filter(username__iexact=identifier).first()
    if user is None:
        handle = identifier.lstrip("@")
        user = User.objects.filter(telegram_username__iexact=handle).exclude(telegram_username="").first()
    return user


def send_code(user: User) -> bool:
    """Create a code for ``user`` (older codes stop working) and send it to their Telegram chat."""
    if not user.telegram_chat_id:
        return False
    code = f"{secrets.randbelow(10**6):06d}"
    PasswordResetCode.objects.filter(user=user, used=False).update(used=True)
    PasswordResetCode.objects.create(user=user, code_hash=make_password(code), expires_at=timezone.now() + CODE_TTL)
    text = _(
        "Your password reset code: %(code)s\nIt is valid for 10 minutes. If this was not you, ignore this message."
    )
    return telegram.send_message(user.telegram_chat_id, text % {"code": code})


def check_code(user: User, code: str) -> bool:
    """True once for the right code; a wrong one uses up a try, and too many tries end the code."""
    record = (
        PasswordResetCode.objects.filter(user=user, used=False, expires_at__gt=timezone.now())
        .order_by("-created_at")
        .first()
    )
    if record is None or record.attempts >= MAX_ATTEMPTS:
        return False
    if check_password(code.strip(), record.code_hash):
        record.used = True
        record.save(update_fields=["used"])
        return True
    record.attempts += 1
    record.save(update_fields=["attempts"])
    return False
