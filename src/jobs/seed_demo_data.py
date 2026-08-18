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
    python -m src.jobs.seed_demo_data
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
    TABLES_PARAM,
    create_indexes_sql,
    create_param_table_sql,
    create_schema_sql,
    create_table_sql,
)

LOGGER = logging.getLogger("backflush.seed")

#: Premier lundi d'historique, quelle que soit la cible.
#:
#: Aligné sur la variable de bundle ``date_from`` (databricks.yml) : le jeu de
#: démonstration doit couvrir la même fenêtre que la production, sinon les
#: bornes du slicer temporel diffèrent entre l'environnement local et
#: Databricks et l'on met au point l'application sur une période qui n'existe
#: nulle part ailleurs.
DEBUT_HISTORIQUE = date(2026, 3, 30)

PROGRAMMES = ["M3", "M3GEN2", "M2BEV", "K9", "COMMUN"]
CATEGORIES_COMPOSANT = ["VIS", "AIMANTS", "MEL", "ROULEMENT", "CABLE", "RESINE", "TOLE"]
UNITES = {"MEL": "KG", "RESINE": "L"}


def iso_week_fields(monday: date) -> tuple[int, int]:
    """Retourne (année ISO, semaine ISO) pour un lundi donné."""
    iso = monday.isocalendar()
    return iso.year, iso.week


