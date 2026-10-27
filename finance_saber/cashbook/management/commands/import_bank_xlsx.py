import datetime
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from finance_saber.cashbook import bank
from finance_saber.cashbook.ledger import balances
from finance_saber.cashbook.models import Account, Article, CashDesk, Category, Counterparty, Kind, Operation, Region

OPENING_SOURCE = "bank:opening"


class DryRun(Exception):
    """Raised to roll back the transaction after a --dry-run."""


class Command(BaseCommand):
    help = (
        "Import the bank history of the finance workbook: counterparties with their article "
        "and region (Handbook columns H:I and Y:AA), the statement lines of the 'Банк' sheet, "
        "and the opening balance from 'Bank saldo'. Safe to re-run: lines recorded before are skipped."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path, help="The .xlsx workbook, e.g. Situation.xlsx")
        parser.add_argument("--desk", default="bank", help="Bank desk slug (default: bank)")
        parser.add_argument("--user", help="Username to record as the author of the imported lines")
        parser.add_argument("--dry-run", action="store_true", help="Show what would be imported, save nothing")

    def handle(self, path, desk, user, dry_run, **options):
        if not path.is_file():
            raise CommandError(f"File not found: {path}")
        try:
            desk = CashDesk.objects.get(slug=desk, kind="bank")
        except CashDesk.DoesNotExist as error:
            raise CommandError(f"No bank desk with slug {desk!r}.") from error
        user = get_user_model().objects.filter(username=user).first() if user else None

        handbook, saldo = self.read_handbook_and_saldo(path)
        try:
            lines = bank.read_statement(path, sheet_name="Банк")
        except bank.StatementError as error:
            raise CommandError(str(error)) from error

        try:
            with transaction.atomic():
                classified = self.import_counterparties(handbook)
                if saldo:
                    self.import_opening(desk, saldo[0], user)
                report = bank.record_lines(desk, lines, user=user)
                self.print_report(desk, lines, classified, report, saldo, dry_run)
                if dry_run:
                    raise DryRun
        except DryRun:
            self.stdout.write(self.style.WARNING("Dry run: nothing was saved."))

    def read_handbook_and_saldo(self, path):
        """Handbook {reference: (article, region)} and Bank saldo [(date, opening balance)]."""
        import openpyxl

        workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
        try:
            handbook = {}
            for row in workbook["Handbook"].iter_rows(min_row=2, values_only=True):
                row = list(row) + [None] * 27
                # H:I reference -> article; Y:AA reference -> (article, region)
                for reference, article, region in ((row[7], row[8], None), (row[24], row[25], row[26])):
                    reference = bank._text(reference)
                    if reference:
                        old_article, old_region = handbook.get(reference, (None, None))
                        handbook[reference] = (bank._text(article) or old_article, bank._text(region) or old_region)
            saldo = []
            if "Bank saldo" in workbook.sheetnames:
                for date, opening, *_ in workbook["Bank saldo"].iter_rows(min_row=2, max_col=3, values_only=True):
                    if isinstance(date, datetime.datetime) and isinstance(opening, int | float):
                        saldo.append((date.date(), Decimal(str(opening)).quantize(Decimal("0.01"))))
        finally:
            workbook.close()
        return handbook, saldo

    def import_counterparties(self, handbook) -> int:
        """Create or classify counterparties from the Handbook. Keeps classifications made in the app."""
        articles = {article.name: article for article in Article.objects.all()}
        regions = {region.name: region for region in Region.objects.all()}
        classified = 0
        for reference, (article_name, region_name) in handbook.items():
            counterparty, _ = Counterparty.objects.get_or_create(
                reference=reference, defaults=Counterparty.parse_reference(reference)
            )
            changed = []
            if counterparty.article is None and article_name in articles:
                counterparty.article = articles[article_name]
                changed.append("article")
            if counterparty.region is None and region_name:
                if region_name not in regions:
                    regions[region_name] = Region.objects.create(name=region_name)
                counterparty.region = regions[region_name]
                changed.append("region")
            if changed:
                counterparty.save(update_fields=changed)
                classified += 1
        return classified

    def import_opening(self, desk, first_saldo, user):
        date, amount = first_saldo
        if amount <= 0 or desk.operations.filter(source=OPENING_SOURCE).exists():
            return
        article, _ = Article.objects.get_or_create(
            name="Начальный остаток", defaults={"kind": Kind.INCOME, "in_cash_flow": False}
        )
        category, _ = Category.objects.get_or_create(article=article, name="Начальный остаток")
        operation = Operation(
            desk=desk,
            kind=Kind.INCOME,
            date=date,
            category=category,
            account=Account.BANK,
            amount=amount,
            note="Начальный остаток из Bank saldo",
            source=OPENING_SOURCE,
            created_by=user,
        )
        operation.full_clean(validate_unique=False)
        operation.save()

    def print_report(self, desk, lines, classified, report, saldo, dry_run):
        write = self.stdout.write
        verb = "to create" if dry_run else "created"
        write(f"Statement lines read: {len(lines)}")
        write(f"Counterparties classified from the Handbook: {classified}")
        write(f"Lines {verb}: {report.created}; already recorded: {report.duplicates}")
        if report.new_counterparties:
            names = ", ".join(c.name for c in report.new_counterparties[:5])
            write(
                self.style.WARNING(f"New counterparties without an article: {len(report.new_counterparties)} ({names})")
            )
        if report.unclassified:
            write(self.style.WARNING(f"Lines filed under 'Не распределено': {report.unclassified}"))

        expected = sum((line.credit or 0) - (line.debit or 0) for line in lines)
        imported = desk.operations.filter(source__startswith="bank:").exclude(source=OPENING_SOURCE)
        net = balances(imported)[Account.BANK]
        style = self.style.SUCCESS if net == expected else self.style.ERROR
        write(style(f"Net of the statement {expected:,.2f}; net imported {net:,.2f}"))
        if saldo:
            closing = balances(desk.operations.all())[Account.BANK]
            write(f"Balance after import: {closing:,.2f} (opening {saldo[0][1]:,.2f} on {saldo[0][0]})")
            write(
                self.style.WARNING(
                    f"'Bank saldo' says {saldo[-1][1]:,.2f} at the start of {saldo[-1][0]}: the sheet's saldo "
                    "and its statement lines don't agree, so no corrections were added. Check against the bank."
                )
            )
