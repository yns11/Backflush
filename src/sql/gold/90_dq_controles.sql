-- =============================================================================
-- 90 — dq_controles : contrôles qualité du modèle
-- =============================================================================
-- Un tableau de bord d'écarts n'est crédible que si l'on sait ce qui est
-- douteux DANS le calcul lui-même. Cette table est publiée dans l'application
-- (bandeau « Qualité des données ») et sert de garde-fou au job : le job échoue
-- si un contrôle de sévérité ERREUR est en anomalie.
--
-- Grain : 1 ligne par contrôle. Colonnes stables → consommable par une alerte
-- Databricks SQL ou un job de monitoring.
-- =============================================================================

CREATE OR REPLACE TABLE {catalog}.{schema}.dq_controles
COMMENT 'Résultat des contrôles qualité du modèle backflush. Sévérité ERREUR = job en échec.'
AS
WITH controles AS (

    -- 1. Parents portant plusieurs versions de nomenclature active.
    --    Une seule est retenue (cf. 11_dim_nomenclature) : le coefficient utilisé
    --    peut différer de celui du bureau d'études.
    SELECT
        'bom_multi_version'                                       AS controle,
        'ALERTE'                                                  AS severite,
        'Nomenclature'                                            AS domaine,
        COUNT(*)                                                  AS valeur,
        0                                                         AS seuil,
        'Articles parents avec plusieurs versions de BOM actives ; une seule version est retenue pour le calcul.' AS message
    FROM (
        SELECT parent_itemid
        FROM (SELECT DISTINCT parent_itemid, bomid FROM {catalog}.{schema}.v_src_bom)
        GROUP BY parent_itemid
        HAVING COUNT(*) > 1
    )

    UNION ALL

    -- 2. Écarts non valorisables : sans coût standard, l'impact € est sous-estimé.
    SELECT
        'composant_sans_cout_standard', 'ALERTE', 'Référentiel',
        COUNT(DISTINCT child_itemid), 0,
        'Composants en écart sans coût standard : leur impact financier est compté pour 0 €.'
    FROM {catalog}.{schema}.fact_ecart_backflush
    WHERE child_cout_standard IS NULL
      AND type_ecart <> 'Conforme'

    UNION ALL

    -- 3. Articles présents dans les mouvements mais absents du référentiel :
    --    programme et catégorie inconnus → lignes non filtrables dans l'app.
    SELECT
        'article_hors_referentiel', 'ERREUR', 'Référentiel',
        COUNT(DISTINCT child_itemid), 0,
        'Composants mouvementés absents de dim_article : programme et catégorie inconnus.'
    FROM {catalog}.{schema}.fact_ecart_backflush
    WHERE child_name IS NULL

    UNION ALL

    -- 4. Coûts standards négatifs (neutralisés en amont).
    SELECT
        'cout_standard_negatif', 'ALERTE', 'Référentiel',
        COUNT(*), 0,
        'Articles avec un coût standard négatif dans la source ; valeur neutralisée (NULL).'
    FROM {catalog}.{schema}.v_src_article
    WHERE std_cost_price < 0

    UNION ALL

    -- 5. Consommations sans ligne de nomenclature : 100 % de surconsommation.
    SELECT
        'lignes_hors_nomenclature', 'ALERTE', 'Cohérence',
        COUNT(*), 0,
        'Couples parent/composant consommés sans ligne de nomenclature correspondante.'
    FROM {catalog}.{schema}.fact_ecart_backflush
    WHERE statut_ligne = 'Hors nomenclature'

    UNION ALL

    -- 6. Parents produits sans aucune nomenclature active : leur consommation
    --    théorique vaut 0, donc aucun écart n'est calculable pour eux.
    SELECT
        'parent_produit_sans_bom', 'ERREUR', 'Nomenclature',
        COUNT(DISTINCT p.parent_itemid), 0,
        'Articles parents produits sans nomenclature active : aucun écart calculable.'
    FROM {catalog}.{schema}.fact_production_parent AS p
    LEFT JOIN (SELECT DISTINCT parent_itemid FROM {catalog}.{schema}.dim_nomenclature) AS n
      ON n.parent_itemid = p.parent_itemid
    WHERE n.parent_itemid IS NULL

    UNION ALL

    -- 7. Coefficients non uniformes : l'équivalent produit n'est pas calculable.
    SELECT
        'coef_non_uniforme', 'INFO', 'Nomenclature',
        COUNT(*), 0,
        'Couples (programme, composant) à coefficient non uniforme : écart en équivalent produit non calculé.'
    FROM {catalog}.{schema}.dim_coef_programme
    WHERE NOT is_coef_uniforme

    UNION ALL

    -- 8. Écarts aberrants : au-delà de ±100 %, l'hypothèse d'un problème de
    --    paramétrage (unité, coefficient) prime sur celle d'un écart physique.
    SELECT
        'ecart_aberrant', 'ALERTE', 'Cohérence',
        COUNT(*), 0,
        'Lignes avec |écart| > 100 % du théorique : suspicion d''erreur d''unité ou de coefficient.'
    FROM {catalog}.{schema}.fact_ecart_backflush
    WHERE conso_theorique > 0
      AND ABS(ecart_pct) > 100

    UNION ALL

    -- 9. Mouvements supprimés dans l'ERP et écartés du calcul.
    --    Volume attendu faible. Une valeur élevée signale des annulations
    --    massives d'ordres de fabrication, à instruire avant de conclure quoi
    --    que ce soit sur les écarts de la période.
    SELECT
        'mouvements_supprimes_exclus', 'INFO', 'Source',
        COUNT(*), 0,
        'Mouvements marqués supprimés dans la source, exclus du modèle.'
    FROM {bronze_catalog}.{bronze_schema}.invent_trans
    WHERE datephysical >= DATE '{date_from}'
      AND (COALESCE(IsDelete, FALSE) OR deleted_at IS NOT NULL)

    UNION ALL

    -- 10. Réconciliation agrégat / détail : tolérance 0,01 € sur le total.
    SELECT
        'reconciliation_agg_detail', 'ERREUR', 'Cohérence',
        CAST(
            CASE WHEN ABS(
                (SELECT COALESCE(SUM(ecart_valorise_total), 0) FROM {catalog}.{schema}.agg_ecart_hebdo_programme)
              - (SELECT COALESCE(SUM(ecart_valorise), 0)       FROM {catalog}.{schema}.fact_ecart_backflush)
            ) > 0.01 THEN 1 ELSE 0 END AS BIGINT),
        0,
        'Écart entre le total valorisé de l''agrégat hebdomadaire et celui de la table de détail.'
)
SELECT
    controle,
    severite,
    domaine,
    valeur,
    seuil,
    valeur > seuil            AS en_anomalie,
    message,
    CURRENT_TIMESTAMP()       AS loaded_at
FROM controles;
