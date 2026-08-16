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
-- SUPPRESSIONS LOGIQUES : la couche bronze conserve les lignes supprimées dans
-- l'ERP, marquées par `IsDelete` et `deleted_at`. Les compter reviendrait à
-- intégrer au calcul des mouvements qui n'existent plus — un OF annulé
-- produirait un écart permanent et parfaitement inexplicable en atelier.
-- Le volume exclu est mesuré par le contrôle `mouvements_supprimes_exclus`.
CREATE OR REPLACE VIEW {catalog}.{schema}.v_src_invent_trans
COMMENT 'Mouvements de stock normalisés (InventTrans), hors lignes supprimées. Source d''autorité pour les quantités et la date physique.'
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
  AND NOT COALESCE(it.IsDelete, FALSE)
  AND it.deleted_at IS NULL
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
WHERE NOT COALESCE(ito.IsDelete, FALSE)
  AND ({company_predicate});

-- --- Ordres de fabrication ---------------------------------------------------
-- `bomid` et `finisheddate` ne servent pas au calcul hebdomadaire actuel, mais
-- sont exposés ici : ce sont les deux colonnes qui rendront possible le
-- rapprochement par ordre de fabrication (cf. docs/AMELIORATIONS.md §1), lequel
-- supprimerait le biais de calage des OF à cheval sur deux semaines.
CREATE OR REPLACE VIEW {catalog}.{schema}.v_src_prod_table
COMMENT 'Ordres de fabrication (ProdTable) : rattache un OF à son article parent, hors OF supprimés.'
AS
SELECT
    CAST(pt.prodid       AS STRING)    AS prod_id,
    CAST(pt.itemid       AS STRING)    AS parent_itemid,
    CAST(pt.bomid        AS STRING)    AS bom_id,
    CAST(pt.prodstatus   AS BIGINT)    AS prod_statut,
    CAST(pt.finisheddate AS TIMESTAMP) AS date_cloture,
    CAST(pt.dataareaid   AS STRING)    AS company
FROM {bronze_catalog}.{bronze_schema}.prod_table AS pt
WHERE NOT COALESCE(pt.IsDelete, FALSE)
  AND ({company_predicate});

-- --- Référentiel article -----------------------------------------------------
-- La table silver ne porte PAS de colonne de snapshot par ligne : elle expose
-- `silver_refreshed_at`, horodatage du dernier rafraîchissement de la table
-- entière. Le dédoublonnage s'appuie donc sur la chronologie de l'article
-- lui-même (date de modification, puis de création, puis identifiant technique)
-- — trois critères qui garantissent un choix déterministe même si les deux
-- premiers sont nuls ou à égalité.
CREATE OR REPLACE VIEW {catalog}.{schema}.v_src_article
COMMENT 'Référentiel article silver, dédoublonné sur la version la plus récente de chaque article.'
AS
WITH ranked AS (
    SELECT
        a.*,
        ROW_NUMBER() OVER (
            PARTITION BY a.item_id
            ORDER BY COALESCE(a.product_modified_at, a.product_created_at) DESC NULLS LAST,
                     a.product_recid DESC NULLS LAST
        ) AS rn
    FROM {silver_catalog}.{silver_schema}.silver_base_article AS a
)
SELECT
    CAST(item_id             AS STRING)  AS item_id,
    CAST(item_name           AS STRING)  AS item_name,
    CAST(item_description    AS STRING)  AS item_description,
    CAST(categorie           AS STRING)  AS categorie,
    CAST(item_group_id       AS STRING)  AS item_group_id,
    CAST(item_group_label    AS STRING)  AS item_group_label,
    CAST(programme           AS STRING)  AS programme,
    CAST(std_cost_price      AS DECIMAL(18, 6)) AS std_cost_price,
    CAST(std_unit            AS STRING)  AS std_unit,
    CAST(silver_refreshed_at AS TIMESTAMP) AS snapshot_date
FROM ranked
WHERE rn = 1;

-- --- Nomenclature ------------------------------------------------------------
-- Aucune dépendance à une colonne d'horodatage : la table silver expose l'état
-- courant des nomenclatures, pas un historique de snapshots. La sélection d'une
-- version unique par parent est faite en aval (11_dim_nomenclature), sur des
-- critères stables — nom de version puis identifiant de BOM.
CREATE OR REPLACE VIEW {catalog}.{schema}.v_src_bom
COMMENT 'Lignes de nomenclature actives, normalisées et filtrées.'
AS
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
    CURRENT_TIMESTAMP()                     AS snapshot_date
FROM {silver_catalog}.{silver_schema}.silver_bom AS b
WHERE b.statut = 'Actif'
  AND b.child_itemid IS NOT NULL
  AND b.parent_itemid IS NOT NULL
  AND b.child_qty IS NOT NULL
  AND b.child_qty > 0;      -- un coef nul ou négatif rendrait l'équivalent produit indéfini

-- --- Produits fabriqués : rattachement d'un parent à sa ligne de production ---
-- La « ligne de prod », nommée PÉRIMÈTRE dans l'application, est l'axe
-- d'analyse opérationnel : c'est elle qui porte un coefficient de nomenclature
-- homogène, donc la conversion d'un écart en équivalent produit fabriqué.
--
-- Contrat métier : une référence parent appartient à un seul périmètre, et un
-- périmètre relève d'un seul programme. Le ROW_NUMBER n'est donc pas une
-- correction de modèle mais un garde-fou : un doublon dans la table source
-- multiplierait les lignes de la table de faits, en silence.
CREATE OR REPLACE VIEW {catalog}.{schema}.v_src_produit_fabrique
COMMENT 'Rattachement parent → ligne de production (périmètre), dédoublonné.'
AS
WITH ranked AS (
    SELECT
        CAST(p.ref_parent    AS STRING) AS parent_itemid,
        CAST(p.item_name     AS STRING) AS parent_name_source,
        CAST(p.type          AS STRING) AS type_produit,
        CAST(p.ligne_de_prod AS STRING) AS perimetre,
        ROW_NUMBER() OVER (
            PARTITION BY p.ref_parent
            ORDER BY p.ligne_de_prod NULLS LAST
        ) AS rn
    FROM {silver_catalog}.{silver_schema}.produits_fabriques AS p
    WHERE p.ref_parent IS NOT NULL
      AND NULLIF(TRIM(p.ligne_de_prod), '') IS NOT NULL
)
SELECT parent_itemid, parent_name_source, type_produit, perimetre
FROM ranked
WHERE rn = 1;
