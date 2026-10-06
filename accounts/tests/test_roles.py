from django.test import TestCase

from accounts.models import User
from accounts.roles import (
    PERM_CHANGE, PERM_DELETE, PERM_VIEW, ROLE_STAFF, ROLE_SUPERUSER, ROLE_USER, get_report_permissions,
    get_role, set_role,
)


def fresh(user):
    """Recharge l'utilisateur (le cache de permissions de Django est propre à l'instance)."""
    return User.objects.get(pk=user.pk)


class RoleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("90123456")

    def test_utilisateur_simple_par_defaut(self):
        self.assertEqual(get_role(self.user), ROLE_USER)
        for perm in (PERM_VIEW, PERM_CHANGE, PERM_DELETE):
            self.assertFalse(self.user.has_perm(perm))

    def test_staff_par_defaut_voir_modifier_supprimer(self):
        set_role(self.user, ROLE_STAFF)
        user = fresh(self.user)
        self.assertEqual(get_role(user), ROLE_STAFF)
        self.assertTrue(user.is_staff and not user.is_superuser)
        for perm in (PERM_VIEW, PERM_CHANGE, PERM_DELETE):
            self.assertTrue(user.has_perm(perm), perm)

    def test_staff_lecture_seule(self):
        set_role(self.user, ROLE_STAFF, can_change=False, can_delete=False)
        user = fresh(self.user)
        self.assertTrue(user.has_perm(PERM_VIEW))
        self.assertFalse(user.has_perm(PERM_CHANGE))
        self.assertFalse(user.has_perm(PERM_DELETE))

    def test_staff_sans_droit_de_suppression(self):
        set_role(self.user, ROLE_STAFF, can_delete=False)
        user = fresh(self.user)
        self.assertTrue(user.has_perm(PERM_CHANGE))
        self.assertFalse(user.has_perm(PERM_DELETE))

    def test_superutilisateur_a_tous_les_droits(self):
        set_role(self.user, ROLE_SUPERUSER)
        user = fresh(self.user)
        self.assertEqual(get_role(user), ROLE_SUPERUSER)
        self.assertTrue(user.is_staff and user.is_superuser)
        for perm in (PERM_VIEW, PERM_CHANGE, PERM_DELETE, "accounts.change_user"):
            self.assertTrue(user.has_perm(perm), perm)
        self.assertEqual(user.user_permissions.count(), 0)  # droits implicites, rien de stocké

    def test_staff_n_a_pas_les_droits_sur_les_utilisateurs(self):
        set_role(self.user, ROLE_STAFF)
        user = fresh(self.user)
        for perm in ("accounts.view_user", "accounts.change_user", "accounts.delete_user"):
            self.assertFalse(user.has_perm(perm), perm)

    def test_retrogradation_retire_tout(self):
        set_role(self.user, ROLE_SUPERUSER)
        set_role(self.user, ROLE_USER)
        user = fresh(self.user)
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertFalse(user.has_perm(PERM_VIEW))
        set_role(self.user, ROLE_STAFF)
        set_role(self.user, ROLE_USER)
        self.assertEqual(fresh(self.user).user_permissions.count(), 0)

    def test_idempotent(self):
        for _ in range(3):
            set_role(self.user, ROLE_STAFF)
        self.assertEqual(fresh(self.user).user_permissions.count(), 3)

    def test_role_inconnu(self):
        with self.assertRaises(ValueError):
            set_role(self.user, "dieu")


class ReportPermissionReadingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("90123456")

    def test_lecture_des_droits(self):
        self.assertEqual(get_report_permissions(self.user), {"view": False, "change": False, "delete": False})
        set_role(self.user, ROLE_STAFF, can_change=True, can_delete=False)
        self.assertEqual(get_report_permissions(fresh(self.user)), {"view": True, "change": True, "delete": False})
        set_role(self.user, ROLE_SUPERUSER)
        self.assertEqual(get_report_permissions(fresh(self.user)), {"view": True, "change": True, "delete": True})

    def test_les_droits_d_un_compte_desactive_restent_lisibles(self):
        """has_perm() répond toujours False pour un compte inactif : la lecture doit rester juste."""
        set_role(self.user, ROLE_STAFF, can_change=True, can_delete=False)
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        inactive = fresh(self.user)
        self.assertFalse(inactive.has_perm(PERM_CHANGE))  # comportement de Django, piège à éviter
        self.assertEqual(get_report_permissions(inactive), {"view": True, "change": True, "delete": False})
