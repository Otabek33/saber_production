from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import translation

from finance_saber.cashbook import exports


class Command(BaseCommand):
    help = (
        "Write the handbooks (articles, categories, regions, counterparties) to an .xlsx file, to fill an "
        "empty database on a new server with load_handbooks or the Handbook page's upload. With --seed, "
        "write the project's handbook files (cashbook/seed/) that are deployed with the code."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path, nargs="?", help="Where to write the file, e.g. handbooks.xlsx")
        parser.add_argument(
            "--seed",
            action="store_true",
            help="Write the project's files: cashbook/seed/handbook.xlsx and counterparties.xlsx",
        )

    def handle(self, path, seed, **options):
        if seed:
            for written in exports.write_seed_files():
                self.stdout.write(self.style.SUCCESS(f"Written {written}"))
            self.stdout.write(
                "Commit them with the code; they hold real counterparties, so keep the repository private."
            )
            return
        if path is None:
            raise CommandError("Give a path, or --seed for the project's files.")
        with translation.override("ru"):
            response = exports.xlsx_response(path.name, exports.seed_sheets())
        path.write_bytes(response.content)
        self.stdout.write(self.style.SUCCESS(f"Handbooks written to {path}"))
