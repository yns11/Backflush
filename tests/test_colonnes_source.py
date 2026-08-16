"""Tests du contrôle préalable du schéma source.

Ce contrôle est la réponse à un mode d'échec coûteux : un nom de colonne
divergent côté ERP ne se découvrait qu'à l'exécution, une erreur à la fois,
chaque correction demandant un cycle complet de déploiement et de relance.

Les tests vérifient aussi que la liste déclarée reste synchrone avec le SQL —
une liste qui ment est pire que pas de liste du tout.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.jobs.build_gold import COLONNES_SOURCE, verifier_colonnes_source

RACINE = Path(__file__).resolve().parents[1]
VUES_SQL = RACINE / "src" / "sql" / "gold" / "01_src_views.sql"

PARAMS = {
    "bronze_catalog": "cat_bronze", "bronze_schema": "sch_bronze",
    "silver_catalog": "cat_silver", "silver_schema": "sch_silver",
}


class SparkFactice:
    """Spark minimal : retourne les colonnes déclarées, ou lève si table inconnue."""

    def __init__(self, schemas: dict[str, list[str]]) -> None:
        self._schemas = schemas
        self.tables_lues: list[str] = []

    def table(self, fqn: str):
        self.tables_lues.append(fqn)
        if fqn not in self._schemas:
            raise ValueError(f"[TABLE_OR_VIEW_NOT_FOUND] The table or view `{fqn}` cannot be found.")
        colonnes = self._schemas[fqn]
        return type("DataFrameFactice", (), {"columns": colonnes})()


def _schemas_complets() -> dict[str, list[str]]:
    """Schémas conformes, construits depuis la déclaration elle-même."""
    prefixes = {"bronze": "cat_bronze.sch_bronze", "silver": "cat_silver.sch_silver"}
    schemas = {}
    for reference, colonnes in COLONNES_SOURCE.items():
        origine, table = reference.split(".", 1)
        # Des colonnes supplémentaires sont normales : on ne vérifie qu'une
        # inclusion, jamais une égalité stricte.
        schemas[f"{prefixes[origine]}.{table}"] = [*colonnes, "colonne_supplementaire"]
    return schemas


class TestVerificationSchema:
    def test_un_schema_conforme_passe(self) -> None:
        spark = SparkFactice(_schemas_complets())
        verifier_colonnes_source(spark, PARAMS)
        assert len(spark.tables_lues) == len(COLONNES_SOURCE)

    def test_la_casse_des_noms_est_ignoree(self) -> None:
        """Les métastores restituent parfois les noms en majuscules."""
        schemas = {fqn: [c.upper() for c in cols] for fqn, cols in _schemas_complets().items()}
        verifier_colonnes_source(SparkFactice(schemas), PARAMS)

    def test_une_colonne_manquante_est_signalee_avec_les_disponibles(self) -> None:
        schemas = _schemas_complets()
        cible = "cat_silver.sch_silver.silver_base_article"
        schemas[cible] = [c for c in schemas[cible] if c != "silver_refreshed_at"]

        with pytest.raises(RuntimeError) as erreur:
            verifier_colonnes_source(SparkFactice(schemas), PARAMS)

        message = str(erreur.value)
        assert "silver_refreshed_at" in message
        assert "colonnes disponibles" in message
        assert "item_id" in message, "Les colonnes réelles doivent être listées."

    def test_toutes_les_anomalies_sont_remontees_ensemble(self) -> None:
        """Le point essentiel : un seul cycle doit suffire à tout corriger."""
        schemas = _schemas_complets()
        schemas["cat_silver.sch_silver.silver_base_article"] = ["item_id"]
        schemas["cat_silver.sch_silver.silver_bom"] = ["bomid"]
        del schemas["cat_bronze.sch_bronze.prod_table"]

        with pytest.raises(RuntimeError) as erreur:
            verifier_colonnes_source(SparkFactice(schemas), PARAMS)

        message = str(erreur.value)
        assert "silver_base_article" in message
        assert "silver_bom" in message
        assert "prod_table" in message
        assert message.count("  • ") == 3

    def test_une_table_absente_ne_masque_pas_les_suivantes(self) -> None:
        schemas = _schemas_complets()
        del schemas["cat_bronze.sch_bronze.invent_trans"]
        with pytest.raises(RuntimeError, match="illisible"):
            verifier_colonnes_source(SparkFactice(schemas), PARAMS)

    def test_le_message_indique_ou_corriger(self) -> None:
        schemas = _schemas_complets()
        schemas["cat_bronze.sch_bronze.prod_table"] = ["prodid"]
        with pytest.raises(RuntimeError) as erreur:
            verifier_colonnes_source(SparkFactice(schemas), PARAMS)
        assert "01_src_views.sql" in str(erreur.value)


class TestSynchronisationAvecLeSql:
    """La liste déclarée doit refléter ce que le SQL lit réellement."""

    @pytest.mark.parametrize(
        ("reference", "table_sql"),
        [
            ("bronze.invent_trans", "invent_trans"),
            ("bronze.invent_trans_origin", "invent_trans_origin"),
            ("bronze.prod_table", "prod_table"),
            ("silver.silver_bom", "silver_bom"),
            ("silver.silver_base_article", "silver_base_article"),
        ],
    )
    def test_chaque_table_du_sql_est_declaree(self, reference: str, table_sql: str) -> None:
        sql = VUES_SQL.read_text(encoding="utf-8")
        assert table_sql in sql, f"{table_sql} n'apparaît plus dans 01_src_views.sql."
        assert reference in COLONNES_SOURCE

    def test_aucune_colonne_declaree_n_est_absente_du_sql(self) -> None:
        """Une colonne déclarée mais jamais lue alourdirait le contrat pour rien."""
        sql = VUES_SQL.read_text(encoding="utf-8").lower()
        for reference, colonnes in COLONNES_SOURCE.items():
            for colonne in colonnes:
                assert re.search(rf"\b{re.escape(colonne)}\b", sql), (
                    f"{reference}.{colonne} est déclarée mais n'apparaît pas dans "
                    f"01_src_views.sql : la liste et le SQL ont divergé."
                )

    def test_snapshot_date_n_est_plus_lu_dans_les_sources(self) -> None:
        """Régression : `snapshot_date` n'existe dans aucune table source.

        Il est produit par le modèle, jamais lu depuis l'ERP.
        """
        for colonnes in COLONNES_SOURCE.values():
            assert "snapshot_date" not in colonnes
