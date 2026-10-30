"""Cash desk ledger: the "Касса" sheets of the finance workbook as data.

A CashDesk (e.g. "Касса БОН") holds three accounts: UZS cash, USD cash and a
UZS card. Every movement of money is one Operation:

* income   — money comes into an account   (Приход)
* expense  — money leaves an account       (Расход)
* transfer — money moves between accounts  (Перевод), including currency
             exchange, where the amount received differs from the amount sent.

Operations are classified by the handbook: a Category ("ЗП") belongs to a
cash-flow Article ("Взаиморасчеты с персоналом"), and the article fixes which
kind of operation may use it. Operations are never deleted; cancelling one keeps
it for the audit trail and removes it from the balances.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

MONEY = {"max_digits": 18, "decimal_places": 2}
POSITIVE = [MinValueValidator(Decimal("0.01"))]


class Kind(models.TextChoices):
    # Contexts keep these apart from the same words elsewhere (e.g. "Расходы" on the dashboard).
    INCOME = "income", pgettext_lazy("operation type", "Income")
    EXPENSE = "expense", pgettext_lazy("operation type", "Expense")
    TRANSFER = "transfer", pgettext_lazy("operation type", "Transfer")


class ArticleKind(models.TextChoices):
    """An article's direction: like Kind, plus articles used both ways (e.g. sales and their refunds)."""

    INCOME = Kind.INCOME.value, Kind.INCOME.label
    EXPENSE = Kind.EXPENSE.value, Kind.EXPENSE.label
    TRANSFER = Kind.TRANSFER.value, Kind.TRANSFER.label
    BOTH = "both", _("Income or expense")


class Account(models.TextChoices):
    CASH_UZS = "cash_uzs", _("Cash UZS")
    CASH_USD = "cash_usd", _("Cash USD")
    CARD = "card", _("Card")
    BANK = "bank", _("Bank account")


ACCOUNT_CURRENCY = {Account.CASH_UZS: "UZS", Account.CASH_USD: "USD", Account.CARD: "UZS", Account.BANK: "UZS"}
# How each account appears on the cash desk page: column header, balance icon and colour.
ACCOUNT_DISPLAY = {
    Account.CASH_UZS: {"short": "UZS", "icon": "solar:wallet-money-line-duotone", "color": "primary"},
    Account.CASH_USD: {"short": "USD", "icon": "solar:dollar-minimalistic-line-duotone", "color": "success"},
    Account.CARD: {"short": _("Card"), "icon": "solar:card-line-duotone", "color": "warning"},
    Account.BANK: {"short": "UZS", "icon": "solar:buildings-2-line-duotone", "color": "info"},
}


class DeskKind(models.TextChoices):
    CASH = "cash", _("Cash")
    BANK = "bank", _("Bank")


def cash_accounts():
    return [Account.CASH_UZS, Account.CASH_USD, Account.CARD]


all_accounts = cash_accounts  # the old name, still referenced by migration 0004


class CashDesk(models.Model):
    name = models.CharField(_("name"), max_length=100, unique=True)
    slug = models.SlugField(_("slug"), max_length=50, unique=True)
    kind = models.CharField(
        _("type"),
        max_length=10,
        choices=DeskKind.choices,
        default=DeskKind.CASH,
        help_text=_("A bank desk records statement lines with counterparties."),
    )
    accounts = models.JSONField(
        _("accounts"),
        default=cash_accounts,
        help_text=_("Which accounts this desk keeps, e.g. only UZS cash."),
    )
    position = models.PositiveSmallIntegerField(_("position"), default=0, help_text=_("Order in lists and reports."))
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["position", "name"]
        verbose_name = _("cash desk")
        verbose_name_plural = _("cash desks")

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("cashbook:operations", kwargs={"desk": self.slug})

    def clean(self):
        if not self.accounts or set(self.accounts) - set(Account.values):
            message = _("Choose one or more of: %(accounts)s.") % {"accounts": ", ".join(Account.values)}
            raise ValidationError({"accounts": message})

    @property
    def account_choices(self) -> list[tuple[str, str]]:
        """This desk's accounts as (value, label), in the standard order."""
        return [(value, label) for value, label in Account.choices if value in self.accounts]

    @property
    def allows_transfers(self) -> bool:
        return len(self.accounts) > 1

    @property
    def is_bank(self) -> bool:
        return self.kind == DeskKind.BANK


