from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "production"
urlpatterns = [
    path("", RedirectView.as_view(pattern_name="production:daily"), name="index"),
    path("daily/", views.daily, name="daily"),
    path("daily/<str:date>/<int:product_pk>/", views.calculation, name="calculation"),
    path("journal/", views.journal, name="journal"),
    path("materials/", views.MaterialStockView.as_view(), name="materials"),
    path("materials/add/", views.MaterialCreateView.as_view(), name="material_add"),
    path("materials/<int:pk>/edit/", views.MaterialUpdateView.as_view(), name="material_edit"),
    path("materials/receipt/", views.material_receipt, name="material_receipt"),
    path("materials/write-off/", views.material_write_off, name="material_write_off"),
    path("materials/export/", views.material_export, name="material_export"),
    path("materials/project-file/", views.material_seed, name="material_seed"),
    path("materials/import/", views.material_import, name="material_import"),
    path("products/", views.ProductStockView.as_view(), name="products"),
    path("products/add/", views.ProductCreateView.as_view(), name="product_add"),
    path("products/shipment/", views.shipment, name="shipment"),
    path("count/<str:kind>/", views.stock_count, name="stock_count"),
    path("recipes/", views.RecipeListView.as_view(), name="recipes"),
    path("recipes/<int:pk>/", views.recipe_edit, name="recipe_edit"),
    path("recipes/export/", views.recipe_export, name="recipe_export"),
    path("recipes/project-file/", views.recipe_seed, name="recipe_seed"),
    path("recipes/import/", views.recipe_import, name="recipe_import"),
    path("moves/", views.moves, name="moves"),
    path("moves/<str:target>/<int:pk>/cancel/", views.cancel_move, name="move_cancel"),
    path("import/", views.import_excel, name="import"),
]
