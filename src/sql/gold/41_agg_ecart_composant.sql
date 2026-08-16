-- =============================================================================
-- 41 — agg_ecart_composant : classement et alertes par composant
-- =============================================================================
-- Usage : top-N, classements, alertes, page « Références » de l'application.
-- Grain : composant × périmètre du parent (un composant COMMUN peut être
-- consommé par plusieurs lignes de production avec des comportements très
-- différents ; l'agréger toutes lignes confondues masquerait la ligne fautive).
-- =============================================================================

CREATE OR REPLACE TABLE {catalog}.{schema}.agg_ecart_composant
COMMENT 'Agrégat cumulé des écarts backflush par composant, programme et périmètre parent. Base des classements et des alertes.'
TBLPROPERTIES (delta.enableChangeDataFeed = true)
AS
SELECT
    f.child_itemid,
    MAX(f.child_name)                                    AS child_name,
    MAX(f.child_categorie)                               AS child_categorie,
    f.parent_programme,
    f.parent_perimetre,

    -- Coefficient représentatif du périmètre : identique pour tous les parents
    -- si is_coef_uniforme, sinon le minimum observé (aligné sur dim_coef_perimetre).
    MIN(f.coef_bom)                                      AS coef_bom,
    MAX(f.coef_bom)                                      AS coef_bom_max,
    BOOL_AND(f.is_coef_uniforme)                         AS is_coef_uniforme,

    COUNT(DISTINCT f.parent_itemid)                      AS nb_parents,
    COUNT(DISTINCT f.semaine_debut)                      AS nb_semaines_actives,
    COUNT_IF(f.type_ecart <> 'Conforme')                 AS nb_semaines_en_ecart,
    MIN(f.semaine_debut)                                 AS premiere_semaine,
    MAX(f.semaine_debut)                                 AS derniere_semaine,

    SUM(f.conso_theorique)                               AS conso_theorique_totale,
    SUM(f.conso_reelle)                                  AS conso_reelle_totale,
    SUM(f.ecart_brut)                                    AS ecart_net,
    SUM(GREATEST(f.ecart_brut, 0))                       AS non_consommation,
    SUM(GREATEST(-f.ecart_brut, 0))                      AS surconsommation,

    CASE
        WHEN SUM(f.conso_theorique) > 0
        THEN (SUM(f.ecart_brut) / SUM(f.conso_theorique)) * 100
    END                                                  AS ecart_pct_global,

    CASE
        WHEN SUM(GREATEST(f.ecart_brut, 0)) >= SUM(GREATEST(-f.ecart_brut, 0))
        THEN 'Non-consommation'
        ELSE 'Surconsommation'
    END                                                  AS type_ecart_dominant,

    SUM(f.ecart_equivalent_produit)                      AS ecart_equivalent_produit_total,
    MAX(f.child_cout_standard)                           AS child_cout_standard,
    SUM(f.ecart_valorise)                                AS ecart_valorise_total,
    SUM(ABS(f.ecart_valorise))                           AS ecart_valorise_absolu,

    CURRENT_TIMESTAMP()                                  AS loaded_at
FROM {catalog}.{schema}.fact_ecart_backflush AS f
GROUP BY f.child_itemid, f.parent_programme, f.parent_perimetre;
