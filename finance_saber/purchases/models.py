"""Purchases: what suppliers bill us for.

Materials received come with their amounts on Production › Material receipts; a Bill
is everything else a supplier charges (services, transport, rent, repairs). Both are
what we owe the supplier, and the cash book's payments to it settle them
(settlements/ledger.py).
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

MONEY = {"max_digits": 18, "decimal_places": 2}


class BillQuerySet(models.QuerySet):
    def posted(self):
        return self.filter(is_cancelled=False)


class Bill(models.Model):
    """A supplier's bill (счёт-фактура) for services or anything that isn't a material received.

    A wrong bill isn't deleted: it is cancelled, and stops counting.
    """

    date = models.DateField(_("date"), db_index=True)
    supplier = models.ForeignKey("cashbook.Company", models.PROTECT, related_name="bills", verbose_name=_("supplier"))
    number = models.CharField(_("bill number"), max_length=50, help_text=_("The number on the supplier's bill."))
    description = models.CharField(_("what for"), max_length=255, help_text=_("e.g. transport, rent, repairs."))
    amount = models.DecimalField(
        _("amount"), **MONEY, validators=[MinValueValidator(Decimal("0.01"))], help_text=_("With VAT, in UZS.")
    )
    is_cancelled = models.BooleanField(_("cancelled"), default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("created by")
    )
    created_at = models.DateTimeField(_("created"), auto_now_add=True)

    objects = BillQuerySet.as_manager()

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = _("bill")
        verbose_name_plural = _("bills")
        constraints = [
            # The same bill of the same supplier entered twice is a duplicate.
            models.UniqueConstraint(
                fields=["supplier", "number"], condition=Q(is_cancelled=False), name="unique_bill_number_per_supplier"
            )
        ]

    def __str__(self):
        return f"№ {self.number} {self.date:%d.%m.%Y} {self.supplier}"

    def get_absolute_url(self):
        return reverse("purchases:bills") + f"?supplier={self.supplier_id}"
