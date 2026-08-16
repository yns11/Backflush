---
name: databricks-livraison
description: >-
  Livrer sur Databricks (bundle DAB, Databricks Apps, jobs, Lakebase Postgres)
  sans série d'allers-retours. À charger AVANT d'écrire un databricks.yml, un
  app.yaml ou une tâche spark_python_task, et avant tout `bundle deploy`.
  Catalogue de pannes réellement observées dont le symptôme désigne la mauvaise
  cause, et vérificateur statique à exécuter avant de déployer.
---

# Livrer sur Databricks du premier coup

## Le problème que ce skill résout

Les pannes coûteuses d'un déploiement Databricks ne sont presque jamais des
erreurs de syntaxe. Elles passent `bundle validate`, se déploient sans un
message, puis se manifestent par un symptôme **qui désigne la mauvaise cause** :

* une application qui répond `503` parce qu'une variable d'environnement n'a pas
  été réclamée — indiscernable d'une ressource non attachée ;
* une interface absente alors que l'API fonctionne, sans la moindre erreur ;
* une tâche rouge après un traitement réussi de bout en bout ;
* un `ModuleNotFoundError` qui ne se produit que dans le conteneur.

Chaque diagnostic coûte un aller-retour complet : corriger, livrer, déployer,
relancer, relire les journaux. Dix pannes de ce type font une demi-journée.

**La parade n'est pas de mieux deviner, c'est de rendre ces défauts visibles
avant le déploiement.** D'où un vérificateur statique, et un catalogue où
chaque ligne part du symptôme.

> Ce catalogue est tiré d'un déploiement réel (application FastAPI + React,
> deux jobs, Lakebase Postgres, cible AWS). Il ne prétend pas à l'exhaustivité :
> il recense ce qui a effectivement coûté du temps.

---

## 1. Avant chaque déploiement

```bash
python .claude/skills/databricks-livraison/verifier_bundle.py .
```

Lecture seule, moins d'une seconde, aucun appel à l'espace de travail. Il
détecte les défauts des sections 2 et 3 ci-dessous.

**Mieux : l'attacher à la suite de tests du projet**, pour qu'il tourne à chaque
modification et pas seulement quand on y pense.

```python
# tests/test_bundle.py
from pathlib import Path
import sys

sys.path.insert(0, ".claude/skills/databricks-livraison")
from verifier_bundle import verifier          # noqa: E402


def test_le_bundle_ne_porte_aucune_anomalie_connue() -> None:
    bloquantes = [a for a in verifier(Path(__file__).resolve().parents[1]) if a.bloquant]
    assert not bloquantes, "\n\n".join(str(a) for a in bloquantes)
```

---

## 2. Databricks Apps — les quatre pièges structurels

### 2.1 Le paquet racine n'existe pas dans le conteneur

Databricks Apps déploie le **contenu** de `source_code_path`, pas le dossier.
`app/server/main.py` devient `<racine>/server/main.py` : le paquet `app` n'existe
plus, alors que tout le code l'importe.

| | |
|---|---|
| **Symptôme** | `ModuleNotFoundError: No module named 'app'` en boucle, chaque worker meurt à la seconde, « App Not Available » |
| **Faux diagnostic** | dépendance manquante, erreur de build |

Deux corrections possibles ; la seconde est préférable car elle garde une seule
arborescence d'imports pour le conteneur, les tests et le poste local :

1. renommer tous les imports en `server.…` — casse les tests et `uvicorn` local ;
2. **rétablir le paquet à l'exécution**, dans un `main.py` placé à la racine du
   `source_code_path` :

```python
import importlib.util, sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent

if "app" not in sys.modules:                    # idempotent : uvicorn --workers réimporte
    specification = importlib.util.spec_from_file_location(
        "app", RACINE / "__init__.py", submodule_search_locations=[str(RACINE)]
    )
    module = importlib.util.module_from_spec(specification)
    sys.modules["app"] = module                 # enregistrer AVANT d'exécuter
    specification.loader.exec_module(module)

from app.server.main import app                 # noqa: E402
```

`app.yaml` lance alors `main:app`, **jamais** `app.server.main:app`.

### 2.2 `LAKEBASE_ENDPOINT` n'est pas injecté automatiquement

Une ressource `postgres` attachée fournit d'elle-même `PGHOST`, `PGPORT`,
`PGDATABASE`, `PGUSER`, `PGSSLMODE`. **Pas `LAKEBASE_ENDPOINT`** : il faut le
réclamer.

