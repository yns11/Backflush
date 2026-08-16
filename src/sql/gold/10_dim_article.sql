-- =============================================================================
-- 10 — dim_article : référentiel article (grain : 1 ligne par item_id)
-- =============================================================================

CREATE OR REPLACE TABLE {catalog}.{schema}.dim_article
COMMENT 'Référentiel article : désignation, catégorie, groupe, programme, coût standard, unité.'
TBLPROPERTIES (delta.enableChangeDataFeed = true)
AS
SELECT
    item_id,
    item_name,
    item_description,
    -- Une catégorie absente casse silencieusement les filtres de l'app : on la
    -- matérialise plutôt que de la laisser NULL.
    COALESCE(NULLIF(TRIM(categorie), ''), 'NON RENSEIGNE')  AS categorie,
    item_group_id,
    item_group_label,
    COALESCE(NULLIF(TRIM(programme), ''), 'NON RENSEIGNE')  AS programme,
    -- Un coût standard négatif est une anomalie de paramétrage : neutralisé pour
    -- ne pas polluer la valorisation, et remonté par les contrôles qualité (90_*).
    CASE WHEN std_cost_price >= 0 THEN std_cost_price END   AS std_cost_price,
    COALESCE(NULLIF(TRIM(std_unit), ''), 'PCE')             AS std_unit,
    snapshot_date,
    CURRENT_TIMESTAMP()                                     AS loaded_at
FROM {catalog}.{schema}.v_src_article;
