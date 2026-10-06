"""Gestion des utilisateurs et des permissions : RÉSERVÉE aux superutilisateurs.

C'est le seul endroit de l'application où les numéros de téléphone complets sont affichés."""
import re

from django.contrib import messages
from django.contrib.auth.forms import SetPasswordForm
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import superuser_required
from accounts.models import User
from accounts.phone import mask_phone
from accounts.roles import ROLE_CHOICES, ROLE_LABELS, ROLE_STAFF, get_role, set_role

from .audit import CHANGE, log_action
from .forms import UserAccessForm

PAGE_SIZE = 25


@superuser_required
def user_list(request):
    queryset = User.objects.annotate(report_count=Count("reports")).order_by("-date_joined")

    search = request.GET.get("q", "").strip()
    if search:
        digits = re.sub(r"\D", "", search)
        queryset = queryset.filter(phone_number__contains=digits) if digits else queryset.none()

    role = request.GET.get("role", "")
    if role == "superuser":
        queryset = queryset.filter(is_superuser=True)
    elif role == "staff":
        queryset = queryset.filter(is_staff=True, is_superuser=False)
    elif role == "user":
        queryset = queryset.filter(is_staff=False)

    active = request.GET.get("active", "")
    if active in ("1", "0"):
        queryset = queryset.filter(is_active=(active == "1"))

    page = Paginator(queryset, PAGE_SIZE).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    users = [{"obj": u, "role": ROLE_LABELS[get_role(u)]} for u in page]
    return render(
        request,
        "dashboard/user_list.html",
        {"page": page, "users": users, "search": search, "role": role, "active": active,
         "role_filters": ROLE_CHOICES, "querystring": params.urlencode()},
    )


@superuser_required
def user_detail(request, pk):
    target = get_object_or_404(User.objects.annotate(report_count=Count("reports")), pk=pk)
    form = UserAccessForm(
        request.POST if request.method == "POST" else None, target=target, actor=request.user
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        before = f"{ROLE_LABELS[get_role(target)]}, {'actif' if target.is_active else 'inactif'}"
        set_role(target, data["role"], can_change=data["can_change"], can_delete=data["can_delete"])
        target.is_active = data["is_active"]
        target.save(update_fields=["is_active"])

        after = f"{ROLE_LABELS[data['role']]}, {'actif' if data['is_active'] else 'inactif'}"
        extra = ""
        if data["role"] == ROLE_STAFF:
            extra = (f" (modifier : {'oui' if data['can_change'] else 'non'}, "
                     f"supprimer : {'oui' if data['can_delete'] else 'non'})")
        log_action(request.user, target, CHANGE, f"Accès : {before} → {after}{extra}.",
                   repr_text=mask_phone(target.phone_number))
        messages.success(request, "Les accès de l'utilisateur ont été mis à jour.")
        return redirect("dashboard:user_detail", pk=pk)

    return render(
        request,
        "dashboard/user_detail.html",
        {"target": target, "form": form, "role_label": ROLE_LABELS[get_role(target)],
         "is_self": target.pk == request.user.pk, "password_form": _password_form(target)},
    )


def _password_form(target, data=None):
    form = SetPasswordForm(target, data)
    for field in form.fields.values():
        field.widget.attrs["class"] = "form-control"
    return form


@sensitive_post_parameters()
@superuser_required
@require_POST
def user_set_password(request, pk):
    """Définit le mot de passe d'administration d'un compte STAFF (jamais d'un citoyen, qui reste en OTP)."""
    target = get_object_or_404(User, pk=pk)
    if target.pk == request.user.pk:
        return redirect("dashboard:password_change")
    if not target.is_staff:
        messages.error(request, "Un utilisateur simple n'a pas de mot de passe : donnez-lui d'abord un accès staff.")
        return redirect("dashboard:user_detail", pk=pk)
    form = _password_form(target, request.POST)
    if form.is_valid():
        form.save()
        log_action(request.user, target, CHANGE, "Mot de passe d'administration défini.",
                   repr_text=mask_phone(target.phone_number))
        messages.success(request, "Le mot de passe d'administration a été défini.")
    else:
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    return redirect("dashboard:user_detail", pk=pk)
