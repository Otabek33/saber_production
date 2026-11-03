from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from finance_saber.cashbook.forms import MoneyField
from finance_saber.core.forms import BootstrapFormMixin

from .models import OpeningBalance


class SignedMoneyField(MoneyField):
    """An amount typed as people type it, which may be negative ("-1 250 000")."""

    def __init__(self, **kwargs):
        kwargs.setdefault("min_value", None)
        super().__init__(**kwargs)

    def to_python(self, value):
        if isinstance(value, str) and value.strip().startswith(("-", "−")):
            amount = super().to_python(value.strip().lstrip("-−"))
            return -amount if amount is not None else None
        return super().to_python(value)


class OpeningBalanceForm(BootstrapFormMixin, forms.ModelForm):
    amount = SignedMoneyField(
        label=_("Opening balance"), help_text=_("Positive: the partner owes us. Negative: we owe the partner.")
    )

    class Meta:
        model = OpeningBalance
        fields = ["date", "amount", "note"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def clean_amount(self):
        return self.cleaned_data["amount"] or Decimal(0)


class PeriodForm(forms.Form):
    """The акт сверки's period; both ends may be left open."""

    date_from = forms.DateField(required=False)
    date_to = forms.DateField(required=False)
