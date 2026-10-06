"""Migration 0003 : conversion des anciens types d'accident vers la nouvelle liste (et retour)."""
from datetime import date, time

from django.contrib.gis.geos import Point
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE = [("reports", "0002_reference_sequence")]
AFTER = [("reports", "0003_accident_types")]


class AccidentTypeMigrationTests(TransactionTestCase):
    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(BEFORE)                       # état d'avant : anciens types
        self.old_apps = executor.loader.project_state(BEFORE).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())   # retour à l'état courant du projet

    def make_old(self, reference, accident_type):
        Report = self.old_apps.get_model("reports", "AccidentReport")
        return Report.objects.create(
            reference=reference, accident_type=accident_type, accident_date=date(2026, 9, 1),
            accident_time=time(10, 0), location=Point(1.2, 6.1, srid=4326), latitude=6.1, longitude=1.2,
        )

    def migrate_forward(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(AFTER)
        return executor.loader.project_state(AFTER).apps.get_model("reports", "AccidentReport")

    def test_conversion_vers_les_nouveaux_types(self):
        cases = {
            "COLLISION_VEHICLES": "COLLISION", "COLLISION_PEDESTRIAN": "PEDESTRIAN", "ROLLOVER": "ROLLOVER",
            "OTHER": "OTHER", "MOTORCYCLE": "OTHER", "SINGLE_VEHICLE": "OTHER",
        }
        for index, old in enumerate(cases):
            self.make_old(f"T-{index}", old)
        Report = self.migrate_forward()
        for index, (old, new) in enumerate(cases.items()):
            self.assertEqual(Report.objects.get(reference=f"T-{index}").accident_type, new, old)

    def test_les_autres_champs_sont_intacts(self):
        old = self.make_old("T-KEEP", "MOTORCYCLE")
        Report = self.migrate_forward()
        new = Report.objects.get(pk=old.pk)
        self.assertEqual((new.accident_date, new.accident_time), (date(2026, 9, 1), time(10, 0)))
        self.assertEqual((round(new.location.x, 3), round(new.location.y, 3)), (1.2, 6.1))

    def test_retour_en_arriere(self):
        self.migrate_forward()
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        Report = executor.loader.project_state(AFTER).apps.get_model("reports", "AccidentReport")
        for index, new in enumerate(["COLLISION", "PEDESTRIAN", "RUN_OFF_ROAD", "INTERSECTION", "OTHER", "ROLLOVER"]):
            Report.objects.create(reference=f"B-{index}", accident_type=new, accident_date=date(2026, 9, 1),
                                  accident_time=time(10, 0), location=Point(1.2, 6.1, srid=4326), latitude=6.1, longitude=1.2)
        executor.migrate(BEFORE)
        Old = executor.loader.project_state(BEFORE).apps.get_model("reports", "AccidentReport")
        expected = {"B-0": "COLLISION_VEHICLES", "B-1": "COLLISION_PEDESTRIAN", "B-2": "SINGLE_VEHICLE",
                    "B-3": "COLLISION_VEHICLES", "B-4": "OTHER", "B-5": "ROLLOVER"}
        for reference, old in expected.items():
            self.assertEqual(Old.objects.get(reference=reference).accident_type, old)


class VehicleCountMigrationTests(TransactionTestCase):
    """Migration 0004 : le nombre de véhicules devient facultatif (NULL = non renseigné)."""

    BEFORE = [("reports", "0003_accident_types")]
    AFTER = [("reports", "0004_vehicle_count_optional")]

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.BEFORE)
        self.old_apps = executor.loader.project_state(self.BEFORE).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def make(self, apps, reference, vehicles):
        Report = apps.get_model("reports", "AccidentReport")
        return Report.objects.create(
            reference=reference, accident_type="COLLISION", accident_date=date(2026, 9, 1), accident_time=time(10, 0),
            location=Point(1.2, 6.1, srid=4326), latitude=6.1, longitude=1.2, vehicle_count=vehicles,
        )

    def test_les_valeurs_existantes_sont_conservees_et_null_devient_possible(self):
        self.make(self.old_apps, "V-1", 3)
        self.make(self.old_apps, "V-2", 1)
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.AFTER)
        Report = executor.loader.project_state(self.AFTER).apps.get_model("reports", "AccidentReport")
        self.assertEqual(Report.objects.get(reference="V-1").vehicle_count, 3)
        self.assertEqual(Report.objects.get(reference="V-2").vehicle_count, 1)
        self.make(executor.loader.project_state(self.AFTER).apps, "V-3", None)       # désormais autorisé
        self.assertIsNone(Report.objects.get(reference="V-3").vehicle_count)

    def test_retour_arriere_remplace_null_par_la_valeur_d_avant(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.AFTER)
        new_apps = executor.loader.project_state(self.AFTER).apps
        self.make(new_apps, "B-1", None)
        self.make(new_apps, "B-2", 4)
        executor = MigrationExecutor(connection)       # exécuteur NEUF : l'ancien ignore les migrations appliquées depuis
        executor.loader.build_graph()
        executor.migrate(self.BEFORE)                  # ne doit pas échouer malgré le NULL
        Old = executor.loader.project_state(self.BEFORE).apps.get_model("reports", "AccidentReport")
        self.assertEqual(Old.objects.get(reference="B-1").vehicle_count, 1)
        self.assertEqual(Old.objects.get(reference="B-2").vehicle_count, 4)
