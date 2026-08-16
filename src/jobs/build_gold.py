"""Job ❶ — Construction du modèle gold « écarts backflush » dans Unity Catalog.

Exécute, dans l'ordre lexicographique, les fichiers de ``src/sql/gold/`` après
substitution des placeholders. À la fin, lit ``dq_controles`` et échoue si un
contrôle de sévérité ``ERREUR`` est en anomalie : mieux vaut un job rouge qu'un
tableau de bord faux.

Exemple d'appel (tâche ``spark_python_task``) ::

    python -m src.jobs.build_gold \
        --catalog emotors_data_champions --schema backflush \
        --bronze-catalog emotors_data_platform --bronze-schema bronze_erp \
        --silver-catalog emotors_data_champions --silver-schema silver_erp_ye \
        --date-from 2026-03-30 --seuil-conformite 0.5
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def _amorcer_chemin_projet() -> Path:
    """Place la racine du projet dans ``sys.path`` et la retourne.

    Une tâche ``spark_python_task`` n'exécute pas un paquet : Databricks lit le
    fichier et l'évalue, sans que la racine du bundle figure dans ``sys.path``.
    Sans cette amorce, ``from src.jobs...`` échoue par ``ModuleNotFoundError``,
    alors que le même script fonctionne en local via ``python -m``.

    Trois pistes sont essayées, car aucune n'est disponible dans tous les modes
    d'exécution : ``__file__`` n'est pas défini quand le code est évalué dans
    des globales de notebook, et ``sys.argv[0]`` ne l'est pas partout non plus.
    Chaque piste est remontée jusqu'au dossier contenant ``src/jobs/sqlutil.py``,
    ce qui identifie la racine sans dépendre d'une profondeur de répertoire.
    """
    candidats: list[Path] = []
    fichier = globals().get("__file__")
    if fichier:
        candidats.append(Path(fichier).resolve())
    if sys.argv and sys.argv[0]:
        candidats.append(Path(sys.argv[0]).resolve())
    candidats.append(Path.cwd().resolve())

    for candidat in candidats:
        for base in (candidat, *candidat.parents):
            if (base / "src" / "jobs" / "sqlutil.py").is_file():
                if str(base) not in sys.path:
                    sys.path.insert(0, str(base))
                return base

    raise RuntimeError(
        "Racine du projet introuvable : aucun dossier parent ne contient "
        "src/jobs/sqlutil.py. Vérifiez que le bundle a bien été synchronisé "
        f"en entier (pistes explorées : {[str(c) for c in candidats]})."
    )


RACINE = _amorcer_chemin_projet()

from src.jobs.sqlutil import (  # noqa: E402 — l'amorce ci-dessus doit précéder l'import
    render_template,
    split_statements,
    validate_identifier,
    validate_iso_date,
    validate_number,
)

LOGGER = logging.getLogger("backflush.build_gold")

#: Répertoire des scripts SQL, résolu depuis la racine détectée.
SQL_DIR = RACINE / "src" / "sql" / "gold"

#: Colonnes que `01_src_views.sql` lit dans chaque table source.
#:
#: Cette liste EST le contrat avec l'ERP. Elle est vérifiée avant d'exécuter
#: quoi que ce soit, pour deux raisons :
#:
#: 1. Un nom de colonne divergent ne se découvre sinon qu'à l'exécution, une
#:    erreur à la fois — chaque correction demandant un nouveau cycle complet de
#:    déploiement et de relance. Le contrôle préalable les remonte TOUTES d'un
#:    coup, avec la liste des colonnes réellement disponibles.
#: 2. Elle documente, en un seul endroit, ce que ce modèle attend de l'amont.
#:
#: Toute modification ici doit rester synchrone avec `01_src_views.sql`.
COLONNES_SOURCE: dict[str, tuple[str, ...]] = {
    "bronze.invent_trans": (
        "inventtransorigin", "itemid", "qty", "datephysical", "dataareaid",
    ),
    "bronze.invent_trans_origin": (
        "recid", "referencecategory", "referenceid", "itemid", "dataareaid",
    ),
    "bronze.prod_table": ("prodid", "itemid", "dataareaid"),
    "silver.silver_bom": (
        "bomid", "parent_itemid", "bom_version_name", "statut", "child_itemid",
        "child_qty", "child_unitid", "parent_physical_stock", "child_physical_stock",
    ),
    "silver.silver_base_article": (
        "item_id", "item_name", "item_description", "categorie", "item_group_id",
        "item_group_label", "programme", "std_cost_price", "std_unit",
        "silver_refreshed_at", "product_recid", "product_modified_at", "product_created_at",
    ),
}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="emotors_data_champions")
    parser.add_argument("--schema", default="backflush")
    parser.add_argument("--bronze-catalog", default="emotors_data_platform")
    parser.add_argument("--bronze-schema", default="bronze_erp")
    parser.add_argument("--silver-catalog", default="emotors_data_champions")
    parser.add_argument("--silver-schema", default="silver_erp_ye")
    parser.add_argument(
        "--date-from",
        default="2026-03-30",
        help="Début de l'historique analysé (AAAA-MM-JJ). Défaut : 30 mars 2026.",
    )
    parser.add_argument(
        "--seuil-conformite",
        type=float,
        default=0.5,
        help="Tolérance en unités au-delà de laquelle une ligne n'est plus conforme.",
    )
    parser.add_argument(
        "--company-predicate",
        default="1 = 1",
        help=(
            "Prédicat SQL de filtrage société D365, appliqué aux vues source. "
            "Exemple : \"dataareaid IN ('YE01','YE02')\". Non paramétrable par "
            "un utilisateur final : la valeur vient de la configuration du job."
        ),
    )
    parser.add_argument(
        "--fail-on-dq-error",
        action="store_true",
        default=True,
        help="Échouer si un contrôle qualité de sévérité ERREUR est en anomalie.",
    )
    parser.add_argument(
        "--no-fail-on-dq-error",
        dest="fail_on_dq_error",
        action="store_false",
        help="Journaliser les anomalies ERREUR sans faire échouer le job.",
    )
    return parser


def build_sql_params(args: argparse.Namespace) -> dict[str, str]:
    """Valide les paramètres et produit le dictionnaire de substitution.

    Les identifiants ne peuvent pas être liés par le driver SQL : ils sont
    interpolés. La validation stricte ci-dessous est donc la seule barrière
    contre une injection via la configuration du job.
    """
    return {
        "catalog": validate_identifier(args.catalog, label="catalog"),
        "schema": validate_identifier(args.schema, label="schema"),
        "bronze_catalog": validate_identifier(args.bronze_catalog, label="bronze_catalog"),
        "bronze_schema": validate_identifier(args.bronze_schema, label="bronze_schema"),
        "silver_catalog": validate_identifier(args.silver_catalog, label="silver_catalog"),
        "silver_schema": validate_identifier(args.silver_schema, label="silver_schema"),
        "date_from": validate_iso_date(args.date_from, label="date_from"),
        "seuil_conformite": validate_number(args.seuil_conformite, label="seuil_conformite"),
        # Non validable syntaxiquement : documenté comme paramètre d'exploitation,
        # jamais alimenté par une saisie utilisateur.
        "company_predicate": args.company_predicate,
    }


def list_sql_files(directory: Path = SQL_DIR) -> list[Path]:
    """Retourne les scripts SQL triés par préfixe numérique (ordre de dépendance)."""
    files = sorted(directory.glob("*.sql"))
    if not files:
        raise FileNotFoundError(f"Aucun script SQL trouvé dans {directory}")
    return files


def verifier_colonnes_source(spark, params: dict[str, str]) -> None:
    """Vérifie que chaque table source expose les colonnes attendues.

    Échoue AVANT toute exécution, en listant d'un seul coup toutes les
    divergences et les colonnes réellement disponibles. Sans ce contrôle, un
    nom de colonne différent ne se découvre qu'à l'exécution, une erreur à la
    fois, chacune coûtant un cycle complet de déploiement et de relance.
    """
    prefixes = {
        "bronze": f"{params['bronze_catalog']}.{params['bronze_schema']}",
        "silver": f"{params['silver_catalog']}.{params['silver_schema']}",
    }

    anomalies: list[str] = []
    for reference, attendues in COLONNES_SOURCE.items():
        origine, table = reference.split(".", 1)
        fqn = f"{prefixes[origine]}.{table}"
        try:
            disponibles = {nom.lower() for nom in spark.table(fqn).columns}
        except Exception as exc:  # table absente, droits manquants, catalogue inconnu…
            anomalies.append(f"  • {fqn} : table illisible — {str(exc).splitlines()[0]}")
            continue

        manquantes = [nom for nom in attendues if nom.lower() not in disponibles]
        if manquantes:
            anomalies.append(
                f"  • {fqn}\n"
                f"      colonnes manquantes : {', '.join(manquantes)}\n"
                f"      colonnes disponibles : {', '.join(sorted(disponibles))}"
            )

    if anomalies:
        raise RuntimeError(
            "Le schéma des sources ne correspond pas à ce que le modèle attend :\n"
            + "\n".join(anomalies)
            + "\n\nCorrigez les vues d'abstraction dans src/sql/gold/01_src_views.sql "
              "et la liste COLONNES_SOURCE de src/jobs/build_gold.py — ce sont les "
              "deux seuls endroits qui connaissent le nommage de l'amont."
        )

    LOGGER.info("Contrôle du schéma source : %d table(s) conformes.", len(COLONNES_SOURCE))


def run(spark, args: argparse.Namespace) -> dict[str, int]:
    """Exécute la construction complète et retourne le nombre de lignes par table."""
    params = build_sql_params(args)
    LOGGER.info(
        "Construction de %s.%s depuis %s (seuil de conformité : %s)",
        params["catalog"], params["schema"], params["date_from"], params["seuil_conformite"],
    )

    verifier_colonnes_source(spark, params)

    for sql_file in list_sql_files():
        statements = split_statements(render_template(sql_file.read_text(encoding="utf-8"), params))
        LOGGER.info("→ %s (%d instruction(s))", sql_file.name, len(statements))
        for statement in statements:
            spark.sql(statement)

    counts = _collect_row_counts(spark, params)
    for table, count in counts.items():
        LOGGER.info("   %-32s %10d ligne(s)", table, count)

    _check_data_quality(spark, params, fail_on_error=args.fail_on_dq_error)
    return counts


# Tables publiées, dans l'ordre de lecture attendu par l'aval.
PUBLISHED_TABLES = (
    "dim_article",
    "dim_nomenclature",
    "dim_coef_programme",
    "fact_production_parent",
    "fact_consommation_composant",
    "fact_ecart_backflush",
    "agg_ecart_hebdo_programme",
    "agg_ecart_composant",
    "dq_controles",
)


def _collect_row_counts(spark, params: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in PUBLISHED_TABLES:
        fqn = f"{params['catalog']}.{params['schema']}.{table}"
        counts[table] = spark.sql(f"SELECT COUNT(*) AS n FROM {fqn}").collect()[0]["n"]
    return counts


def _check_data_quality(spark, params: dict[str, str], *, fail_on_error: bool) -> None:
    fqn = f"{params['catalog']}.{params['schema']}.dq_controles"
    rows = spark.sql(
        f"SELECT controle, severite, valeur, message FROM {fqn} "
        f"WHERE en_anomalie ORDER BY severite, controle"
    ).collect()

    if not rows:
        LOGGER.info("Contrôles qualité : aucune anomalie.")
        return

    blocking = [row for row in rows if row["severite"] == "ERREUR"]
    for row in rows:
        LOGGER.warning(
            "[%s] %s = %s — %s", row["severite"], row["controle"], row["valeur"], row["message"]
        )

    if blocking and fail_on_error:
        noms = ", ".join(row["controle"] for row in blocking)
        raise RuntimeError(
            f"Contrôles qualité bloquants en anomalie : {noms}. "
            f"Consulter {fqn} pour le détail."
        )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
        stream=sys.stdout,
    )
    args = build_arg_parser().parse_args(argv)

    # Import tardif : le module doit rester importable hors d'un cluster Spark
    # (tests unitaires, lint, documentation).
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.getOrCreate()
    run(spark, args)
    LOGGER.info("Modèle gold construit avec succès.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
