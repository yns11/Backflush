# Déploiement

## 0. Prérequis

| Élément | Détail |
|---|---|
| CLI Databricks | ≥ 0.288 — `databricks -v` |
| Node.js | ≥ 20, pour compiler le frontend |
| Projet Lakebase | Une instance avec une branche et un endpoint actifs |
| Unity Catalog | `USE CATALOG`, `USE SCHEMA`, `CREATE TABLE` sur le schéma cible ; `SELECT` sur les tables bronze et silver |
| Endpoint de serving | Un modèle de fondation accessible, pour l'assistant IA |

## 1. Relever les identifiants Lakebase

```bash
databricks postgres list-projects --profile <PROFIL>
databricks postgres list-branches   <PROJET> --profile <PROFIL>
databricks postgres get-endpoint "projects/<ID>/branches/<BRANCHE>/endpoints/<ENDPOINT>" \
  --profile <PROFIL> -o json | jq -r '.status.hosts.host'
```

Trois valeurs à conserver :

- `lakebase_host` — l'hôte retourné ci-dessus ;
- `lakebase_endpoint` — le chemin `projects/<ID>/branches/<BRANCHE>/endpoints/<ENDPOINT>` ;
- `lakebase_instance` — le nom de l'instance, pour la ressource de l'application.

## 2. Compiler le frontend

```bash
./scripts/build_frontend.sh
```

Le bundle est écrit dans `app/server/static`. **Il n'est pas versionné** : sans
cette étape, l'application déployée sert l'API mais aucune interface.

## 3. Valider et déployer le bundle

```bash
databricks bundle validate -t dev --profile <PROFIL>

databricks bundle deploy -t dev --profile <PROFIL> \
  --var="lakebase_host=<HOTE>" \
  --var="lakebase_endpoint=projects/<ID>/branches/production/endpoints/<EP>" \
  --var="lakebase_instance=<INSTANCE>" \
  --var="notification_email=<equipe@exemple.fr>"
```

En `preprod` et `prod`, fixer ces variables dans la cible correspondante de
`databricks.yml` plutôt que sur la ligne de commande.

## 4. Première exécution du pipeline

```bash
databricks bundle run backflush_pipeline -t dev --profile <PROFIL>
```

Deux tâches enchaînées :

1. `construire_modele_gold` — reconstruit `emotors_data_champions.backflush` et
   **échoue si un contrôle qualité de sévérité ERREUR est en anomalie**. C'est
   volontaire : mieux vaut un job rouge qu'un tableau de bord faux.
2. `publier_lakebase` — ne démarre que si la première a réussi.

Vérification :

```sql
SELECT controle, severite, valeur, message
FROM emotors_data_champions.backflush.dq_controles
WHERE en_anomalie
ORDER BY severite;
```

## 5. Droits Postgres du principal de service

⚠️ **Étape obligatoire, à faire une seule fois.** Le job réattribue `SELECT` sur
chaque table après la bascule, mais le principal de service a besoin de
`USAGE` sur le schéma, qui n'est pas recréé à chaque exécution.

Relever le `client_id` de l'application :

```bash
databricks apps get backflush-analytics-dev --profile <PROFIL> -o json \
  | jq -r '.service_principal_client_id'
```

Puis, connecté à Lakebase avec un rôle propriétaire :

```sql
GRANT USAGE ON SCHEMA backflush TO "<client_id>";
GRANT SELECT ON ALL TABLES IN SCHEMA backflush TO "<client_id>";
-- Filet de sécurité si une table venait à être créée hors du job.
ALTER DEFAULT PRIVILEGES IN SCHEMA backflush GRANT SELECT ON TABLES TO "<client_id>";
```

Reporter ensuite ce `client_id` dans la variable `app_service_principal` du
bundle, pour que chaque bascule réattribue le droit.

## 6. Vérifier l'application

```bash
databricks apps get   backflush-analytics-dev --profile <PROFIL> -o json | jq '.app_status'
databricks apps logs  backflush-analytics-dev --follow --profile <PROFIL>
```

Puis, sur l'URL de l'application :

| Contrôle | Attendu |
|---|---|
| `/api/health` | `base.statut = "ok"` |
| `/api/meta/fraicheur` | `derniere_ingestion` renseignée, aucune table en échec |
| `/` | Le tableau de bord s'affiche, KPI et graphiques peuplés |
| `/api/docs` | Documentation OpenAPI complète |

## 7. Diagnostic

| Symptôme | Cause la plus fréquente | Correction |
|---|---|---|
| `503` sur toutes les routes de données, `/api/health` OK | Ressource `database` non attachée | L'attacher, redéployer l'application |
| `permission denied for table …` | Le `GRANT` de l'étape 5 n'a pas été fait, ou `app_service_principal` est vide dans le bundle | Refaire l'étape 5, redéployer, relancer le job |
| Interface absente, API fonctionnelle | `scripts/build_frontend.sh` non exécuté avant le déploiement | Compiler puis redéployer |
| L'application plante au démarrage | `psycopg` absent des dépendances | Vérifier `app/requirements.txt` |
| Première requête lente après une période creuse | Instance Lakebase mise à l'échelle zéro | Attendu ; le pre-ping du pool absorbe le réveil |
| Assistant en `503` | Ressource `serving-endpoint` absente, ou principal de service sans `CAN_QUERY` | Attacher la ressource, accorder le droit |
| Job en échec sur `article_hors_referentiel` | Des composants mouvementés manquent dans `silver_base_article` | Corriger la source ; en dernier recours, `--no-fail-on-dq-error` pour débloquer, en sachant que les chiffres sont incomplets |

## 8. Passage en production

```bash
./scripts/build_frontend.sh
databricks bundle validate -t prod --profile <PROFIL>
databricks bundle deploy   -t prod --profile <PROFIL>
databricks bundle run backflush_pipeline -t prod --profile <PROFIL>
```

Points de contrôle avant ouverture aux utilisateurs :

- [ ] `schedule_pause_status = UNPAUSED` sur la cible (défaut en `prod`)
- [ ] `notification_email` renseigné — un job qui échoue en silence est un job
      dont personne ne sait qu'il a échoué
- [ ] `GRANT` du principal de service effectué (étape 5)
- [ ] `dq_controles` sans anomalie de sévérité `ERREUR`
- [ ] Comparaison d'un total avec la source ERP sur une semaine témoin
- [ ] Permissions de l'application accordées au groupe d'utilisateurs cible
