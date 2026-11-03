from django.urls import path, reverse_lazy
from django.views.generic import RedirectView

from . import views

app_name = "sales"
urlpatterns = [
    path("", RedirectView.as_view(pattern_name="sales:plan"), name="index"),
    path("plan/", views.plan, name="plan"),
    path("plan/import/", views.import_plan, name="import"),
    # The old daily sales, debts and clients pages: their work moved to shipments, settlements and companies.
    path("daily/", RedirectView.as_view(pattern_name="production:shipments"), name="daily"),
    path("import/", RedirectView.as_view(pattern_name="production:shipments_import")),
    path("debts/", RedirectView.as_view(pattern_name="settlements:balances"), name="debts"),
    path("debts/<str:kind>/<int:pk>/", RedirectView.as_view(url=reverse_lazy("settlements:balances"))),
    path("clients/", RedirectView.as_view(pattern_name="cashbook:companies"), name="clients"),
]
