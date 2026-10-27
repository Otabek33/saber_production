from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import translation

from finance_saber.cashbook.exports import xlsx_bytes
from finance_saber.production import recipe_excel


class Command(BaseCommand):
    help = (
        "Write products, materials, recipes and other product names to an .xlsx file, to fill an empty "
        "database on a new server with load_recipes or the Recipes page's upload. With --seed, write the "
        "project's recipes file (production/seed/recipes.xlsx) that is deployed with the code."
    )
    kind = "recipes"  # see recipe_excel.SEED_FILES

    def add_arguments(self, parser):
        name = recipe_excel.SEED_FILES[self.kind][0]
        parser.add_argument("path", type=Path, nargs="?", help=f"Where to write the file, e.g. {name}")
        parser.add_argument("--seed", action="store_true", help=f"Write the project's file: production/seed/{name}")

    def handle(self, path, seed, **options):
        if seed:
            written = recipe_excel.write_seed_file(self.kind)
            self.stdout.write(self.style.SUCCESS(f"Written {written}"))
            self.stdout.write("Commit it with the code.")
            return
        if path is None:
            raise CommandError("Give a path, or --seed for the project's file.")
        with translation.override("ru"):
            path.write_bytes(xlsx_bytes(recipe_excel.SEED_FILES[self.kind][2]()))
        self.stdout.write(self.style.SUCCESS(f"Written {path}"))
