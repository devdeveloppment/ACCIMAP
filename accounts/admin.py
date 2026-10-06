"""Django Admin : utilisateurs. Réservé aux superutilisateurs (permissions « accounts.* » jamais données au staff)."""
from django.contrib import admin, messages
from django.db.models import Count

from accounts.models import User
from accounts.phone import mask_phone
from accounts.roles import (
    ROLE_LABELS, ROLE_STAFF, ROLE_SUPERUSER, ROLE_USER, get_report_permissions, get_role, set_role,
)
from dashboard.audit import CHANGE, log_action


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("phone_number", "role_display", "is_active", "date_joined", "last_login", "identified_reports")
    list_filter = ("is_active", "is_staff", "is_superuser", ("date_joined", admin.DateFieldListFilter))
    search_fields = ("phone_number",)
    ordering = ("-date_joined",)
    list_per_page = 25
    actions = ["activate", "deactivate", "make_user", "make_staff", "make_staff_readonly", "make_superuser"]

    fieldsets = (
        ("Compte", {"fields": ("phone_number", "is_active")}),
        ("Niveau d'accès (modifiable par les actions de la liste)",
         {"fields": ("role_display", "report_permissions_display", "is_staff", "is_superuser")}),
        ("Activité", {"fields": ("date_joined", "last_login", "identified_reports")}),
    )
    readonly_fields = ("phone_number", "role_display", "report_permissions_display", "is_staff", "is_superuser",
                       "date_joined", "last_login", "identified_reports")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_report_count=Count("reports"))

    # Les comptes se créent par OTP (ou createsuperuser) et se désactivent plutôt qu'ils ne se suppriment.
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Niveau d'accès")
    def role_display(self, obj):
        return ROLE_LABELS[get_role(obj)]

    @admin.display(description="Droits sur les signalements")
    def report_permissions_display(self, obj):
        rights = get_report_permissions(obj)
        granted = [label for key, label in (("view", "voir"), ("change", "modifier"), ("delete", "supprimer")) if rights[key]]
        return ", ".join(granted) or "aucun"

    @admin.display(description="Signalements identifiés", ordering="_report_count")
    def identified_reports(self, obj):
        return getattr(obj, "_report_count", obj.reports.count())

    # ---- Garde-fou : un superutilisateur ne se verrouille pas lui-même ---------------------------
    def save_model(self, request, obj, form, change):
        if obj.pk == request.user.pk and not obj.is_active:
            obj.is_active = True
            self.message_user(request, "Vous ne pouvez pas désactiver votre propre compte.", messages.ERROR)
        super().save_model(request, obj, form, change)
        if change and "is_active" in form.changed_data:
            log_action(request.user, obj, CHANGE, f"Compte {'activé' if obj.is_active else 'désactivé'}.",
                       repr_text=mask_phone(obj.phone_number))

    # ---- Actions ---------------------------------------------------------------------------------
    def _apply(self, request, queryset, description, operation):
        done, skipped_self = 0, False
        for user in queryset:
            if user.pk == request.user.pk:
                skipped_self = True
                continue
            before = f"{ROLE_LABELS[get_role(user)]}, {'actif' if user.is_active else 'inactif'}"
            operation(user)
            user = User.objects.get(pk=user.pk)
            log_action(request.user, user, CHANGE,
                       f"{description} : {before} → {ROLE_LABELS[get_role(user)]}, {'actif' if user.is_active else 'inactif'}.",
                       repr_text=mask_phone(user.phone_number))
            done += 1
        self.message_user(request, f"{done} compte(s) mis à jour.", messages.SUCCESS)
        if skipped_self:
            self.message_user(request, "Votre propre compte a été ignoré (protection anti-verrouillage).", messages.WARNING)

    @admin.action(description="Activer les comptes", permissions=["change"])
    def activate(self, request, queryset):
        self._apply(request, queryset, "Activation", lambda u: User.objects.filter(pk=u.pk).update(is_active=True))

    @admin.action(description="Désactiver les comptes", permissions=["change"])
    def deactivate(self, request, queryset):
        self._apply(request, queryset, "Désactivation", lambda u: User.objects.filter(pk=u.pk).update(is_active=False))

    @admin.action(description="Niveau : utilisateur simple", permissions=["change"])
    def make_user(self, request, queryset):
        self._apply(request, queryset, "Changement de niveau", lambda u: set_role(u, ROLE_USER))

    @admin.action(description="Niveau : staff (voir, modifier, supprimer)", permissions=["change"])
    def make_staff(self, request, queryset):
        self._apply(request, queryset, "Changement de niveau", lambda u: set_role(u, ROLE_STAFF))

    @admin.action(description="Niveau : staff en lecture seule", permissions=["change"])
    def make_staff_readonly(self, request, queryset):
        self._apply(request, queryset, "Changement de niveau",
                    lambda u: set_role(u, ROLE_STAFF, can_change=False, can_delete=False))

    @admin.action(description="Niveau : superutilisateur", permissions=["change"])
    def make_superuser(self, request, queryset):
        self._apply(request, queryset, "Changement de niveau", lambda u: set_role(u, ROLE_SUPERUSER))
