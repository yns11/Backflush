"""Générateur de jeu de données de démonstration (développement local uniquement).

Crée le schéma Lakebase dans un Postgres local et le remplit avec des données
synthétiques mais **réalistes** : programmes, nomenclatures multi-niveaux de
composants, saisonnalité de production, et surtout des anomalies représentatives
du terrain (dérive de coefficient, composant hors nomenclature, semaine sans
consommation, référence à fort impact financier).

Objectif : permettre de démarrer l'application, de relire l'UI et de faire
tourner les tests d'intégration sans accès à Databricks.

⚠️ Ne JAMAIS exécuter sur une base de production : le script détruit et recrée
les tables du schéma cible.

Usage ::

    export LAKEBASE_PG_URL="postgresql://postgres:backflush@localhost:5432/postgres"
    python -m src.jobs.seed_demo_data --weeks 26
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
from psycopg import sql as pgsql

from src.jobs.lakebase_schema import (
    META_INGESTION,
    SCHEMA,
    TABLES,
    TABLES_BY_NAME,
    create_indexes_sql,
    create_schema_sql,
    create_table_sql,
)

LOGGER = logging.getLogger("backflush.seed")

PROGRAMMES = ["M3", "M3GEN2", "M2BEV", "K9", "COMMUN"]
CATEGORIES_COMPOSANT = ["VIS", "AIMANTS", "MEL", "ROULEMENT", "CABLE", "RESINE", "TOLE"]
UNITES = {"MEL": "KG", "RESINE": "L"}


def iso_week_fields(monday: date) -> tuple[int, int]:
    """Retourne (année ISO, semaine ISO) pour un lundi donné."""
    iso = monday.isocalendar()
    return iso.year, iso.week


def build_dataset(weeks: int, seed: int) -> dict[str, list[tuple[Any, ...]]]:
    """Construit toutes les tables en mémoire. Déterministe pour un ``seed`` donné."""
    rng = random.Random(seed)
    now = datetime.now(UTC)

    # --- Référentiel article -------------------------------------------------
    articles: list[dict[str, Any]] = []
    parents: list[dict[str, Any]] = []
    composants: list[dict[str, Any]] = []

    for programme in PROGRAMMES[:-1]:                     # COMMUN n'a pas de parent
        for index in range(1, 13):
            item_id = f"{programme}-STA-{index:03d}"
            parent = {
                "item_id": item_id,
                "item_name": f"Stator {programme} v{index}",
                "categorie": "STATOR",
                "item_group_id": "PFINI" if index % 3 else "PSMFI",
                "programme": programme,
                "std_cost_price": Decimal(rng.randrange(18_000, 45_000)) / 100,
                "std_unit": "PCE",
            }
            parents.append(parent)
            articles.append(parent)

    for index in range(1, 91):
        categorie = CATEGORIES_COMPOSANT[index % len(CATEGORIES_COMPOSANT)]
        programme = PROGRAMMES[index % len(PROGRAMMES)]
        composant = {
            "item_id": f"CMP-{categorie[:3]}-{index:04d}",
            "item_name": f"{categorie.capitalize()} réf. {index:04d}",
            "categorie": categorie,
            "item_group_id": "COMPO",
            "programme": programme,
            # 4 % du référentiel sans coût standard : cas réel, et cas de test
            # du contrôle qualité « composant_sans_cout_standard ».
            "std_cost_price": None if index % 25 == 0 else Decimal(rng.randrange(5, 9_000)) / 100,
            "std_unit": UNITES.get(categorie, "PCE"),
        }
        composants.append(composant)
        articles.append(composant)

    dim_article = [
        (
            a["item_id"], a["item_name"], f"{a['item_name']} — description longue",
            a["categorie"],
            a["item_group_id"],
            {"COMPO": "Composant", "PSMFI": "Produit semi-fini", "PFINI": "Produit fini"}[a["item_group_id"]],
            a["programme"], a["std_cost_price"], a["std_unit"], now, now,
        )
        for a in articles
    ]

    # --- Nomenclatures -------------------------------------------------------
    # Chaque parent consomme 6 à 10 composants. Un composant « COMMUN » est
    # partagé entre programmes ; sur l'un d'eux le coefficient varie d'un parent
    # à l'autre, ce qui rend l'équivalent produit non calculable — cas métier
    # réel que l'application doit signaler.
    nomenclature: dict[tuple[str, str], Decimal] = {}
    for parent in parents:
        pool = [c for c in composants if c["programme"] in (parent["programme"], "COMMUN")]
        chosen = rng.sample(pool, k=min(len(pool), rng.randint(6, 10)))
        for composant in chosen:
            base = Decimal(rng.randint(1, 12))
            if composant["categorie"] in ("MEL", "RESINE"):
                base = Decimal(rng.randrange(50, 400)) / 100          # KG / L
            if composant["programme"] == "COMMUN" and parent["programme"] == "M3GEN2":
                base += Decimal(rng.randint(0, 2))                    # coef non uniforme
            nomenclature[(parent["item_id"], composant["item_id"])] = base

    dim_nomenclature = [
        (parent_id, child_id, f"BOM-{parent_id}", "V1", "Actif", qty,
         next(c["std_unit"] for c in composants if c["item_id"] == child_id),
         Decimal(0), Decimal(0), 1, now, now)
        for (parent_id, child_id), qty in nomenclature.items()
    ]

    programme_par_parent = {p["item_id"]: p["programme"] for p in parents}
    coefs: dict[tuple[str, str], list[Decimal]] = {}
    for (parent_id, child_id), qty in nomenclature.items():
        coefs.setdefault((programme_par_parent[parent_id], child_id), []).append(qty)

    dim_coef_programme = [
        (programme, child_id, len(quantites), min(quantites), max(quantites), min(quantites),
         max(quantites) - min(quantites) <= Decimal("0.000001"), now, now)
        for (programme, child_id), quantites in coefs.items()
    ]

    # --- Production et consommation -----------------------------------------
    lundi_courant = date.today() - timedelta(days=date.today().weekday())
    semaines = [lundi_courant - timedelta(weeks=offset) for offset in range(weeks - 1, -1, -1)]

    production: dict[tuple[date, str], Decimal] = {}
    for semaine in semaines:
        for parent in parents:
            if rng.random() < 0.08:                       # arrêt de ligne
                continue
            volume = Decimal(rng.randint(400, 2_600))
            production[(semaine, parent["item_id"])] = volume

    nom_par_parent = {p["item_id"]: p["item_name"] for p in parents}
    fact_production = [
        (
            semaine, parent_id, *iso_week_fields(semaine),
            programme_par_parent[parent_id], nom_par_parent[parent_id],
            "STATOR", qty, rng.randint(3, 40),
            datetime.combine(semaine, datetime.min.time(), tzinfo=UTC),
            datetime.combine(semaine + timedelta(days=4), datetime.min.time(), tzinfo=UTC),
            now,
        )
        for (semaine, parent_id), qty in production.items()
    ]

    # Composants « à problème » : dérive systématique, choisis une fois pour
    # toutes afin que le tableau de bord raconte une histoire stable.
    derive_forte = set(rng.sample([c["item_id"] for c in composants], k=6))
    hors_nomenclature = rng.sample([c["item_id"] for c in composants], k=3)

    consommation: dict[tuple[date, str, str], Decimal] = {}
    for (semaine, parent_id), volume in production.items():
        for (bom_parent, child_id), coef in nomenclature.items():
            if bom_parent != parent_id:
                continue
            theorique = volume * coef
            if rng.random() < 0.04:
                # Semaine non backflushée : AUCUN mouvement de sortie n'est créé.
                # C'est le cas « Sans consommation » — il doit rester absent de
                # la table de consommation, pas y figurer avec une quantité nulle.
                continue
            if child_id in derive_forte:
                facteur = Decimal(rng.randrange(103, 118)) / 100      # surconsommation
            else:
                facteur = Decimal(rng.randrange(985, 1_015)) / 1_000  # bruit normal
            consommation[(semaine, parent_id, child_id)] = (theorique * facteur).quantize(
                Decimal("0.000001")
            )

        # Composants sortis sans ligne de nomenclature (erreur de saisie d'OF).
        if rng.random() < 0.02:
            child_id = rng.choice(hors_nomenclature)
            if (semaine, parent_id, child_id) not in consommation:
                consommation[(semaine, parent_id, child_id)] = Decimal(rng.randint(5, 60))

    return _assemble(
        articles, nom_par_parent, nomenclature, programme_par_parent,
        production, consommation, dim_article, dim_nomenclature,
        dim_coef_programme, fact_production, coefs, now, rng,
    )


def _assemble(
    articles, nom_par_parent, nomenclature, programme_par_parent,
    production, consommation, dim_article, dim_nomenclature,
    dim_coef_programme, fact_production, coefs, now, rng,
) -> dict[str, list[tuple[Any, ...]]]:
    """Calcule les faits d'écart et les agrégats, en miroir du SQL gold."""
    par_id = {a["item_id"]: a for a in articles}
    uniformite = {
        (programme, child): max(q) - min(q) <= Decimal("0.000001")
        for (programme, child), q in coefs.items()
    }
    seuil = Decimal("0.5")

    fact_consommation: list[tuple[Any, ...]] = []
    for (semaine, parent_id, child_id), qty in consommation.items():
        annee, num = iso_week_fields(semaine)
        fact_consommation.append((
            semaine, parent_id, child_id, annee, num,
            programme_par_parent[parent_id], qty, rng.randint(1, 12), 0,
            datetime.combine(semaine, datetime.min.time(), tzinfo=UTC),
            datetime.combine(semaine + timedelta(days=4), datetime.min.time(), tzinfo=UTC),
            now,
        ))

    cles = set(consommation) | {
        (semaine, parent_id, child_id)
        for (semaine, parent_id) in production
        for (bom_parent, child_id) in nomenclature
        if bom_parent == parent_id
    }

    fact_ecart: list[tuple[Any, ...]] = []
    for semaine, parent_id, child_id in sorted(cles):
        coef = nomenclature.get((parent_id, child_id))
        volume = production.get((semaine, parent_id), Decimal(0))
        theorique = volume * coef if coef is not None else Decimal(0)
        reelle = consommation.get((semaine, parent_id, child_id), Decimal(0))
        ecart = theorique - reelle
        article_enfant = par_id[child_id]
        cout = article_enfant["std_cost_price"]
        programme = programme_par_parent[parent_id]

        statut = (
            "Hors nomenclature" if coef is None
            else "Sans consommation" if (semaine, parent_id, child_id) not in consommation
            else "Nominal"
        )
        uniforme = bool(uniformite.get((programme, child_id), False))
        annee, num = iso_week_fields(semaine)

        fact_ecart.append((
            semaine, parent_id, child_id, annee, num,
            programme, nom_par_parent[parent_id], "STATOR",
            article_enfant["item_name"], article_enfant["categorie"],
            article_enfant["programme"], article_enfant["std_unit"],
            coef, volume, reelle, theorique, ecart,
            (ecart / theorique * 100) if theorique > 0 else None,
            "Non-consommation" if ecart > seuil else "Surconsommation" if ecart < -seuil else "Conforme",
            statut,
            cout,
            ecart * (cout if cout is not None else Decimal(0)),
            uniforme,
            (ecart / coef) if (uniforme and coef and coef > 0) else None,
            rng.randint(0, 12), 0, now,
        ))

    agg_hebdo = _aggregate_hebdo(fact_ecart, now)
    agg_composant = _aggregate_composant(fact_ecart, now)
    dq = _quality_rows(fact_ecart, now)

    return {
        "dim_article": dim_article,
        "dim_nomenclature": dim_nomenclature,
        "dim_coef_programme": dim_coef_programme,
        "fact_production_parent": fact_production,
        "fact_consommation_composant": fact_consommation,
        "fact_ecart_backflush": fact_ecart,
        "agg_ecart_hebdo_programme": agg_hebdo,
        "agg_ecart_composant": agg_composant,
        "dq_controles": dq,
    }


