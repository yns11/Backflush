-- =============================================================================
-- 11 — dim_nomenclature : nomenclature active « aplatie »
-- =============================================================================
-- RÈGLE MÉTIER CRITIQUE
-- Un article parent peut porter plusieurs versions de BOM actives simultanément.
-- Utiliser toutes les versions dupliquerait la consommation théorique (donc
-- l'écart) autant de fois qu'il y a de versions. On sélectionne donc UNE version
-- par parent, de façon déterministe :
--     1. version la plus récente (snapshot), puis
--     2. bom_version_name décroissant, puis bomid — pour lever toute ambiguïté.
-- Les parents multi-versions sont comptés dans dq_bom_multi_version (90_*).
--
-- Un même composant peut apparaître sur plusieurs LIGNES d'une même version
-- (deux emplacements de montage) : ces lignes sont sommées, jamais dédoublonnées.
-- =============================================================================

CREATE OR REPLACE TABLE {catalog}.{schema}.dim_nomenclature
COMMENT 'Nomenclature active aplatie : 1 ligne par (parent, composant), quantité sommée sur la version retenue.'
TBLPROPERTIES (delta.enableChangeDataFeed = true)
AS
WITH version_ranked AS (
    SELECT
        parent_itemid,
        bomid,
        bom_version_name,
        snapshot_date,
        ROW_NUMBER() OVER (
            PARTITION BY parent_itemid
            ORDER BY snapshot_date DESC NULLS LAST,
                     bom_version_name DESC NULLS LAST,
                     bomid DESC
        ) AS rn
    FROM (
        SELECT DISTINCT parent_itemid, bomid, bom_version_name, snapshot_date
        FROM {catalog}.{schema}.v_src_bom
    )
),
version_retenue AS (
    SELECT parent_itemid, bomid, bom_version_name, snapshot_date
    FROM version_ranked
    WHERE rn = 1
)
SELECT
    v.bomid,
    v.parent_itemid,
    v.bom_version_name,
    'Actif'                                   AS statut,
    b.child_itemid,
    SUM(b.child_qty)                          AS child_qty,
    MAX(b.child_unitid)                       AS child_unitid,
    MAX(b.parent_physical_stock)              AS parent_physical_stock,
    MAX(b.child_physical_stock)               AS child_physical_stock,
    COUNT(*)                                  AS nb_lignes_bom,
    v.snapshot_date,
    CURRENT_TIMESTAMP()                       AS loaded_at
FROM version_retenue AS v
JOIN {catalog}.{schema}.v_src_bom AS b
  ON  b.parent_itemid = v.parent_itemid
  AND b.bomid         = v.bomid
GROUP BY v.bomid, v.parent_itemid, v.bom_version_name, v.snapshot_date, b.child_itemid;
