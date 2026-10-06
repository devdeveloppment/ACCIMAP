# Déployer ACCIMAP sur Render

Le dépôt contient tout le nécessaire : `Dockerfile` (Python 3.11 + GDAL/GEOS/PROJ), `docker/start.sh` (démarrage) et
`render.yaml` (Blueprint : service web + base PostgreSQL). **Aucun secret n'est dans le dépôt** : ils se saisissent
dans le tableau de bord Render.

## 1. Créer les services (une seule fois)

1. Créer un compte sur https://render.com et le relier à GitHub (accès au dépôt `ACCIMAP`).
2. **New > Blueprint**, choisir le dépôt, branche `main`. Render lit `render.yaml` et propose :
   - `accimap-db` : PostgreSQL 17 (offre gratuite, région Frankfurt) ;
   - `accimap` : service web Docker (offre gratuite, région Frankfurt).
3. Render demande les variables marquées « à renseigner » :

| Variable | Valeur à saisir | Rôle |
|---|---|---|
| `ADMIN_PHONE` | ex. `90000001` | Identifiant du superutilisateur créé au premier démarrage |
| `ADMIN_PASSWORD` | mot de passe robuste (12 caractères ou plus, lettres, chiffres, symbole) | Son mot de passe (jamais affiché dans les journaux) |
| `GEOCODER_CONTACT` | adresse e-mail de contact du projet | Exigé par la politique d'usage de Nominatim (OpenStreetMap) |

4. **Apply**. La première construction prend quelques minutes. Au démarrage, le conteneur exécute automatiquement :
   migrations (avec activation de PostGIS), table de cache, création du compte administrateur, puis gunicorn.
5. L'adresse publique s'affiche en haut du service (`https://accimap-xxxx.onrender.com`). `ALLOWED_HOSTS` et
   `CSRF_TRUSTED_ORIGINS` sont configurés automatiquement pour cette adresse.

## 2. Variables déjà fixées par `render.yaml`

| Variable | Valeur | Remarque |
|---|---|---|
| `SECRET_KEY` | générée par Render | ne jamais la copier ailleurs |
| `DEBUG` | `False` | HTTPS forcé, cookies sécurisés, HSTS |
| `DATABASE_URL` | fournie par la base `accimap-db` | connexion interne, base inaccessible depuis Internet |
| `WEB_CONCURRENCY` | `2` | processus gunicorn |
| `OTP_DEV_MODE` / `SMS_BACKEND` | `True` / `ConsoleSMSBackend` | **SMS simulé** (prototype, soutenance) : le code s'affiche à l'écran |
| `SEED_DEMO` | `False` | `True` = 60 signalements de démonstration créés au démarrage (une seule fois) |

Un domaine personnalisé s'ajoute dans *Settings > Custom Domains* ; ajouter alors `ALLOWED_HOSTS` et
`CSRF_TRUSTED_ORIGINS` (ex. `accimap.tg` et `https://accimap.tg`).

## 3. Vérifier après le déploiement

- `https://…onrender.com/healthz/` répond `ok` ; l'accueil, la carte et « À propos » s'affichent.
- Espace d'administration : `https://…onrender.com/dashboard/login/` avec `ADMIN_PHONE` / `ADMIN_PASSWORD`, puis
  changer le mot de passe (bouton « Mot de passe »).
- Services externes (depuis votre poste) :
  `ACCIMAP_URL=https://…onrender.com SKIP_NOMINATIM=1 python scripts/check_external_services.py`.

## 4. Limites de l'offre gratuite

- **Mise en veille** : le service s'endort après 15 minutes sans visite ; le premier accès suivant prend ~1 minute.
  Ouvrir le site quelques minutes avant une démonstration.
- **Base de données gratuite** : supprimée par Render au bout de 30 jours ; passer à un plan payant pour la conserver.
- **Photos** : le disque d'un service Render est effacé à chaque redéploiement ou redémarrage. Les photos jointes aux
  signalements sont donc perdues ; les signalements eux-mêmes (base de données) sont conservés. Pour les garder :
  disque persistant Render (offre payante, monté sur `/app/media`) ou stockage externe via `MEDIA_STORAGE_BACKEND`.
- **SMS** : simulés. Pour de vrais SMS : `OTP_DEV_MODE=False`, `SMS_BACKEND=accounts.sms.BrevoSMSBackend`,
  `SMS_API_KEY` (clé Brevo) et `SMS_SENDER_ID` (Sender ID approuvé pour le Togo), crédits SMS Brevo.

## 5. Opérations courantes

- **Mise à jour** : `git push` sur `main` ; Render reconstruit et redéploie automatiquement (migrations comprises).
- **Mot de passe administrateur perdu** : sans terminal sur l'offre gratuite, définir un nouvel `ADMIN_PASSWORD`,
  puis remplacer temporairement la commande de démarrage par
  `sh -c "python manage.py ensure_admin --reset-password && ./docker/start.sh"` (*Settings > Docker Command*),
  redéployer, puis remettre la commande par défaut.
- **Données de démonstration** : `SEED_DEMO=True` puis redéployer ; elles sont marquées « démonstration » et
  peuvent être retirées avec `python manage.py seed_demo --clear`.

## Test local de l'image (facultatif, Docker requis)

```bash
docker build -t accimap .
docker run --rm -p 10000:10000 -e SECRET_KEY=essai -e DEBUG=False -e SECURE_SSL_REDIRECT=False \
  -e DATABASE_URL=postgres://utilisateur:motdepasse@hote:5432/base accimap
```
