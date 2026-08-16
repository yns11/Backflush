-- =============================================================================
-- 21 — fact_consommation_composant : consommation réelle par parent × composant × semaine
-- =============================================================================
-- Source : sorties de stock sur ligne de nomenclature d'OF
--          (referencecategory = 8, qty < 0), rattachées au parent via prod_table.
--
-- Le signe est inversé à la source : la quantité consommée est stockée POSITIVE,
-- pour que « théorique − réel » ait le sens attendu sans piège de signe en aval.
--
-- Un retour de composant au stock (qty > 0 sur la catégorie 8) est un
-- dé-backflush ; il diminue la consommation nette de la semaine. Il est donc
-- intégré au net, pas ignoré — sinon toute correction d'erreur apparaîtrait
-- comme une surconsommation permanente.
-- =============================================================================

CREATE OR REPLACE TABLE {catalog}.{schema}.fact_consommation_composant
COMMENT 'Consommation réelle de composants issue du backflush. Grain : parent × composant × semaine ISO. Quantité nette positive.'
TBLPROPERTIES (delta.enableChangeDataFeed = true)
AS
WITH mouvements AS (
    SELECT
        pt.parent_itemid,
        it.item_id                           AS child_itemid,
        DATE_TRUNC('WEEK', it.date_physique) AS semaine_debut_ts,
        it.qty,
        it.date_physique
    FROM {catalog}.{schema}.v_src_invent_trans        AS it
    JOIN {catalog}.{schema}.v_src_invent_trans_origin AS ito
      ON ito.transorigin_id = it.transorigin_id
    JOIN {catalog}.{schema}.v_src_prod_table          AS pt
      ON  pt.prod_id = ito.reference_id
      AND pt.company = ito.company
    WHERE ito.reference_category = 8
      AND it.date_physique >= DATE '{date_from}'
),
agrege AS (
    SELECT
        CAST(semaine_debut_ts AS DATE)                    AS semaine_debut,
        parent_itemid,
        child_itemid,
        -- SUM(-qty) : les sorties (qty < 0) deviennent positives, les retours
        -- (qty > 0) viennent en déduction. Le résultat est la conso NETTE.
        SUM(-qty)                                         AS qty_consommee,
        COUNT(*)                                          AS nb_transactions,
        COUNT_IF(qty > 0)                                 AS nb_retours,
        MIN(date_physique)                                AS premiere_date,
        MAX(date_physique)                                AS derniere_date
    FROM mouvements
    GROUP BY semaine_debut_ts, parent_itemid, child_itemid
)
SELECT
    YEAR(DATE_ADD(g.semaine_debut, 3))  AS annee,
    WEEKOFYEAR(g.semaine_debut)         AS semaine,
    g.semaine_debut,
    g.parent_itemid,
    g.child_itemid,
    COALESCE(a.programme, 'NON RENSEIGNE') AS parent_programme,
    g.qty_consommee,
    g.nb_transactions,
    g.nb_retours,
    g.premiere_date,
    g.derniere_date,
    CURRENT_TIMESTAMP()                 AS loaded_at
FROM agrege AS g
LEFT JOIN {catalog}.{schema}.dim_article AS a
  ON a.item_id = g.parent_itemid;
