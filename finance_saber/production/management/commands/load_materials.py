import datetime

from finance_saber.production import recipe_excel

from .load_recipes import Command as LoadRecipes


class Command(LoadRecipes):
    help = (
        "Load raw materials and packaging from an .xlsx file written by export_materials or the Download "
        "Excel button (the project's file if no path is given). With --opening-date, the file's stock becomes "
        "each material's opening balance on that date, for materials with no stock movements yet. "
        "Safe to re-run: materials are matched by name."
    )
    kind = "materials"
    only = (recipe_excel.MATERIALS,)

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--opening-date",
            type=datetime.date.fromisoformat,
            help="Load the stock column as opening balances on this date (YYYY-MM-DD)",
        )

    def load(self, path, dry_run, opening_date=None, **options):
        return recipe_excel.load_recipes(path, dry_run=dry_run, only=self.only, opening_date=opening_date)
