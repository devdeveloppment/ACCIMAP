# ACCIMAP : image de production (Render ou tout hébergeur Docker).
# GeoDjango exige les bibliothèques système GDAL, GEOS et PROJ : installées depuis les paquets Debian officiels.
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# gdal-bin installe libgdal, libgeos_c et libproj ; binutils permet à Django de localiser ces bibliothèques.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gdal-bin binutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dépendances Python d'abord (couche mise en cache tant que requirements.txt ne change pas).
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Fichiers statiques collectés et compressés à la construction (servis par WhiteNoise).
# SECRET_KEY factice : uniquement pour cette étape, la vraie clé est fournie par l'hébergeur au démarrage.
RUN sed -i 's/\r$//' docker/start.sh && chmod +x docker/start.sh \
    && SECRET_KEY=collectstatic-build-only DEBUG=False python manage.py collectstatic --noinput -v 0

# Exécution sans privilèges root.
RUN useradd --create-home --uid 1000 accimap \
    && mkdir -p /app/media \
    && chown -R accimap:accimap /app/media
USER accimap

# Render fournit le port dans $PORT (10000 par défaut).
EXPOSE 10000
CMD ["./docker/start.sh"]
