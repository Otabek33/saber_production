"""Root URL configuration."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth.decorators import login_not_required
from django.urls import include, path
from django.views.i18n import set_language

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("accounts/", include("finance_saber.users.urls")),
    path("cash/", include("finance_saber.cashbook.urls")),
    path("production/", include("finance_saber.production.urls")),
    path("sales/", include("finance_saber.sales.urls")),
    path("planning/", include("finance_saber.planning.urls")),
    # Language switcher target; public so the sign-in page can switch too.
    path("i18n/setlang/", login_not_required(set_language), name="set_language"),
    path("", include("finance_saber.core.urls")),
]

if settings.DEBUG:
    # Serve user-uploaded media in development only.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

    if getattr(settings, "USE_DEBUG_TOOLBAR", False):
        from debug_toolbar.toolbar import debug_toolbar_urls

        urlpatterns += debug_toolbar_urls()
