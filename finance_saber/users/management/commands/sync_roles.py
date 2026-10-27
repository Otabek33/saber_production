from django.core.management.base import BaseCommand

from finance_saber.users.roles import sync_roles


class Command(BaseCommand):
    help = "Create the role groups (see users/roles.py) and set their permissions."

    def handle(self, **options):
        missing = sync_roles()
        for name in missing:
            self.stdout.write(self.style.WARNING(f"Permission not found: {name}"))
        self.stdout.write(self.style.SUCCESS("Roles are up to date."))
