"""Profils d'utilisateurs et outils communs aux tests de l'espace administrateur."""
from django.test import TestCase

from accounts.decorators import ADMIN_SESSION_KEY
from accounts.models import User
from accounts.roles import ROLE_STAFF, ROLE_SUPERUSER, set_role
from reports.tests.helpers import make_report  # noqa: F401  (réexporté pour les tests)


def open_admin_session(client, user):
    """Simule une connexion par l'ESPACE PRIVÉ (identifiant + mot de passe) : session marquée administrateur.
    Un simple force_login() équivaut, lui, à une connexion citoyenne par OTP (aucun accès à l'administration)."""
    client.force_login(user)
    session = client.session
    session[ADMIN_SESSION_KEY] = user.pk
    session.save()


def make_user(phone, role=None, **role_options):
    user = User.objects.create_user(phone)
    if role:
        set_role(user, role, **role_options)
    return User.objects.get(pk=user.pk)


class PersonasTestCase(TestCase):
    """Un profil pour chaque niveau d'accès. `self.login(persona)` ouvre la session."""

    PHONES = {
        "citizen": "90000001",
        "staff": "90000002",
        "staff_readonly": "90000003",
        "staff_no_delete": "90000004",
        "staff_no_view": "90000005",
        "superuser": "90000006",
    }

    def setUp(self):
        super().setUp()
        p = self.PHONES
        self.personas = {
            "citizen": make_user(p["citizen"]),
            "staff": make_user(p["staff"], ROLE_STAFF),
            "staff_readonly": make_user(p["staff_readonly"], ROLE_STAFF, can_change=False, can_delete=False),
            "staff_no_delete": make_user(p["staff_no_delete"], ROLE_STAFF, can_delete=False),
            # Marqué « staff » mais sans aucune permission : ne doit rien pouvoir faire.
            "staff_no_view": User.objects.create_user(p["staff_no_view"], is_staff=True),
            "superuser": make_user(p["superuser"], ROLE_SUPERUSER),
        }

    def login(self, persona):
        self.client.logout()
        if persona == "anonymous":
            return
        user = self.personas[persona]
        if user.is_staff:
            open_admin_session(self.client, user)      # le staff passe par la connexion de l'espace privé
        else:
            self.client.force_login(user)              # le citoyen se connecte par OTP

    @staticmethod
    def phone_forms(user):
        """Toutes les écritures d'un numéro (complet, local) à ne jamais voir fuiter."""
        return [user.phone_number, user.phone_number[4:]]
