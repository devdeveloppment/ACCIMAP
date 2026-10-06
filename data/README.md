# Données géographiques de la zone couverte

ACCIMAP utilise une **zone de couverture** uniquement pour **avertir** l'utilisateur lorsqu'une
position semble hors de la zone couverte. Elle ne sert **jamais** à refuser un signalement ni à
modifier des coordonnées.

## Situation actuelle (prototype)

Aucun fichier n'est fourni : ACCIMAP utilise un **rectangle indicatif** défini dans
`reports/zone.py` (`INDICATIVE_BOUNDS`). Ses valeurs sont **approximatives, choisies par l'équipe de
développement, non issues d'une source officielle**. Il est toujours présenté comme tel dans
l'interface (« Zone indicative approximative (non officielle) »).

## Brancher la limite officielle du District Autonome du Grand Lomé

1. Obtenir le polygone officiel au format **GeoJSON**, en coordonnées **WGS 84 (EPSG:4326)**,
   ordre `[longitude, latitude]`. Sont acceptés : `FeatureCollection`, `Feature` ou géométrie seule,
   de type `Polygon` ou `MultiPolygon`.
2. Le placer ici, par exemple `data/grand_lome.geojson`.
3. Renseigner les variables d'environnement (`.env` en local, tableau de bord sur Render) :

   ```
   COVERAGE_ZONE_GEOJSON=data/grand_lome.geojson
   COVERAGE_ZONE_IS_OFFICIAL=True
   COVERAGE_ZONE_NAME=District Autonome du Grand Lomé
   ```

4. Redémarrer l'application. **Aucune modification de code n'est nécessaire** : la carte, le
   message d'avertissement et la vérification serveur utilisent automatiquement le nouveau polygone.

Tant que `COVERAGE_ZONE_IS_OFFICIAL` n'est pas `True`, la zone reste présentée comme non officielle.
