"""Settlements with partners (взаиморасчёты): what each company owes us, or we owe it.

Nothing here is a balance kept up to date: a partner's balance is worked out from
the documents and the money (ledger.py). The only thing stored is where counting
starts: a partner's opening balance on a date, taken from the books kept before.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

MONEY = {"max_digits": 18, "decimal_places": 2}


class OpeningBalance(models.Model):
    """A partner's balance at the start of ``date``: positive, it owes us; negative, we owe it.

    Documents and payments before that day are not counted for this partner (they are
    in this figure). A partner is a head company; its filials count with it.
    """

    company = models.OneToOneField(
        "cashbook.Company", models.CASCADE, related_name="opening_balance", verbose_name=_("partner")
    )
    date = models.DateField(_("date"), help_text=_("The balance at the start of this day."))
    amount = models.DecimalField(
        _("opening balance"), **MONEY, help_text=_("Positive: the partner owes us. Negative: we owe the partner.")
    )
    note = models.CharField(_("note"), max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("created by")
    )
    updated_at = models.DateTimeField(_("updated"), auto_now=True)

    class Meta:
        ordering = ["company__name"]
        verbose_name = _("opening balance")
        verbose_name_plural = _("opening balances")

    def __str__(self):
        return f"{self.company}: {self.amount} ({self.date:%d.%m.%Y})"
