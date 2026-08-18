-- =============================================================================
-- 31 — fact_ecart_of : le même écart, un cran plus fin — par ORDRE DE FABRICATION
-- =============================================================================
-- Grain : OF × parent × composant × semaine ISO.
--
-- C'est la table de détail `fact_ecart_backflush` avec une colonne de plus, et
-- rien d'autre : mêmes sources, mêmes règles de signe, mêmes statuts de ligne.
-- Elle sert la lecture « quel OF porte l'écart », que la maille parent × semaine
-- ne peut pas donner : deux OF du même parent sur la même semaine y sont
-- confondus, alors qu'en atelier ce sont deux lancements distincts, souvent sur
-- des équipes ou des postes différents.
--
-- CE QU'IL FAUT SAVOIR AVANT DE COMPARER LES DEUX TABLES
-- ---------------------------------------------------------------------------
-- Le TOTAL de `ecart_brut` est identique entre les deux tables : descendre d'un
-- cran ne crée ni ne détruit de matière. En revanche, la DÉCOMPOSITION change,
-- et c'est attendu :
--
--   • la non-consommation et la surconsommation augmentent toutes deux, du même
--     montant. Un OF qui produit en S26 alors que ses composants sont sortis en
--     S25 donne, à cette maille, une non-consommation en S26 et une
--     surconsommation en S25. Agrégés au parent, ces deux écarts se compensaient
--     partiellement avec ceux des autres OF de la même semaine ;
--   • le nombre de lignes conformes baisse pour la même raison.
--
-- Ce n'est pas un défaut de cette table : c'est le BIAIS DE CALAGE des OF à
-- cheval sur deux semaines, que la maille parent × semaine masquait. Le
-- contrôle `reconciliation_of_detail` vérifie l'égalité des totaux, seule
-- propriété qui doive tenir.
--
-- COHÉRENCE AVEC 20 ET 21 — les CTE ci-dessous reprennent les règles
-- d'agrégation de `fact_production_parent` et `fact_consommation_composant`
-- (catégories de référence, exclusion des mouvements supprimés, inversion du
-- signe, retours en déduction). Elles sont dupliquées parce que ces deux tables
-- ont déjà perdu l'OF au moment de leur GROUP BY. Toute évolution de leurs
-- règles doit être répercutée ici ; c'est le contrôle de réconciliation qui le
-- détectera si on l'oublie.
--
-- Placeholders : {catalog} {schema} {seuil_conformite} {date_from}
-- =============================================================================

