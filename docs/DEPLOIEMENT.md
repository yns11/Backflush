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

Toutes ces commandes prennent **un seul argument** : le chemin de ressource du
parent. Un nom court est refusé (`No API found for 'GET /postgres/<nom>/...'`).

```bash
databricks postgres list-projects  --profile <PROFIL>
databricks postgres list-branches  projects/<PROJET> --profile <PROFIL>
databricks postgres list-databases projects/<PROJET>/branches/<BRANCHE> --profile <PROFIL>
databricks postgres list-endpoints projects/<PROJET>/branches/<BRANCHE> --profile <PROFIL>
databricks postgres get-endpoint   projects/<PROJET>/branches/<BRANCHE>/endpoints/<ENDPOINT> \
  --profile <PROFIL> -o json | jq -r '.status.hosts.host'
```

Chaque niveau donne le chemin du suivant : `list-projects` retourne
`projects/backflush`, que l'on passe à `list-branches`, qui retourne
`projects/backflush/branches/production`, et ainsi de suite.

Quatre valeurs à conserver :

| Variable | Forme attendue |
|---|---|
| `lakebase_host` | `ep-….database.<region>.cloud.databricks.com` |
| `lakebase_endpoint` | `projects/<PROJET>/branches/<BRANCHE>/endpoints/<ENDPOINT>` |
| `lakebase_branch` | `projects/<PROJET>/branches/<BRANCHE>` |
| `lakebase_database_path` | `projects/<PROJET>/branches/<BRANCHE>/databases/<BASE>` |

> **Ce sont des chemins de ressource complets, pas des noms courts.** Passer le
> seul nom du projet à la ressource de l'application fait échouer le déploiement
> avec `Database instance <nom> does not exist` — voir §7.
>
> Le nom de la base est souvent **tireté** (`databricks-postgres`) là où le nom
> Postgres est souligné (`databricks_postgres`). `list-databases` donne la forme
> exacte ; ne pas la deviner.

Exemple complet, pour un projet `backflush` sur sa branche `production` :

| Variable | Valeur |
|---|---|
| `lakebase_endpoint` | `projects/backflush/branches/production/endpoints/primary` |
| `lakebase_branch` | `projects/backflush/branches/production` |
| `lakebase_database_path` | `projects/backflush/branches/production/databases/<BASE>` |
| `lakebase_host` | valeur de `status.hosts.host` retournée par `get-endpoint` |

> Si `list-projects` affiche `"enable_pg_native_login": false` — le cas par
> défaut — le projet n'accepte **que** l'authentification OAuth. C'est le mode
> que l'application privilégie ; aucune action n'est requise.

### Hôte direct, jamais l'hôte mutualisé

`get-endpoint` retourne deux hôtes :

```
hosts.host                    ep-….database.<region>.cloud.databricks.com
hosts.read_write_pooled_host  ep-…-pooler.database.<region>.cloud.databricks.com
```

**Renseignez `lakebase_host` avec le premier.** L'hôte « pooler » mutualise les
connexions en mode transaction, ce qui casserait deux mécanismes de cette
application :

* les **curseurs serveur nommés** de l'export Excel (`DECLARE CURSOR`), qui
  vivent à l'échelle de la session et non de la transaction ;
* les réglages de session posés à l'ouverture de chaque connexion
  (`statement_timeout`, `default_transaction_read_only`, `search_path`), qu'un
  pooler en mode transaction ne garantit pas de conserver.

L'application gère déjà son propre pool, dimensionné pour rester bien en deçà du
plafond de connexions de l'endpoint : un second niveau de mutualisation
n'apporterait rien et retirerait ces garanties.

### Dimensionnement de l'endpoint

Vérifiez les bornes d'autoscaling de l'endpoint, distinctes des valeurs par
défaut du projet :

