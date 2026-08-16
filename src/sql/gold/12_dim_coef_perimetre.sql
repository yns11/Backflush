-- =============================================================================
-- 12 — dim_coef_perimetre : uniformité du coefficient BOM par PÉRIMÈTRE
-- =============================================================================
-- Un composant est dit « à coefficient uniforme » dans un périmètre si TOUS les
-- parents de ce périmètre qui l'utilisent le consomment avec la même quantité.
-- C'est la condition nécessaire pour convertir un écart en « équivalent produit
-- fabriqué » : sans coefficient unique, la conversion n'a pas de sens physique.
--
-- POURQUOI LE PÉRIMÈTRE ET NON LE PROGRAMME
-- ---------------------------------------------------------------------------
-- Un programme regroupe plusieurs lignes de production, qui fabriquent des
-- produits différents à partir de nomenclatures différentes. Tester
-- l'uniformité à la maille du programme mélange donc des coefficients qui
-- n'ont aucune raison d'être égaux : l'uniformité y est rare, et l'équivalent
-- produit reste vide pour des composants qui, dans leur ligne, sont parfaitement
-- réguliers. Le périmètre est la maille où la question a un sens industriel.
--
-- Tolérance : la comparaison min/max est faite à 1e-6 près pour absorber le
-- bruit de représentation des DECIMAL, pas pour masquer un vrai écart de coef.
-- =============================================================================

CREATE OR REPLACE TABLE {catalog}.{schema}.dim_coef_perimetre
COMMENT 'Coefficient BOM consolidé par (périmètre, composant) et indicateur d''uniformité, pour le calcul de l''écart en équivalent produit.'
TBLPROPERTIES (delta.enableChangeDataFeed = true)
AS
SELECT
    COALESCE(a.perimetre, 'NON RENSEIGNE')                AS perimetre,
    MAX(a.programme)                                      AS programme,
    n.child_itemid,
    COUNT(DISTINCT n.parent_itemid)                       AS nb_parents,
    MIN(n.child_qty)                                      AS child_qty_min,
    MAX(n.child_qty)                                      AS child_qty_max,
    MIN(n.child_qty)                                      AS child_qty,
    (MAX(n.child_qty) - MIN(n.child_qty)) <= 1e-6         AS is_coef_uniforme,
    CURRENT_TIMESTAMP()                                   AS snapshot_date,
    CURRENT_TIMESTAMP()                                   AS loaded_at
FROM {catalog}.{schema}.dim_nomenclature AS n
JOIN {catalog}.{schema}.dim_article      AS a
  ON a.item_id = n.parent_itemid
GROUP BY COALESCE(a.perimetre, 'NON RENSEIGNE'), n.child_itemid;
