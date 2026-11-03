from django import forms
from django.core.validators import FileExtensionValidator
from django.utils.translation import gettext_lazy as _

from finance_saber.core.forms import BootstrapFormMixin

MAX_WORKBOOK_SIZE = 20 * 1024 * 1024


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


class PlanUploadForm(BootstrapFormMixin, forms.Form):
    file = WorkbookField(
        label=_("Plan workbook (.xlsx)"),
        help_text=_("A sheet like “Plan 2026”: blocks “план реализации” and “план производства” by month."),
    )
    dry_run = forms.BooleanField(label=_("Only check, don't save"), required=False)
