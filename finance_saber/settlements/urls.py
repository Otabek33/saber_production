from django.urls import path

from . import views

app_name = "settlements"
urlpatterns = [
    path("", views.balances, name="balances"),
    path("openings/export/", views.openings_export, name="openings_export"),
    path("openings/import/", views.openings_import, name="openings_import"),
    path("<int:pk>/", views.statement, name="statement"),
]
