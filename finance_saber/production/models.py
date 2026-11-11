"""Production and warehouse: products, raw materials, recipes and stock movements.

Stock is never stored as a number to edit. It is the sum of movements, like a
ledger: an opening balance, receipts, consumption by production, shipments and
stock-count adjustments. Posting a day's production of a product (a
ProductionRun) adds the cans to finished-goods stock and writes off each material.

A production run is a document with a status, like 1C's «проведён»: a draft is
being prepared (quantities and norms can change, and it can be deleted) and
moves no stock; posting it writes off its materials and adds its output, and only
posted runs count in stock and reports; a posted run is not deleted but
cancelled, so the history stays complete.

Each run keeps the recipe it was made with (RunLine: norm, production defect,
allowed loss), copied from the product's recipe and adjustable for that run, so
later recipe changes never rewrite past days. Raw materials are written off for
the drink brewed, packaging for the finished cans, like the production
calculation sheet (Калькуляция производства):

    raw material:  norm per can × brewed (in cans)   + defect
    packaging:     norm per can × finished cans      + defect
"""

import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

QUANTITY = {"max_digits": 18, "decimal_places": 4}
MONEY = {"max_digits": 18, "decimal_places": 2}
NORM = {"max_digits": 20, "decimal_places": 10}  # norms per can go down to millionths of a kilogram
PERCENT = {"max_digits": 5, "decimal_places": 2}
QUANTITY_STEP = Decimal("0.0001")
SHELF_LIFE_HELP = _("e.g. 12 месяцев, 2 года, 180 дней. Stock shows until when it is good.")
DEFAULT_ALLOWED_LOSS = Decimal(2)  # %, as in the production calculation sheet
# Currencies a supplier's invoice (a receipt) may be in; UZS per unit of each at the receipt's rate.
DOCUMENT_CURRENCIES = [
    ("UZS", "UZS"),
    ("USD", "USD"),
    ("EUR", "EUR"),
    ("RUB", "RUB"),
]


class Material(models.Model):
    """A raw or packaging material kept in the warehouse (сырьё), e.g. "Сахар", "Кришка золотая 202"."""

    class Kind(models.TextChoices):
        RAW = "raw", _("Raw material")
        PACKAGING = "packaging", _("Packaging")

    name = models.CharField(_("name"), max_length=150, unique=True)
    unit = models.CharField(_("unit"), max_length=20, help_text=_("e.g. кг, шт, л"))
    kind = models.CharField(_("type"), max_length=10, choices=Kind.choices, default=Kind.RAW)
    # The date the material in stock is good until (the supplier's label). Not required. The text
    # shelf life ("18 месяцев", from the materials Excel) counts only for a material without one.
    best_before = models.DateField(
        _("good until"), null=True, blank=True, help_text=_("Not required: the date the stock is good until.")
    )
    shelf_life = models.CharField(_("shelf life"), max_length=50, blank=True, help_text=SHELF_LIFE_HELP)
    min_stock = models.DecimalField(
        _("minimum stock"),
        **QUANTITY,
        default=0,
        validators=[MinValueValidator(0)],
        help_text=_("Highlighted when stock falls below this."),
    )
    # Production cost (costing.py) takes the price of the deliveries actually used; this one
    # only stands in where no delivery of the material has a price yet.
    price = models.DecimalField(
        _("standard price"),
        **MONEY,
        default=0,
        validators=[MinValueValidator(0)],
        help_text=_("UZS per unit, with VAT. Used for production cost only while no receipt has a price."),
    )
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["kind", "name"]
        verbose_name = _("material")
        verbose_name_plural = _("materials")

    def __str__(self):
        return self.name


class Product(models.Model):
    """A finished product (готовая продукция), counted in units (cans)."""

    name = models.CharField(_("name"), max_length=150, unique=True, help_text=_("As in production reports."))
    sales_name = models.CharField(_("sales name"), max_length=150, blank=True, help_text=_("As in sales reports."))
    volume = models.DecimalField(
        _("volume, l"), max_digits=6, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))]
    )
    position = models.PositiveSmallIntegerField(_("position"), default=0, help_text=_("Order in lists and reports."))
    # Read by expiry.py: a batch is good until its production date + this.
    shelf_life = models.CharField(_("shelf life"), max_length=50, blank=True, help_text=SHELF_LIFE_HELP)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["position", "name"]
        verbose_name = _("product")
        verbose_name_plural = _("products")
        permissions = [
            ("import_excel", _("Can import production data from Excel")),
            ("view_costs", _("Can view production cost and margin")),
        ]

    def __str__(self):
        return self.name


