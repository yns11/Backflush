-- =============================================================================
-- 01 — Couche d'abstraction des sources
-- =============================================================================
-- OBJECTIF : concentrer en UN SEUL FICHIER toutes les hypothèses de nommage sur
-- les tables bronze/silver. Si l'ERP ou la couche silver renomme une colonne,
-- seule cette vue change — aucun autre fichier SQL n'est impacté.
--
-- Placeholders :
--   {catalog} {schema}            cible
--   {bronze_catalog} {bronze_schema}   ex. emotors_data_platform / bronze_erp
--   {silver_catalog} {silver_schema}   ex. emotors_data_champions / silver_erp_ye
--   {company_predicate}           filtre société D365 (défaut : 1 = 1)
--
-- Catégories de référence D365 (InventTransOrigin.ReferenceCategory) :
--   2 = Production (entrée du produit fini/semi-fini en stock)
--   8 = Ligne de nomenclature de l'OF (sortie du composant)
-- =============================================================================

-- --- Mouvements de stock -----------------------------------------------------
CREATE OR REPLACE VIEW {catalog}.{schema}.v_src_invent_trans
COMMENT 'Mouvements de stock normalisés (InventTrans). Source d''autorité pour les quantités et la date physique.'
AS
SELECT
    CAST(it.inventtransorigin AS BIGINT)  AS transorigin_id,
    CAST(it.itemid            AS STRING)  AS item_id,
    CAST(it.qty               AS DECIMAL(38, 6)) AS qty,
    CAST(it.datephysical      AS DATE)    AS date_physique,
    CAST(it.dataareaid        AS STRING)  AS company
FROM {bronze_catalog}.{bronze_schema}.invent_trans AS it
WHERE it.datephysical IS NOT NULL      -- un mouvement non validé physiquement n'est pas consommé
  AND it.qty IS NOT NULL
  AND it.qty <> 0
  AND ({company_predicate});

-- --- Origine des mouvements --------------------------------------------------
CREATE OR REPLACE VIEW {catalog}.{schema}.v_src_invent_trans_origin
COMMENT 'Origine des mouvements (InventTransOrigin) : type de référence et identifiant de l''ordre de fabrication.'
AS
SELECT
    CAST(ito.recid             AS BIGINT) AS transorigin_id,
    CAST(ito.referencecategory AS INT)    AS reference_category,
    CAST(ito.referenceid       AS STRING) AS reference_id,   -- = ProdId pour les catégories 2 et 8
    CAST(ito.itemid            AS STRING) AS item_id,
    CAST(ito.dataareaid        AS STRING) AS company
FROM {bronze_catalog}.{bronze_schema}.invent_trans_origin AS ito
WHERE ({company_predicate});

-- --- Ordres de fabrication ---------------------------------------------------
CREATE OR REPLACE VIEW {catalog}.{schema}.v_src_prod_table
COMMENT 'Ordres de fabrication (ProdTable) : rattache un OF à son article parent.'
AS
SELECT
    CAST(pt.prodid     AS STRING) AS prod_id,
    CAST(pt.itemid     AS STRING) AS parent_itemid,
    CAST(pt.dataareaid AS STRING) AS company
FROM {bronze_catalog}.{bronze_schema}.prod_table AS pt
WHERE ({company_predicate});

-- --- Référentiel article -----------------------------------------------------
CREATE OR REPLACE VIEW {catalog}.{schema}.v_src_article
COMMENT 'Référentiel article silver, dédoublonné sur le dernier snapshot.'
AS
WITH ranked AS (
    SELECT
        a.*,
        ROW_NUMBER() OVER (
            PARTITION BY a.item_id
            ORDER BY a.snapshot_date DESC NULLS LAST
        ) AS rn
    FROM {silver_catalog}.{silver_schema}.silver_base_article AS a
)
SELECT
    CAST(item_id          AS STRING)  AS item_id,
    CAST(item_name        AS STRING)  AS item_name,
    CAST(item_description AS STRING)  AS item_description,
    CAST(categorie        AS STRING)  AS categorie,
    CAST(item_group_id    AS STRING)  AS item_group_id,
    CAST(item_group_label AS STRING)  AS item_group_label,
    CAST(programme        AS STRING)  AS programme,
    CAST(std_cost_price   AS DECIMAL(18, 6)) AS std_cost_price,
    CAST(std_unit         AS STRING)  AS std_unit,
    CAST(snapshot_date    AS TIMESTAMP) AS snapshot_date
FROM ranked
WHERE rn = 1;

-- --- Nomenclature ------------------------------------------------------------
CREATE OR REPLACE VIEW {catalog}.{schema}.v_src_bom
COMMENT 'Nomenclatures actives silver, dernier snapshot uniquement.'
AS
WITH last_snapshot AS (
    SELECT MAX(snapshot_date) AS snapshot_date
    FROM {silver_catalog}.{silver_schema}.silver_bom
)
SELECT
    CAST(b.bomid                 AS STRING) AS bomid,
    CAST(b.parent_itemid         AS STRING) AS parent_itemid,
    CAST(b.bom_version_name      AS STRING) AS bom_version_name,
    CAST(b.statut                AS STRING) AS statut,
    CAST(b.child_itemid          AS STRING) AS child_itemid,
    CAST(b.child_qty             AS DECIMAL(18, 6)) AS child_qty,
    CAST(b.child_unitid          AS STRING) AS child_unitid,
    CAST(b.parent_physical_stock AS DECIMAL(18, 6)) AS parent_physical_stock,
    CAST(b.child_physical_stock  AS DECIMAL(18, 6)) AS child_physical_stock,
    CAST(b.snapshot_date         AS TIMESTAMP) AS snapshot_date
FROM {silver_catalog}.{silver_schema}.silver_bom AS b
JOIN last_snapshot AS s
  ON b.snapshot_date = s.snapshot_date
WHERE b.statut = 'Actif'
  AND b.child_itemid IS NOT NULL
  AND b.parent_itemid IS NOT NULL
  AND b.child_qty IS NOT NULL
  AND b.child_qty > 0;      -- un coef nul ou négatif rendrait l'équivalent produit indéfini