def build_dataset(
    seed: int, *, depuis: date = DEBUT_HISTORIQUE, jusqu_a: date | None = None
) -> dict[str, list[tuple[Any, ...]]]:
    """Construit toutes les tables en mémoire. Déterministe pour un ``seed`` donné.

    :param depuis: premier lundi d'historique (recalé sur le lundi de sa semaine).
    :param jusqu_a: dernier lundi ; par défaut, la semaine en cours.
    """
    rng = random.Random(seed)
    now = datetime.now(UTC)

    # --- Référentiel article -------------------------------------------------
    articles: list[dict[str, Any]] = []
    parents: list[dict[str, Any]] = []
    composants: list[dict[str, Any]] = []

    for programme in PROGRAMMES[:-1]:                     # COMMUN n'a pas de parent
        for index in range(1, 13):
            item_id = f"{programme}-STA-{index:03d}"
            # Deux lignes de production par programme : de quoi vérifier que le
            # périmètre discrimine bien à l'intérieur d'un même programme.
            parent = {
                "perimetre": f"{programme} - STATOR {'A' if index <= 6 else 'B'}",
                "type_produit": "STATOR",
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
            a["programme"], a.get("perimetre"), a.get("type_produit"),
            a["std_cost_price"], a["std_unit"], now, now,
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
    perimetre_par_parent = {p["item_id"]: p["perimetre"] for p in parents}
    programme_par_perimetre = {p["perimetre"]: p["programme"] for p in parents}
    coefs: dict[tuple[str, str], list[Decimal]] = {}
    for (parent_id, child_id), qty in nomenclature.items():
        coefs.setdefault((perimetre_par_parent[parent_id], child_id), []).append(qty)

    dim_coef_perimetre = [
        (perimetre, child_id, programme_par_perimetre[perimetre],
         len(quantites), min(quantites), max(quantites), min(quantites),
         max(quantites) - min(quantites) <= Decimal("0.000001"), now, now)
        for (perimetre, child_id), quantites in coefs.items()
    ]

    # --- Production et consommation -----------------------------------------
    # L'historique est ancré sur une date FIXE, pas sur un nombre de semaines
    # glissant : le jeu de démonstration doit débuter au même lundi que les
    # données réelles, faute de quoi il dérive d'une semaine à chaque exécution.
    premier = depuis - timedelta(days=depuis.weekday())
    dernier = jusqu_a or date.today()
    dernier -= timedelta(days=dernier.weekday())
    nb_semaines = max(1, (dernier - premier).days // 7 + 1)
    semaines = [premier + timedelta(weeks=offset) for offset in range(nb_semaines)]

    # La production est engendrée au niveau de l'ORDRE DE FABRICATION, puis
    # agrégée au parent. L'ordre importe : les deux mailles doivent décrire les
    # mêmes mouvements, sinon la table par OF et la table de détail ne se
    # réconcilieraient pas — et le contrôle qualité qui les compare échouerait
    # sur un défaut du générateur, pas du modèle.
    production_of: dict[tuple[date, str, str], Decimal] = {}
    for semaine in semaines:
        for parent in parents:
            if rng.random() < 0.08:                       # arrêt de ligne
                continue
            volume = Decimal(rng.randint(400, 2_600))
            # Un à trois lancements par semaine, de tailles inégales : c'est ce
            # que la maille parent × semaine confond.
            nb_of = rng.choices([1, 2, 3], weights=[5, 3, 2])[0]
            parts = [Decimal(rng.randint(1, 10)) for _ in range(nb_of)]
            total_parts = sum(parts)
            restant = volume
            for index, part in enumerate(parts):
                prod_id = f"OF-{parent['item_id']}-{semaine:%y%m%d}-{index + 1}"
                quantite = (
                    restant if index == nb_of - 1
                    else (volume * part / total_parts).quantize(Decimal("1"))
                )
                restant -= quantite
                if quantite > 0:
                    production_of[(semaine, parent["item_id"], prod_id)] = quantite

    production: dict[tuple[date, str], Decimal] = {}
    for (semaine, parent_id, _), quantite in production_of.items():
        production[(semaine, parent_id)] = production.get((semaine, parent_id), Decimal(0)) + quantite

    nom_par_parent = {p["item_id"]: p["item_name"] for p in parents}
    fact_production = [
        (
            semaine, parent_id, *iso_week_fields(semaine),
            programme_par_parent[parent_id], perimetre_par_parent[parent_id],
            nom_par_parent[parent_id],
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

    # Consommation, elle aussi engendrée par OF. La semaine du mouvement n'est
    # pas toujours celle de la production : un OF lancé en fin de semaine sort
    # ses composants avant de déclarer son produit fini. Ce DÉCALAGE DE CALAGE
    # est un phénomène réel, invisible à la maille parent × semaine où il se
    # compense entre lancements — c'est précisément ce que la vue par OF montre.
    consommation_of: dict[tuple[date, str, str, str], Decimal] = {}
    for (semaine, parent_id, prod_id), volume in production_of.items():
        # 10 % des OF sortent leurs composants la semaine précédente. Le premier
        # lundi de l'historique est épargné : un mouvement antérieur à la fenêtre
        # n'aurait pas de production en regard et fausserait la lecture du jeu
        # de démonstration sans rien démontrer.
        decale = rng.random() < 0.10 and semaine > semaines[0]
        semaine_conso = semaine - timedelta(weeks=1) if decale else semaine

        for (bom_parent, child_id), coef in nomenclature.items():
            if bom_parent != parent_id:
                continue
            theorique = volume * coef
            if rng.random() < 0.04:
                # OF non backflushé : AUCUN mouvement de sortie n'est créé.
                # C'est le cas « Sans consommation » — il doit rester absent de
                # la table de consommation, pas y figurer avec une quantité nulle.
                continue
            if child_id in derive_forte:
                facteur = Decimal(rng.randrange(103, 118)) / 100      # surconsommation
            else:
                facteur = Decimal(rng.randrange(985, 1_015)) / 1_000  # bruit normal
            cle = (semaine_conso, parent_id, child_id, prod_id)
            consommation_of[cle] = consommation_of.get(cle, Decimal(0)) + (
                theorique * facteur
            ).quantize(Decimal("0.000001"))

        # Composants sortis sans ligne de nomenclature (erreur de saisie d'OF).
        if rng.random() < 0.02:
            child_id = rng.choice(hors_nomenclature)
            cle = (semaine_conso, parent_id, child_id, prod_id)
            if cle not in consommation_of:
                consommation_of[cle] = Decimal(rng.randint(5, 60))

    consommation: dict[tuple[date, str, str], Decimal] = {}
    for (semaine, parent_id, child_id, _), qty in consommation_of.items():
        cle = (semaine, parent_id, child_id)
        consommation[cle] = consommation.get(cle, Decimal(0)) + qty

    return _assemble(
        articles, nom_par_parent, nomenclature, programme_par_parent,
        perimetre_par_parent,
        production, consommation, dim_article, dim_nomenclature,
        dim_coef_perimetre, fact_production, coefs, now, rng,
        production_of, consommation_of,
    )


def _assemble(
    articles, nom_par_parent, nomenclature, programme_par_parent,
    perimetre_par_parent,
    production, consommation, dim_article, dim_nomenclature,
    dim_coef_perimetre, fact_production, coefs, now, rng,
    production_of, consommation_of,
) -> dict[str, list[tuple[Any, ...]]]:
    """Calcule les faits d'écart et les agrégats, en miroir du SQL gold."""
    par_id = {a["item_id"]: a for a in articles}
    uniformite = {
        (perimetre, child): max(q) - min(q) <= Decimal("0.000001")
        for (perimetre, child), q in coefs.items()
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
        perimetre = perimetre_par_parent[parent_id]
        uniforme = bool(uniformite.get((perimetre, child_id), False))
        annee, num = iso_week_fields(semaine)

        fact_ecart.append((
            semaine, parent_id, child_id, annee, num,
            programme, perimetre, nom_par_parent[parent_id], "STATOR",
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

    fact_ecart_of = _ecarts_par_of(
        production_of, consommation_of, nomenclature, par_id, nom_par_parent,
        programme_par_parent, perimetre_par_parent, uniformite, seuil, now, rng,
    )

    agg_hebdo = _aggregate_hebdo(fact_ecart, now)
    agg_composant = _aggregate_composant(fact_ecart, now)
    dq = _quality_rows(fact_ecart, now)

    return {
        "dim_article": dim_article,
        "dim_nomenclature": dim_nomenclature,
        "dim_coef_perimetre": dim_coef_perimetre,
        "fact_production_parent": fact_production,
        "fact_consommation_composant": fact_consommation,
        "fact_ecart_backflush": fact_ecart,
        "fact_ecart_of": fact_ecart_of,
        "agg_ecart_hebdo_programme": agg_hebdo,
        "agg_ecart_composant": agg_composant,
        "dq_controles": dq,
    }


#: Cycle de vie D365 (`ProdStatus`) tel que traduit par `31_fact_ecart_of.sql`,
#: avec le poids de chacun dans un parc réel : la quasi-totalité des ordres est
#: clôturée, une poignée reste en cours. Un tirage uniforme donnerait un sixième
#: d'OF « Créé », et masquerait le fait qu'un statut non terminé est l'exception
#: — donc précisément le cas qu'on cherche quand on filtre là-dessus.
#:
#: Le booléen dit si l'ordre porte une date de clôture. Il ne s'invente pas :
#: `finisheddate` n'est renseignée qu'à partir de la déclaration de fin, et la
#: vue source ramène à NULL la sentinelle `1900-01-01` que D365 y écrit avant.
STATUTS_OF_DEMO: tuple[tuple[str, int, bool], ...] = (
    ("Créé", 1, False),
    ("Estimé", 1, False),
    ("Planifié", 1, False),
    ("Lancé", 3, False),
    ("Démarré", 4, False),
    ("Déclaré terminé", 8, True),
    ("Clôturé", 81, True),
)


def _ecarts_par_of(
    production_of, consommation_of, nomenclature, par_id, nom_par_parent,
    programme_par_parent, perimetre_par_parent, uniformite, seuil, now, rng,
) -> list[tuple[Any, ...]]:
    """Écarts à la maille OF, en miroir de ``31_fact_ecart_of.sql``.

    Même jointure complète qu'à la maille parent : la clé est l'union des
    couples consommés et des couples attendus. Un OF dont les composants sont
    sortis la semaine précédente produit donc DEUX lignes — une non-consommation
    la semaine de production, une surconsommation la semaine du mouvement. C'est
    le comportement attendu, et ce que la maille parent masquait.
    """
    volumes = {
        (semaine, parent_id, prod_id): volume
        for (semaine, parent_id, prod_id), volume in production_of.items()
    }
    attendus = {
        (semaine, prod_id, parent_id, child_id)
        for (semaine, parent_id, prod_id) in volumes
        for (bom_parent, child_id) in nomenclature
        if bom_parent == parent_id
    }
    reels = {
        (semaine, prod_id, parent_id, child_id)
        for (semaine, parent_id, child_id, prod_id) in consommation_of
    }

    # Le statut est un attribut de l'ORDRE, pas de la ligne : tiré une fois par
    # OF, puis relu. Tiré à chaque ligne, le même ordre apparaîtrait « Clôturé »
    # sur un composant et « Lancé » sur un autre — et le filtre « Statut OF »
    # renverrait des demi-ordres, un défaut qu'aucun total ne révélerait.
    libelles = [libelle for libelle, _, _ in STATUTS_OF_DEMO]
    poids = [poids for _, poids, _ in STATUTS_OF_DEMO]
    cloture_par_statut = {libelle: cloture for libelle, _, cloture in STATUTS_OF_DEMO}
    ofs = {prod_id for (_, _, prod_id) in production_of}
    ofs |= {prod_id for (_, _, _, prod_id) in consommation_of}
    statut_par_of = {
        prod_id: rng.choices(libelles, weights=poids)[0] for prod_id in sorted(ofs)
    }

    lignes: list[tuple[Any, ...]] = []
    for semaine, prod_id, parent_id, child_id in sorted(attendus | reels):
        coef = nomenclature.get((parent_id, child_id))
        volume = volumes.get((semaine, parent_id, prod_id), Decimal(0))
        theorique = volume * coef if coef is not None else Decimal(0)
        reelle = consommation_of.get((semaine, parent_id, child_id, prod_id), Decimal(0))
        ecart = theorique - reelle
        enfant = par_id[child_id]
        cout = enfant["std_cost_price"]
        perimetre = perimetre_par_parent[parent_id]
        uniforme = bool(uniformite.get((perimetre, child_id), False))
        annee, num = iso_week_fields(semaine)
        statut = (
            "Hors nomenclature" if coef is None
            else "Sans consommation"
            if (semaine, parent_id, child_id, prod_id) not in consommation_of
            else "Nominal"
        )
        statut_of = statut_par_of[prod_id]
        lignes.append((
            semaine, prod_id, parent_id, child_id, annee, num,
            f"BOM-{parent_id}",
            # Pas de date de clôture tant que l'ordre n'est pas déclaré terminé.
            datetime.combine(semaine + timedelta(days=6), datetime.min.time(), tzinfo=UTC)
            if cloture_par_statut[statut_of] else None,
            statut_of,
            programme_par_parent[parent_id], perimetre,
            nom_par_parent[parent_id], "STATOR",
            enfant["item_name"], enfant["categorie"], enfant["std_unit"],
            coef, volume, reelle, theorique, ecart,
            (ecart / theorique * 100) if theorique > 0 else None,
            "Non-consommation" if ecart > seuil
            else "Surconsommation" if ecart < -seuil else "Conforme",
            statut,
            cout,
            ecart * (cout if cout is not None else Decimal(0)),
            uniforme,
            (ecart / coef) if (uniforme and coef and coef > 0) else None,
            rng.randint(0, 6), 0, now,
        ))
    return lignes


# Indices des colonnes de fact_ecart_backflush utilisées par les agrégats.
_I = {name: position for position, name in enumerate(TABLES_BY_NAME["fact_ecart_backflush"].column_names)}


def _aggregate_hebdo(fact: list[tuple[Any, ...]], now: datetime) -> list[tuple[Any, ...]]:
    buckets: dict[tuple[date, str], dict[str, Any]] = {}
    for row in fact:
        key = (row[_I["semaine_debut"]], row[_I["parent_programme"]],
               row[_I["parent_perimetre"]])
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
        (semaine, programme, perimetre, *iso_week_fields(semaine),
         len(b["parents"]), len(b["composants"]), b["lignes"], b["avec_ecart"],
         b["hors_nom"], b["sans_conso"], b["theo"], b["reel"], b["net"], b["nc"], b["sc"],
         b["val"], b["val_nc"], b["val_sc"], b["val_abs"], now)
        for (semaine, programme, perimetre), b in sorted(buckets.items())
    ]


def _aggregate_composant(fact: list[tuple[Any, ...]], now: datetime) -> list[tuple[Any, ...]]:
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for row in fact:
        key = (row[_I["child_itemid"]], row[_I["parent_programme"]],
               row[_I["parent_perimetre"]])
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
        (child_id, programme, perimetre, b["name"], b["cat"],
         min(b["coefs"]) if b["coefs"] else None, max(b["coefs"]) if b["coefs"] else None,
         b["uniforme"], len(b["parents"]), len(b["semaines"]), b["en_ecart"],
         min(b["semaines"]), max(b["semaines"]), b["theo"], b["reel"], b["net"],
         b["nc"], b["sc"],
         (b["net"] / b["theo"] * 100) if b["theo"] > 0 else None,
         "Non-consommation" if b["nc"] >= b["sc"] else "Surconsommation",
         b["eq"], b["cout"], b["val"], b["val_abs"], now)
        for (child_id, programme, perimetre), b in sorted(buckets.items())
    ]


def _quality_rows(fact: list[tuple[Any, ...]], now: datetime) -> list[tuple[Any, ...]]:
    hors_nom = sum(1 for row in fact if row[_I["statut_ligne"]] == "Hors nomenclature")
    sans_cout = len({
        row[_I["child_itemid"]] for row in fact
        if row[_I["child_cout_standard"]] is None and row[_I["type_ecart"]] != "Conforme"
    })
    non_uniforme = len({
        (row[_I["parent_perimetre"]], row[_I["child_itemid"]])
        for row in fact if not row[_I["is_coef_uniforme"]]
    })
    controles = [
        ("lignes_hors_nomenclature", "ALERTE", "Cohérence", hors_nom,
         "Couples parent/composant consommés sans ligne de nomenclature correspondante."),
        ("composant_sans_cout_standard", "ALERTE", "Référentiel", sans_cout,
         "Composants en écart sans coût standard : leur impact financier est compté pour 0 €."),
        ("coef_non_uniforme", "INFO", "Nomenclature", non_uniforme,
         "Couples (périmètre, composant) à coefficient non uniforme."),
        # Le jeu de démonstration renseigne toujours la ligne de production : le
        # contrôle est présent mais à zéro, ce qui reste sa valeur nominale.
        ("parent_sans_perimetre", "ALERTE", "Référentiel", 0,
         "Part de la production, en %, dont le parent n'a pas de ligne de production renseignée."),
        ("article_hors_referentiel", "ERREUR", "Référentiel", 0,
         "Composants mouvementés absents de dim_article."),
        ("reconciliation_agg_detail", "ERREUR", "Cohérence", 0,
         "Écart entre le total valorisé de l'agrégat et celui du détail."),
        # Le générateur produit les deux mailles à partir des MÊMES mouvements :
        # la réconciliation est vraie par construction, et le test
        # `test_grain_of` le vérifie plutôt que de s'en remettre à cette ligne.
        ("reconciliation_of_detail", "ERREUR", "Cohérence", 0,
         "Écart entre le total des écarts par OF et celui de la table de détail."),
        # Les statuts du générateur sont pris dans STATUTS_OF_DEMO, qui est
        # l'énumération traduite par 31_*. Zéro est donc la valeur juste, et
        # non une valeur par défaut.
        ("of_statut_inconnu", "ALERTE", "Référentiel", 0,
         "Ordres de fabrication portant un statut D365 hors énumération traduite."),
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

    # Les tables de paramétrage sont créées mais JAMAIS vidées : elles portent
    # des arbitrages saisis à la main, que ce script n'a aucun moyen de
    # reproduire. C'est le même contrat qu'en production.
    with conn.cursor() as cur:
        for table in TABLES_PARAM:
            cur.execute(create_param_table_sql(table, schema=pg_schema))
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
    parser.add_argument(
        "--depuis",
        type=date.fromisoformat,
        default=DEBUT_HISTORIQUE,
        help="Premier lundi d'historique (AAAA-MM-JJ). Défaut : %(default)s.",
    )
    parser.add_argument("--seed", type=int, default=20260330, help="Graine aléatoire.")
    parser.add_argument("--pg-schema", default=SCHEMA)
    args = parser.parse_args(argv)

    url = os.getenv("LAKEBASE_PG_URL")
    if not url:
        raise RuntimeError("LAKEBASE_PG_URL doit être défini (Postgres de développement).")

    LOGGER.info("Génération des données de démonstration depuis le %s…", args.depuis)
    dataset = build_dataset(args.seed, depuis=args.depuis)
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
