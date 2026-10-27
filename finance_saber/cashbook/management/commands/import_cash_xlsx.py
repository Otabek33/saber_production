from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from finance_saber.cashbook.importer import (
    BALANCE_TOLERANCE,
    LAYOUTS,
    CashSheetImporter,
    SheetError,
    read_sheet,
    source_prefix,
)
from finance_saber.cashbook.ledger import balances
from finance_saber.cashbook.models import CashDesk


class DryRun(Exception):
    """Raised to roll back the transaction after a --dry-run."""


class Command(BaseCommand):
    help = (
        "Import a cash desk sheet of the finance workbook into a cash desk, then check the "
        "resulting balances against the sheet's Остаток columns. Safe to re-run: rows "
        "imported before are skipped."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path, help="The .xlsx workbook, e.g. Situation.xlsx")
        parser.add_argument(
            "--sheet", default="Касса Бон", choices=list(LAYOUTS), help='Sheet name (default: "Касса Бон")'
        )
        parser.add_argument("--desk", help="Cash desk slug (default: the sheet's usual desk, e.g. bon, inkassa)")
        parser.add_argument("--user", help="Username to record as the author of the imported operations")
        parser.add_argument("--dry-run", action="store_true", help="Show what would be imported, save nothing")

    def handle(self, path, sheet, desk, user, dry_run, **options):
        if not path.is_file():
            raise CommandError(f"File not found: {path}")
        desk = desk or LAYOUTS[sheet].desk
        try:
            desk_obj = CashDesk.objects.get(slug=desk)
        except CashDesk.DoesNotExist as error:
            raise CommandError(f"No cash desk with slug {desk!r}.") from error
        user_obj = None
        if user:
            try:
                user_obj = get_user_model().objects.get(username=user)
            except get_user_model().DoesNotExist as error:
                raise CommandError(f"No user {user!r}.") from error

        try:
            parsed = read_sheet(path, sheet)
        except SheetError as error:
            raise CommandError(str(error)) from error
        prefix = source_prefix(sheet)

        try:
            with transaction.atomic():
                importer = CashSheetImporter(desk_obj, prefix, user=user_obj)
                report = importer.import_rows(parsed.rows, opening=parsed.opening)
                imported = balances(desk_obj.operations.filter(source__startswith=f"{prefix}/"))
                self.print_report(report, imported, parsed.closing, dry_run)
                if report.conflicts:
                    raise CommandError(
                        "Rows imported before changed in Excel (listed above); nothing was saved. Make the sheet "
                        "and the app agree, then run the import again."
                    )
                if dry_run:
                    raise DryRun
        except DryRun:
            self.stdout.write(self.style.WARNING("Dry run: nothing was saved."))

    def print_report(self, report, imported, sheet_closing, dry_run):
        write = self.stdout.write
        write(f"Rows with amounts: {report.rows}")
        for account, amount in report.opening.items():
            write(f"Opening balance {account}: {amount:,.2f} (added as 'Начальный остаток')")
        created = ", ".join(f"{kind} {count}" for kind, count in sorted(report.created.items())) or "none"
        write(f"Operations {'to create' if dry_run else 'created'}: {created}")
        if report.skipped_existing:
            write(f"Already imported, skipped: {report.skipped_existing}")
        if report.new_categories:
            write(
                f"Handbook categories added ({len(report.new_categories)}): {', '.join(sorted(report.new_categories))}"
            )
        if report.unsorted_rows:
            write(f"Rows without a usable category, filed under 'Требует проверки': {len(report.unsorted_rows)}")
        if report.reclassified_transfers:
            rows = ", ".join(map(str, sorted(set(report.reclassified_transfers))))
            write(f"Rows recognised as transfers/exchanges outside a transfer article: {rows}")
        for row, account, difference in report.adjustments:
            write(
                self.style.WARNING(
                    f"Row {row}: the sheet's {account} balance is typed in, {difference:+,.2f} from the sum of the "
                    f"rows above; added a 'Корректировка остатка' operation."
                )
            )
        if report.filled_dates:
            write(f"Rows without a date (previous row's date used): {', '.join(map(str, report.filled_dates))}")
        for conflict in report.conflicts:
            was, now = conflict.operation, conflict.sheet
            write(
                self.style.ERROR(
                    f"Row {conflict.row} changed since it was imported: operation #{was.pk} is {was.date} {was.kind} "
                    f"{was.amount:,.2f} {was.account}, the sheet now has {now.date} {now.kind} {now.amount:,.2f} "
                    f"{now.account}."
                )
            )

        write("Balance check against the sheet's Остаток (imported operations only):")
        ok = True
        for account, expected in sheet_closing.items():
            actual = imported[account]
            match = abs(actual - expected) <= BALANCE_TOLERANCE
            ok &= match
            style = self.style.SUCCESS if match else self.style.ERROR
            write(
                style(
                    f"  {account:<9} sheet {expected:>20,.2f}   imported {actual:>20,.2f}   "
                    f"{'OK' if match else 'MISMATCH'}"
                )
            )
        if not ok:
            write(self.style.ERROR("Balances differ from the sheet; check the rows above before relying on them."))
