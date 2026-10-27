from django.urls import path

from . import views

app_name = "cashbook"
urlpatterns = [
    path("", views.index, name="index"),
    path("handbook/", views.HandbookView.as_view(), name="handbook"),
    path("handbook/export/", views.handbook_export, name="handbook_export"),
    path("handbook/project-file/", views.handbook_seed, name="handbook_seed"),
    path("handbook/import/", views.handbook_import, name="handbook_import"),
    path("handbook/articles/add/", views.ArticleCreateView.as_view(), name="article_add"),
    path("handbook/articles/<int:pk>/edit/", views.ArticleUpdateView.as_view(), name="article_edit"),
    path("handbook/categories/add/", views.CategoryCreateView.as_view(), name="category_add"),
    path("handbook/categories/<int:pk>/edit/", views.CategoryUpdateView.as_view(), name="category_edit"),
    path("import/", views.workbook_import, name="workbook_import"),
    path("counterparties/", views.CounterpartyListView.as_view(), name="counterparties"),
    path("counterparties/export/", views.counterparty_export, name="counterparty_export"),
    path("counterparties/project-file/", views.counterparty_seed, name="counterparty_seed"),
    path("counterparties/import/", views.counterparty_import, name="counterparty_import"),
    path("counterparties/add/", views.CounterpartyCreateView.as_view(), name="counterparty_add"),
    path("counterparties/<int:pk>/edit/", views.CounterpartyUpdateView.as_view(), name="counterparty_edit"),
    path("regions/add/", views.RegionCreateView.as_view(), name="region_add"),
    path("regions/<int:pk>/edit/", views.RegionUpdateView.as_view(), name="region_edit"),
    # Desk pages last: their slug would otherwise match the fixed paths above.
    path("<slug:desk>/", views.OperationListView.as_view(), name="operations"),
    path("<slug:desk>/statement/", views.upload_statement, name="statement_upload"),
    path("<slug:desk>/<int:pk>/edit/", views.OperationUpdateView.as_view(), name="operation_edit"),
    path("<slug:desk>/<int:pk>/cancel/", views.toggle_cancelled, name="operation_cancel"),
]
