"""Schéma Lakebase Postgres — source de vérité unique.

Le schéma est décrit ici de façon déclarative plutôt que dans un fichier ``.sql``
figé, pour trois raisons :

1. le job d'ingestion doit générer le MÊME DDL pour la table cible et pour la
   table de transit (« staging »), au suffixe près — un fichier SQL statique
   imposerait de dupliquer chaque définition ;
2. la liste ordonnée des colonnes pilote le ``COPY``, ce qui interdit tout
   décalage silencieux entre l'ordre des colonnes lues et écrites ;
3. le backend importe :data:`TABLES` pour valider les noms de colonnes exposés
   par l'API — un seul endroit à modifier quand le modèle évolue.

Le DDL réellement appliqué peut être imprimé à tout moment ::

    python -m src.jobs.lakebase_schema > schema.sql
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

#: Nom du schéma Postgres cible.
SCHEMA = "backflush"


@dataclass(frozen=True)
class Column:
    name: str
    pg_type: str
    #: ``True`` si la colonne fait partie de la clé primaire.
    primary_key: bool = False
    comment: str = ""


@dataclass(frozen=True)
class Index:
    #: Suffixe du nom de l'index ; le préfixe est le nom de la table.
    suffix: str
    #: Expression indexée, telle qu'elle sera écrite dans le ``CREATE INDEX``.
    expression: str
    method: str = "btree"


@dataclass(frozen=True)
class Table:
    name: str
    comment: str
    columns: Sequence[Column]
    indexes: Sequence[Index] = field(default_factory=tuple)
    #: Table Unity Catalog source (nom court dans le schéma gold).
    source: str = ""

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    @property
    def primary_key(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns if column.primary_key)


# ---------------------------------------------------------------------------
# Types réutilisés
# ---------------------------------------------------------------------------
QTY = "numeric(38,6)"      # quantités : marge large, l'ERP peut porter des KG à 6 décimales
MONEY = "numeric(20,6)"    # montants en euros
PCT = "numeric(12,4)"      # pourcentages
TS = "timestamptz"


# ---------------------------------------------------------------------------
# Dimensions
# ---------------------------------------------------------------------------
DIM_ARTICLE = Table(
    name="dim_article",
    source="dim_article",
    comment="Référentiel article : désignation, catégorie, groupe, programme, coût standard.",
    columns=(
        Column("item_id", "text", primary_key=True, comment="Référence article"),
        Column("item_name", "text"),
        Column("item_description", "text"),
        Column("categorie", "text"),
        Column("item_group_id", "text"),
        Column("item_group_label", "text"),
        Column("programme", "text"),
        Column("std_cost_price", MONEY),
        Column("std_unit", "text"),
        Column("snapshot_date", TS),
        Column("loaded_at", TS),
    ),
    indexes=(
        Index("programme", "(programme)"),
        Index("categorie", "(categorie)"),
        Index("groupe", "(item_group_id)"),
    ),
)

DIM_NOMENCLATURE = Table(
    name="dim_nomenclature",
    source="dim_nomenclature",
    comment="Nomenclature active aplatie : 1 ligne par (parent, composant).",
    columns=(
        Column("parent_itemid", "text", primary_key=True),
        Column("child_itemid", "text", primary_key=True),
        Column("bomid", "text"),
        Column("bom_version_name", "text"),
        Column("statut", "text"),
        Column("child_qty", QTY),
        Column("child_unitid", "text"),
        Column("parent_physical_stock", QTY),
        Column("child_physical_stock", QTY),
        Column("nb_lignes_bom", "integer"),
        Column("snapshot_date", TS),
        Column("loaded_at", TS),
    ),
    indexes=(Index("child", "(child_itemid)"),),
)

DIM_COEF_PROGRAMME = Table(
    name="dim_coef_programme",
    source="dim_coef_programme",
    comment="Coefficient BOM consolidé par (programme, composant) et uniformité.",
    columns=(
        Column("programme", "text", primary_key=True),
        Column("child_itemid", "text", primary_key=True),
        Column("nb_parents", "integer"),
        Column("child_qty_min", QTY),
        Column("child_qty_max", QTY),
        Column("child_qty", QTY),
        Column("is_coef_uniforme", "boolean"),
        Column("snapshot_date", TS),
        Column("loaded_at", TS),
    ),
    indexes=(Index("child", "(child_itemid)"),),
)


# ---------------------------------------------------------------------------
# Faits sources
# ---------------------------------------------------------------------------
FACT_PRODUCTION_PARENT = Table(
    name="fact_production_parent",
    source="fact_production_parent",
    comment="Production déclarée par article parent et semaine ISO.",
    columns=(
        Column("semaine_debut", "date", primary_key=True),
        Column("parent_itemid", "text", primary_key=True),
        Column("annee", "integer"),
        Column("semaine", "integer"),
        Column("parent_programme", "text"),
        Column("parent_name", "text"),
        Column("parent_categorie", "text"),
        Column("qty_produite", QTY),
        Column("nb_transactions", "integer"),
        Column("premiere_date", TS),
        Column("derniere_date", TS),
        Column("loaded_at", TS),
    ),
    indexes=(
        Index("prog_semaine", "(parent_programme, semaine_debut)"),
        Index("parent", "(parent_itemid, semaine_debut)"),
    ),
)

FACT_CONSOMMATION_COMPOSANT = Table(
    name="fact_consommation_composant",
    source="fact_consommation_composant",
    comment="Consommation réelle de composants (backflush), nette des retours.",
    columns=(
        Column("semaine_debut", "date", primary_key=True),
        Column("parent_itemid", "text", primary_key=True),
        Column("child_itemid", "text", primary_key=True),
        Column("annee", "integer"),
        Column("semaine", "integer"),
        Column("parent_programme", "text"),
        Column("qty_consommee", QTY),
        Column("nb_transactions", "integer"),
        Column("nb_retours", "integer"),
        Column("premiere_date", TS),
        Column("derniere_date", TS),
        Column("loaded_at", TS),
    ),
    indexes=(
        Index("child", "(child_itemid, semaine_debut)"),
        Index("prog_semaine", "(parent_programme, semaine_debut)"),
    ),
)


# ---------------------------------------------------------------------------
# Fait principal
# ---------------------------------------------------------------------------
#: Expression indexée pour la recherche plein texte. Le repository DOIT utiliser
#: cette expression à l'identique pour que l'index trigramme soit retenu.
SEARCH_EXPRESSION = (
    "(coalesce(child_itemid,'') || ' ' || coalesce(child_name,'') || ' ' "
    "|| coalesce(parent_itemid,'') || ' ' || coalesce(parent_name,''))"
)

FACT_ECART_BACKFLUSH = Table(
    name="fact_ecart_backflush",
    source="fact_ecart_backflush",
    comment="Écarts de consommation composant. Grain : parent × composant × semaine ISO.",
    columns=(
        Column("semaine_debut", "date", primary_key=True),
        Column("parent_itemid", "text", primary_key=True),
        Column("child_itemid", "text", primary_key=True),
        Column("annee", "integer"),
        Column("semaine", "integer"),
        Column("parent_programme", "text"),
        Column("parent_name", "text"),
        Column("parent_categorie", "text"),
        Column("child_name", "text"),
        Column("child_categorie", "text"),
        Column("child_programme", "text"),
        Column("child_unite", "text"),
        Column("coef_bom", QTY),
        Column("qty_parent_produite", QTY),
        Column("conso_reelle", QTY),
        Column("conso_theorique", QTY),
        Column("ecart_brut", QTY),
        Column("ecart_pct", PCT),
        Column("type_ecart", "text"),
        Column("statut_ligne", "text"),
        Column("child_cout_standard", MONEY),
        Column("ecart_valorise", MONEY),
        Column("is_coef_uniforme", "boolean"),
        Column("ecart_equivalent_produit", QTY),
        Column("nb_transactions_conso", "integer"),
        Column("nb_retours", "integer"),
        Column("loaded_at", TS),
    ),
    indexes=(
        Index("semaine", "(semaine_debut)"),
        Index("prog_semaine", "(parent_programme, semaine_debut)"),
        Index("child_semaine", "(child_itemid, semaine_debut)"),
        Index("parent_semaine", "(parent_itemid, semaine_debut)"),
        Index("cat_semaine", "(child_categorie, semaine_debut)"),
        Index("type_semaine", "(type_ecart, semaine_debut)"),
        # Tri par impact financier : très fréquent (« top écarts »).
        Index("impact", "(semaine_debut, (abs(ecart_valorise)) DESC)"),
        # Note : un index d'expression GIN exige une parenthèse EXTÉRIEURE en
        # plus de celles de l'expression → USING gin ((expr) gin_trgm_ops).
        Index("recherche", f"(({SEARCH_EXPRESSION}) gin_trgm_ops)", method="gin"),
    ),
)


# ---------------------------------------------------------------------------
# Agrégats
# ---------------------------------------------------------------------------
AGG_ECART_HEBDO_PROGRAMME = Table(
    name="agg_ecart_hebdo_programme",
    source="agg_ecart_hebdo_programme",
    comment="Agrégat hebdomadaire par programme (tendances, KPI, réconciliation).",
    columns=(
        Column("semaine_debut", "date", primary_key=True),
        Column("programme", "text", primary_key=True),
        Column("annee", "integer"),
        Column("semaine", "integer"),
        Column("nb_parents", "integer"),
        Column("nb_composants", "integer"),
        Column("nb_lignes", "integer"),
        Column("nb_lignes_avec_ecart", "integer"),
        Column("nb_lignes_hors_nomenclature", "integer"),
        Column("nb_lignes_sans_consommation", "integer"),
        Column("conso_theorique_totale", QTY),
        Column("conso_reelle_totale", QTY),
        Column("ecart_net", QTY),
        Column("non_consommation", QTY),
        Column("surconsommation", QTY),
        Column("ecart_valorise_total", MONEY),
        Column("non_consommation_valorisee", MONEY),
        Column("surconsommation_valorisee", MONEY),
        Column("ecart_valorise_absolu", MONEY),
        Column("loaded_at", TS),
    ),
    indexes=(Index("semaine", "(semaine_debut)"),),
)

AGG_ECART_COMPOSANT = Table(
    name="agg_ecart_composant",
    source="agg_ecart_composant",
    comment="Agrégat cumulé par composant et programme parent (classements, alertes).",
    columns=(
        Column("child_itemid", "text", primary_key=True),
        Column("parent_programme", "text", primary_key=True),
        Column("child_name", "text"),
        Column("child_categorie", "text"),
        Column("coef_bom", QTY),
        Column("coef_bom_max", QTY),
        Column("is_coef_uniforme", "boolean"),
        Column("nb_parents", "integer"),
        Column("nb_semaines_actives", "integer"),
        Column("nb_semaines_en_ecart", "integer"),
        Column("premiere_semaine", "date"),
        Column("derniere_semaine", "date"),
        Column("conso_theorique_totale", QTY),
        Column("conso_reelle_totale", QTY),
        Column("ecart_net", QTY),
        Column("non_consommation", QTY),
        Column("surconsommation", QTY),
        Column("ecart_pct_global", PCT),
        Column("type_ecart_dominant", "text"),
        Column("ecart_equivalent_produit_total", QTY),
        Column("child_cout_standard", MONEY),
        Column("ecart_valorise_total", MONEY),
        Column("ecart_valorise_absolu", MONEY),
        Column("loaded_at", TS),
    ),
    indexes=(Index("impact", "((abs(ecart_valorise_total)) DESC)"),),
)

DQ_CONTROLES = Table(
    name="dq_controles",
    source="dq_controles",
    comment="Résultat des contrôles qualité du modèle gold.",
    columns=(
        Column("controle", "text", primary_key=True),
        Column("severite", "text"),
        Column("domaine", "text"),
        Column("valeur", "bigint"),
        Column("seuil", "bigint"),
        Column("en_anomalie", "boolean"),
        Column("message", "text"),
        Column("loaded_at", TS),
    ),
)

#: Tables répliquées depuis Unity Catalog, dans l'ordre de publication.
TABLES: tuple[Table, ...] = (
    DIM_ARTICLE,
    DIM_NOMENCLATURE,
    DIM_COEF_PROGRAMME,
    FACT_PRODUCTION_PARENT,
    FACT_CONSOMMATION_COMPOSANT,
    FACT_ECART_BACKFLUSH,
    AGG_ECART_HEBDO_PROGRAMME,
    AGG_ECART_COMPOSANT,
    DQ_CONTROLES,
)

TABLES_BY_NAME = {table.name: table for table in TABLES}


# ---------------------------------------------------------------------------
# Table de supervision de l'ingestion (alimentée par le job, pas répliquée)
# ---------------------------------------------------------------------------
META_INGESTION = Table(
    name="meta_ingestion",
    source="",
    comment="Journal d'ingestion : une ligne par table et par exécution réussie.",
    columns=(
        Column("table_name", "text", primary_key=True),
        Column("source_table", "text"),
        Column("row_count", "bigint"),
        Column("started_at", TS),
        Column("ended_at", TS),
        Column("duration_ms", "bigint"),
        Column("status", "text"),
        Column("error_message", "text"),
        Column("run_id", "text"),
        Column("gold_loaded_at", TS),
    ),
)


# ---------------------------------------------------------------------------
# Génération du DDL
# ---------------------------------------------------------------------------
def qualified(name: str, *, schema: str = SCHEMA) -> str:
    return f'"{schema}"."{name}"'


def create_schema_sql(schema: str = SCHEMA) -> str:
    return f'CREATE SCHEMA IF NOT EXISTS "{schema}";'


def create_table_sql(table: Table, *, name: str | None = None, schema: str = SCHEMA) -> str:
    """DDL de création d'une table (ou de sa version de transit via ``name``)."""
    physical_name = name or table.name
    lines = [f'    "{col.name}" {col.pg_type}' for col in table.columns]
    if table.primary_key:
        keys = ", ".join(f'"{key}"' for key in table.primary_key)
        # Le nom de la contrainte suit le nom physique : deux tables (cible et
        # transit) ne peuvent pas porter le même nom de contrainte.
        lines.append(f'    CONSTRAINT "{physical_name}_pkey" PRIMARY KEY ({keys})')
    body = ",\n".join(lines)
    return f'CREATE TABLE {qualified(physical_name, schema=schema)} (\n{body}\n);'


