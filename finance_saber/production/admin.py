from django.contrib import admin

from .models import Material, MaterialMove, Product, ProductAlias, ProductionRun, ProductMove, RecipeLine


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ["name", "unit", "kind", "shelf_life", "min_stock", "is_active"]
    list_filter = ["kind", "is_active"]
    search_fields = ["name"]


class RecipeLineInline(admin.TabularInline):
    model = RecipeLine
    extra = 0
    autocomplete_fields = ["material"]


class ProductAliasInline(admin.TabularInline):
    model = ProductAlias
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "sales_name", "volume", "position", "is_active"]
    list_editable = ["position", "is_active"]
    search_fields = ["name", "sales_name", "aliases__name"]
    inlines = [ProductAliasInline, RecipeLineInline]


@admin.register(ProductionRun)
class ProductionRunAdmin(admin.ModelAdmin):
    list_display = ["date", "product", "quantity", "affects_stock", "is_cancelled"]
    list_filter = ["product", "is_cancelled"]
    date_hierarchy = "date"

    def has_delete_permission(self, request, obj=None):
        return False  # cancel instead, so the history stays


class MoveAdmin(admin.ModelAdmin):
    list_display = ["date", "kind", "quantity", "note", "is_cancelled"]
    list_filter = ["kind", "is_cancelled"]
    date_hierarchy = "date"
    readonly_fields = ["run", "source", "created_by", "created_at"]

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MaterialMove)
class MaterialMoveAdmin(MoveAdmin):
    list_display = ["date", "material", *MoveAdmin.list_display[1:]]
    search_fields = ["material__name", "note"]


@admin.register(ProductMove)
class ProductMoveAdmin(MoveAdmin):
    list_display = ["date", "product", *MoveAdmin.list_display[1:]]
    search_fields = ["product__name", "note"]