```bash
databricks postgres list-endpoints projects/<PROJET>/branches/<BRANCHE> --profile <PROFIL> \
  -o json | jq '.[].status | {autoscaling_limit_min_cu, autoscaling_limit_max_cu, suspend_timeout_duration}'
```

Le profil de charge de cette application est très creux : une ingestion
quotidienne, puis des requêtes indexées de quelques dizaines de millisecondes.
Un plancher élevé associé à un délai de suspension long maintient l'instance
allumée en permanence, pour un bénéfice nul. Un plancher bas et une suspension
plus courte conviennent mieux ; le réveil consécutif est absorbé par le
pre-ping du pool, au prix de quelques centaines de millisecondes sur la première
requête de la journée. À arbitrer avec l'équipe plateforme selon le coût du CU.

## 2. Compiler le frontend

```bash
./scripts/build_frontend.sh            # Linux, macOS, Git Bash
```

```powershell
.\scripts\build_frontend.ps1           # Windows PowerShell
```

```bat
scripts\build_frontend.cmd             :: Windows, stratégie d'exécution restrictive
```

### « L'exécution de scripts est désactivée sur ce système »

Message courant sur un poste d'entreprise : la stratégie d'exécution PowerShell
bloque les fichiers `.ps1`. Trois réponses, de la plus légère à la plus sûre.

**1. Les enveloppes `.cmd`** — un fichier `.cmd` n'est pas soumis à la stratégie
et relance le script dans un processus qui la contourne, sans rien modifier
durablement :

```bat
scripts\build_frontend.cmd
scripts\deploy.cmd dev PROD --var="lakebase_host=..." --var="..."
```

