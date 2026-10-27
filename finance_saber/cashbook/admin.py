from django.contrib import admin

from .models import Article, CashDesk, Category, ExchangeRate, Operation


@admin.register(CashDesk)
class CashDeskAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "is_active"]
    prepopulated_fields = {"slug": ["name"]}


class CategoryInline(admin.TabularInline):
    model = Category
    extra = 0


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ["name", "kind", "position", "is_active"]
    list_filter = ["kind", "is_active"]
    list_editable = ["position", "is_active"]
    search_fields = ["name"]
    inlines = [CategoryInline]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "article", "is_active"]
    list_filter = ["article__kind", "article", "is_active"]
    search_fields = ["name", "article__name"]


@admin.register(Operation)
class OperationAdmin(admin.ModelAdmin):
    list_display = ["date", "desk", "kind", "category", "account", "amount", "to_account", "to_amount", "is_cancelled"]
    list_filter = ["desk", "kind", "account", "is_cancelled", "category__article"]
    date_hierarchy = "date"
    search_fields = ["note", "category__name"]
    autocomplete_fields = ["category"]
    readonly_fields = ["created_by", "created_at", "updated_at"]

    def has_delete_permission(self, request, obj=None):
        # Operations are cancelled, never deleted, so the ledger keeps its history.
        return False


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ["date", "rate"]
    date_hierarchy = "date"
