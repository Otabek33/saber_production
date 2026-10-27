from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from finance_saber.users import telegram


class Command(BaseCommand):
    help = (
        "Tell Telegram where the bot's messages go. Give the site's public https address, e.g. "
        "https://example.com. Run it once after deploying, and again if the secret or the address changes."
    )

    def add_arguments(self, parser):
        parser.add_argument("base_url", help="The site's public address, e.g. https://example.com")

    def handle(self, base_url, **options):
        if not telegram.configured() or not settings.TELEGRAM_WEBHOOK_SECRET:
            raise CommandError("Set TELEGRAM_BOT_TOKEN, TELEGRAM_BOT_USERNAME and TELEGRAM_WEBHOOK_SECRET first.")
        if not base_url.startswith("https://"):
            raise CommandError("Telegram needs an https address.")
        path = reverse("users:telegram_webhook", args=[settings.TELEGRAM_WEBHOOK_SECRET])
        if telegram.call("setWebhook", {"url": base_url.rstrip("/") + path}):
            self.stdout.write(self.style.SUCCESS("Webhook set."))
        else:
            raise CommandError("Telegram did not accept the webhook. Check the token and the address.")