**2. Le contournement ponctuel**, à l'invocation :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy.ps1 dev PROD --var="..."
```

**3. La procédure manuelle**, qui n'exécute aucun script — la seule qui
fonctionne quand la stratégie est imposée par une règle de groupe (`Get-ExecutionPolicy -List`
affiche alors une valeur sur la ligne `MachinePolicy` ou `UserPolicy`) :

```powershell
cd app\client
npm ci
npm run typecheck
npm run build
cd ..\..
databricks bundle deploy -t dev -p PROD --var="..." --var="..."
```

Dans ce dernier cas, veillez vous-même à l'ordre : la compilation **avant** le
déploiement. C'est précisément ce que les scripts garantissent.

Les deux scripts **localisent npm automatiquement**, y compris hors `PATH` — cas
courant d'une installation Node.js sous `C:\Program Files\nodejs`. Si votre
installation est ailleurs, indiquez-la par variable d'environnement plutôt que
de modifier le script (une modification locale serait écrasée à la prochaine
mise à jour, et casserait la compilation pour les autres postes) :

```powershell
$env:NPM = "C:\Program Files\nodejs\npm.cmd"
```

```bash
NPM="/c/Program Files/nodejs/npm" ./scripts/build_frontend.sh
```

Le bundle est écrit dans `app/server/static`, dossier **non versionné** (c'est un
artefact de build) mais **explicitement ré-inclus** dans la synchronisation par
le bloc `sync.include` de `databricks.yml`. Sans cette ré-inclusion, le bundle
Databricks l'écarterait comme tout fichier ignoré par Git, et l'application se
déploierait sans interface — sans qu'aucune commande n'échoue.

### Quand faut-il recompiler ?

| Situation | Recompiler ? |
|---|---|
| Premier déploiement, ou dossier de travail neuf (clone, machine différente) | **Oui** — `app/server/static` n'existe pas encore |
| Un fichier de `app/client/**` a changé | **Oui** |
| Seuls le backend, le SQL, les jobs ou la documentation ont changé | Non — le bundle existant reste valide |
| Redéploiement à l'identique, même poste | Non |

En cas de doute, recompilez : l'opération prend quelques secondes et est
idempotente. Pour vérifier la fraîcheur sans réfléchir :

```bash
# Le bundle est-il postérieur au dernier changement de source frontend ?
find app/client/src app/client/index.html -newer app/server/static/index.html 2>/dev/null | head
# Aucune sortie = bundle à jour. Une ligne ou une erreur = recompiler.
```

## 3. Valider et déployer le bundle

```bash
databricks bundle validate -t dev --profile <PROFIL>

databricks bundle deploy -t dev --profile <PROFIL> \
  --var="lakebase_host=<HOTE>" \
  --var="lakebase_endpoint=projects/<PROJET>/branches/production/endpoints/<EP>" \
  --var="lakebase_branch=projects/<PROJET>/branches/production" \
  --var="lakebase_database_path=projects/<PROJET>/branches/production/databases/<BASE>" \
  --var="notification_email=<equipe@exemple.fr>"
```

En `preprod` et `prod`, fixer ces variables dans la cible correspondante de
`databricks.yml` plutôt que sur la ligne de commande.

### Repli si la clé `postgres` est refusée par le schéma DAB

Le schéma des bundles est parfois en retard sur l'API Lakebase. Si
`bundle validate` rejette la ressource `postgres`, déployer sans elle puis
l'attacher par l'API — c'est la méthode documentée pour toute application :

1. Commenter le bloc `- name: postgres` dans `resources/backflush_app.yml`, puis
   `databricks bundle deploy`.
2. Lire les ressources actuelles de l'application — `create-update` **remplace**
   tout le tableau `resources`, il faut donc y réinjecter le endpoint de serving :

   ```bash
   databricks apps get backflush-analytics-dev --profile <PROFIL> -o json | jq '.resources'
   ```

3. Écrire `update.json` avec l'existant **plus** la ressource Lakebase :

   ```json
   {
     "update_mask": "resources",
     "app": {
       "resources": [
         {
           "name": "postgres",
           "postgres": {
             "branch": "projects/<PROJET>/branches/production",
             "database": "projects/<PROJET>/branches/production/databases/<BASE>",
             "permission": "CAN_CONNECT_AND_CREATE"
           }
         },
         {
           "name": "serving-endpoint",
           "serving_endpoint": { "name": "<ENDPOINT_LLM>", "permission": "CAN_QUERY" }
         }
       ]
     }
   }
   ```

4. `databricks apps create-update backflush-analytics-dev --json @update.json --profile <PROFIL>`

Utiliser `create-update`, et non `apps update` : ce dernier est l'ancienne
commande et ne sait pas modifier les ressources d'une application.

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
| `Database instance <nom> does not exist (404)` au déploiement | Clé de ressource `database` (dépréciée) au lieu de `postgres`, ou nom court au lieu d'un chemin de ressource | Utiliser `postgres` avec `branch` et `database` en chemins complets (§1) |
| `503` sur toutes les routes de données, `/api/health` OK | Ressource `postgres` non attachée | L'attacher, redéployer l'application |
| `permission denied for schema backflush (42501)` | Le schéma appartient au job (exécuté sous votre identité), pas au principal de service, qui n'a que `CAN_CONNECT_AND_CREATE` | Faire le `GRANT` de l'étape 5 — obligatoire, la ressource seule ne suffit pas |
| Journal « Connexion par mot de passe injecté » | `LAKEBASE_ENDPOINT` absent : la ressource n'a fourni qu'un `PGPASSWORD` | Fonctionnel, mais la rotation dépend de la plateforme. Définir `LAKEBASE_ENDPOINT` pour que l'application gère son propre jeton |
| `permission denied for table …` | Le `GRANT` de l'étape 5 n'a pas été fait, ou `app_service_principal` est vide dans le bundle | Refaire l'étape 5, redéployer, relancer le job |
| Interface absente, API fonctionnelle | `scripts/build_frontend.sh` non exécuté avant le déploiement, ou bloc `sync.include` retiré de `databricks.yml` | Compiler, vérifier que `sync.include` couvre `app/server/static/**`, redéployer |
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
