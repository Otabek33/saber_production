import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django import forms
from django.core.validators import FileExtensionValidator
from django.forms import formset_factory, inlineformset_factory
from django.forms.formsets import DELETION_FIELD_NAME
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from finance_saber.cashbook.forms import MoneyField
from finance_saber.cashbook.models import Company
from finance_saber.core.forms import BootstrapFormMixin

from .models import (
    DEFAULT_ALLOWED_LOSS,
    QUANTITY_STEP,
    Material,
    MaterialMove,
    MaterialReceipt,
    MaterialReceiptLine,
    Product,
    ProductMove,
    RecipeLine,
)

DATE_WIDGET = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
SEARCHABLE = {"data-searchable": ""}  # production.js turns these selects into Select2


class QuantityField(MoneyField):
    """A quantity typed like "1 250,5" or "0,125", with up to 4 decimals.

    Unlike amounts, a lone comma is always the decimal mark ("0,125" kg is 0.125, not 125):
    thousands are grouped by spaces, as app.js formats them while typing.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("decimal_places", 4)
        super().__init__(**kwargs)

    def to_python(self, value):
        if isinstance(value, str):
            text = re.sub(r"[\s  ']", "", value)
            if text.count(",") == 1 and "." not in text:
                value = text.replace(",", ".")
        return super().to_python(value)


class NormField(forms.DecimalField):
    """A norm or small quantity: "0,04752", "0.04752", "105,6" or a fraction like "1/12".

    Unlike amounts, a comma is always the decimal separator here (norms have no
    thousands), and spaces are ignored.
    """

    widget = forms.TextInput  # number inputs reject commas and fractions

    def __init__(self, **kwargs):
        kwargs.setdefault("max_digits", 20)
        kwargs.setdefault("decimal_places", 10)
        kwargs.setdefault("min_value", Decimal(0))
        super().__init__(**kwargs)

    def widget_attrs(self, widget):
        attrs = super().widget_attrs(widget)
        attrs.pop("step", None)
        attrs.update({"inputmode": "decimal", "autocomplete": "off"})
        return attrs

    def to_python(self, value):
        if isinstance(value, str):
            value = re.sub(r"[\s\u00a0\u202f']", "", value).replace(",", ".")
            fraction = re.fullmatch(r"(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)", value)
            if fraction:
                try:
                    numerator, denominator = Decimal(fraction[1]), Decimal(fraction[2])
                except InvalidOperation:
                    raise forms.ValidationError(self.error_messages["invalid"], code="invalid") from None
                if not denominator:
                    raise forms.ValidationError(self.error_messages["invalid"], code="invalid")
                step = Decimal(1).scaleb(-self.decimal_places)
                value = str((numerator / denominator).quantize(step, rounding=ROUND_HALF_UP))
        return super().to_python(value)


def plain(value: Decimal | None) -> str:
    """A decimal without trailing zeros, as typed: 0.0475200000 -> "0.04752", 103.0000 -> "103"."""
    if value is None:
        return ""
    text = format(value.normalize(), "f")
    return text


class DayForm(BootstrapFormMixin, forms.Form):
    """Pick the production day to show or fill in."""

    date = forms.DateField(label=_("Date"), widget=DATE_WIDGET)


class DailyProductionForm(BootstrapFormMixin, forms.Form):
    """One quantity per active product for a day, like a column of the Warehouse sheet."""

    def __init__(self, *args, products, initial_quantities=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.products = list(products)
        initial_quantities = initial_quantities or {}
        for product in self.products:
            self.fields[self.field_name(product)] = forms.IntegerField(
                label=product.name,
                min_value=0,
                required=False,
                initial=initial_quantities.get(product.pk) or None,
                widget=forms.NumberInput(attrs={"inputmode": "numeric", "placeholder": "0", "min": 0}),
            )

    @staticmethod
    def field_name(product) -> str:
        return f"product_{product.pk}"

    def rows(self):
        """(product, bound field) pairs for the template."""
        return [(product, self[self.field_name(product)]) for product in self.products]

    def quantities(self) -> dict:
        return {product: self.cleaned_data.get(self.field_name(product)) or 0 for product in self.products}


class MaterialMoveForm(BootstrapFormMixin, forms.ModelForm):
    """A material movement entered by hand; ``kind`` and ``sign`` (+1 in, −1 out) say what it is."""

    quantity = QuantityField(label=_("Quantity"))

    class Meta:
        model = MaterialMove
        fields = ["date", "material", "quantity", "note"]
        widgets = {"date": DATE_WIDGET, "material": forms.Select(attrs=SEARCHABLE)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = Material.objects.filter(is_active=True)
        self.fields["date"].initial = timezone.localdate()


class MaterialReceiptForm(BootstrapFormMixin, forms.ModelForm):
    """The receipt document's header: who it came from, when, and the supplier's invoice number."""

    class Meta:
        model = MaterialReceipt
        fields = ["supplier", "date", "number", "note"]
        # data-supplier-first: production.js shows the material lines once a supplier is chosen
        widgets = {"date": DATE_WIDGET, "supplier": forms.Select(attrs={**SEARCHABLE, "data-supplier-first": ""})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Suppliers are the companies of Cash desk › Companies, other than our own.
        self.fields["supplier"].queryset = Company.objects.filter(is_own=False).order_by("name")
        self.fields["supplier"].empty_label = _("Choose a supplier")
        # Today by default. Not field.initial: with an instance, its empty date would win.
        self.initial["date"] = self.initial.get("date") or timezone.localdate()

    def clean_number(self):
        # Kept in capitals, so "нк-101" and "НК-101" are one number on any database (SQLite
        # compares Cyrillic case-sensitively) and the unique constraint catches a duplicate.
        return " ".join(self.cleaned_data["number"].split()).upper()

    def clean(self):
        cleaned = super().clean()
        supplier, number = cleaned.get("supplier"), cleaned.get("number")
        duplicate = MaterialReceipt.objects.posted().filter(supplier=supplier, number=number)
        if supplier and number and duplicate.exclude(pk=self.instance.pk).exists():
            self.add_error(
                "number",
                _("This supplier's document № %(number)s is already entered.") % {"number": number},
            )
        return cleaned


class ReceiptLineForm(BootstrapFormMixin, forms.ModelForm):
    # data-number: app.js groups the digits as they are typed ("1 250,5"), as QuantityField reads them
    quantity = QuantityField(
        label=_("Quantity"), min_value=QUANTITY_STEP, widget=forms.TextInput(attrs={"data-number": "4"})
    )

    class Meta:
        model = MaterialReceiptLine
        fields = ["material", "quantity"]
        widgets = {"material": forms.Select(attrs=SEARCHABLE)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = Material.objects.filter(is_active=True)


class BaseReceiptLineFormSet(forms.BaseInlineFormSet):
    def add_fields(self, form, index):
        super().add_fields(form, index)
        # A removed line stays in the page, hidden, with DELETE set by production.js: the
        # formset then skips it, so lines can be removed anywhere without renumbering forms.
        form.fields[DELETION_FIELD_NAME].widget = forms.HiddenInput(attrs={"data-delete": ""})

    def clean(self):
        # Before Django's own unique check, so a repeated material gets this message on its line.
        seen = set()
        for form in self.forms:
            if self._should_delete_form(form):
                continue
            material = form.cleaned_data.get("material") if form.has_changed() else None
            if material is None:
                continue
            if material in seen:
                message = _("%(material)s is already in this receipt: add its quantity to one line.")
                form.add_error("material", message % {"material": material})
            seen.add(material)
        super().clean()


ReceiptLineFormSet = inlineformset_factory(
    MaterialReceipt,
    MaterialReceiptLine,
    form=ReceiptLineForm,
    formset=BaseReceiptLineFormSet,
    extra=2,
    can_delete=True,
    min_num=1,
    validate_min=True,
)


class MaterialWriteOffForm(MaterialMoveForm):
    kind, sign = MaterialMove.Kind.WRITE_OFF, -1

    class Meta(MaterialMoveForm.Meta):
        labels = {"note": _("Reason")}


class ShipmentForm(BootstrapFormMixin, forms.ModelForm):
    kind, sign = ProductMove.Kind.SHIPMENT, -1
    quantity = QuantityField(label=_("Quantity"), decimal_places=0)

    class Meta:
        model = ProductMove
        fields = ["date", "product", "quantity", "note"]
        widgets = {"date": DATE_WIDGET, "product": forms.Select(attrs=SEARCHABLE)}
        labels = {"note": _("Customer / note")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
        self.fields["date"].initial = timezone.localdate()


class StockCountForm(BootstrapFormMixin, forms.Form):
    """A physical stock count: the books are corrected to the counted quantity."""

    date = forms.DateField(label=_("Date"), widget=DATE_WIDGET)
    item = forms.ModelChoiceField(
        label=_("Item"), queryset=Material.objects.none(), widget=forms.Select(attrs=SEARCHABLE)
    )
    counted = QuantityField(label=_("Counted quantity"), min_value=Decimal(0))
    note = forms.CharField(label=_("Note"), required=False, max_length=255)

    def __init__(self, *args, model, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = model.objects.filter(is_active=True)
        self.fields["item"].label = model._meta.verbose_name.capitalize()
        self.fields["date"].initial = timezone.localdate()


class MaterialForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Material
        fields = ["name", "unit", "kind", "shelf_life", "min_stock", "is_active"]


class ProductForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "sales_name", "volume", "position", "is_active"]


class RecipeLineForm(BootstrapFormMixin, forms.ModelForm):
    quantity_per_unit = NormField(label=_("Norm per unit"), min_value=Decimal("0.0000000001"))
    allowed_loss = NormField(
        label=_("Allowed loss, %"), max_digits=5, decimal_places=2, max_value=Decimal(100), required=False
    )

    class Meta:
        model = RecipeLine
        fields = ["material", "quantity_per_unit", "allowed_loss", "position"]
        widgets = {"material": forms.Select(attrs=SEARCHABLE)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = Material.objects.filter(is_active=True)

    def clean_allowed_loss(self):
        value = self.cleaned_data.get("allowed_loss")
        return DEFAULT_ALLOWED_LOSS if value is None else value


RecipeFormSet = inlineformset_factory(
    Product, RecipeLine, form=RecipeLineForm, extra=2, can_delete=True, min_num=1, validate_min=True
)


class CalculationForm(BootstrapFormMixin, forms.Form):
    """The production calculation of one product and day: how much was made, and from what."""

    quantity = forms.IntegerField(
        label=_("Finished product, units"),
        min_value=1,
        widget=forms.NumberInput(attrs={"inputmode": "numeric", "min": 1, "data-calc": "quantity"}),
    )
    batch_quantity = forms.IntegerField(
        label=_("Brewed, units"),
        min_value=1,
        required=False,
        help_text=_("Drink prepared, in units; raw materials are written off by it. Empty: same as finished."),
        widget=forms.NumberInput(attrs={"inputmode": "numeric", "min": 1, "data-calc": "batch"}),
    )


class CalculationLineForm(BootstrapFormMixin, forms.Form):
    """A line of the calculation: raw materials take their norm per 1000 l, packaging per unit."""

    material = forms.ModelChoiceField(
        label=_("Material"),
        queryset=Material.objects.none(),
        widget=forms.Select(attrs={**SEARCHABLE, "data-calc": "material"}),
    )
    norm = NormField(label=_("Norm"), widget=forms.TextInput(attrs={"data-calc": "norm"}))
    defect = NormField(
        label=_("Production defect"),
        max_digits=18,
        decimal_places=4,
        required=False,
        widget=forms.TextInput(attrs={"data-calc": "defect", "placeholder": "0"}),
    )
    allowed_loss = NormField(
        label=_("Allowed loss, %"),
        max_digits=5,
        decimal_places=2,
        max_value=Decimal(100),
        required=False,
        widget=forms.TextInput(attrs={"data-calc": "allowed", "placeholder": "2"}),
    )

    def __init__(self, *args, materials=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = materials if materials is not None else Material.objects.all()


class BaseCalculationLineFormSet(forms.BaseFormSet):
    def __init__(self, *args, product: Product, **kwargs):
        self.product = product
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        return {**super().get_form_kwargs(index), "materials": Material.objects.all()}

    def clean(self):
        seen = set()
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE") or not form.cleaned_data.get("material"):
                continue
            material = form.cleaned_data["material"]
            if material in seen:
                form.add_error("material", _("This material is already in the calculation."))
            seen.add(material)

    def per_unit(self, material: Material, norm: Decimal) -> Decimal:
        """Norm per unit (can): raw materials are entered per 1000 l of drink."""
        if material.kind == Material.Kind.RAW:
            norm = norm * self.product.volume / 1000
        return norm.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)

    def lines(self):
        from .services import CalculationLine

        return [
            CalculationLine(
                material=form.cleaned_data["material"],
                quantity_per_unit=self.per_unit(form.cleaned_data["material"], form.cleaned_data["norm"]),
                defect=form.cleaned_data.get("defect") or Decimal(0),
                allowed_loss=(
                    form.cleaned_data["allowed_loss"]
                    if form.cleaned_data.get("allowed_loss") is not None
                    else DEFAULT_ALLOWED_LOSS
                ),
            )
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE") and form.cleaned_data.get("material")
        ]

    @staticmethod
    def initial_for(product: Product, lines) -> list[dict]:
        """Initial rows from a run's or recipe's lines, with raw norms shown per 1000 l."""
        rows = []
        for line in lines:
            norm = line.quantity_per_unit
            if line.material.kind == Material.Kind.RAW:
                norm = (norm * 1000 / product.volume).quantize(Decimal("0.0000001"), rounding=ROUND_HALF_UP)
            rows.append(
                {
                    "material": line.material,
                    "norm": plain(norm),
                    "defect": plain(line.defect) if line.defect else "",
                    "allowed_loss": plain(line.allowed_loss),
                }
            )
        return rows


CalculationLineFormSet = formset_factory(
    CalculationLineForm, formset=BaseCalculationLineFormSet, extra=0, can_delete=True, min_num=1, validate_min=True
)


MAX_WORKBOOK_SIZE = 20 * 1024 * 1024
XLSX_WIDGET = forms.ClearableFileInput(attrs={"accept": ".xlsx"})


class WorkbookField(forms.FileField):
    """An uploaded .xlsx workbook; it is read in memory and never stored on the server."""

    default_validators = [FileExtensionValidator(["xlsx"])]

    def __init__(self, **kwargs):
        super().__init__(widget=XLSX_WIDGET, **kwargs)

    def clean(self, *args, **kwargs):
        file = super().clean(*args, **kwargs)
        if file and file.size > MAX_WORKBOOK_SIZE:
            raise forms.ValidationError(_("The file is too large (over 20 MB)."))
        return file


class ImportForm(BootstrapFormMixin, forms.Form):
    """Production data from Excel: the report workbook (Сбор) and the finance workbook (Warehouse)."""

    recipes = WorkbookField(
        label=_("Report workbook (sheet Сбор)"),
        help_text=_("Recipes, and the materials list with stock in columns W–AA."),
    )
    warehouse = WorkbookField(
        label=_("Finance workbook (sheet Warehouse)"),
        help_text=_("Products and units produced per day."),
    )
    stock_date = forms.DateField(
        label=_("Stock counted on"),
        widget=DATE_WIDGET,
        help_text=_("Date of the stock column in Сбор; it becomes the opening balance."),
    )
    dry_run = forms.BooleanField(label=_("Only check, don't save"), required=False, initial=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stock_date"].initial = timezone.localdate()


class RecipeUploadForm(BootstrapFormMixin, forms.Form):
    """A recipes workbook (from the Download Excel button or export_recipes) to load."""

    file = WorkbookField(label=_("Recipes workbook (.xlsx)"))
    dry_run = forms.BooleanField(label=_("Only check, don't save"), required=False, initial=True)


class MaterialUploadForm(BootstrapFormMixin, forms.Form):
    """Materials from an uploaded workbook or the project's file, with their stock as opening balances if asked."""

    file = WorkbookField(label=_("Materials workbook (.xlsx)"), required=False)
    opening = forms.BooleanField(
        label=_("Load the «Остаток» column as opening balances"),
        required=False,
        help_text=_(
            "Only for materials with no stock movements yet. Tick it only if the file's stock is a real count."
        ),
    )
    opening_date = forms.DateField(label=_("Opening balance date"), widget=DATE_WIDGET, required=False)
    dry_run = forms.BooleanField(label=_("Only check, don't save"), required=False, initial=True)

    def __init__(self, *args, use_seed=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.use_seed = use_seed  # the project's file instead of an upload
        self.fields["opening_date"].initial = timezone.localdate()

    def clean(self):
        data = super().clean()
        if not self.use_seed and not data.get("file") and "file" not in self.errors:
            self.add_error("file", _("Choose a file, or load the project's file."))
        if data.get("opening") and not data.get("opening_date"):
            self.add_error("opening_date", _("Give the date of the stock count."))
        return data