# Indices des colonnes de fact_ecart_backflush utilisées par les agrégats.
_I = {name: position for position, name in enumerate(TABLES_BY_NAME["fact_ecart_backflush"].column_names)}


def _aggregate_hebdo(fact: list[tuple[Any, ...]], now: datetime) -> list[tuple[Any, ...]]:
    buckets: dict[tuple[date, str], dict[str, Any]] = {}
    for row in fact:
        key = (row[_I["semaine_debut"]], row[_I["parent_programme"]])
        bucket = buckets.setdefault(key, {
            "parents": set(), "composants": set(), "lignes": 0, "avec_ecart": 0,
            "hors_nom": 0, "sans_conso": 0, "theo": Decimal(0), "reel": Decimal(0),
            "net": Decimal(0), "nc": Decimal(0), "sc": Decimal(0),
            "val": Decimal(0), "val_nc": Decimal(0), "val_sc": Decimal(0), "val_abs": Decimal(0),
        })
        bucket["parents"].add(row[_I["parent_itemid"]])
        bucket["composants"].add(row[_I["child_itemid"]])
        bucket["lignes"] += 1
        bucket["avec_ecart"] += row[_I["type_ecart"]] != "Conforme"
        bucket["hors_nom"] += row[_I["statut_ligne"]] == "Hors nomenclature"
        bucket["sans_conso"] += row[_I["statut_ligne"]] == "Sans consommation"
        bucket["theo"] += row[_I["conso_theorique"]]
        bucket["reel"] += row[_I["conso_reelle"]]
        ecart, valorise = row[_I["ecart_brut"]], row[_I["ecart_valorise"]]
        bucket["net"] += ecart
        bucket["nc"] += max(ecart, Decimal(0))
        bucket["sc"] += max(-ecart, Decimal(0))
        bucket["val"] += valorise
        bucket["val_nc"] += max(valorise, Decimal(0))
        bucket["val_sc"] += max(-valorise, Decimal(0))
        bucket["val_abs"] += abs(valorise)

    return [
        (semaine, programme, *iso_week_fields(semaine),
         len(b["parents"]), len(b["composants"]), b["lignes"], b["avec_ecart"],
         b["hors_nom"], b["sans_conso"], b["theo"], b["reel"], b["net"], b["nc"], b["sc"],
         b["val"], b["val_nc"], b["val_sc"], b["val_abs"], now)
        for (semaine, programme), b in sorted(buckets.items())
    ]


