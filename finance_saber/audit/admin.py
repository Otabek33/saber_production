from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    """Read-only: the log is written by the app, never edited by hand."""

    list_display = ["created_at", "username", "action", "model", "object_repr", "ip"]
    list_filter = ["action", "model"]
    search_fields = ["username", "object_repr", "ip"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
