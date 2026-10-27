import re
from decimal import Decimal

from django import forms
from django.core.validators import FileExtensionValidator
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from finance_saber.core.forms import BootstrapFormMixin

from .models import Account, Article, ArticleKind, Category, Counterparty, DeskKind, Kind, Operation, Region

# The whole finance workbook may be uploaded (Situation.xlsx is ~1.2 MB); a daily bank
# statement is a few hundred KB. nginx allows 25 MB (deploy/nginx/finance_saber.conf).
MAX_WORKBOOK_SIZE = 20 * 1024 * 1024


class MoneyField(forms.DecimalField):
    """A decimal field that accepts amounts as people type them.

    "1 850 000", "1,850,000.00", "1850000,50" and "1.850.000,50" are all read
    correctly: spaces are ignored, and when both "," and "." appear the last one
    is the decimal separator. A lone "," followed by one or two digits is a
    decimal comma; otherwise it is a thousands separator.
    """

    widget = forms.TextInput  # number inputs reject spaces and commas

    def __init__(self, **kwargs):
        kwargs.setdefault("max_digits", 18)
        kwargs.setdefault("decimal_places", 2)
        kwargs.setdefault("min_value", Decimal("0.01"))
        super().__init__(**kwargs)

    def widget_attrs(self, widget):
        attrs = super().widget_attrs(widget)
        attrs.pop("step", None)
        attrs.update({"inputmode": "decimal", "autocomplete": "off"})
        return attrs

    def to_python(self, value):
        if isinstance(value, str):
            value = re.sub(r"[\s  ']", "", value)
            if "," in value and "." in value:
                decimal_sep = "," if value.rfind(",") > value.rfind(".") else "."
                thousands_sep = "." if decimal_sep == "," else ","
                value = value.replace(thousands_sep, "").replace(decimal_sep, ".")
            elif value.count(",") == 1 and re.search(r",\d{1,2}$", value):
                value = value.replace(",", ".")
            else:
                value = value.replace(",", "")
        return super().to_python(value)