def create_indexes_sql(table: Table, *, name: str | None = None, schema: str = SCHEMA) -> list[str]:
    physical_name = name or table.name
    return [
        f'CREATE INDEX "ix_{physical_name}_{index.suffix}" '
        f"ON {qualified(physical_name, schema=schema)} "
        f"USING {index.method} {index.expression};"
        for index in table.indexes
    ]


def comment_sql(table: Table, *, name: str | None = None, schema: str = SCHEMA) -> str:
    physical_name = name or table.name
    escaped = table.comment.replace("'", "''")
    return f"COMMENT ON TABLE {qualified(physical_name, schema=schema)} IS '{escaped}';"


def full_ddl(schema: str = SCHEMA) -> str:
    """DDL complet, à des fins de documentation et de revue."""
    parts: list[str] = [
        "-- Généré par src/jobs/lakebase_schema.py — ne pas éditer à la main.",
        create_schema_sql(schema),
        "CREATE EXTENSION IF NOT EXISTS pg_trgm;",
        "",
    ]
    for table in (*TABLES, META_INGESTION):
        parts.append(create_table_sql(table, schema=schema))
        parts.append(comment_sql(table, schema=schema))
        parts.extend(create_indexes_sql(table, schema=schema))
        parts.append("")
    return "\n".join(parts)


if __name__ == "__main__":  # pragma: no cover
    print(full_ddl())
