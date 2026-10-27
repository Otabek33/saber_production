from django.contrib import admin

from .models import Channel, Client, Plan, Sale


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    list_display = ["name", "is_export", "is_active"]
    list_editable = ["is_export", "is_active"]


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ["name", "payer", "region", "opening_balance", "is_active"]
    list_filter = ["is_active", "region"]
    search_fields = ["name", "payer__name"]
    raw_id_fields = ["payer"]


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ["date", "client", "product", "channel", "quantity", "amount", "source", "is_cancelled"]
    list_filter = ["source", "channel", "is_cancelled", "product"]
    search_fields = ["client__name", "product__name", "external_id"]
    date_hierarchy = "date"
    readonly_fields = ["affects_stock", "created_by", "created_at"]

    def has_delete_permission(self, request, obj=None):
        return False  # cancel instead, so stock and debts keep their history


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ["kind", "year", "month", "product", "quantity"]
    list_filter = ["kind", "year", "product"]
