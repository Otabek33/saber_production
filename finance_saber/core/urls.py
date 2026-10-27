from django.urls import path

from . import views

app_name = "core"
urlpatterns = [
    path("", views.home, name="home"),
    path("health/", views.health, name="health"),
]

# Placeholder pages for ERP modules that are not built yet. As each module is
# implemented, replace its entry here with the module's own URLs and point the
# matching item in navigation.py at them.
COMING_SOON = [
    ("finance/accounts/", "accounts"),
    ("finance/journal/", "journal"),
    ("finance/banking/", "banking"),
    ("sales/invoices/", "invoices"),
    ("purchases/vendors/", "vendors"),
    ("purchases/bills/", "bills"),
    ("purchases/payments/", "payments_made"),
    ("reports/profit-and-loss/", "profit_and_loss"),
    ("reports/balance-sheet/", "balance_sheet"),
    ("reports/cash-flow/", "cash_flow"),
]
urlpatterns += [path(route, views.ComingSoonView.as_view(), name=name) for route, name in COMING_SOON]
