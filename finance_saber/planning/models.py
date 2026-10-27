"""Short-term money plans, compared with what actually happened.

* CashPlan — the payment calendar: money expected in or out on a day under a
  cash-flow article (the workbook's "1-week" sheet). The fact is the cash book.
* CollectionPlan — money expected from each sales region on a day (the "Sells
  plan" sheet). The fact is bank receipts from counterparties of that region.

Plans are amounts in UZS and stay editable; facts are always computed.
"""

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from finance_saber.cashbook.models import Article, Region

MONEY = {"max_digits": 18, "decimal_places": 2}


class CashPlan(models.Model):
    date = models.DateField(_("date"), db_index=True)
    article = models.ForeignKey(Article, models.PROTECT, related_name="plans", verbose_name=_("article"))
    amount = models.DecimalField(_("amount"), **MONEY, validators=[MinValueValidator(0)])
    note = models.CharField(_("note"), max_length=255, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("updated by")
    )
    updated_at = models.DateTimeField(_("updated"), auto_now=True)

    class Meta:
        ordering = ["date", "article"]
        verbose_name = _("cash plan")
        verbose_name_plural = _("cash plans")
        constraints = [models.UniqueConstraint(fields=["date", "article"], name="one_cash_plan_per_article_day")]

    def __str__(self):
        return f"{self.date:%d.%m.%Y} {self.article}: {self.amount}"


class CollectionPlan(models.Model):
    date = models.DateField(_("date"), db_index=True)
    region = models.ForeignKey(Region, models.PROTECT, related_name="collection_plans", verbose_name=_("region"))
    amount = models.DecimalField(_("amount"), **MONEY, validators=[MinValueValidator(0)])
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("updated by")
    )
    updated_at = models.DateTimeField(_("updated"), auto_now=True)

    class Meta:
        ordering = ["date", "region"]
        verbose_name = _("collection plan")
        verbose_name_plural = _("collection plans")
        constraints = [models.UniqueConstraint(fields=["date", "region"], name="one_collection_plan_per_region_day")]

    def __str__(self):
        return f"{self.date:%d.%m.%Y} {self.region}: {self.amount}"
