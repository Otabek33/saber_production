from django.urls import path

from . import views

app_name = "audit"
urlpatterns = [
    path("", views.events, name="events"),
    path("export/", views.export, name="export"),
]
