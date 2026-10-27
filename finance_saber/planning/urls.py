from django.urls import path

from . import views

app_name = "planning"
urlpatterns = [
    path("cash/", views.cash_plan, name="cash"),
    path("collections/", views.collection_plan, name="collections"),
    path("rates/", views.exchange_rates, name="rates"),
]
