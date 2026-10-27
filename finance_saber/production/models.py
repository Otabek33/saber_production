"""Production and warehouse: products, raw materials, recipes and stock movements.

Stock is never stored as a number to edit. It is the sum of movements, like a
ledger: an opening balance, receipts, consumption by production, shipments and
stock-count adjustments. Recording a day's production of a product (a
ProductionRun) adds the cans to finished-goods stock and writes off each material.

Each run keeps the recipe it was made with (RunLine: norm, production defect,
allowed loss), copied from the product's recipe and adjustable for that run, so
later recipe changes never rewrite past days. Raw materials are written off for
the drink brewed, packaging for the finished cans, like the production
calculation sheet (Калькуляция производства):

    raw material:  norm per can × brewed (in cans)   + defect
    packaging:     norm per can × finished cans      + defect
"""

from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q, Sum
from django.utils.translation import gettext_lazy as _

QUANTITY = {"max_digits": 18, "decimal_places": 4}
NORM = {"max_digits": 20, "decimal_places": 10}  # norms per can go down to millionths of a kilogram
PERCENT = {"max_digits": 5, "decimal_places": 2}
QUANTITY_STEP = Decimal("0.0001")
DEFAULT_ALLOWED_LOSS = Decimal(2)  # %, as in the production calculation sheet


class Material(models.Model):
    """A raw or packaging material kept in the warehouse (сырьё), e.g. "Сахар", "Кришка золотая 202"."""

    class Kind(models.TextChoices):
        RAW = "raw", _("Raw material")
        PACKAGING = "packaging", _("Packaging")

    name = models.CharField(_("name"), max_length=150, unique=True)
    unit = models.CharField(_("unit"), max_length=20, help_text=_("e.g. кг, шт, л"))
    kind = models.CharField(_("type"), max_length=10, choices=Kind.choices, default=Kind.RAW)
    shelf_life = models.CharField(_("shelf life"), max_length=50, blank=True)
    min_stock = models.DecimalField(
        _("minimum stock"),
        **QUANTITY,
        default=0,
        validators=[MinValueValidator(0)],
        help_text=_("Highlighted when stock falls below this."),
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
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["position", "name"]
        verbose_name = _("product")
        verbose_name_plural = _("products")
        permissions = [("import_excel", _("Can import production data from Excel"))]

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


class ProductionRun(models.Model):
    """How many units of a product were produced on a day. One per product and day."""

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
    is_cancelled = models.BooleanField(_("cancelled"), default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("created by")
    )
    created_at = models.DateTimeField(_("created"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated"), auto_now=True)

    class Meta:
        ordering = ["date", "product__position", "id"]
        verbose_name = _("production run")
        verbose_name_plural = _("production runs")
        constraints = [models.UniqueConstraint(fields=["date", "product"], name="one_run_per_product_and_day")]

    def __str__(self):
        return f"{self.date} {self.product} {self.quantity}"

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
        if not self.affects_stock or self.is_cancelled or not self.quantity:
            return
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
    DOCUMENT_SOURCES = ("sale:",)

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

    class Meta(Move.Meta):
        verbose_name = _("material movement")
        verbose_name_plural = _("material movements")
        constraints = [
            models.UniqueConstraint(fields=["source"], condition=~Q(source=""), name="unique_material_move_source")
        ]

    def __str__(self):
        return f"{self.date} {self.material} {self.quantity}"


class ProductMove(Move):
    class Kind(models.TextChoices):
        OPENING = "opening", _("Opening balance")
        OUTPUT = "output", _("Produced")
        SHIPMENT = "shipment", _("Shipment")
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
