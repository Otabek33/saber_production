import time

from django.core.management.base import BaseCommand, CommandError

from finance_saber.core import daily_report
from finance_saber.users import telegram

REPORT_CHECK_SECONDS = 60


class Command(BaseCommand):
    help = (
        "Receive the bot's messages on this computer, without a public address, and send the daily reports "
        "when their time comes: keep this running in a terminal. Press Ctrl+C to stop. On a server, use "
        "set_telegram_webhook and run send_daily_reports from cron instead."
    )

    def handle(self, **options):
        if not telegram.configured():
            raise CommandError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_BOT_USERNAME in .env/local.env first.")
        # Telegram doesn't deliver to getUpdates while a webhook is set; remove it.
        telegram.call("deleteWebhook", {})
        self.stdout.write(self.style.SUCCESS("Listening for messages to the bot. Press Ctrl+C to stop."))
        offset = 0
        last_check = 0.0
        try:
            while True:
                if time.monotonic() - last_check >= REPORT_CHECK_SECONDS:
                    last_check = time.monotonic()
                    try:
                        if sent := daily_report.send_due():
                            self.stdout.write(f"  sent {sent} daily report(s)")
                    except Exception as error:  # keep listening; the next check tries again
                        self.stderr.write(f"  daily reports failed: {error}")
                updates = telegram.get_updates(offset)
                for update in updates:
                    offset = update["update_id"] + 1
                    telegram.handle_update(update)
                    self.stdout.write(f"  handled message {update['update_id']}")
                if not updates:
                    time.sleep(1)
        except KeyboardInterrupt:
            self.stdout.write("Stopped.")