def _aggregate_composant(fact: list[tuple[Any, ...]], now: datetime) -> list[tuple[Any, ...]]:
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for row in fact:
        key = (row[_I["child_itemid"]], row[_I["parent_programme"]])
        bucket = buckets.setdefault(key, {
            "name": row[_I["child_name"]], "cat": row[_I["child_categorie"]],
            "coefs": [], "uniforme": True, "parents": set(), "semaines": set(),
            "en_ecart": 0, "theo": Decimal(0), "reel": Decimal(0), "net": Decimal(0),
            "nc": Decimal(0), "sc": Decimal(0), "eq": Decimal(0),
            "cout": row[_I["child_cout_standard"]], "val": Decimal(0), "val_abs": Decimal(0),
        })
        if row[_I["coef_bom"]] is not None:
            bucket["coefs"].append(row[_I["coef_bom"]])
        bucket["uniforme"] &= bool(row[_I["is_coef_uniforme"]])
        bucket["parents"].add(row[_I["parent_itemid"]])
        bucket["semaines"].add(row[_I["semaine_debut"]])
        bucket["en_ecart"] += row[_I["type_ecart"]] != "Conforme"
        bucket["theo"] += row[_I["conso_theorique"]]
        bucket["reel"] += row[_I["conso_reelle"]]
        ecart, valorise = row[_I["ecart_brut"]], row[_I["ecart_valorise"]]
        bucket["net"] += ecart
        bucket["nc"] += max(ecart, Decimal(0))
        bucket["sc"] += max(-ecart, Decimal(0))
        bucket["eq"] += row[_I["ecart_equivalent_produit"]] or Decimal(0)
        bucket["val"] += valorise
        bucket["val_abs"] += abs(valorise)

    return [
        (child_id, programme, b["name"], b["cat"],
         min(b["coefs"]) if b["coefs"] else None, max(b["coefs"]) if b["coefs"] else None,
         b["uniforme"], len(b["parents"]), len(b["semaines"]), b["en_ecart"],
         min(b["semaines"]), max(b["semaines"]), b["theo"], b["reel"], b["net"],
         b["nc"], b["sc"],
         (b["net"] / b["theo"] * 100) if b["theo"] > 0 else None,
         "Non-consommation" if b["nc"] >= b["sc"] else "Surconsommation",
         b["eq"], b["cout"], b["val"], b["val_abs"], now)
        for (child_id, programme), b in sorted(buckets.items())
    ]


