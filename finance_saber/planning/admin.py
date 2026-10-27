from django.contrib import admin

from .models import CashPlan, CollectionPlan


@admin.register(CashPlan)
class CashPlanAdmin(admin.ModelAdmin):
    list_display = ["date", "article", "amount", "note", "updated_by"]
    list_filter = ["article"]
    date_hierarchy = "date"


@admin.register(CollectionPlan)
class CollectionPlanAdmin(admin.ModelAdmin):
    list_display = ["date", "region", "amount", "updated_by"]
    list_filter = ["region"]
    date_hierarchy = "date"
