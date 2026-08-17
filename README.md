# Backflush Analytics — Écarts de consommation composant

Solution **Databricks Apps** de reporting et d'analytique sur les **écarts de
consommation de composants générés par le backflush de production** (ERP
Dynamics 365 F&O → Lakehouse → Lakebase → App).

> Écart = Consommation théorique − Consommation réelle
> = (Qté parent produite × Coef nomenclature) − Qté composant sortie du stock

| Écart | Interprétation métier |
|---|---|
| `> 0` | **Non-consommation** — le stock composant n'a pas été déduit à hauteur du théorique (stock système surévalué) |
| `< 0` | **Surconsommation** — plus de composant sorti que ce que la nomenclature prévoit (rebut, vol, erreur BOM, sur-service) |
| `≈ 0` | **Conforme** (seuil paramétrable, défaut ±0,5 unité) |

---

## 1. Ce que fait l'application

| Écran | Contenu |
|---|---|
| **Synthèse** | 8 KPI (écart net valorisé, non-conso, surconso, taux de conformité, fiabilité backflush, couverture, concentration, fraîcheur), tendance hebdomadaire théorique vs réel, variance hebdomadaire, classements **programme / périmètre / catégorie**, top composants — **tous drill-through** |
| **Programmes** | Grille agrégée par programme × semaine, ouverture vers le détail filtré |
| **Périmètres** | Grille agrégée par ligne de production × semaine, et **vue synthétique** en tableau croisé (production par parent, écart par composant en équivalent produit) |
| **Références** | Grille agrégée par composant (écart cumulé, éq. produit, impact €, coef BOM, uniformité), recherche plein texte |
| **Détail** | Grille ligne à ligne `parent × composant × semaine` — la granularité d'audit |
| **Base article** | Référentiel article : **exclure** des références de l'analyse, à la ligne ou par lot, avec motif et auteur. L'impact porté par chaque référence est affiché en face du bouton |
| **Nomenclature** | Nomenclature active : **désactiver** une ligne ou **corriger** son coefficient. Le coefficient de l'ERP reste affiché à côté du coefficient retenu |
| **Assistant IA** | Chat outillé (function calling) sur production, consommation, écarts, base article et nomenclature |

Transverse à tous les écrans :

- **Bascule valeur / quantité** — un seul bouton fait basculer indicateurs, graphiques, classements et vue synthétique entre l'euro et l'unité. Ce n'est pas un formatage : le tri suit la bascule côté serveur, sans quoi on lirait un classement en euros habillé d'unités.
- **Blocs repliables** — filtres, période, indicateurs, graphiques et grilles se replient d'un clic ; l'état est mémorisé, et un bloc fermé résume ce qu'il cache.
- **Timeline slicer** — une case par semaine ISO, brossage, presets (4/8/13/26/52 semaines, YTD, tout), navigation clavier. La période annoncée se termine au **dimanche** de la dernière semaine retenue.
- **Axe temporel continu** — une semaine sans production reste une colonne vide plutôt que de disparaître : un arrêt de ligne ne doit pas se lire comme une semaine ordinaire.
- **Grilles serveur** — tri, pagination, filtres, sélection multiple, **pied de totaux** (calculés sur la sélection entière, pas sur la page), **copie presse-papiers** (TSV, collable dans Excel), **export XLSX**, **analyse IA du lot sélectionné**.
- **Points de vigilance cliquables** — chaque contrôle qualité du bandeau ouvre les lignes qu'il dénonce, filtres déjà appliqués.
- **Thème clair / sombre** avec palette de data-visualisation validée (voir §6).

---

## 2. Architecture

```
Dynamics 365 F&O
   └─ emotors_data_platform.bronze_erp     invent_trans, invent_trans_origin, prod_table
   └─ emotors_data_champions.silver_erp_ye silver_bom, silver_base_article
            │
            │  ❶ Job « backflush_gold » (quotidien, serverless SQL)
            ▼
   emotors_data_champions.backflush        dim_* / fact_* / agg_* + dq_* (Unity Catalog)
            │
            │  ❷ Job « backflush_lakebase_sync » (staging + bascule atomique)
            ▼
   Lakebase Postgres — schéma `backflush`  tables + index OLTP + meta_ingestion
            │
            │  ❸ requêtes < 200 ms, paramétrées
            ▼
   Databricks App « backflush-analytics »  FastAPI (Python 3.11) + React 19 / Vite
```