| | |
|---|---|
| **Symptôme** | l'application démarre, puis `503` sur toutes les routes de données |
| **Faux diagnostic** | « la ressource n'est pas attachée » — elle l'est pourtant ; `apps get` le confirme |

```yaml
# app.yaml
env:
  - name: LAKEBASE_ENDPOINT
    valueFrom: postgres        # doit être le NOM de la ressource dans le bundle
```

Sans endpoint, pas de génération de jeton : l'application a l'hôte et rien pour
s'authentifier. Le repli par `PGPASSWORD` injecté existe selon la génération,
mais sa rotation ne vous appartient plus.

### 2.3 La clé de ressource `database` est dépréciée

| | |
|---|---|
| **Symptôme** | `Database instance <nom> does not exist (404)` au déploiement, alors que le projet Lakebase existe |
| **Faux diagnostic** | mauvais nom de projet, droits manquants |

```yaml
resources:
  - name: postgres
    postgres:
      branch:   projects/<projet>/branches/<branche>
      database: projects/<projet>/branches/<branche>/databases/<base>
      permission: CAN_CONNECT_AND_CREATE
```

Ce sont des **chemins de ressource complets**, pas des noms courts. Les relever
avec `databricks postgres list-branches` / `list-databases` — le nom de la base
est souvent tireté (`databricks-postgres`).

### 2.4 Un bundle exclut ce que `.gitignore` ignore

| | |
|---|---|
| **Symptôme** | déploiement réussi, application démarrée, API fonctionnelle… et **interface absente** |
| **Faux diagnostic** | build cassé, cache navigateur |

Aucune erreur nulle part : le frontend compilé (`app/server/static/`) est un
artefact de build, donc gitignoré, donc jamais synchronisé.

```yaml
# databricks.yml
sync:
  include:
    - app/server/static/**
```

Corollaire : le bundle compilé doit exister **au moment** du déploiement.
`bundle deploy` ne le vérifie pas. Écrire un script `deploy` qui compile puis
déploie, et ne jamais enchaîner les deux commandes à la main.

---

## 3. Jobs — trois pièges structurels

### 3.1 `spark_python_task` n'a pas la racine du bundle dans `sys.path`

| | |
|---|---|
| **Symptôme** | `ModuleNotFoundError: No module named 'src'` |
| **Faux diagnostic** | fichiers non synchronisés |

Le fichier est évalué directement. Amorcer explicitement, en tête du script,
avant tout import du dépôt :

```python
def _amorcer_chemin_projet() -> Path:
    """Remonte jusqu'au dossier contenant un fichier repère du dépôt."""
    candidats = [Path(p).resolve() for p in (globals().get("__file__"), sys.argv[0]) if p]
    candidats.append(Path.cwd().resolve())
    for candidat in candidats:
        for base in (candidat, *candidat.parents):
            if (base / "src" / "jobs" / "<un_fichier_repere>.py").is_file():
                sys.path.insert(0, str(base))
                return base
    raise RuntimeError("Racine du projet introuvable — bundle synchronisé en entier ?")
```

Ce code est volontairement **dupliqué** dans chaque script de tâche : le
factoriser imposerait de l'importer, ce qui est précisément ce qui échoue avant
l'amorce.

### 3.2 `SystemExit` fait échouer une tâche réussie

| | |
|---|---|
| **Symptôme** | `SystemExit: 0` et tâche rouge, après un journal de succès complet |
| **Faux diagnostic** | erreur silencieuse en fin de traitement |

Le noyau qui exécute la tâche traite `SystemExit` comme une exception, **y
compris avec le code 0**. Donc : `main()` retourne `None`, et les conditions
d'erreur lèvent une exception explicite.

```python
if __name__ == "__main__":
    main()              # jamais raise SystemExit(main()), jamais sys.exit(...)
```

### 3.3 Journaliser un échec ne doit jamais remplacer l'échec

Un `except` qui écrit dans une table de suivi *avant* de relancer masque la
cause réelle dès que cette écriture échoue à son tour — et elle échoue souvent,
puisque la connexion vient de tomber.

```python
except Exception as exc:
    LOGGER.exception("[%s] échec", table)
    try:
        journaliser_echec(...)                       # jamais entre la cause et le raise
    except Exception:
        LOGGER.exception("journalisation impossible ; l'erreur d'origine reste ci-dessus")
    raise
```

---

## 4. Ce qu'aucun contrôle statique ne verra

