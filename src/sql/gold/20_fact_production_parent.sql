-- =============================================================================
-- 20 — fact_production_parent : production déclarée par parent × semaine
-- =============================================================================
-- Source : entrées en stock d'ordres de fabrication (referencecategory = 2, qty > 0).
--
-- CALENDRIER ISO — la semaine est la maille d'analyse imposée par le métier :
--   semaine_debut = lundi de la semaine       date_trunc('WEEK', d)
--   semaine       = numéro ISO                weekofyear(d)
--   annee         = ANNÉE ISO, pas l'année civile. Le jeudi de la semaine porte
--                   l'année ISO (définition ISO-8601) : year(semaine_debut + 3).
--                   Sans cette correction, la semaine 1 de janvier serait
--                   rattachée à l'année précédente une année sur cinq.
--
-- Placeholder supplémentaire : {date_from} — borne basse d'historique (AAAA-MM-JJ).
-- =============================================================================

CREATE OR REPLACE TABLE {catalog}.{schema}.fact_production_parent
COMMENT 'Production déclarée (entrée en stock) par article parent et semaine ISO. Grain : parent × semaine.'
TBLPROPERTIES (delta.enableChangeDataFeed = true)
AS
WITH mouvements AS (
    SELECT
        it.item_id                                AS parent_itemid,
        DATE_TRUNC('WEEK', it.date_physique)      AS semaine_debut_ts,
        it.qty,
        it.date_physique
    FROM {catalog}.{schema}.v_src_invent_trans        AS it
    JOIN {catalog}.{schema}.v_src_invent_trans_origin AS ito
      ON ito.transorigin_id = it.transorigin_id
    WHERE ito.reference_category = 2
      AND it.qty > 0
      AND it.date_physique >= DATE '{date_from}'
),
agrege AS (
    SELECT
        CAST(semaine_debut_ts AS DATE)      AS semaine_debut,
        parent_itemid,
        SUM(qty)                            AS qty_produite,
        COUNT(*)                            AS nb_transactions,
        MIN(date_physique)                  AS premiere_date,
        MAX(date_physique)                  AS derniere_date
    FROM mouvements
    GROUP BY semaine_debut_ts, parent_itemid
)
SELECT
    YEAR(DATE_ADD(g.semaine_debut, 3))      AS annee,       -- année ISO
    WEEKOFYEAR(g.semaine_debut)             AS semaine,     -- semaine ISO
    g.semaine_debut,
    g.parent_itemid,
    COALESCE(a.programme, 'NON RENSEIGNE')  AS parent_programme,
    COALESCE(a.perimetre, 'NON RENSEIGNE')  AS parent_perimetre,
    a.item_name                             AS parent_name,
    COALESCE(a.categorie, 'NON RENSEIGNE')  AS parent_categorie,
    g.qty_produite,
    g.nb_transactions,
    g.premiere_date,
    g.derniere_date,
    CURRENT_TIMESTAMP()                     AS loaded_at
FROM agrege AS g
LEFT JOIN {catalog}.{schema}.dim_article AS a
  ON a.item_id = g.parent_itemid;