Le détail des choix de conception est dans [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
La procédure de déploiement est dans [`docs/DEPLOIEMENT.md`](docs/DEPLOIEMENT.md).
Les 20 axes d'amélioration priorisés sont dans [`docs/AMELIORATIONS.md`](docs/AMELIORATIONS.md).

---

## 3. Arborescence

```
.
├── .claude/skills/
│   └── databricks-livraison/      Catalogue des pièges de déploiement + vérificateur statique
├── databricks.yml                 Bundle DAB (jobs + app), 3 cibles dev/preprod/prod
├── resources/
│   ├── backflush_gold.job.yml     ❶ Construction du modèle gold dans Unity Catalog
│   ├── backflush_sync.job.yml     ❷ Ingestion Unity Catalog → Lakebase
│   └── backflush_app.yml          Ressource Databricks App
├── src/
│   ├── sql/gold/                  SQL déclaratif du modèle (10 fichiers numérotés)
│   ├── sql/lakebase/              DDL Postgres (tables, index, vues, rôles)
│   └── jobs/                      Tâches Python des jobs (build gold, sync Lakebase)
├── app/
│   ├── app.yaml                   Manifeste Databricks Apps
│   ├── main.py                    Point d'entrée du conteneur (rétablit le paquet « app »)
│   ├── requirements.txt
│   ├── server/                    Backend FastAPI — logique métier, isolée de l'UI
│   │   ├── core/                  config, pool Lakebase, erreurs, journalisation
│   │   ├── domain/                modèles Pydantic + règles métier pures (testables)
│   │   ├── data/                  repository SQL (le seul endroit qui écrit du SQL)
│   │   │                          dont faits.py — surcharges du key-user appliquées
│   │   │                          à la lecture, et parametrage.py — seul module écrivant
│   │   ├── services/              KPI, export XLSX, assistant IA
│   │   └── api/                   routeurs HTTP (aucune logique métier)
│   └── client/                    Frontend React 19 + TypeScript + Vite
├── logo.svg · logo-sombre.png     Logos eMotors d'origine (scripts/preparer_logos.py
│                                  en dérive les versions web d'app/client/public)
└── tests/                         Tests unitaires backend (pytest)
```

**Séparation logique / UI** : le frontend ne contient aucune règle métier. Toute
définition d'indicateur, tout seuil, toute agrégation vit dans
`app/server/domain/` et `app/server/data/`, et est couverte par des tests.

---

## 4. Démarrage local

```bash
# Backend
python -m venv .venv && source .venv/bin/activate
pip install -r app/requirements.txt -r requirements-dev.txt

# Option A — Postgres local avec jeu de données de démonstration
docker run -d --name backflush-pg -e POSTGRES_PASSWORD=backflush -p 5432:5432 postgres:16
export LAKEBASE_PG_URL="postgresql://postgres:backflush@localhost:5432/postgres"
python -m src.jobs.seed_demo_data           # DDL + ~15 000 lignes réalistes
                                            # historique ancré au 30/03/2026, comme en production

# Option B — Lakebase distant (OAuth via profil CLI Databricks)
export DATABRICKS_CONFIG_PROFILE=DEFAULT
export PGHOST=... PGDATABASE=databricks_postgres LAKEBASE_ENDPOINT=projects/.../endpoints/...

uvicorn app.server.main:app --reload --port 8000

# Frontend (proxy /api → :8000)
cd app/client && npm install && npm run dev     # http://localhost:5173
```

Sans variable de connexion, l'API démarre quand même et renvoie `503` avec un
message explicite sur les routes de données : l'application reste diagnosticable.

## 5. Tests

```bash
pytest                       # règles métier, filtres, validateur SQL, KPI
cd app/client && npm run typecheck && npm run build
```

## 6. Palette de data-visualisation

La palette est **validée par script**, pas à l'œil (bandes de luminosité, plancher
de chroma, séparation pour daltonisme, contraste). Valeurs et résultats dans
[`docs/ARCHITECTURE.md#datavisualisation`](docs/ARCHITECTURE.md#datavisualisation).

- Catégoriel : bleu `#2a78d6` · orange `#eb6834` · aqua `#1baf7a` · jaune `#eda100`
- Divergent (polarité de l'écart) : bleu ↔ rouge, milieu neutre gris
- Statut (matérialité, réservé) : `#0ca30c` / `#fab219` / `#ec835a` / `#d03b3b`

Le divergent **n'est pas** un code « bon/mauvais » : non-consommation et
surconsommation sont deux anomalies de sens opposé, pas un bien et un mal.