### 4.1 Les droits Postgres du principal de service

La ressource attachée donne `CAN_CONNECT_AND_CREATE` : le droit de créer, pas
celui de lire l'existant. Trois ordres, trois portées :

| Ordre | Portée | Rejoué par le job ? |
|---|---|---|
| `GRANT USAGE ON SCHEMA` | le schéma, créé une seule fois | **Non — irremplaçable, à faire à la main** |
| `GRANT SELECT ON ALL TABLES` | les tables existant à cet instant | Sans objet : une bascule les remplace |
| `ALTER DEFAULT PRIVILEGES` | les tables futures, créées **par le rôle qui exécute cet ordre** | — |

`ALTER DEFAULT PRIVILEGES` n'est un filet de sécurité que s'il est exécuté sous
l'identité qui fait tourner le job : les privilèges par défaut sont attachés au
rôle créateur, pas au schéma.

Une bascule `RENAME` crée une table **neuve**, sans privilèges : le job doit
réattribuer le `SELECT` après chaque bascule, sinon l'application perd l'accès
tous les matins.

### 4.2 Les colonnes réelles des tables source

Un modèle qui suppose `snapshot_date` ou `is_deleted` échoue une colonne à la
fois : un déploiement, une exécution, une erreur, une correction, et on
recommence. **Un préflight qui vérifie toutes les colonnes attendues et les
rapporte en une fois** transforme cinq allers-retours en un seul.

Vérifier aussi les colonnes de suppression logique (`IsDelete`, `deleted_at`) :
les ignorer ne produit pas une erreur mais des chiffres faux, ce qui est pire.

### 4.3 Le poste de l'exploitant

Sous Windows : la politique d'exécution bloque les `.ps1` — livrer un `.cmd`
qui appelle `powershell -NoProfile -ExecutionPolicy Bypass -File`. Ne jamais
coder en dur un chemin local (`C:\Program Files\nodejs\npm`) : localiser l'outil
et laisser une variable d'environnement pour le surcharger.

---

## 5. Règles de conception qui suppriment des classes entières

1. **Rendre l'application auto-diagnostique.** Un état « non configuré » doit
   dire *ce qui manque*. Journaliser au démarrage, et exposer dans
   `/api/health`, la liste des variables attendues présentes et absentes —
   **noms seuls**, jamais les valeurs : les journaux de la plateforme ne sont
   pas un coffre-fort.
2. **Un déploiement n'exécute rien.** `bundle deploy` met à jour des
   définitions ; seul `bundle run` exécute. Le savoir évite de croire qu'on a
   cassé les données en corrigeant une variable.
3. **Les variables de cible ne sont pas des variables globales.** Un principal
   de service, une URL, un schéma : propres à `dev`, `preprod`, `prod`. Un
   défaut global finit par accorder à dev un droit sur la prod.
4. **Une variable vide n'est pas une absence.** `--role=${var.x}` avec `x` vide
   transmet `--role=` : le script reçoit `[""]`, pas `[]`. Filtrer à l'entrée.
5. **Sévérité proportionnée à la matérialité.** Un contrôle qualité ne bloque
   que sur une incohérence interne ou un dépassement de seuil matériel ; une
   anomalie de source se signale, elle ne fait pas échouer la chaîne.
6. **L'investissement qui rend tout ceci secondaire : une CI qui déploie et
   exécute sur la cible `dev` à chaque commit.** Aucun des défauts de ce
   catalogue n'aurait survécu à un premier `bundle deploy && bundle run`
   automatique. Sans elle, l'environnement de développement ne ressemble pas à
   l'environnement d'exécution, et l'écart se paie en allers-retours.

---

## 6. Installation

Le fichier vit dans le dépôt (`.claude/skills/databricks-livraison/`), donc
versionné et disponible pour quiconque le clone.

Pour l'avoir dans **tous** les projets, le copier dans le dossier personnel :

```powershell
# Windows
robocopy .claude\skills\databricks-livraison "$env:USERPROFILE\.claude\skills\databricks-livraison" /E
```

```bash
# macOS / Linux
cp -r .claude/skills/databricks-livraison ~/.claude/skills/
```

Une fois installé, il se charge de lui-même dès qu'un déploiement Databricks est
en jeu. À enrichir : chaque nouvelle panne dont le symptôme ne désignait pas la
cause mérite une ligne ici et, si elle est détectable statiquement, un contrôle
dans `verifier_bundle.py`.
