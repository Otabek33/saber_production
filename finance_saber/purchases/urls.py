from django.urls import path

from . import views

app_name = "purchases"
urlpatterns = [
    path("vendors/", views.vendors, name="vendors"),
    path("bills/", views.bills, name="bills"),
    path("bills/new/", views.bill_new, name="bill_new"),
    path("bills/<int:pk>/cancel/", views.bill_cancel, name="bill_cancel"),
    path("payments/", views.payments, name="payments"),
]