CREATE OR REPLACE TABLE {catalog}.{schema}.fact_ecart_of
COMMENT 'Écarts de consommation composant par ordre de fabrication. Grain : OF × parent × composant × semaine ISO. Le total des écarts est identique à fact_ecart_backflush ; leur décomposition non-conso / surconso diffère, par effet du calage hebdomadaire des OF.'
TBLPROPERTIES (
    delta.enableChangeDataFeed = true,
    delta.autoOptimize.optimizeWrite = true
)
AS
WITH
-- 1) Production déclarée, par OF et par semaine.
--    L'entrée en stock (catégorie 2) porte l'OF dans `reference_id`.
production AS (
    SELECT
        ito.reference_id                          AS prod_id,
        it.item_id                                AS parent_itemid,
        CAST(DATE_TRUNC('WEEK', it.date_physique) AS DATE) AS semaine_debut,
        SUM(it.qty)                               AS qty_produite
    FROM {catalog}.{schema}.v_src_invent_trans        AS it
    JOIN {catalog}.{schema}.v_src_invent_trans_origin AS ito
      ON ito.transorigin_id = it.transorigin_id
    WHERE ito.reference_category = 2
      AND it.qty > 0
      AND it.date_physique >= DATE '{date_from}'
    GROUP BY ito.reference_id, it.item_id, DATE_TRUNC('WEEK', it.date_physique)
),
-- 2) Consommation réelle, par OF, composant et semaine.
--    SUM(-qty) : les sorties (qty < 0) deviennent positives, les retours au
--    stock (qty > 0) viennent en déduction — la conso est NETTE.
consommation AS (
    SELECT
        pt.prod_id,
        pt.parent_itemid,
        it.item_id                                AS child_itemid,
        CAST(DATE_TRUNC('WEEK', it.date_physique) AS DATE) AS semaine_debut,
        SUM(-it.qty)                              AS conso_reelle,
        COUNT(*)                                  AS nb_transactions_conso,
        COUNT_IF(it.qty > 0)                      AS nb_retours
    FROM {catalog}.{schema}.v_src_invent_trans        AS it
    JOIN {catalog}.{schema}.v_src_invent_trans_origin AS ito
      ON ito.transorigin_id = it.transorigin_id
    JOIN {catalog}.{schema}.v_src_prod_table          AS pt
      ON  pt.prod_id = ito.reference_id
      AND pt.company = ito.company
    WHERE ito.reference_category = 8
      AND it.date_physique >= DATE '{date_from}'
    GROUP BY pt.prod_id, pt.parent_itemid, it.item_id,
             DATE_TRUNC('WEEK', it.date_physique)
),
-- 3) Consommation ATTENDUE : production de l'OF explosée sur la nomenclature.
attendu AS (
    SELECT
        p.prod_id,
        p.parent_itemid,
        n.child_itemid,
        p.semaine_debut,
        n.child_qty                  AS coef_bom,
        p.qty_produite               AS qty_parent_produite,
        p.qty_produite * n.child_qty AS conso_theorique
    FROM production                                AS p
    JOIN {catalog}.{schema}.dim_nomenclature       AS n
      ON n.parent_itemid = p.parent_itemid
),
-- 4) Réconciliation — jointure COMPLÈTE, pour les mêmes raisons qu'en 30 :
--    une jointure interne masquerait le composant sorti sans ligne de
--    nomenclature et la ligne de nomenclature jamais servie.
socle AS (
    SELECT
        COALESCE(a.prod_id,       r.prod_id)       AS prod_id,
        COALESCE(a.parent_itemid, r.parent_itemid) AS parent_itemid,
        COALESCE(a.child_itemid,  r.child_itemid)  AS child_itemid,
        COALESCE(a.semaine_debut, r.semaine_debut) AS semaine_debut,
        a.coef_bom,
        COALESCE(a.qty_parent_produite, 0)         AS qty_parent_produite,
        COALESCE(a.conso_theorique,     0)         AS conso_theorique,
        COALESCE(r.conso_reelle,        0)         AS conso_reelle,
        COALESCE(r.nb_transactions_conso, 0)       AS nb_transactions_conso,
        COALESCE(r.nb_retours,          0)         AS nb_retours,
        CASE
            WHEN a.child_itemid IS NULL THEN 'Hors nomenclature'
            WHEN r.child_itemid IS NULL THEN 'Sans consommation'
            ELSE 'Nominal'
        END                                        AS statut_ligne
    FROM attendu AS a
    FULL OUTER JOIN consommation AS r
      ON  a.prod_id       = r.prod_id
      AND a.parent_itemid = r.parent_itemid
      AND a.child_itemid  = r.child_itemid
      AND a.semaine_debut = r.semaine_debut
),
calcule AS (
    SELECT s.*, s.conso_theorique - s.conso_reelle AS ecart_brut
    FROM socle AS s
),
-- 5) Un OF par identifiant, pour l'enrichissement final.
--    `v_src_prod_table` peut porter le même `prod_id` sur plusieurs sociétés :
--    joint tel quel, il DUPLIQUERAIT les lignes d'écart et fausserait tous les
--    totaux. Le dédoublonnage est déterministe, faute d'une société portée par
--    la production (l'entrée en stock ne référence que l'OF).
ordre AS (
    SELECT prod_id, bom_id, prod_statut, date_cloture
    FROM (
        SELECT pt.*,
               ROW_NUMBER() OVER (
                   PARTITION BY pt.prod_id
                   ORDER BY pt.date_cloture DESC NULLS LAST, pt.company
               ) AS rang
        FROM {catalog}.{schema}.v_src_prod_table AS pt
    )
    WHERE rang = 1
)
SELECT
    YEAR(DATE_ADD(c.semaine_debut, 3))               AS annee,
    WEEKOFYEAR(c.semaine_debut)                      AS semaine,
    c.semaine_debut,

    -- Axe OF
    c.prod_id,
    pt.bom_id                                        AS prod_bomid,
    pt.date_cloture                                  AS prod_date_cloture,

    -- Statut D365 — énumération `ProdStatus`, dans l'ordre du cycle de vie :
    --   0 Aucun · 1 Créé · 2 Estimé · 3 Planifié · 4 Lancé
    --   5 Démarré · 6 Déclaré terminé · 7 Clôturé · 8 Annulé
    --
    -- L'échelle compte autant que les libellés : un écart sur un OF qui n'a
    -- pas atteint « Déclaré terminé » est ATTENDU — il lui reste des mouvements
    -- à venir. Ne pas lancer d'investigation dessus est la première règle de
    -- lecture de cette table, et elle suppose de savoir où l'OF en est.
    --
    -- Les libellés ne sont pas décoratifs : ils sont la valeur du filtre
    -- « Statut OF » de l'application. Une valeur inconnue est donc marquée
    -- explicitement plutôt que fondue dans un « Autre » muet — le contrôle
    -- `of_statut_inconnu` (90_*) la fait remonter si l'énumération évolue.
    CASE
        WHEN pt.prod_statut IS NULL THEN NULL
        WHEN pt.prod_statut = 0 THEN 'Aucun'
        WHEN pt.prod_statut = 1 THEN 'Créé'
        WHEN pt.prod_statut = 2 THEN 'Estimé'
        WHEN pt.prod_statut = 3 THEN 'Planifié'
        WHEN pt.prod_statut = 4 THEN 'Lancé'
        WHEN pt.prod_statut = 5 THEN 'Démarré'
        WHEN pt.prod_statut = 6 THEN 'Déclaré terminé'
        WHEN pt.prod_statut = 7 THEN 'Clôturé'
        WHEN pt.prod_statut = 8 THEN 'Annulé'
        ELSE CONCAT('Inconnu (', CAST(pt.prod_statut AS STRING), ')')
    END                                              AS prod_statut,

    -- Axe parent
    c.parent_itemid,
    COALESCE(pa.programme, 'NON RENSEIGNE')          AS parent_programme,
    COALESCE(pa.perimetre, 'NON RENSEIGNE')          AS parent_perimetre,
    pa.item_name                                     AS parent_name,
    COALESCE(pa.categorie, 'NON RENSEIGNE')          AS parent_categorie,

    -- Axe composant
    c.child_itemid,
    ca.item_name                                     AS child_name,
    COALESCE(ca.categorie, 'NON RENSEIGNE')          AS child_categorie,
    COALESCE(ca.std_unit, 'PCE')                     AS child_unite,

    -- Mesures
    c.coef_bom,
    c.qty_parent_produite,
    c.conso_reelle,
    c.conso_theorique,
    c.ecart_brut,
    CASE WHEN c.conso_theorique > 0
         THEN (c.ecart_brut / c.conso_theorique) * 100
    END                                              AS ecart_pct,
    CASE
        WHEN c.ecart_brut >  {seuil_conformite} THEN 'Non-consommation'
        WHEN c.ecart_brut < -{seuil_conformite} THEN 'Surconsommation'
        ELSE 'Conforme'
    END                                              AS type_ecart,
    c.statut_ligne,

    ca.std_cost_price                                AS child_cout_standard,
    c.ecart_brut * COALESCE(ca.std_cost_price, 0)    AS ecart_valorise,

    COALESCE(cp.is_coef_uniforme, FALSE)             AS is_coef_uniforme,
    CASE
        WHEN COALESCE(cp.is_coef_uniforme, FALSE) AND c.coef_bom > 0
        THEN c.ecart_brut / c.coef_bom
    END                                              AS ecart_equivalent_produit,

    c.nb_transactions_conso,
    c.nb_retours,
    CURRENT_TIMESTAMP()                              AS loaded_at

FROM calcule AS c
LEFT JOIN ordre                                 AS pt ON pt.prod_id  = c.prod_id
LEFT JOIN {catalog}.{schema}.dim_article        AS pa ON pa.item_id  = c.parent_itemid
LEFT JOIN {catalog}.{schema}.dim_article        AS ca ON ca.item_id  = c.child_itemid
LEFT JOIN {catalog}.{schema}.dim_coef_perimetre AS cp
       ON  cp.child_itemid = c.child_itemid
       AND cp.perimetre    = COALESCE(pa.perimetre, 'NON RENSEIGNE');
