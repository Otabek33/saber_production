from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "sales"
urlpatterns = [
    path("", RedirectView.as_view(pattern_name="sales:daily"), name="index"),
    path("daily/", views.daily, name="daily"),
    path("<int:pk>/edit/", views.sale_edit, name="sale_edit"),
    path("<int:pk>/cancel/", views.sale_cancel, name="sale_cancel"),
    path("import/", views.import_excel, name="import"),
    path("debts/", views.debts, name="debts"),
    path("debts/<str:kind>/<int:pk>/", views.statement, name="statement"),
    path("clients/", views.ClientListView.as_view(), name="clients"),
    path("clients/<int:pk>/edit/", views.ClientUpdateView.as_view(), name="client_edit"),
    path("plan/", views.plan, name="plan"),
]
