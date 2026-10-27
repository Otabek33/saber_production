from django.contrib.auth.decorators import login_not_required
from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

from . import dashboard, navigation


@require_GET
def home(request: HttpRequest) -> HttpResponse:
    """The dashboard: the company's money, sales, production and plans for a chosen period."""
    period = dashboard.period_from(request.GET)
    return render(request, "core/home.html", dashboard.build(request.user, period))


class ComingSoonView(TemplateView):
    """Stand-in page for an ERP module that has a menu entry but no implementation yet."""

    template_name = "core/coming_soon.html"

    def get_context_data(self, **kwargs):
        section, item = navigation.find(self.request.resolver_match.view_name)
        return super().get_context_data(
            title=item.label if item else _("Coming soon"),
            section=section.label if section else "",
            **kwargs,
        )


@login_not_required
@require_GET
def health(request: HttpRequest) -> JsonResponse:
    """Liveness/readiness probe for load balancers and uptime monitors."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})
