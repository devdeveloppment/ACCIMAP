"""Commande ensure_admin : création du superutilisateur initial depuis l'environnement (hébergement sans terminal)."""
import io
import os
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from accounts.models import User

PASSWORD = "Deploiement-Accimap-2026!"


def run(env, *args):
    out = io.StringIO()
    with patch.dict(os.environ, env, clear=False):
        for key in ("ADMIN_PHONE", "ADMIN_PASSWORD"):
            if key not in env:
                os.environ.pop(key, None)
        call_command("ensure_admin", *args, stdout=out)
    return out.getvalue()


class EnsureAdminTests(TestCase):
    def test_sans_variables_ne_fait_rien(self):
        self.assertIn("aucun compte créé", run({}))
        self.assertFalse(User.objects.exists())

    def test_creation_superutilisateur(self):
        out = run({"ADMIN_PHONE": "90000001", "ADMIN_PASSWORD": PASSWORD})
        user = User.objects.get(phone_number="+22890000001")
        self.assertTrue(user.is_superuser and user.is_staff and user.check_password(PASSWORD))
        self.assertTrue(user.has_perm("reports.change_accidentreport"))
        self.assertIn("+228****01", out)
        self.assertNotIn(PASSWORD, out)                     # jamais affiché
        self.assertNotIn("+22890000001", out)               # numéro masqué

    def test_compte_existant_non_modifie(self):
        run({"ADMIN_PHONE": "90000001", "ADMIN_PASSWORD": PASSWORD})
        out = run({"ADMIN_PHONE": "90000001", "ADMIN_PASSWORD": "Autre-Mot-De-Passe-99!"})
        self.assertIn("existe déjà", out)
        self.assertTrue(User.objects.get().check_password(PASSWORD))   # changé depuis le tableau de bord : conservé

    def test_reinitialisation_explicite(self):
        run({"ADMIN_PHONE": "90000001", "ADMIN_PASSWORD": PASSWORD})
        run({"ADMIN_PHONE": "90000001", "ADMIN_PASSWORD": "Autre-Mot-De-Passe-99!"}, "--reset-password")
        self.assertTrue(User.objects.get().check_password("Autre-Mot-De-Passe-99!"))

    def test_mot_de_passe_faible_refuse(self):
        with self.assertRaisesMessage(CommandError, "trop faible"):
            run({"ADMIN_PHONE": "90000001", "ADMIN_PASSWORD": "12345678"})
        self.assertFalse(User.objects.exists())

    def test_variables_incompletes_ou_numero_invalide(self):
        with self.assertRaisesMessage(CommandError, "ensemble"):
            run({"ADMIN_PHONE": "90000001"})
        with self.assertRaisesMessage(CommandError, "invalide"):
            run({"ADMIN_PHONE": "abc", "ADMIN_PASSWORD": PASSWORD})
