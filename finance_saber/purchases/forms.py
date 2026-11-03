from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from finance_saber.cashbook.forms import MoneyField
from finance_saber.cashbook.models import Company
from finance_saber.core.forms import BootstrapFormMixin

from .models import Bill


class BillForm(BootstrapFormMixin, forms.ModelForm):
    amount = MoneyField(label=_("Amount"), help_text=_("With VAT, in UZS."))

    class Meta:
        model = Bill
        fields = ["supplier", "date", "number", "description", "amount"]
        widgets = {
            "supplier": forms.Select(attrs={"data-searchable": ""}),
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = Company.objects.filter(is_own=False).order_by("name")
        self.fields["supplier"].empty_label = _("Choose a supplier")
        self.initial["date"] = self.initial.get("date") or timezone.localdate()

    def clean_number(self):
        return " ".join(self.cleaned_data["number"].split()).upper()  # capitals: one number on any database

    def clean(self):
        cleaned = super().clean()
        supplier, number = cleaned.get("supplier"), cleaned.get("number")
        if supplier and number and Bill.objects.posted().filter(supplier=supplier, number=number).exists():
            self.add_error("number", _("This supplier's bill № %(number)s is already entered.") % {"number": number})
        return cleaned
