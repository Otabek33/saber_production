"""Sales (shipments to clients), what clients owe, and the monthly sales and production plan.

A sale is one shipment line as SmartUp exports it: a day, a client, a product, a
quantity and its total price. It is entered by hand or loaded from a SmartUp
Excel export now, and will come from the SmartUp API later (``external_id``).

What a client owes is not stored: it is the client's opening balance plus
shipments, minus the payments its bank payer made (cash book operations with
that counterparty), counted from the first recorded sale. Positive: a debtor;
negative: paid in advance, a creditor.
"""

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from finance_saber.cashbook.models import Counterparty, Region
from finance_saber.production.models import Product, ProductMove

MONEY = {"max_digits": 18, "decimal_places": 2}


class Channel(models.Model):
    """Sales channel, SmartUp's "Название проекта": Key Account, Первичные отгрузки, Export…"""

    name = models.CharField(_("name"), max_length=100, unique=True)
    is_export = models.BooleanField(_("export"), default=False)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _("sales channel")
        verbose_name_plural = _("sales channels")

    def __str__(self):
        return self.name


class Client(models.Model):
    """Who goods are shipped to, as SmartUp names them, e.g. "M094-ТАШ..ЭКС.ЮНУСОБОД 6КВ".

    ``payer`` links the client to the bank counterparty that pays for it; several
    clients (a chain's shops) can share one payer, and then owe as one.
    """

    name = models.CharField(_("name"), max_length=255, unique=True)
    payer = models.ForeignKey(
        Counterparty,
        models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales_clients",
        verbose_name=_("payer"),
        help_text=_("The bank counterparty whose payments settle this client's shipments."),
    )
    region = models.ForeignKey(
        Region, models.PROTECT, null=True, blank=True, related_name="sales_clients", verbose_name=_("region")
    )
    opening_balance = models.DecimalField(
        _("opening balance"),
        **MONEY,
        default=0,
        help_text=_("Owed before the first recorded sale; negative if the client had paid in advance."),
    )
    is_active = models.BooleanField(_("active"), default=True)
    created_at = models.DateTimeField(_("created"), auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _("client")
        verbose_name_plural = _("clients")

    def __str__(self):
        return self.name


class SaleQuerySet(models.QuerySet):
    def posted(self):
        return self.filter(is_cancelled=False)


class Sale(models.Model):
    """One shipment line: a product shipped to a client on a day, and its total price."""

    class Source(models.TextChoices):
        MANUAL = "manual", _("Entered by hand")
        EXCEL = "excel", _("Excel (SmartUp export)")
        SMARTUP = "smartup", _("SmartUp")

    date = models.DateField(_("shipment date"), db_index=True)
    client = models.ForeignKey(Client, models.PROTECT, related_name="sales", verbose_name=_("client"))
    product = models.ForeignKey(Product, models.PROTECT, related_name="sales", verbose_name=_("product"))
    channel = models.ForeignKey(
        Channel, models.PROTECT, null=True, blank=True, related_name="sales", verbose_name=_("channel")
    )
    quantity = models.PositiveIntegerField(_("quantity"), validators=[MinValueValidator(1)])
    amount = models.DecimalField(_("total price"), **MONEY, validators=[MinValueValidator(0)])
    # History before finished-goods stock was counted doesn't move stock (see services.stock_tracked_from).
    affects_stock = models.BooleanField(_("affects stock"), default=True, editable=False)
    source = models.CharField(_("source"), max_length=10, choices=Source.choices, default=Source.MANUAL)
    external_id = models.CharField(
        _("external ID"), max_length=100, blank=True, help_text=_("The line's ID in SmartUp, once integrated.")
    )
    note = models.CharField(_("note"), max_length=255, blank=True)
    is_cancelled = models.BooleanField(_("cancelled"), default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("created by")
    )
    created_at = models.DateTimeField(_("created"), auto_now_add=True)

    objects = SaleQuerySet.as_manager()

    class Meta:
        ordering = ["date", "id"]
        verbose_name = _("sale")
        verbose_name_plural = _("sales")
        permissions = [("import_sales", _("Can import sales and plans from Excel"))]
        constraints = [
            # The same product for the same price to the same client on the same day is a duplicate.
            models.UniqueConstraint(
                fields=["date", "client", "product", "amount"],
                condition=Q(is_cancelled=False),
                name="unique_sale_per_day_client_product_amount",
            ),
            models.UniqueConstraint(
                fields=["external_id"], condition=~Q(external_id=""), name="unique_sale_external_id"
            ),
        ]

    def __str__(self):
        return f"{self.date} {self.client} {self.product} {self.quantity}"

    @property
    def stock_source(self) -> str:
        return f"sale:{self.pk}"

    def rebuild_moves(self):
        """(Re)create the finished-goods movement this shipment makes."""
        ProductMove.objects.filter(source=self.stock_source).delete()
        if self.affects_stock and not self.is_cancelled:
            ProductMove.objects.create(
                product=self.product,
                date=self.date,
                kind=ProductMove.Kind.SHIPMENT,
                quantity=-self.quantity,
                note=str(self.client)[:255],
                source=self.stock_source,
                created_by=self.created_by,
            )


class Plan(models.Model):
    """Planned units of a product for a month: sales (реализация) or production."""

    class Kind(models.TextChoices):
        SALES = "sales", _("Sales plan")
        PRODUCTION = "production", _("Production plan")

    kind = models.CharField(_("type"), max_length=10, choices=Kind.choices)
    year = models.PositiveSmallIntegerField(_("year"))
    month = models.PositiveSmallIntegerField(_("month"), validators=[MinValueValidator(1)])
    product = models.ForeignKey(Product, models.CASCADE, related_name="plans", verbose_name=_("product"))
    quantity = models.PositiveIntegerField(_("quantity"), default=0)
    updated_at = models.DateTimeField(_("updated"), auto_now=True)

    class Meta:
        ordering = ["kind", "year", "product__position", "month"]
        verbose_name = _("plan")
        verbose_name_plural = _("plans")
        constraints = [
            models.UniqueConstraint(fields=["kind", "year", "month", "product"], name="one_plan_per_product_month"),
            models.CheckConstraint(condition=Q(month__gte=1, month__lte=12), name="plan_month_1_12"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.month:02}.{self.year} {self.product}: {self.quantity}"
