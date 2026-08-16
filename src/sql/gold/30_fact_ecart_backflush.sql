-- =============================================================================
-- 30 — fact_ecart_backflush ⭐ TABLE PRINCIPALE
-- =============================================================================
-- Grain : parent × composant × semaine ISO.
--
-- ÉCART = CONSOMMATION THÉORIQUE − CONSOMMATION RÉELLE
--       = (qté parent produite × coef BOM) − qté composant sortie du stock
--
--   écart > 0  →  Non-consommation  : le backflush n'a pas déduit tout le
--                 théorique. Le stock système est surévalué de cette quantité.
--   écart < 0  →  Surconsommation   : plus sorti que prévu (rebut non déclaré,
--                 erreur de nomenclature, servitude non modélisée, vol).
--   |écart| <= {seuil_conformite}  →  Conforme.
--
-- JOINTURE COMPLÈTE (FULL OUTER) — choix de conception essentiel
-- ---------------------------------------------------------------------------
-- Une jointure interne entre « attendu » et « réel » masquerait les deux
-- anomalies les plus coûteuses en gestion de stock :
--   • composant sorti sur un OF SANS ligne de nomenclature correspondante
--     (statut « Hors nomenclature ») → 100 % de surconsommation, invisible ;
--   • composant prévu par la BOM mais JAMAIS sorti sur la semaine
--     (statut « Sans consommation ») → 100 % de non-consommation, invisible.
-- La jointure complète les fait remonter explicitement via statut_ligne.
--
-- Placeholders : {catalog} {schema} {seuil_conformite}
-- =============================================================================

CREATE OR REPLACE TABLE {catalog}.{schema}.fact_ecart_backflush
COMMENT 'Écarts de consommation composant du backflush. Grain : parent × composant × semaine ISO. Table de détail de référence pour l''application et Power BI.'
TBLPROPERTIES (
    delta.enableChangeDataFeed = true,
    delta.autoOptimize.optimizeWrite = true
)
AS
WITH
-- 1) Consommation ATTENDUE : production de la semaine explosée sur la nomenclature
attendu AS (
    SELECT
        p.semaine_debut,
        p.parent_itemid,
        n.child_itemid,
        n.child_qty                                  AS coef_bom,
        p.qty_produite                               AS qty_parent_produite,
        p.qty_produite * n.child_qty                 AS conso_theorique
    FROM {catalog}.{schema}.fact_production_parent AS p
    JOIN {catalog}.{schema}.dim_nomenclature       AS n
      ON n.parent_itemid = p.parent_itemid
),
-- 2) Consommation RÉELLE
reel AS (
    SELECT
        semaine_debut,
        parent_itemid,
        child_itemid,
        qty_consommee                                AS conso_reelle,
        nb_transactions                              AS nb_transactions_conso,
        nb_retours
    FROM {catalog}.{schema}.fact_consommation_composant
),
-- 3) Réconciliation
socle AS (
    SELECT
        COALESCE(a.semaine_debut,  r.semaine_debut)  AS semaine_debut,
        COALESCE(a.parent_itemid,  r.parent_itemid)  AS parent_itemid,
        COALESCE(a.child_itemid,   r.child_itemid)   AS child_itemid,
        a.coef_bom,
        COALESCE(a.qty_parent_produite, 0)           AS qty_parent_produite,
        COALESCE(a.conso_theorique,     0)           AS conso_theorique,
        COALESCE(r.conso_reelle,        0)           AS conso_reelle,
        COALESCE(r.nb_transactions_conso, 0)         AS nb_transactions_conso,
        COALESCE(r.nb_retours,          0)           AS nb_retours,
        CASE
            WHEN a.child_itemid IS NULL THEN 'Hors nomenclature'
            WHEN r.child_itemid IS NULL THEN 'Sans consommation'
            ELSE 'Nominal'
        END                                          AS statut_ligne
    FROM attendu AS a
    FULL OUTER JOIN reel AS r
      ON  a.semaine_debut  = r.semaine_debut
      AND a.parent_itemid  = r.parent_itemid
      AND a.child_itemid   = r.child_itemid
),
-- 4) Calcul de l'écart
calcule AS (
    SELECT
        s.*,
        s.conso_theorique - s.conso_reelle           AS ecart_brut
    FROM socle AS s
)
SELECT
    YEAR(DATE_ADD(c.semaine_debut, 3))               AS annee,
    WEEKOFYEAR(c.semaine_debut)                      AS semaine,
    c.semaine_debut,

    -- Axe parent. Les axes d'analyse sont matérialisés (jamais NULL) : ils
    -- servent de clé aux agrégats et de valeur aux filtres de l'application ;
    -- un NULL y produirait des lignes invisibles et une clé primaire invalide
    -- côté Lakebase.
    c.parent_itemid,
    COALESCE(pa.programme, 'NON RENSEIGNE')          AS parent_programme,
    pa.item_name                                     AS parent_name,
    COALESCE(pa.categorie, 'NON RENSEIGNE')          AS parent_categorie,

    -- Axe composant
    c.child_itemid,
    ca.item_name                                     AS child_name,
    COALESCE(ca.categorie, 'NON RENSEIGNE')          AS child_categorie,
    COALESCE(ca.programme, 'NON RENSEIGNE')          AS child_programme,
    COALESCE(ca.std_unit, 'PCE')                     AS child_unite,

    -- Mesures brutes
    c.coef_bom,
    c.qty_parent_produite,
    c.conso_reelle,
    c.conso_theorique,
    c.ecart_brut,

    -- Écart relatif : indéfini si aucun théorique (division par zéro), on
    -- renvoie NULL plutôt que 0 ou l'infini — un NULL se filtre, pas un +Inf.
    CASE
        WHEN c.conso_theorique > 0
        THEN (c.ecart_brut / c.conso_theorique) * 100
    END                                              AS ecart_pct,

    CASE
        WHEN c.ecart_brut >  {seuil_conformite} THEN 'Non-consommation'
        WHEN c.ecart_brut < -{seuil_conformite} THEN 'Surconsommation'
        ELSE 'Conforme'
    END                                              AS type_ecart,

    c.statut_ligne,

    -- Valorisation
    ca.std_cost_price                                AS child_cout_standard,
    c.ecart_brut * COALESCE(ca.std_cost_price, 0)    AS ecart_valorise,

    -- Équivalent produit fabriqué : nombre d'unités parent « manquantes » ou
    -- « en trop » vu depuis ce composant. N'a de sens que si le coefficient est
    -- uniforme sur le programme (sinon la division est ambiguë).
    COALESCE(cp.is_coef_uniforme, FALSE)             AS is_coef_uniforme,
    CASE
        WHEN COALESCE(cp.is_coef_uniforme, FALSE) AND c.coef_bom > 0
        THEN c.ecart_brut / c.coef_bom
    END                                              AS ecart_equivalent_produit,

    -- Traçabilité
    c.nb_transactions_conso,
    c.nb_retours,
    CURRENT_TIMESTAMP()                              AS loaded_at

FROM calcule AS c
LEFT JOIN {catalog}.{schema}.dim_article       AS pa ON pa.item_id = c.parent_itemid
LEFT JOIN {catalog}.{schema}.dim_article       AS ca ON ca.item_id = c.child_itemid
LEFT JOIN {catalog}.{schema}.dim_coef_programme AS cp
       ON  cp.child_itemid = c.child_itemid
       AND cp.programme    = pa.programme;
