#!/bin/sh
# Démarrage du conteneur ACCIMAP : préparation de la base, puis serveur gunicorn.
set -e

# Migrations (crée aussi l'extension PostGIS si elle est absente).
python manage.py migrate --noinput

# Table du cache partagé entre les processus gunicorn (sans effet si elle existe déjà).
python manage.py createcachetable

# Superutilisateur initial depuis ADMIN_PHONE / ADMIN_PASSWORD (ne modifie jamais un compte existant).
python manage.py ensure_admin

# Données de démonstration (signalements marqués « démonstration ») si SEED_DEMO=True ; jamais recréées si présentes.
if [ "${SEED_DEMO:-False}" = "True" ]; then
    python manage.py seed_demo
fi

exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-10000}" \
    --workers "${WEB_CONCURRENCY:-2}" \
    --timeout 60 \
    --access-logfile - \
    --error-logfile -
