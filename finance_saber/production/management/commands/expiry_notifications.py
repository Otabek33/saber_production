import datetime

from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse
from django.utils import timezone

from finance_saber.production import notifications
from finance_saber.users.models import Notification, User

# A made-up morning for --sample: one batch at each alert step, the most urgent first.
SAMPLE = (
    ("Green Tea мёд 0,450 л", -3, 300, "expired"),
    ("7 Energy Drink 0,450 л", 9, 1200, "weeks_2"),
    ("B Fresh анор 0,450 л", 25, 4000, "month_1"),
    ("Blak Tea лимон-мёд-имбир 0,450 л", 50, 2200, "months_2"),
    ("7 Energy Drink mango 0,450 л", 80, 900, "months_3"),
)


def sample_rows(today: datetime.date) -> list[dict]:
    return [
        {
            "product": product,
            "code": f"{today:%y%m%d}-{number:03d}",
            "run_pk": None,
            "best_before": (today + datetime.timedelta(days=days_left)).isoformat(),
            "days_left": days_left,
            "remaining": str(units),
            "level": level,
        }
        for number, (product, days_left, units, level) in enumerate(SAMPLE, start=1)
    ]


class Command(BaseCommand):
    help = (
        "Make the morning expiry notifications (the header's bell) now. Without options it does what the "
        "06:00 job does: only from 06:00, only if batches need checking, once a day each. --now: don't wait "
        "for 06:00. --sample --user NAME: put a made-up notification (one batch at each step) in NAME's bell, "
        "to see how it looks; it replaces NAME's notification for today. Only the site's bell: nothing is sent out."
    )

    def add_arguments(self, parser):
        parser.add_argument("--now", action="store_true", help="Make them now, whatever the time.")
        parser.add_argument("--sample", action="store_true", help="A made-up notification, for testing (--user).")
        parser.add_argument("--user", help="Site username: only this user.")

    def handle(self, now=False, sample=False, user=None, **options):
        person = None
        if user:
            person = User.objects.filter(username=user, is_active=True).first()
            if person is None:
                raise CommandError(f"No active user {user!r}.")
        if sample:
            if person is None:
                raise CommandError("--sample needs --user NAME.")
            self.make_sample(person)
            return
        moment = timezone.localtime()
        if now:  # as if it were 06:00 today
            moment = moment.replace(hour=notifications.NOTIFY_AT.hour, minute=notifications.NOTIFY_AT.minute)
        made = notifications.create_due(moment, user=person)
        if made:
            self.stdout.write(self.style.SUCCESS(f"Expiry notifications made: {made}"))
        else:
            self.stdout.write(
                "No notification made: before 06:00 (use --now), nothing needs checking, "
                "or everyone already has today's."
            )

    def make_sample(self, person):
        today = timezone.localdate()
        Notification.objects.filter(user=person, kind=Notification.Kind.EXPIRY, day=today).delete()
        data = notifications.snapshot(sample_rows(today))
        data["sample"] = True
        Notification.objects.create(
            user=person,
            kind=Notification.Kind.EXPIRY,
            day=today,
            data=data,
            url=f"{reverse('production:expiry')}?show=alerts",
        )
        self.stdout.write(self.style.SUCCESS(f"Sample notification put in {person}'s bell. Reload any page."))
