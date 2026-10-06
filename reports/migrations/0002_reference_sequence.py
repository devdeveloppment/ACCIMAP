"""Séquence PostgreSQL utilisée pour numéroter les références ACC-AAAA-NNNNNN."""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="CREATE SEQUENCE IF NOT EXISTS reports_reference_seq START 1;",
            reverse_sql="DROP SEQUENCE IF EXISTS reports_reference_seq;",
        ),
    ]
