-- =============================================================================
-- 40 — agg_ecart_hebdo_programme : KPI et tendances par programme × semaine
-- =============================================================================
-- Usage : graphiques de tendance, KPI par programme, contrôle de cohérence de
-- l'application (les agrégats recalculés à la volée par l'app doivent retomber
-- sur ces valeurs pour une plage couvrant tout l'historique).
--
-- Convention de signe : `non_consommation` et `surconsommation` sont toutes deux
-- exprimées en valeur ABSOLUE (des volumes, pas des soldes) ; `ecart_net` est le
-- solde signé. On a toujours : ecart_net = non_consommation − surconsommation.
-- =============================================================================

CREATE OR REPLACE TABLE {catalog}.{schema}.agg_ecart_hebdo_programme
COMMENT 'Agrégat hebdomadaire des écarts backflush par programme. Grain : programme × semaine ISO.'
TBLPROPERTIES (delta.enableChangeDataFeed = true)
AS
SELECT
    annee,
    semaine,
    semaine_debut,
    parent_programme                                          AS programme,

    COUNT(DISTINCT parent_itemid)                             AS nb_parents,
    COUNT(DISTINCT child_itemid)                              AS nb_composants,
    COUNT(*)                                                  AS nb_lignes,
    COUNT_IF(type_ecart <> 'Conforme')                        AS nb_lignes_avec_ecart,
    COUNT_IF(statut_ligne = 'Hors nomenclature')              AS nb_lignes_hors_nomenclature,
    COUNT_IF(statut_ligne = 'Sans consommation')              AS nb_lignes_sans_consommation,

    SUM(conso_theorique)                                      AS conso_theorique_totale,
    SUM(conso_reelle)                                         AS conso_reelle_totale,

    SUM(ecart_brut)                                           AS ecart_net,
    SUM(GREATEST(ecart_brut, 0))                              AS non_consommation,
    SUM(GREATEST(-ecart_brut, 0))                             AS surconsommation,

    SUM(ecart_valorise)                                       AS ecart_valorise_total,
    SUM(GREATEST(ecart_valorise, 0))                          AS non_consommation_valorisee,
    SUM(GREATEST(-ecart_valorise, 0))                         AS surconsommation_valorisee,
    SUM(ABS(ecart_valorise))                                  AS ecart_valorise_absolu,

    CURRENT_TIMESTAMP()                                       AS loaded_at
FROM {catalog}.{schema}.fact_ecart_backflush
GROUP BY annee, semaine, semaine_debut, parent_programme;
