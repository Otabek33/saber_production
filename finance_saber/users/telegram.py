"""The Telegram bot: sending messages and reading the updates Telegram posts to us.

Telegram only lets a bot message a person who has started a chat with it, so accounts
are linked first, when the person presses Start in the bot:

* by Telegram username: the account that registered with that username is linked (the
  username comes from Telegram itself, so it can't be faked);
* or by a one-time link from the profile page (t.me/<bot>?start=<token>), for people
  without a Telegram username.

The chat id is stored on the user; reset codes are sent there.
"""

import hashlib
import json
import logging
import secrets
import urllib.error
import urllib.request

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import TelegramLinkToken, User

logger = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"
LINK_TTL = timezone.timedelta(minutes=15)


def configured() -> bool:
    return bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_BOT_USERNAME)


def request(method: str, payload: dict, timeout: int = 10):
    """Call a Bot API method and return its ``result``, or None (and log) if it fails or isn't configured."""
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.warning("Telegram is not configured (TELEGRAM_BOT_TOKEN is empty); %s not sent.", method)
        return None
    http_request = urllib.request.Request(
        API.format(token=settings.TELEGRAM_BOT_TOKEN, method=method),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(http_request, timeout=timeout) as response:  # noqa: S310 (fixed https URL)
            body = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError):
        logger.exception("Telegram %s failed", method)
        return None
    return body.get("result", True) if body.get("ok") else None


def call(method: str, payload: dict) -> bool:
    return request(method, payload) is not None


def get_updates(offset: int, timeout: int = 25) -> list:
    """Messages sent to the bot since ``offset``; waits up to ``timeout`` seconds for new ones."""
    return request("getUpdates", {"offset": offset, "timeout": timeout}, timeout=timeout + 10) or []


def send_message(chat_id: int, text: str, parse_mode: str = "") -> bool:
    payload = {"chat_id": chat_id, "text": text, "link_preview_options": {"is_disabled": True}}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return call("sendMessage", payload)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_link(user: User) -> str:
    """A fresh link for ``user``; earlier unused links stop working. Returns the t.me URL."""
    TelegramLinkToken.objects.filter(user=user).delete()
    token = secrets.token_urlsafe(24)
    TelegramLinkToken.objects.create(user=user, token_hash=hash_token(token), expires_at=timezone.now() + LINK_TTL)
    return f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={token}"


def bot_url() -> str:
    return f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}" if settings.TELEGRAM_BOT_USERNAME else ""


def attach(user: User, chat_id: int, username: str) -> User:
    """Store the chat on ``user``; a chat belongs to one account only."""
    User.objects.filter(telegram_chat_id=chat_id).exclude(pk=user.pk).update(telegram_chat_id=None)
    user.telegram_chat_id = chat_id
    if username:
        user.telegram_username = username
    user.save(update_fields=["telegram_chat_id", "telegram_username"])
    return user


def link_by_username(chat_id: int, username: str) -> User | None:
    """Link the one account registered with this Telegram username, if there is exactly one."""
    if not username:
        return None
    matches = list(User.objects.filter(telegram_username__iexact=username.lstrip("@"))[:2])
    return attach(matches[0], chat_id, username) if len(matches) == 1 else None


def link_chat(token: str, chat_id: int, username: str) -> User | None:
    """Attach the chat to the user the token was made for. Returns that user, or None if the token is bad."""
    link = TelegramLinkToken.objects.select_related("user").filter(token_hash=hash_token(token)).first()
    if link is None or not link.is_valid():
        return None
    user = attach(link.user, chat_id, username)
    link.delete()
    return user


def send_report(chat_id: int) -> None:
    """/report: today's dashboard summary for the account linked to this chat."""
    from finance_saber.core import daily_report  # late: core's report imports this module

    user = User.objects.filter(telegram_chat_id=chat_id, is_active=True).first()
    if user is None:
        send_message(chat_id, _("Press /start to link your account to this bot."))
    elif not daily_report.send(user):
        send_message(chat_id, _("The report could not be built. Please try again later."))


def handle_update(update: dict) -> None:
    """Answer the bot's messages: Start links the account (by Telegram username or a profile link).

    /report sends today's dashboard summary to a linked account.
    """
    message = update.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    if not chat_id:
        return
    text = (message.get("text") or "").strip()
    username = (message.get("from") or {}).get("username", "")
    if text.split("@")[0] == "/report":
        send_report(chat_id)
        return
    if not text.startswith("/start"):
        if User.objects.filter(telegram_chat_id=chat_id).exists():
            send_message(chat_id, _("Send /report for today's dashboard summary."))
        else:
            send_message(chat_id, _("Press /start to link your account to this bot."))
        return
    parts = text.split(maxsplit=1)
    user = link_chat(parts[1], chat_id, username) if len(parts) == 2 else None
    user = user or link_by_username(chat_id, username)
    if user:
        reply = _("Your account %(user)s is linked. Password reset codes will come here.") % {"user": user.username}
        reply += "\n" + _("Send /report for today's dashboard summary.")
    elif username:
        reply = _(
            "No account uses the Telegram username @%(username)s. Enter it on the site (sign-up or Profile), "
            "then press /start here again."
        ) % {"username": username}
    else:
        reply = _("Your Telegram has no username. Open the link from the site's Profile page and press Start.")
    send_message(chat_id, reply)
