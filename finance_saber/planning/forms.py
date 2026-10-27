from decimal import Decimal

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from finance_saber.cashbook.forms import MoneyField
from finance_saber.core.forms import BootstrapFormMixin


class ExchangeRateForm(BootstrapFormMixin, forms.Form):
    date = forms.DateField(label=_("Date"), widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    rate = MoneyField(label=_("UZS per 1 USD"), min_value=Decimal(1))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = timezone.localdate()