class ArticleQuerySet(models.QuerySet):
    def for_desk(self, desk):
        """Articles usable on ``desk``: those limited to it, and those not limited to any desk."""
        return self.filter(models.Q(desks=desk) | models.Q(desks__isnull=True)).distinct()


# Bookkeeping articles: they move balances but are not money coming in or going out.
BOOKKEEPING_ARTICLES = ("Начальный остаток", "Корректировка остатка (+)", "Корректировка остатка (−)")


class Article(models.Model):
    """Cash-flow article (статья ДДС), e.g. "Взаиморасчеты с персоналом"."""

    name = models.CharField(_("name"), max_length=100, unique=True)
    kind = models.CharField(_("type"), max_length=10, choices=ArticleKind.choices)
    desks = models.ManyToManyField(
        CashDesk,
        blank=True,
        related_name="articles",
        verbose_name=_("cash desks"),
        help_text=_("Leave empty to use the article on every cash desk."),
    )
    position = models.PositiveSmallIntegerField(_("position"), default=0, help_text=_("Order in lists and reports."))
    in_cash_flow = models.BooleanField(
        _("counts in cash flow"),
        default=True,
        help_text=_("Untick for bookkeeping entries such as opening balances and balance corrections."),
    )
    is_active = models.BooleanField(_("active"), default=True)

    objects = ArticleQuerySet.as_manager()

    class Meta:
        ordering = ["kind", "position", "name"]
        verbose_name = _("article")
        verbose_name_plural = _("articles")

    def __str__(self):
        return self.name

    def is_available_on(self, desk) -> bool:
        desk_ids = {d.pk for d in self.desks.all()}
        return not desk_ids or desk.pk in desk_ids

    def accepts(self, kind: str) -> bool:
        """Whether an operation of ``kind`` (income / expense / transfer) can use this article."""
        if self.kind == ArticleKind.BOTH:
            return kind in (Kind.INCOME, Kind.EXPENSE)
        return self.kind == kind


class CategoryQuerySet(models.QuerySet):
    def for_desk(self, desk):
        """Categories whose article is usable on ``desk``."""
        return self.filter(article__in=Article.objects.for_desk(desk))


class Category(models.Model):
    """Category within an article, e.g. "ЗП" under "Взаиморасчеты с персоналом"."""

    article = models.ForeignKey(Article, models.PROTECT, related_name="categories", verbose_name=_("article"))
    name = models.CharField(_("name"), max_length=100)
    is_active = models.BooleanField(_("active"), default=True)

    objects = CategoryQuerySet.as_manager()

    class Meta:
        ordering = ["article__kind", "article__position", "article__name", "name"]
        verbose_name = _("category")
        verbose_name_plural = _("categories")
        constraints = [
            models.UniqueConstraint(fields=["article", "name"], name="unique_category_per_article"),
        ]

    def __str__(self):
        return f"{self.article.name} › {self.name}"


class Region(models.Model):
    """Sales region of a counterparty, e.g. "Tash reg", "KEY ACCOUNT", "Samarqand"."""

    name = models.CharField(_("name"), max_length=100, unique=True)
    manager = models.CharField(_("sales manager"), max_length=100, blank=True, help_text=_("e.g. TDM2"))
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _("region")
        verbose_name_plural = _("regions")

    def __str__(self):
        return self.name