def _quality_rows(fact: list[tuple[Any, ...]], now: datetime) -> list[tuple[Any, ...]]:
    hors_nom = sum(1 for row in fact if row[_I["statut_ligne"]] == "Hors nomenclature")
    sans_cout = len({
        row[_I["child_itemid"]] for row in fact
        if row[_I["child_cout_standard"]] is None and row[_I["type_ecart"]] != "Conforme"
    })
    non_uniforme = len({
        (row[_I["parent_programme"]], row[_I["child_itemid"]])
        for row in fact if not row[_I["is_coef_uniforme"]]
    })
    controles = [
        ("lignes_hors_nomenclature", "ALERTE", "Cohérence", hors_nom,
         "Couples parent/composant consommés sans ligne de nomenclature correspondante."),
        ("composant_sans_cout_standard", "ALERTE", "Référentiel", sans_cout,
         "Composants en écart sans coût standard : leur impact financier est compté pour 0 €."),
        ("coef_non_uniforme", "INFO", "Nomenclature", non_uniforme,
         "Couples (programme, composant) à coefficient non uniforme."),
        ("article_hors_referentiel", "ERREUR", "Référentiel", 0,
         "Composants mouvementés absents de dim_article."),
        ("reconciliation_agg_detail", "ERREUR", "Cohérence", 0,
         "Écart entre le total valorisé de l'agrégat et celui du détail."),
    ]
    return [(nom, sev, dom, valeur, 0, valeur > 0, message, now)
            for nom, sev, dom, valeur, message in controles]


