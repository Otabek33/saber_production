from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from finance_saber.core import daily_report
from finance_saber.users.models import User


class Command(BaseCommand):
    help = (
        "Send the daily Telegram report to everyone whose time (Profile → Telegram) has come and who hasn't "
        "had it today. Run it every 5 minutes from cron or a systemd timer, e.g. "
        "'*/5 * * * * cd /srv/finance_saber && .venv/bin/python manage.py send_daily_reports'. "
        "Running it more often never sends twice. Locally, telegram_poll does this every minute."
    )

    def add_arguments(self, parser):
        parser.add_argument("--user", help="Send now to this user (site username), whatever the time; for testing.")
        parser.add_argument("--date", type=parse_date, help="With --user: the report for this day (YYYY-MM-DD).")

    def handle(self, user=None, date=None, **options):
        if user:
            account = User.objects.filter(username=user).first()
            if account is None:
                raise CommandError(f"No user {user!r}.")
            if not account.telegram_chat_id:
                raise CommandError(f"{user} has no Telegram linked: they must press Start in the bot first.")
            if not daily_report.send(account, date):
                raise CommandError("Telegram did not accept the message; see the log.")
            self.stdout.write(self.style.SUCCESS(f"Report sent to {user}."))
            return
        sent = daily_report.send_due()
        self.stdout.write(f"Daily reports sent: {sent}")
