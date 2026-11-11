"""Forms of goods in transit: a supplier order with its lines, and receiving it into stock."""

import datetime
from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from finance_saber.cashbook.forms import MoneyField
from finance_saber.cashbook.models import Company
from finance_saber.core.forms import BootstrapFormMixin

from .forms import (
    DATE_WIDGET,
    SEARCHABLE,
    BaseDocumentLineFormSet,
    PricedLineMixin,
    QuantityField,
    price_field,
    rate_on,
)
from .models import QUANTITY_STEP, Material, MaterialOrder, MaterialOrderLine, MaterialReceipt
from .periods import OpenPeriodFormMixin


def plain(quantity: Decimal) -> str:
    """A quantity as people write it in a message: 1000, 12.5 (not 1E+3, 12.5000)."""
    return format(quantity.normalize(), "f")


class MaterialOrderForm(BootstrapFormMixin, forms.ModelForm):
    """The order's header: the supplier, when it was ordered and is expected, its currency and status.

    The arrival is the expected date, or the order date plus the delivery time in days.
    Received and cancelled are set by their own actions, not here.
    """

    lead_days = forms.IntegerField(
        label=_("Delivery time, days"),
        required=False,
        min_value=0,
        max_value=730,
        help_text=_("Instead of the date: the expected arrival is the order date plus these days."),
    )

    class Meta:
        model = MaterialOrder
        fields = ["supplier", "number", "order_date", "lead_days", "expected_date", "currency", "status", "note"]
        widgets = {
            "supplier": forms.Select(attrs={**SEARCHABLE, "data-choose-first": ""}),
            "order_date": DATE_WIDGET,
            "expected_date": DATE_WIDGET,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = Company.objects.filter(is_own=False).order_by("name")
        self.fields["supplier"].empty_label = _("Choose a supplier")
        # Dense form: the hints are placeholders (and on the labels), not lines under the fields.
        self.fields["number"].widget.attrs["placeholder"] = _("empty: numbered")
        self.fields["lead_days"].widget.attrs["placeholder"] = _("e.g. 45")
        self.fields["note"].widget.attrs["placeholder"] = _("e.g. container from China")
        if self.instance.status == MaterialOrder.Status.PARTIAL:
            del self.fields["status"]  # set by its receipts: partly received
        else:
            self.fields["status"].choices = [
                (value, label) for value, label in MaterialOrder.Status.choices if value in MaterialOrder.ON_THE_WAY
            ]
        self.fields["expected_date"].required = False
        self.initial["order_date"] = self.initial.get("order_date") or timezone.localdate()

    def clean_number(self):
        return " ".join(self.cleaned_data["number"].split()).upper()

    def clean(self):
        cleaned = super().clean()
        ordered, expected, days = cleaned.get("order_date"), cleaned.get("expected_date"), cleaned.get("lead_days")
        if ordered and days is not None:
            expected = cleaned["expected_date"] = ordered + datetime.timedelta(days=days)
        if not expected:
            self.add_error("expected_date", _("Give the expected arrival, or the delivery time in days."))
        elif ordered and expected < ordered:
            self.add_error("expected_date", _("The goods can't arrive before they are ordered."))
        return cleaned


class OrderLineForm(PricedLineMixin, BootstrapFormMixin, forms.ModelForm):
    quantity = QuantityField(
        label=_("Quantity"), min_value=QUANTITY_STEP, widget=forms.TextInput(attrs={"data-number": "4"})
    )
    price = price_field()

    class Meta:
        model = MaterialOrderLine
        fields = ["material", "quantity", "price", "amount", "status", "expected_date"]
        field_classes = {"amount": MoneyField}
        widgets = {
            "material": forms.Select(attrs=SEARCHABLE),
            "amount": forms.TextInput(),
            "expected_date": DATE_WIDGET,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = Material.objects.filter(is_active=True)
        self._setup_price()  # the price may come later


class BaseOrderLineFormSet(BaseDocumentLineFormSet):
    item_field = "material"
    duplicate_message = _("%(item)s is already in this order: add its quantity to one line.")

    def clean(self):
        super().clean()
        # What has arrived stays: a line can't go below its received quantity, nor be removed.
        kept_away = []
        for form in self.forms:
            line = form.instance
            if not line.pk or not (received := line.received):
                continue
            if self._should_delete_form(form):  # a removed row's own errors would be ignored: the formset's
                kept_away.append(_("%(item)s has arrived in part: it can't be removed.") % {"item": line.material})
            elif form.cleaned_data.get("quantity") is not None and form.cleaned_data["quantity"] < received:
                form.add_error("quantity", _("%(received)s has arrived already.") % {"received": plain(received)})
        if kept_away:
            raise forms.ValidationError(kept_away)


OrderLineFormSet = inlineformset_factory(
    MaterialOrder,
    MaterialOrderLine,
    form=OrderLineForm,
    formset=BaseOrderLineFormSet,
    extra=1,  # one empty row; "Add a line" adds more
    can_delete=True,
    min_num=1,
    validate_min=True,
)


class ReceiveOrderForm(OpenPeriodFormMixin, BootstrapFormMixin, forms.Form):
    """What arrived: the receipt's date and the supplier's invoice number, the rate to UZS, and per line the quantity.

    Each line still to come has a field (line_<id>), empty: only what really came is typed
    (the page's "all" buttons fill in what is left). Empty or 0: that line didn't come.
    Nothing is assumed: not the quantities, nor the supplier's invoice number.
    """

    date = forms.DateField(label=_("Date received"), widget=DATE_WIDGET)
    number = forms.CharField(
        label=_("Registration number"), max_length=50, help_text=_("The number of the supplier's invoice or waybill.")
    )
    rate = MoneyField(
        label=_("Rate to UZS"),
        required=False,
        help_text=_("UZS per 1 unit of the order's currency, for the receipt's amounts."),
    )

    def __init__(self, *args, order: MaterialOrder, lines, **kwargs):
        super().__init__(*args, **kwargs)
        self.order = order
        self.lines = [line for line in lines if line.remaining > 0]
        today = timezone.localdate()
        self.initial.setdefault("date", today)
        self.fields["number"].widget.attrs["placeholder"] = _("the supplier's invoice or waybill")
        if order.currency == MaterialOrder.Currency.UZS:
            del self.fields["rate"]
        elif order.currency == MaterialOrder.Currency.USD:
            self.initial.setdefault("rate", rate_on(today))
        for line in self.lines:
            name = f"line_{line.pk}"
            self.fields[name] = QuantityField(
                label=str(line.material), required=False, min_value=Decimal(0),
                widget=forms.TextInput(attrs={"data-number": "4", "placeholder": "0"}),
            )  # fmt: skip
            # The delivery's "good until" from its label: not required.
            self.fields[f"best_before_{line.pk}"] = forms.DateField(
                label=_("Good until"), required=False, widget=DATE_WIDGET
            )

    def line_fields(self):
        """(order line, its quantity field, its "good until" field) for the template's table."""
        return [(line, self[f"line_{line.pk}"], self[f"best_before_{line.pk}"]) for line in self.lines]

    def best_before(self, line) -> datetime.date | None:
        return self.cleaned_data.get(f"best_before_{line.pk}")

    def arrived(self) -> list[tuple[MaterialOrderLine, Decimal]]:
        return [
            (line, self.cleaned_data[f"line_{line.pk}"])
            for line in self.lines
            if self.cleaned_data.get(f"line_{line.pk}")
        ]

    def clean_number(self):
        return " ".join(self.cleaned_data["number"].split()).upper()

    def clean(self):
        cleaned = super().clean()
        # What arrives never adds up to more than was ordered: at most what is still to come.
        for line in self.lines:
            name = f"line_{line.pk}"
            if (quantity := cleaned.get(name)) and quantity > line.remaining:
                self.add_error(name, _("At most %(left)s: %(ordered)s ordered, %(received)s arrived already.") % {
                    "left": plain(line.remaining), "ordered": plain(line.quantity), "received": plain(line.received),
                })  # fmt: skip
        received = cleaned.get("date")
        for line in self.lines:
            name = f"best_before_{line.pk}"
            if received and cleaned.get(name) and cleaned[name] < received:
                self.add_error(name, _("Earlier than the receipt's date: check the date on the label."))
        if not self.errors and not self.arrived():
            raise forms.ValidationError(_("Give the quantity of at least one material that arrived."))
        if "rate" in self.fields and not cleaned.get("rate") and any(line.amount for line, _q in self.arrived()):
            self.add_error("rate", _("Give the rate: the order's amounts are in %(currency)s.") % {
                "currency": self.order.currency
            })  # fmt: skip
        number = cleaned.get("number")
        if number and MaterialReceipt.objects.posted().filter(supplier=self.order.supplier, number=number).exists():
            self.add_error(
                "number", _("This supplier's document № %(number)s is already entered.") % {"number": number}
            )
        return cleaned
