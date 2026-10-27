from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from finance_saber.users.roles import SUPERUSER, Role, role_label, set_role

CHOICES = {
    "superuser": SUPERUSER,
    "finance": Role.FINANCE,
    "planner": Role.PLANNER,
    "technologist": Role.TECHNOLOGIST,
    "operator": Role.OPERATOR,
    "director": Role.MANAGER,
    "manager": Role.MANAGER,  # the old name of "director"
    "none": "",
}


class Command(BaseCommand):
    help = "Give a user a role: superuser, finance, planner, technologist, operator, director or none."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("role", choices=list(CHOICES))

    def handle(self, username, role, **options):
        try:
            user = get_user_model().objects.get(username=username)
        except get_user_model().DoesNotExist as error:
            raise CommandError(f"No user {username!r}.") from error
        set_role(user, CHOICES[role])
        self.stdout.write(self.style.SUCCESS(f"{username}: {role_label(CHOICES[role])}"))
