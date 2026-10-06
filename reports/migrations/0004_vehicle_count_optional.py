"""
Le nombre de véhicules impliqués devient FACULTATIF : la colonne accepte NULL (« non renseigné »).

Pourquoi : avec une valeur obligatoire valant 1 par défaut, un « 1 » réellement saisi était indiscernable d'un
« 1 » laissé par défaut, ce qui faussait les statistiques et les exports. Les lignes existantes conservent leur valeur.

Retour arrière : les valeurs NULL redeviennent 1 (la valeur par défaut d'avant) avant de rétablir NOT NULL.
"""
import django.core.validators
from django.db import migrations, models


def _null_to_one(apps, schema_editor):
    AccidentReport = apps.get_model("reports", "AccidentReport")
    AccidentReport.objects.filter(vehicle_count__isnull=True).update(vehicle_count=1)


class Migration(migrations.Migration):

    dependencies = [
        ('reports', '0003_accident_types'),
    ]

    operations = [
        migrations.AlterField(
            model_name='accidentreport',
            name='vehicle_count',
            field=models.PositiveSmallIntegerField(blank=True, help_text='Facultatif. Vide = non renseigné (différent de 0).', null=True, validators=[django.core.validators.MaxValueValidator(100)], verbose_name='nombre approximatif de véhicules impliqués'),
        ),
        # Placée après l'AlterField : à l'annulation, elle s'exécute avant le retour à NOT NULL.
        migrations.RunPython(migrations.RunPython.noop, _null_to_one),
    ]
