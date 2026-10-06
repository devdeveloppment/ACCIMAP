"""
Nouveaux types d'accident : la liste décrit désormais la NATURE de l'accident (collision, renversement,
sortie de voie...) et non plus le véhicule concerné.

Changement de choix uniquement : la colonne reste un texte de 30 caractères, aucun schéma SQL ne change.
Les signalements existants sont convertis :

    COLLISION_VEHICLES   -> COLLISION
    COLLISION_PEDESTRIAN -> PEDESTRIAN
    ROLLOVER, OTHER      -> inchangés
    MOTORCYCLE           -> OTHER   (« moto » est un véhicule, pas une nature d'accident : la nature est inconnue)
    SINGLE_VEHICLE       -> OTHER   (ambigu : sortie de voie ? perte de contrôle ? renversement ?)

La conversion inverse est approximative (plusieurs nouveaux types n'existaient pas avant).
"""
from django.db import migrations, models

FORWARD = {
    "COLLISION_VEHICLES": "COLLISION",
    "COLLISION_PEDESTRIAN": "PEDESTRIAN",
    "MOTORCYCLE": "OTHER",
    "SINGLE_VEHICLE": "OTHER",
}
BACKWARD = {
    "COLLISION": "COLLISION_VEHICLES",
    "PEDESTRIAN": "COLLISION_PEDESTRIAN",
    "RUN_OFF_ROAD": "SINGLE_VEHICLE",
    "LOSS_OF_CONTROL": "SINGLE_VEHICLE",
    "INTERSECTION": "COLLISION_VEHICLES",
    "PILEUP": "COLLISION_VEHICLES",
}


def _convert(mapping):
    def run(apps, schema_editor):
        AccidentReport = apps.get_model("reports", "AccidentReport")
        for old, new in mapping.items():
            AccidentReport.objects.filter(accident_type=old).update(accident_type=new)
    return run


class Migration(migrations.Migration):

    dependencies = [
        ('reports', '0002_reference_sequence'),
    ]

    operations = [
        migrations.RunPython(_convert(FORWARD), _convert(BACKWARD)),
        migrations.AlterField(
            model_name='accidentreport',
            name='accident_type',
            field=models.CharField(choices=[('COLLISION', 'Collision'), ('ROLLOVER', 'Renversement'), ('RUN_OFF_ROAD', 'Sortie de voie'), ('LOSS_OF_CONTROL', 'Perte de contrôle'), ('INTERSECTION', 'Accident à une intersection'), ('PILEUP', 'Carambolage / multi-collision'), ('PEDESTRIAN', 'Accident impliquant un piéton'), ('OTHER', 'Autre')], max_length=30, verbose_name="type d'accident"),
        ),
    ]