class Company(models.Model):
    """A company by its INN, ours or a counterparty; its bank accounts are connected to it (Counterparty.company)."""

    name = models.CharField(_("name"), max_length=255)
    inn = models.CharField(_("INN"), max_length=20, unique=True)
    is_own = models.BooleanField(_("our company"), default=False, help_text=_("One of our own companies."))
    region = models.ForeignKey(
        Region,
        models.PROTECT,
        null=True,
        blank=True,
        related_name="companies",
        verbose_name=_("region"),
        help_text=_("Not required, but please choose one: reports by region count the company there."),
    )
    created_at = models.DateTimeField(_("created"), auto_now_add=True)

    class Meta:
        ordering = ["-is_own", "name"]  # ours first
        verbose_name = _("company")
        verbose_name_plural = _("companies")

    def __str__(self):
        return self.name


class Counterparty(models.Model):
    """A bank counterparty as the statement names it: "account/INN/name".

    Its article and region classify every bank line with this counterparty, so
    a counterparty is classified once and later statements follow automatically.
    """

    reference = models.CharField(
        _("statement reference"),
        max_length=300,
        unique=True,
        help_text=_('As in the bank statement: "account/INN/name".'),
    )
    account_number = models.CharField(_("account number"), max_length=30, blank=True)
    inn = models.CharField(_("INN"), max_length=20, blank=True)
    name = models.CharField(_("name"), max_length=255)
    article = models.ForeignKey(
        Article,
        models.PROTECT,
        null=True,
        blank=True,
        related_name="counterparties",
        verbose_name=_("article"),
        help_text=_("Used for this counterparty's bank lines."),
    )
    region = models.ForeignKey(
        Region, models.PROTECT, null=True, blank=True, related_name="counterparties", verbose_name=_("region")
    )
    company = models.ForeignKey(
        Company,
        models.SET_NULL,
        null=True,
        blank=True,
        related_name="accounts",
        verbose_name=_("company"),
        help_text=_("The company this bank account belongs to."),
    )
    created_at = models.DateTimeField(_("created"), auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _("counterparty")
        verbose_name_plural = _("counterparties")

    def __str__(self):
        return self.name

    @classmethod
    def parse_reference(cls, reference: str) -> dict:
        """Split "20208000900713230001/304577774/GOODWILL ..." into account number, INN and name."""
        parts = reference.split("/", 2)
        if len(parts) == 3 and parts[0].strip().isdigit():
            return {"account_number": parts[0].strip(), "inn": parts[1].strip(), "name": parts[2].strip() or reference}
        return {"account_number": "", "inn": "", "name": reference}


class OperationQuerySet(models.QuerySet):
    def posted(self):
        """Operations that count towards balances (not cancelled)."""
        return self.filter(is_cancelled=False)


class Operation(models.Model):
    desk = models.ForeignKey(CashDesk, models.PROTECT, related_name="operations", verbose_name=_("cash desk"))
    kind = models.CharField(_("type"), max_length=10, choices=Kind.choices)
    date = models.DateField(_("date"), db_index=True)
    category = models.ForeignKey(Category, models.PROTECT, related_name="operations", verbose_name=_("category"))
    account = models.CharField(
        _("account"),
        max_length=10,
        choices=Account.choices,
        help_text=_("For a transfer, the account the money leaves."),
    )
    amount = models.DecimalField(_("amount"), **MONEY, validators=POSITIVE)
    # Transfers only: where the money goes and how much arrives there.
    to_account = models.CharField(_("to account"), max_length=10, choices=Account.choices, blank=True)
    to_amount = models.DecimalField(_("amount received"), **MONEY, validators=POSITIVE, null=True, blank=True)
    rate = models.DecimalField(
        _("exchange rate"),
        max_digits=12,
        decimal_places=2,
        validators=POSITIVE,
        null=True,
        blank=True,
        help_text=_("UZS per 1 USD."),
    )
    note = models.CharField(_("note"), max_length=500, blank=True)
    # Bank desks: who paid or was paid, and the bank's document number.
    counterparty = models.ForeignKey(
        Counterparty,
        models.PROTECT,
        null=True,
        blank=True,
        related_name="operations",
        verbose_name=_("counterparty"),
    )
    document_number = models.CharField(_("document no."), max_length=50, blank=True)
    # Where an imported operation came from, e.g. "Situation.xlsx/Касса Бон/2000/1" (file/sheet/row/part).
    # Lets an import be re-run without creating duplicates. Empty for operations entered in the app.
    source = models.CharField(_("source"), max_length=200, blank=True, editable=False)

    is_cancelled = models.BooleanField(_("cancelled"), default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        models.PROTECT,
        related_name="+",
        verbose_name=_("created by"),
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(_("created"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated"), auto_now=True)

    objects = OperationQuerySet.as_manager()

    class Meta:
        ordering = ["date", "id"]
        verbose_name = _("operation")
        verbose_name_plural = _("operations")
        indexes = [models.Index(fields=["desk", "date", "id"])]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="operation_amount_positive"),
            models.UniqueConstraint(
                fields=["desk", "source"],
                condition=~models.Q(source=""),
                name="unique_operation_source_per_desk",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(kind=Kind.TRANSFER, to_amount__gt=0) & ~models.Q(to_account="")
                    | ~models.Q(kind=Kind.TRANSFER) & models.Q(to_account="", to_amount__isnull=True)
                ),
                name="operation_transfer_fields",
            ),
        ]

    def __str__(self):
        return f"{self.date} {self.get_kind_display()} {self.amount} {self.get_account_display()}"

    def clean(self):
        errors = {}
        if self.category_id and self.kind and not self.category.article.accepts(self.kind):
            errors["category"] = _("This category belongs to a %(kind)s article.") % {
                "kind": self.category.article.get_kind_display().lower()
            }
        if self.kind == Kind.TRANSFER:
            if not self.to_account:
                errors["to_account"] = _("Choose the account the money goes to.")
            elif self.to_account == self.account:
                errors["to_account"] = _("Choose a different account from the one the money leaves.")
            elif ACCOUNT_CURRENCY[self.account] == ACCOUNT_CURRENCY[self.to_account]:
                # Same currency: the same amount arrives as leaves.
                if self.to_amount and self.amount and self.to_amount != self.amount:
                    errors["to_amount"] = _("Both accounts are in the same currency, so the amounts must match.")
                else:
                    self.to_amount = self.amount
            elif self.amount and not self.to_amount:
                errors["to_amount"] = _("Enter the amount received in the other currency.")
        else:
            self.to_account = ""
            self.to_amount = None
        if self.desk_id:
            if self.account and self.account not in self.desk.accounts:
                errors.setdefault("account", _("This cash desk doesn't keep this account."))
            if self.to_account and self.to_account not in self.desk.accounts:
                errors.setdefault("to_account", _("This cash desk doesn't keep this account."))
            if self.category_id and not self.category.article.is_available_on(self.desk):
                errors.setdefault("category", _("This category isn't used on this cash desk."))
        if errors:
            raise ValidationError(errors)

    def movements(self):
        """This operation's effect on each account, as {account: signed amount}."""
        if self.kind == Kind.INCOME:
            return {self.account: self.amount}
        if self.kind == Kind.EXPENSE:
            return {self.account: -self.amount}
        return {self.account: -self.amount, self.to_account: self.to_amount}


class ExchangeRate(models.Model):
    """UZS per 1 USD from a date on (the Central Bank rate), used to show USD amounts in UZS."""

    date = models.DateField(_("date"), unique=True)
    rate = models.DecimalField(_("rate"), max_digits=12, decimal_places=2, validators=POSITIVE)

    class Meta:
        ordering = ["-date"]
        get_latest_by = "date"
        verbose_name = _("exchange rate")
        verbose_name_plural = _("exchange rates")

    def __str__(self):
        return f"{self.date:%d.%m.%Y}: {self.rate}"

    @classmethod
    def on(cls, date):
        """The rate in force on ``date`` (the latest one set on or before it), or None."""
        found = cls.objects.filter(date__lte=date).first()
        return found.rate if found else None
