"""Django Admin : journal d'historique (traçabilité) en LECTURE SEULE, réservé aux superutilisateurs."""
from django.contrib import admin
from django.contrib.admin.models import LogEntry

from accounts.phone import mask_phone
from dashboard.audit import EXPORT_TAG

# En-têtes du site : voir dashboard/admin_site.py (AccimapAdminSite).


@admin.register(LogEntry)
class LogEntryAdmin(admin.ModelAdmin):
    list_display = ("action_time", "actor", "action_type", "content_type", "object_repr", "message")
    list_filter = ("action_flag", "content_type", ("action_time", admin.DateFieldListFilter))
    search_fields = ("object_repr", "change_message")
    date_hierarchy = "action_time"
    list_select_related = ("user", "content_type")
    list_per_page = 50
    ordering = ("-action_time",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Utilisateur")
    def actor(self, obj):
        return mask_phone(obj.user.phone_number) if obj.user_id else "—"

    @admin.display(description="Action")
    def action_type(self, obj):
        if (obj.change_message or "").startswith(EXPORT_TAG):
            return "Export"
        return obj.get_action_flag_display()

    @admin.display(description="Détail")
    def message(self, obj):
        return obj.get_change_message()