class ProductAlias(models.Model):
    """Another name a product goes by in an outside system, e.g. "7 Energy 450 ML  1/24" in SmartUp.

    Sales and plan imports look products up by name, sales name, then these
    aliases, so the same product can be spelled differently in each source.
    """

    name = models.CharField(_("name"), max_length=200, unique=True)
    product = models.ForeignKey(Product, models.CASCADE, related_name="aliases", verbose_name=_("product"))
    source = models.CharField(_("source"), max_length=50, blank=True, help_text=_("e.g. SmartUp"))

    class Meta:
        ordering = ["product", "name"]
        verbose_name = _("product alias")
        verbose_name_plural = _("product aliases")

    def __str__(self):
        return f"{self.name} → {self.product}"


def product_lookup() -> dict:
    """{normalized name: Product} for every name, sales name and alias, to match names from files."""
    from .importer import key

    found = {}
    for product in Product.objects.prefetch_related("aliases"):
        for name in (product.name, product.sales_name, *(alias.name for alias in product.aliases.all())):
            if name:
                found.setdefault(key(name), product)
    return found


class SkippedName(models.Model):
    """A name in an outside system's file that isn't one of our products (e.g. resold water): its rows are skipped."""

    name = models.CharField(_("name"), max_length=200, unique=True)
    source = models.CharField(_("source"), max_length=50, blank=True, help_text=_("e.g. SmartUp"))
    created_at = models.DateTimeField(_("created"), auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _("skipped name")
        verbose_name_plural = _("skipped names")

    def __str__(self):
        return self.name


class RecipeLine(models.Model):
    """How much of a material one unit (can) of a product uses: the production norm."""

    product = models.ForeignKey(Product, models.CASCADE, related_name="recipe", verbose_name=_("product"))
    material = models.ForeignKey(Material, models.PROTECT, related_name="recipe_lines", verbose_name=_("material"))
    quantity_per_unit = models.DecimalField(
        _("norm per unit"), **NORM, validators=[MinValueValidator(Decimal("0.0000000001"))]
    )
    allowed_loss = models.DecimalField(
        _("allowed loss, %"),
        **PERCENT,
        default=DEFAULT_ALLOWED_LOSS,
        validators=[MinValueValidator(0)],
        help_text=_("Production defect above this share of the use is highlighted."),
    )
    position = models.PositiveSmallIntegerField(_("position"), default=0)

    class Meta:
        ordering = ["product", "position", "id"]
        verbose_name = _("recipe line")
        verbose_name_plural = _("recipe lines")
        constraints = [models.UniqueConstraint(fields=["product", "material"], name="unique_material_per_recipe")]

    def __str__(self):
        return f"{self.product} › {self.material}"

    @property
    def dose_per_1000_l(self) -> Decimal:
        """The same norm per 1000 litres of drink, as recipes are usually written."""
        return self.quantity_per_unit * 1000 / self.product.volume


def round_quantity(value) -> Decimal:
    return Decimal(value).quantize(QUANTITY_STEP, rounding=ROUND_HALF_UP)


class ProductionRunQuerySet(models.QuerySet):
    def posted(self):
        """Runs that count: in stock, the journal and every report."""
        return self.filter(status=ProductionRun.Status.POSTED)

    def not_cancelled(self):
        """Drafts and posted runs: what a day's page shows."""
        return self.exclude(status=ProductionRun.Status.CANCELLED)


class ProductionRun(models.Model):
    """How many units of a product were produced on a day. One per product and day."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        POSTED = "posted", _("Posted")
        CANCELLED = "cancelled", _("Cancelled")

    date = models.DateField(_("date"), db_index=True)
    product = models.ForeignKey(Product, models.PROTECT, related_name="runs", verbose_name=_("product"))
    quantity = models.PositiveIntegerField(_("quantity"))
    batch_quantity = models.PositiveIntegerField(
        _("brewed, units"),
        null=True,
        blank=True,
        help_text=_(
            "Drink prepared, counted in units (cans). Raw materials are written off by it; "
            "empty means the same as the units produced."
        ),
    )
    note = models.CharField(_("note"), max_length=255, blank=True)
    # Imported history: stock at the time already included it, so it doesn't move stock again.
    affects_stock = models.BooleanField(_("affects stock"), default=True, editable=False)
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("created by")
    )
    created_at = models.DateTimeField(_("created"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated"), auto_now=True)

    objects = ProductionRunQuerySet.as_manager()

    class Meta:
        ordering = ["date", "product__position", "id"]
        verbose_name = _("production run")
        verbose_name_plural = _("production runs")
        constraints = [models.UniqueConstraint(fields=["date", "product"], name="one_run_per_product_and_day")]

    def __str__(self):
        return f"{self.date} {self.product} {self.quantity}"

    @property
    def is_draft(self) -> bool:
        return self.status == self.Status.DRAFT

    @property
    def is_posted(self) -> bool:
        return self.status == self.Status.POSTED

    @property
    def is_cancelled(self) -> bool:
        return self.status == self.Status.CANCELLED

    @property
    def brewed(self) -> int:
        """Units of drink prepared: what raw materials are written off by."""
        return self.batch_quantity or self.quantity

    def copy_recipe(self):
        """Give the run its own copy of the product's current recipe, if it has none yet."""
        if self.lines.exists():
            return
        RunLine.objects.bulk_create(
            RunLine(
                run=self,
                material_id=line.material_id,
                quantity_per_unit=line.quantity_per_unit,
                allowed_loss=line.allowed_loss,
                position=line.position,
            )
            for line in self.product.recipe.all()
        )

    def rebuild_moves(self):
        """(Re)create this run's stock movements from its own recipe (see RunLine)."""
        # The run itself is kept (cancelled, not deleted); its movements are derived from it.
        MaterialMove.objects.filter(run=self).delete()
        ProductMove.objects.filter(run=self).delete()
        self.copy_recipe()
        lines = list(self.lines.select_related("material"))
        for line in lines:
            line.consumed = line.consumption(self)
        RunLine.objects.bulk_update(lines, ["consumed"])
        if not self.affects_stock or not self.is_posted or not self.quantity:
            return  # a draft or cancelled run moves no stock; its lines still show what it would use
        ProductMove.objects.create(
            product=self.product,
            date=self.date,
            kind=ProductMove.Kind.OUTPUT,
            quantity=self.quantity,
            run=self,
            created_by=self.created_by,
        )
        MaterialMove.objects.bulk_create(
            MaterialMove(
                material_id=line.material_id,
                date=self.date,
                kind=MaterialMove.Kind.CONSUMPTION,
                quantity=-line.consumed,
                run=self,
                created_by=self.created_by,
            )
            for line in lines
            if line.consumed
        )


class RunLine(models.Model):
    """One material of the recipe a production run was made with.

    Copied from the product's recipe when the run is recorded, and adjustable for
    that run (the calculation sheet of the day), so each day keeps its own norms.
    """

    class DefectReason(models.TextChoices):
        """Why material was scrapped in production: the losses report (losses.py) adds them up."""

        PACKAGING = "packaging", _("Faulty packaging (cans, lids)")
        LABELLING = "labelling", _("Label or marking")
        SPILLAGE = "spillage", _("Spillage, leak")
        EQUIPMENT = "equipment", _("Equipment fault")
        RAW = "raw", _("Poor raw material")
        SETUP = "setup", _("Line setup, start-up")
        OTHER = "other", _("Other")

    run = models.ForeignKey(ProductionRun, models.CASCADE, related_name="lines", verbose_name=_("production run"))
    material = models.ForeignKey(Material, models.PROTECT, related_name="run_lines", verbose_name=_("material"))
    quantity_per_unit = models.DecimalField(_("norm per unit"), **NORM, validators=[MinValueValidator(0)])
    defect = models.DecimalField(
        _("production defect"),
        **QUANTITY,
        default=0,
        validators=[MinValueValidator(0)],
        help_text=_("Scrapped in production, in the material's unit; written off on top of the norm."),
    )
    defect_reason = models.CharField(
        _("defect reason"), max_length=12, choices=DefectReason.choices, blank=True, db_index=True
    )
    allowed_loss = models.DecimalField(
        _("allowed loss, %"), **PERCENT, default=DEFAULT_ALLOWED_LOSS, validators=[MinValueValidator(0)]
    )
    consumed = models.DecimalField(_("written off"), **QUANTITY, default=0, editable=False)
    position = models.PositiveSmallIntegerField(_("position"), default=0)

    class Meta:
        ordering = ["run", "position", "id"]
        verbose_name = _("production run line")
        verbose_name_plural = _("production run lines")
        constraints = [models.UniqueConstraint(fields=["run", "material"], name="unique_material_per_run")]

    def __str__(self):
        return f"{self.run} › {self.material}"

    @staticmethod
    def base_for(material: Material, run: ProductionRun) -> int:
        """Units the norm applies to: the drink brewed for raw materials, the finished cans for packaging."""
        return run.brewed if material.kind == Material.Kind.RAW else run.quantity

    def norm_use(self, run: ProductionRun) -> Decimal:
        """Use by the norm alone (Excel «Общий расход» / «Готовый продукт, расход»)."""
        return round_quantity(self.quantity_per_unit * self.base_for(self.material, run))

    def consumption(self, run: ProductionRun) -> Decimal:
        return self.norm_use(run) + round_quantity(self.defect)

    def loss_percent(self, run: ProductionRun) -> Decimal | None:
        use = self.norm_use(run)
        return round(self.defect / use * 100, 2) if use else None


class MoveQuerySet(models.QuerySet):
    def posted(self):
        """Movements that count towards stock (not cancelled)."""
        return self.filter(is_cancelled=False)

    def stock_by(self, field: str) -> dict:
        """{item id: stock} for the movements in this queryset."""
        rows = self.posted().values(field).annotate(total=Sum("quantity"))
        return {row[field]: row["total"] or Decimal(0) for row in rows}


class Move(models.Model):
    """A change of stock (positive in, negative out). Base of material and product movements."""

    date = models.DateField(_("date"), db_index=True)
    quantity = models.DecimalField(_("quantity"), **QUANTITY)
    note = models.CharField(_("note"), max_length=255, blank=True)
    # Counted quantity, for stock-count adjustments: the movement is the difference to the books.
    counted = models.DecimalField(_("counted"), **QUANTITY, null=True, blank=True)
    run = models.ForeignKey(
        ProductionRun,
        models.CASCADE,
        null=True,
        blank=True,
        related_name="%(class)ss",
        verbose_name=_("production run"),
    )  # set for movements a production run made (output and consumption)
    is_cancelled = models.BooleanField(_("cancelled"), default=False)
    source = models.CharField(_("source"), max_length=200, blank=True, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("created by")
    )
    created_at = models.DateTimeField(_("created"), auto_now_add=True)

    objects = MoveQuerySet.as_manager()

    # Movements a document made (a production run, a sale) change with that document, not by hand.
    DOCUMENT_SOURCES = ("sale:", "receipt:", "shipment:")

    class Meta:
        abstract = True
        ordering = ["date", "id"]

    @property
    def made_by_document(self) -> bool:
        return bool(self.run_id) or self.source.startswith(self.DOCUMENT_SOURCES)


class MaterialMove(Move):
    class Kind(models.TextChoices):
        OPENING = "opening", _("Opening balance")
        RECEIPT = "receipt", _("Receipt")
        CONSUMPTION = "consumption", _("Used in production")
        ADJUSTMENT = "adjustment", _("Stock count")
        WRITE_OFF = "write_off", _("Write-off")

    material = models.ForeignKey(Material, models.PROTECT, related_name="moves", verbose_name=_("material"))
    kind = models.CharField(_("type"), max_length=12, choices=Kind.choices)
    # Stock entered as an opening balance (or found by a count) really arrived earlier than its
    # ledger date. This is that day, for shelf life only (expiry.py): the ledger keeps ``date``.
    received_on = models.DateField(_("actually received"), null=True, blank=True)

    class Meta(Move.Meta):
        verbose_name = _("material movement")
        verbose_name_plural = _("material movements")
        constraints = [
            models.UniqueConstraint(fields=["source"], condition=~Q(source=""), name="unique_material_move_source")
        ]

    def __str__(self):
        return f"{self.date} {self.material} {self.quantity}"


class MaterialReceiptQuerySet(models.QuerySet):
    def posted(self):
        return self.filter(is_cancelled=False)


class MaterialReceipt(models.Model):
    """Materials received from a supplier: one document (the supplier's invoice) with its lines.

    Like 1C's «Поступление»: the document is the record, and its lines make the stock
    movements (rebuild_moves), so they change only with the document. A wrong document
    isn't deleted: it is cancelled, and its movements stop counting. Its amounts are
    what we owe the supplier for it (Settlements).
    """

    date = models.DateField(_("date"), db_index=True)
    supplier = models.ForeignKey(
        "cashbook.Company", models.PROTECT, related_name="material_receipts", verbose_name=_("supplier")
    )
    number = models.CharField(
        _("registration number"), max_length=50, help_text=_("The number of the supplier's invoice or waybill.")
    )
    note = models.CharField(_("note"), max_length=255, blank=True)
    # The supplier's invoice may be in another currency: its lines keep the amounts as invoiced
    # (currency_amount) and in UZS at this rate (amount), which costs and settlements read.
    currency = models.CharField(_("currency"), max_length=3, choices=DOCUMENT_CURRENCIES, default="UZS")
    rate = models.DecimalField(
        _("exchange rate"),
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.0001"))],
        help_text=_("UZS per 1 unit of the currency, on the receipt's date."),
    )
    order = models.ForeignKey(
        "MaterialOrder",
        models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="receipts",
        verbose_name=_("material order"),
        help_text=_("The supplier order (goods in transit) this receipt received, in full or in part."),
    )
    is_cancelled = models.BooleanField(_("cancelled"), default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("created by")
    )
    created_at = models.DateTimeField(_("created"), auto_now_add=True)

    objects = MaterialReceiptQuerySet.as_manager()

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = _("material receipt")
        verbose_name_plural = _("material receipts")
        constraints = [
            # The same invoice of the same supplier entered twice is a duplicate.
            models.UniqueConstraint(
                fields=["supplier", "number"],
                condition=Q(is_cancelled=False),
                name="unique_material_receipt_number_per_supplier",
            )
        ]

    def __str__(self):
        return f"№ {self.number} {self.date:%d.%m.%Y} {self.supplier}"

    @property
    def total(self) -> Decimal:
        return sum((line.amount for line in self.lines.all()), Decimal(0))

    @property
    def currency_total(self) -> Decimal:
        """The total as invoiced, in the receipt's currency."""
        return sum((line.currency_amount for line in self.lines.all()), Decimal(0))

    @property
    def in_foreign_currency(self) -> bool:
        return self.currency != "UZS"

    def in_uzs(self, value: Decimal) -> Decimal:
        """An amount in the receipt's currency, in UZS at its rate."""
        if not self.in_foreign_currency:
            return value
        return (value * (self.rate or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def price_lines_as_invoiced(self):
        """Its lines were entered in the receipt's currency: keep that as invoiced, and the amount in UZS."""
        for line in self.lines.all():
            line.currency_amount = line.amount
            line.amount = self.in_uzs(line.amount)
            line.save(update_fields=["currency_amount", "amount"])

    @property
    def stock_source(self) -> str:
        """Prefix of its movements' source: "receipt:<document>:<line>"."""
        return f"receipt:{self.pk}:"

    def moves(self):
        return MaterialMove.objects.filter(source__startswith=self.stock_source)

    def rebuild_moves(self):
        """(Re)create one receipt movement per line; none while the document is cancelled."""
        self.moves().delete()
        if self.is_cancelled:
            return
        note = f"{self.supplier.name}, № {self.number}"[:255]
        MaterialMove.objects.bulk_create(
            MaterialMove(
                material_id=line.material_id,
                date=self.date,
                kind=MaterialMove.Kind.RECEIPT,
                quantity=line.quantity,
                note=note,
                source=f"{self.stock_source}{line.pk}",
                created_by=self.created_by,
            )
            for line in self.lines.all()
        )


class MaterialReceiptLine(models.Model):
    receipt = models.ForeignKey(MaterialReceipt, models.CASCADE, related_name="lines", verbose_name=_("receipt"))
    material = models.ForeignKey(Material, models.PROTECT, related_name="receipt_lines", verbose_name=_("material"))
    quantity = models.DecimalField(_("quantity"), **QUANTITY, validators=[MinValueValidator(QUANTITY_STEP)])
    amount = models.DecimalField(
        _("amount"), **MONEY, default=0, validators=[MinValueValidator(0)], help_text=_("With VAT, in UZS.")
    )
    # This delivery's own "good until" (its label); not required. The shelf-life page uses it first,
    # then the material's date, then its text shelf life (expiry.py).
    best_before = models.DateField(_("good until"), null=True, blank=True)
    currency_amount = models.DecimalField(
        _("amount in the currency"),
        **MONEY,
        default=0,
        validators=[MinValueValidator(0)],
        help_text=_("As invoiced, in the receipt's currency (the same as the amount for UZS)."),
    )
    order_line = models.ForeignKey(
        "MaterialOrderLine",
        models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="receipt_lines",
        verbose_name=_("material order line"),
    )

    class Meta:
        ordering = ["id"]
        verbose_name = _("receipt line")
        verbose_name_plural = _("receipt lines")
        constraints = [
            models.UniqueConstraint(fields=["receipt", "material"], name="unique_material_per_receipt"),
        ]

    def __str__(self):
        return f"{self.material} {self.quantity}"

    @property
    def price(self) -> Decimal | None:
        return (self.amount / self.quantity).quantize(Decimal("0.01")) if self.quantity else None

    @property
    def currency_price(self) -> Decimal | None:
        """The price per unit as invoiced, in the receipt's currency."""
        return (self.currency_amount / self.quantity).quantize(Decimal("0.0001")) if self.quantity else None


class MaterialOrder(models.Model):
    """Materials ordered from a supplier and not in the warehouse yet: goods in transit (товары в пути).

    Like 1C's «Заказ поставщику»: the planner enters what was ordered, from whom, at what
    price (in the supplier's currency) and when it is expected. The goods often don't
    travel together, so each line has its own status (ordered, in transit, at customs)
    and, if it differs, its own expected date. What arrives is received in parts: each
    arrival makes a material receipt (in UZS, at the day's rate) for the quantities that
    came, and the order shows what is still to come. Only that remainder shows on the
    Raw materials page as in transit. The order doesn't move stock itself, and a wrong
    one is cancelled, not deleted.
    """

    class Status(models.TextChoices):
        ORDERED = "ordered", _("Ordered")
        IN_TRANSIT = "in_transit", _("In transit")
        CUSTOMS = "customs", _("At customs")
        PARTIAL = "partial", _("Partly received")
        RECEIVED = "received", pgettext_lazy("order", "Received")
        CANCELLED = "cancelled", pgettext_lazy("order", "Cancelled")

    OPEN = (Status.ORDERED, Status.IN_TRANSIT, Status.CUSTOMS, Status.PARTIAL)
    # What the goods themselves can be doing: an order's lines and its manual status.
    ON_THE_WAY = (Status.ORDERED, Status.IN_TRANSIT, Status.CUSTOMS)

    class Currency(models.TextChoices):
        UZS = "UZS", "UZS"
        USD = "USD", "USD"
        EUR = "EUR", "EUR"
        RUB = "RUB", "RUB"
        CNY = "CNY", "CNY"

    supplier = models.ForeignKey(
        "cashbook.Company", models.PROTECT, related_name="material_orders", verbose_name=_("supplier")
    )
    number = models.CharField(
        _("order number"), max_length=50, blank=True, help_text=_("The contract or invoice number; empty: numbered.")
    )
    order_date = models.DateField(_("order date"))
    expected_date = models.DateField(_("expected arrival"), db_index=True)
    currency = models.CharField(_("currency"), max_length=3, choices=Currency.choices, default=Currency.USD)
    status = models.CharField(_("status"), max_length=12, choices=Status.choices, default=Status.ORDERED)
    note = models.CharField(_("note"), max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("created by")
    )
    created_at = models.DateTimeField(_("created"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated"), auto_now=True)

    class Meta:
        ordering = ["expected_date", "id"]
        verbose_name = _("material order")
        verbose_name_plural = _("material orders")

    def __str__(self):
        return f"№ {self.number} {self.supplier}"

    def assign_number(self):
        """A number for an order saved without one: "ЗК-00042" from its id."""
        if not self.number:
            self.number = f"ЗК-{self.pk:05d}"
            self.save(update_fields=["number"])

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN

    @property
    def days_left(self) -> int:
        """Days until the expected arrival; negative when it is late."""
        return (self.expected_date - timezone.localdate()).days

    @property
    def is_late(self) -> bool:
        return self.is_open and self.days_left < 0

    @property
    def days_late(self) -> int:
        return max(-self.days_left, 0)

    @property
    def total(self) -> Decimal:
        return sum((line.amount for line in self.lines.all()), Decimal(0))

    def refresh_status(self):
        """After a receipt (or its cancelling): received when nothing is left to come, partly when some came."""
        if self.status == self.Status.CANCELLED:
            return
        lines = list(with_received(MaterialOrderLine.objects.filter(order=self)))  # fresh, not a prefetched list
        received = [line.received for line in lines]
        if lines and all(line.remaining == 0 for line in lines):
            status = self.Status.RECEIVED
        elif any(received):
            status = self.Status.PARTIAL
        elif self.status in (self.Status.PARTIAL, self.Status.RECEIVED):
            status = self.Status.IN_TRANSIT  # its receipts were cancelled: the goods are on the way again
        else:
            return
        if status != self.status:
            self.status = status
            self.save(update_fields=["status", "updated_at"])


class MaterialOrderLine(models.Model):
    """One material of an order, with its own status and arrival: the goods may come separately."""

    order = models.ForeignKey(MaterialOrder, models.CASCADE, related_name="lines", verbose_name=_("material order"))
    material = models.ForeignKey(Material, models.PROTECT, related_name="order_lines", verbose_name=_("material"))
    quantity = models.DecimalField(_("quantity"), **QUANTITY, validators=[MinValueValidator(QUANTITY_STEP)])
    amount = models.DecimalField(
        _("amount"), **MONEY, default=0, validators=[MinValueValidator(0)], help_text=_("In the order's currency.")
    )
    status = models.CharField(
        _("status"),
        max_length=12,
        choices=[(value, label) for value, label in MaterialOrder.Status.choices if value in MaterialOrder.ON_THE_WAY],
        default=MaterialOrder.Status.ORDERED,
    )
    expected_date = models.DateField(
        _("expected arrival"), null=True, blank=True, help_text=_("Empty: the order's expected arrival.")
    )

    class Meta:
        ordering = ["id"]
        verbose_name = _("material order line")
        verbose_name_plural = _("material order lines")
        constraints = [models.UniqueConstraint(fields=["order", "material"], name="unique_material_per_order")]

    def __str__(self):
        return f"{self.material} {self.quantity}"

    @property
    def price(self) -> Decimal | None:
        return (self.amount / self.quantity).quantize(Decimal("0.01")) if self.quantity else None

    @property
    def expected(self) -> datetime.date:
        return self.expected_date or self.order.expected_date

    @property
    def received(self) -> Decimal:
        """What has arrived: the quantities of its posted material receipts."""
        if hasattr(self, "received_quantity"):  # annotated (with_received)
            return self.received_quantity or Decimal(0)
        total = self.receipt_lines.filter(receipt__is_cancelled=False).aggregate(total=models.Sum("quantity"))
        return total["total"] or Decimal(0)

    @property
    def remaining(self) -> Decimal:
        return max(self.quantity - self.received, Decimal(0))

    @property
    def is_late(self) -> bool:
        return self.remaining > 0 and self.expected < timezone.localdate()


def with_received(lines):
    """Order lines annotated with what has arrived (``line.received`` without a query each)."""
    return lines.annotate(
        received_quantity=models.Sum("receipt_lines__quantity", filter=Q(receipt_lines__receipt__is_cancelled=False))
    )


def materials_in_transit() -> dict[int, dict]:
    """{material id: what is still to come of open orders: quantity, of it at customs, the earliest arrival, late}."""
    today = timezone.localdate()
    found = {}
    lines = with_received(
        MaterialOrderLine.objects.filter(order__status__in=MaterialOrder.OPEN).select_related("order")
    )
    for line in lines:
        if not line.remaining:
            continue
        item = found.setdefault(
            line.material_id, {"quantity": Decimal(0), "customs": Decimal(0), "expected": None, "orders": 0}
        )
        item["quantity"] += line.remaining
        if line.status == MaterialOrder.Status.CUSTOMS:
            item["customs"] += line.remaining
        item["orders"] += 1
        if item["expected"] is None or line.expected < item["expected"]:
            item["expected"] = line.expected
    for item in found.values():
        item["late"] = item["expected"] < today
    return found


class ProductMove(Move):
    class Kind(models.TextChoices):
        OPENING = "opening", _("Opening balance")
        OUTPUT = "output", _("Produced")
        SHIPMENT = "shipment", _("Shipment")
        RETURN = "return", _("Customer return")
        ADJUSTMENT = "adjustment", _("Stock count")
        WRITE_OFF = "write_off", _("Write-off")

    product = models.ForeignKey(Product, models.PROTECT, related_name="moves", verbose_name=_("product"))
    kind = models.CharField(_("type"), max_length=12, choices=Kind.choices)

    class Meta(Move.Meta):
        verbose_name = _("product movement")
        verbose_name_plural = _("product movements")
        constraints = [
            models.UniqueConstraint(fields=["source"], condition=~Q(source=""), name="unique_product_move_source")
        ]

    def __str__(self):
        return f"{self.date} {self.product} {self.quantity}"


class ProductShipmentQuerySet(models.QuerySet):
    def posted(self):
        return self.filter(is_cancelled=False)


class ProductShipment(models.Model):
    """Finished goods shipped to a customer: one document (the waybill) with its lines.

    Like 1C's «Реализация»: the customer is a company of the handbook (Cash desk ›
    Companies), and the lines make the stock movements (rebuild_moves), so they change
    only with the document. A wrong shipment isn't deleted: it is cancelled, and its
    goods return to stock.

    Entered by hand, or loaded from SmartUp's sales report: one document per day,
    customer and project, found again by ``external_key`` when a report is loaded
    again (shipment_import.py). A customer return (``is_return``) is the same document
    the other way: its goods come back into stock.
    """

    class Source(models.TextChoices):
        MANUAL = "manual", _("Entered by hand")
        SMARTUP = "smartup", _("SmartUp report")

    date = models.DateField(_("date"), db_index=True)
    customer = models.ForeignKey(
        "cashbook.Company", models.PROTECT, related_name="product_shipments", verbose_name=_("customer")
    )
    number = models.CharField(
        _("waybill number"), max_length=50, blank=True, help_text=_("Leave empty to number it automatically.")
    )
    note = models.CharField(_("note"), max_length=255, blank=True)
    is_return = models.BooleanField(
        _("customer return"), default=False, help_text=_("Goods the customer sent back: they return to stock.")
    )
    channel = models.CharField(
        _("project"), max_length=100, blank=True, help_text=_("SmartUp's project, e.g. Key Account.")
    )
    source = models.CharField(_("source"), max_length=10, choices=Source.choices, default=Source.MANUAL)
    external_key = models.CharField(_("external key"), max_length=200, blank=True, editable=False)
    affects_stock = models.BooleanField(
        _("affects stock"),
        default=True,
        editable=False,
        help_text=_("Off for shipments from before the warehouse was counted (history from the old Sales module)."),
    )
    is_cancelled = models.BooleanField(_("cancelled"), default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("created by")
    )
    created_at = models.DateTimeField(_("created"), auto_now_add=True)

    objects = ProductShipmentQuerySet.as_manager()

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = _("shipment")
        verbose_name_plural = _("shipments")
        constraints = [
            # A waybill number is used once (cancelled ones free theirs).
            models.UniqueConstraint(
                fields=["number"], condition=Q(is_cancelled=False) & ~Q(number=""), name="unique_shipment_number"
            ),
            # A report's document (day, customer, project) is loaded once, then updated.
            models.UniqueConstraint(fields=["external_key"], condition=~Q(external_key=""), name="unique_shipment_key"),
        ]

    def __str__(self):
        return f"№ {self.number} {self.date:%d.%m.%Y} {self.customer}"

    def assign_number(self):
        """A number for a document saved without one: "ОТ-00042" from its id ("ВЗ-00042" for a return)."""
        if not self.number:
            self.number = f"{'ВЗ' if self.is_return else 'ОТ'}-{self.pk:05d}"
            self.save(update_fields=["number"])

    @property
    def total(self) -> Decimal:
        return sum((line.amount for line in self.lines.all()), Decimal(0))

    @property
    def stock_source(self) -> str:
        """Prefix of its movements' source: "shipment:<document>:<line>"."""
        return f"shipment:{self.pk}:"

    def moves(self):
        return ProductMove.objects.filter(source__startswith=self.stock_source)

    def rebuild_moves(self):
        """(Re)create one shipment movement per line; none while the document is cancelled, or for history."""
        self.moves().delete()
        if self.is_cancelled or not self.affects_stock:
            return
        note = f"{self.customer.name}, № {self.number}"[:255]
        kind, sign = (ProductMove.Kind.RETURN, 1) if self.is_return else (ProductMove.Kind.SHIPMENT, -1)
        ProductMove.objects.bulk_create(
            ProductMove(
                product_id=line.product_id,
                date=self.date,
                kind=kind,
                quantity=sign * line.quantity,
                note=note,
                source=f"{self.stock_source}{line.pk}",
                created_by=self.created_by,
            )
            for line in self.lines.all()
        )


class ProductShipmentLine(models.Model):
    shipment = models.ForeignKey(ProductShipment, models.CASCADE, related_name="lines", verbose_name=_("shipment"))
    product = models.ForeignKey(Product, models.PROTECT, related_name="shipment_lines", verbose_name=_("product"))
    quantity = models.PositiveIntegerField(_("quantity"), validators=[MinValueValidator(1)])
    amount = models.DecimalField(
        _("amount"), **MONEY, default=0, validators=[MinValueValidator(0)], help_text=_("With discount, in UZS.")
    )

    class Meta:
        ordering = ["id"]
        verbose_name = _("shipment line")
        verbose_name_plural = _("shipment lines")
        constraints = [
            models.UniqueConstraint(fields=["shipment", "product"], name="unique_product_per_shipment"),
        ]

    def __str__(self):
        return f"{self.product} {self.quantity}"

    @property
    def price(self) -> Decimal | None:
        return (self.amount / self.quantity).quantize(Decimal("0.01")) if self.quantity else None


def material_stock(on_date=None) -> dict[int, Decimal]:
    """{material id: stock}, at the end of ``on_date`` if given."""
    moves = MaterialMove.objects.all()
    if on_date:
        moves = moves.filter(date__lte=on_date)
    return moves.stock_by("material_id")


def product_stock(on_date=None) -> dict[int, Decimal]:
    """{product id: stock in units}, at the end of ``on_date`` if given."""
    moves = ProductMove.objects.all()
    if on_date:
        moves = moves.filter(date__lte=on_date)
    return moves.stock_by("product_id")


class ClosedPeriod(models.Model):
    """A closing (or reopening) of the period: from now on, nothing dated up to ``closed_until`` changes.

    The latest row is in force (periods.py); earlier rows are the history. Never edited or deleted.
    """

    closed_until = models.DateField(_("closed until"))
    note = models.CharField(_("note"), max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("closed by")
    )
    created_at = models.DateTimeField(_("when"), auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = _("closed period")
        verbose_name_plural = _("closed periods")

    def __str__(self):
        return f"{self.closed_until:%d.%m.%Y}"