# ---------------------------------------------------------------------------
# Écriture
# ---------------------------------------------------------------------------
def write(conn: psycopg.Connection, dataset: dict[str, list[tuple[Any, ...]]], pg_schema: str) -> None:
    with conn.cursor() as cur:
        cur.execute(create_schema_sql(pg_schema))
    conn.commit()

    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        conn.commit()
        trgm = True
    except psycopg.Error:
        conn.rollback()
        trgm = False
        LOGGER.warning("pg_trgm indisponible : index de recherche ignorés.")

    for table in (*TABLES, META_INGESTION):
        with conn.cursor() as cur:
            cur.execute(
                pgsql.SQL("DROP TABLE IF EXISTS {}.{} CASCADE").format(
                    pgsql.Identifier(pg_schema), pgsql.Identifier(table.name)
                )
            )
            cur.execute(create_table_sql(table, schema=pg_schema))
        conn.commit()

    now = datetime.now(UTC)
    for table in TABLES:
        rows = dataset[table.name]
        columns = pgsql.SQL(", ").join(pgsql.Identifier(name) for name in table.column_names)
        statement = pgsql.SQL("COPY {}.{} ({}) FROM STDIN").format(
            pgsql.Identifier(pg_schema), pgsql.Identifier(table.name), columns
        )
        with conn.cursor() as cur, cur.copy(statement) as copy:
            for row in rows:
                copy.write_row(row)
        with conn.cursor() as cur:
            for index_sql in create_indexes_sql(table, schema=pg_schema):
                if not trgm and "gin_trgm_ops" in index_sql:
                    continue
                cur.execute(index_sql)
            cur.execute(
                pgsql.SQL("ANALYZE {}.{}").format(
                    pgsql.Identifier(pg_schema), pgsql.Identifier(table.name)
                )
            )
            cur.execute(
                pgsql.SQL(
                    "INSERT INTO {}.{} (table_name, source_table, row_count, started_at, "
                    "ended_at, duration_ms, status, run_id) "
                    "VALUES (%s, %s, %s, %s, %s, 0, 'SUCCES', 'seed-demo')"
                ).format(pgsql.Identifier(pg_schema), pgsql.Identifier(META_INGESTION.name)),
                (table.name, table.source, len(rows), now, now),
            )
        conn.commit()
        LOGGER.info("%-32s %8d ligne(s)", table.name, len(rows))


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s", stream=sys.stdout)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weeks", type=int, default=26, help="Nombre de semaines d'historique.")
    parser.add_argument("--seed", type=int, default=20260330, help="Graine aléatoire.")
    parser.add_argument("--pg-schema", default=SCHEMA)
    args = parser.parse_args(argv)

    url = os.getenv("LAKEBASE_PG_URL")
    if not url:
        raise RuntimeError("LAKEBASE_PG_URL doit être défini (Postgres de développement).")

    LOGGER.info("Génération de %d semaines de données de démonstration…", args.weeks)
    dataset = build_dataset(args.weeks, args.seed)
    with psycopg.connect(url) as conn:
        write(conn, dataset, args.pg_schema)
    LOGGER.info("Jeu de démonstration prêt.")


if __name__ == "__main__":  # pragma: no cover
    # `main()` est appelée directement, jamais via `raise SystemExit(main())`.
    #
    # Une tâche Databricks évalue ce fichier dans un noyau IPython : une
    # SystemExit y remonte comme une exception ordinaire et fait échouer la
    # tâche — MÊME avec le code 0. Le job affichait donc « construit avec
    # succès » puis « Workload failed » dans la foulée.
    #
    # Les échecs réels lèvent des exceptions, qui produisent de toute façon un
    # code de retour non nul en ligne de commande : rien n'est perdu.
    main()
