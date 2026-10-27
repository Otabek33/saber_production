from decimal import Decimal

from django import forms
from django.core.validators import FileExtensionValidator
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from finance_saber.cashbook.forms import MoneyField
from finance_saber.cashbook.models import Counterparty
from finance_saber.core.forms import BootstrapFormMixin
from finance_saber.production.models import Product

from . import services
from .models import Channel, Client, Sale

DATE_WIDGET = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
SEARCHABLE = {"data-searchable": ""}  # production.js turns these selects into Select2
MAX_WORKBOOK_SIZE = 20 * 1024 * 1024


class ClientField(forms.CharField):
    """A client picked from the list (its id), or a new client's name typed in (Select2 "tags").

    Cleans to a Client, or to the new name as a string; the form creates that client on save,
    so an invalid form never leaves a stray client behind.
    """

    def to_python(self, value):
        value = super().to_python(value)
        if not value:
            return None
        if value.isdigit() and (client := Client.objects.filter(pk=value).first()):
            return client
        return services.client_for(value, create=False) or value


class SaleForm(BootstrapFormMixin, forms.ModelForm):
    """One shipment line typed in by hand."""

    client = ClientField(
        label=_("Client"),
        widget=forms.Select(attrs={**SEARCHABLE, "data-tags": "true"}),
        help_text=_("Pick a client, or type a new client's name."),
    )
    amount = MoneyField(label=_("Total price"), min_value=Decimal(0))  # 0: free goods

    class Meta:
        model = Sale
        fields = ["date", "product", "channel", "quantity", "amount", "note"]  # client: see ClientField
        widgets = {
            "date": DATE_WIDGET,
            "product": forms.Select(attrs=SEARCHABLE),
            "quantity": forms.NumberInput(attrs={"min": 1, "inputmode": "numeric"}),
        }

    field_order = ["date", "client", "product", "channel", "quantity", "amount", "note"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial.setdefault("client", str(self.instance.client_id))
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
        self.fields["channel"].queryset = Channel.objects.filter(is_active=True)
        self.fields["date"].initial = timezone.localdate()
        # The client select lists the clients; a typed name is kept as an extra option on errors.
        choices = [("", "")] + [(str(c.pk), c.name) for c in Client.objects.filter(is_active=True)]
        typed = self.data.get(self.add_prefix("client")) if self.is_bound else None
        if typed and not typed.isdigit():
            choices.append((typed, typed))
        self.fields["client"].widget.choices = choices

    def clean(self):
        cleaned = super().clean()
        client, product = cleaned.get("client"), cleaned.get("product")
        if isinstance(client, Client) and product and cleaned.get("date") and cleaned.get("amount") is not None:
            if services.is_duplicate(cleaned["date"], client, product, cleaned["amount"], exclude_pk=self.instance.pk):
                raise forms.ValidationError(
                    _(
                        "This sale is already recorded: the same product for the same total price "
                        "to the same client on that day."
                    ),
                    code="duplicate",
                )
        return cleaned

    def save(self, commit=True):
        client = self.cleaned_data["client"]
        self.instance.client = services.client_for(client) if isinstance(client, str) else client
        return super().save(commit=commit)


class WorkbookField(forms.FileField):
    """An uploaded .xlsx workbook; it is read in memory and never stored on the server."""

    default_validators = [FileExtensionValidator(["xlsx"])]

    def __init__(self, **kwargs):
        super().__init__(widget=forms.ClearableFileInput(attrs={"accept": ".xlsx"}), **kwargs)

    def clean(self, *args, **kwargs):
        file = super().clean(*args, **kwargs)
        if file and file.size > MAX_WORKBOOK_SIZE:
            raise forms.ValidationError(_("The file is too large (over 20 MB)."))
        return file


class SalesUploadForm(BootstrapFormMixin, forms.Form):
    file = WorkbookField(
        label=_("SmartUp export (.xlsx)"),
        help_text=_("Columns Дата отгрузки, Название проекта, ТМЦ, Клиент, Количество продажи, Сумма."),
    )
    dry_run = forms.BooleanField(label=_("Only check, don't save"), required=False)


class PlanUploadForm(BootstrapFormMixin, forms.Form):
    file = WorkbookField(
        label=_("Plan workbook (.xlsx)"),
        help_text=_("A sheet like “Plan 2026”: blocks “план реализации” and “план производства” by month."),
    )
    dry_run = forms.BooleanField(label=_("Only check, don't save"), required=False)


class ClientForm(BootstrapFormMixin, forms.ModelForm):
    opening_balance = MoneyField(label=_("Opening balance"), required=False, min_value=None)  # < 0: prepaid

    class Meta:
        model = Client
        fields = ["name", "payer", "region", "opening_balance", "is_active"]
        widgets = {"payer": forms.Select(attrs=SEARCHABLE)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payer"].queryset = Counterparty.objects.order_by("name")
        self.fields["opening_balance"].help_text = Client._meta.get_field("opening_balance").help_text

    def clean_opening_balance(self):
        return self.cleaned_data["opening_balance"] or 0
