-- =============================================================================
-- 10 — dim_article : référentiel article (grain : 1 ligne par item_id)
-- =============================================================================

CREATE OR REPLACE TABLE {catalog}.{schema}.dim_article
COMMENT 'Référentiel article : désignation, catégorie, groupe, programme, périmètre (ligne de production), coût standard, unité.'
TBLPROPERTIES (delta.enableChangeDataFeed = true)
AS
SELECT
    a.item_id,
    a.item_name,
    a.item_description,
    -- Une catégorie absente casse silencieusement les filtres de l'app : on la
    -- matérialise plutôt que de la laisser NULL.
    COALESCE(NULLIF(TRIM(a.categorie), ''), 'NON RENSEIGNE') AS categorie,
    a.item_group_id,
    a.item_group_label,
    COALESCE(NULLIF(TRIM(a.programme), ''), 'NON RENSEIGNE') AS programme,
    -- Périmètre (ligne de production). NULL pour un article qui n'est pas
    -- fabriqué : un composant acheté n'a pas de ligne de production, et lui en
    -- inventer une fausserait les regroupements. La matérialisation en
    -- « NON RENSEIGNE » est faite sur l'axe parent de la table de faits, où un
    -- NULL casserait la clé d'agrégat.
    pf.perimetre                                            AS perimetre,
    pf.type_produit                                         AS type_produit,
    -- Un coût standard négatif est une anomalie de paramétrage : neutralisé pour
    -- ne pas polluer la valorisation, et remonté par les contrôles qualité (90_*).
    CASE WHEN a.std_cost_price >= 0 THEN a.std_cost_price END AS std_cost_price,
    COALESCE(NULLIF(TRIM(a.std_unit), ''), 'PCE')           AS std_unit,
    a.snapshot_date,
    CURRENT_TIMESTAMP()                                     AS loaded_at
FROM {catalog}.{schema}.v_src_article AS a
LEFT JOIN {catalog}.{schema}.v_src_produit_fabrique AS pf
       ON pf.parent_itemid = a.item_id;