class OperationForm(BootstrapFormMixin, forms.ModelForm):
    amount = MoneyField(label=_("Amount"))
    to_amount = MoneyField(label=_("Amount received"), required=False)
    rate = MoneyField(label=_("Rate"), required=False, help_text=_("UZS per 1 USD."))

    class Meta:
        model = Operation
        fields = [
            "kind",
            "date",
            "category",
            "account",
            "amount",
            "to_account",
            "to_amount",
            "rate",
            "counterparty",
            "document_number",
            "note",
        ]
        widgets = {
            "kind": forms.RadioSelect,
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "note": forms.TextInput(attrs={"autocomplete": "off"}),
        }

    def __init__(self, *args, desk, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.desk = desk
        categories = Category.objects.for_desk(desk).filter(is_active=True, article__is_active=True)
        if self.instance.category_id:
            # Keep an operation's current category selectable even if it has since been deactivated.
            categories = categories | Category.objects.filter(pk=self.instance.category_id)
        self.fields["category"].queryset = categories.select_related("article")
        # Only this desk's accounts, and no transfers on a single-account desk. No blank
        # option for the type and account: one of them is always chosen.
        kinds = Kind.choices if desk.allows_transfers else [c for c in Kind.choices if c[0] != Kind.TRANSFER]
        self.fields["kind"].choices = kinds
        self.fields["account"].choices = desk.account_choices
        self.fields["to_account"].choices = [("", "—"), *desk.account_choices]
        self.show_account = len(desk.accounts) > 1
        self.show_rate = Account.CASH_USD in desk.accounts  # the rate only matters where USD is kept
        if desk.is_bank:
            self.fields["counterparty"].queryset = Counterparty.objects.select_related("article")
            self.fields["category"].label = _("Article")  # bank lines are classified by article
        else:
            del self.fields["counterparty"], self.fields["document_number"]

    def category_groups(self):
        """Active categories grouped by article, for the <select> with <optgroup>s and type filtering."""
        groups = {}
        for category in self.fields["category"].queryset.order_by(*Category._meta.ordering):
            groups.setdefault(category.article, []).append(category)
        return groups.items()


class CounterpartyForm(BootstrapFormMixin, forms.ModelForm):
    apply_to_existing = forms.BooleanField(
        label=_("Also re-file this counterparty's existing bank lines"),
        required=False,
        initial=True,
    )

    class Meta:
        model = Counterparty
        fields = ["reference", "name", "inn", "account_number", "article", "region"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Articles usable on a bank desk (or on every desk), excluding transfers.
        self.fields["article"].queryset = (
            Article.objects.filter(is_active=True)
            .exclude(kind=ArticleKind.TRANSFER)
            .filter(Q(desks__kind=DeskKind.BANK) | Q(desks__isnull=True))
            .distinct()
        )
        self.fields["region"].queryset = Region.objects.filter(is_active=True)
        if self.instance.pk:
            # The reference identifies the counterparty in statements; changing it would break matching.
            self.fields["reference"].disabled = True

    def clean(self):
        cleaned = super().clean()
        if not self.instance.pk and cleaned.get("reference") and not cleaned.get("name"):
            cleaned["name"] = Counterparty.parse_reference(cleaned["reference"])["name"]
        return cleaned


class RegionForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Region
        fields = ["name", "manager", "is_active"]


class StatementUploadForm(BootstrapFormMixin, forms.Form):
    file = forms.FileField(
        label=_("Bank statement (.xlsx)"),
        validators=[FileExtensionValidator(["xlsx"])],
        widget=forms.ClearableFileInput(attrs={"accept": ".xlsx"}),
    )

    def clean_file(self):
        file = self.cleaned_data["file"]
        if file.size > MAX_WORKBOOK_SIZE:
            raise forms.ValidationError(_("The file is too large (over 20 MB)."))
        return file


class WorkbookUploadForm(BootstrapFormMixin, forms.Form):
    """The finance workbook (Situation.xlsx), checked before it is imported into every cash desk."""

    file = forms.FileField(
        label=_("Finance workbook (.xlsx)"),
        validators=[FileExtensionValidator(["xlsx"])],
        widget=forms.ClearableFileInput(attrs={"accept": ".xlsx"}),
    )

    def clean_file(self):
        file = self.cleaned_data["file"]
        if file.size > MAX_WORKBOOK_SIZE:
            raise forms.ValidationError(_("The file is too large (over 20 MB)."))
        return file


class WorkbookImportForm(forms.Form):
    """The second step: import the checked workbook, deciding about counterparties not in the app yet."""

    ADD, SKIP = "add", "skip"
    counterparties = forms.ChoiceField(
        label=_("New counterparties"),
        choices=[
            (ADD, _("Add them automatically. Their lines go to “Не распределено” until you give them an article.")),
            (SKIP, _("Don't add them: I will create them myself first. Their lines are imported on the next upload.")),
        ],
        widget=forms.RadioSelect,
        initial=ADD,
        required=False,  # only asked when the workbook has new counterparties
    )
    update_changed = forms.BooleanField(
        label=_("Update these rows from Excel: Excel is the official record."),
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        required=False,  # only asked when rows imported before changed in Excel
    )

    def __init__(self, *args, can_add_counterparties=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.can_add_counterparties = can_add_counterparties
        if not can_add_counterparties:
            self.fields["counterparties"].choices = self.fields["counterparties"].choices[1:]
            self.fields["counterparties"].initial = self.SKIP

    @property
    def add_counterparties(self) -> bool:
        return self.can_add_counterparties and self.cleaned_data.get("counterparties") != self.SKIP


class ArticleForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Article
        fields = ["name", "kind", "desks", "position", "in_cash_flow", "is_active"]
        widgets = {"desks": forms.CheckboxSelectMultiple}


class CategoryForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Category
        fields = ["article", "name", "is_active"]


class LedgerFilterForm(BootstrapFormMixin, forms.Form):
    date_from = forms.DateField(label=_("From"), widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    date_to = forms.DateField(label=_("To"), widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    kind = forms.ChoiceField(label=_("Type"), choices=[("", _("All types")), *Kind.choices], required=False)
    article = forms.ModelChoiceField(
        label=_("Article"), queryset=Article.objects.all(), required=False, empty_label=_("All articles")
    )
    account = forms.ChoiceField(label=_("Account"), choices=[("", _("All accounts")), *Account.choices], required=False)
    q = forms.CharField(label=_("Search"), required=False, widget=forms.TextInput(attrs={"type": "search"}))

    region = forms.ModelChoiceField(
        label=_("Region"), queryset=Region.objects.all(), required=False, empty_label=_("All regions")
    )

    def __init__(self, *args, desk, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["article"].queryset = Article.objects.for_desk(desk)
        if not desk.is_bank:
            del self.fields["region"]
        self.fields["account"].choices = [("", _("All accounts")), *desk.account_choices]
        if not desk.allows_transfers:
            self.fields["kind"].choices = [c for c in self.fields["kind"].choices if c[0] != Kind.TRANSFER]
        self.show_account = len(desk.accounts) > 1

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("date_from") and cleaned.get("date_to") and cleaned["date_from"] > cleaned["date_to"]:
            raise forms.ValidationError(_("The start date is after the end date."))
        return cleaned


class HandbookUploadForm(BootstrapFormMixin, forms.Form):
    """A handbook workbook (from the Download Excel buttons or export_handbooks) to load."""

    file = forms.FileField(
        label=_("Handbook workbook (.xlsx)"),
        validators=[FileExtensionValidator(["xlsx"])],
        widget=forms.ClearableFileInput(attrs={"accept": ".xlsx"}),
    )
    dry_run = forms.BooleanField(label=_("Only check, don't save"), required=False, initial=True)

    def clean_file(self):
        file = self.cleaned_data["file"]
        if file.size > MAX_WORKBOOK_SIZE:
            raise forms.ValidationError(_("The file is too large (over 20 MB)."))
        return file
