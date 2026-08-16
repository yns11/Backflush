-- =============================================================================
-- 91 — Tables de détail des anomalies qualité
-- =============================================================================
-- `dq_controles` compte les anomalies ; ces tables disent LESQUELLES. Sans
-- elles, un contrôle en anomalie n'est qu'un chiffre : on sait qu'il y a un
-- problème, pas quelle référence transmettre à l'équipe ERP.
--
-- Elles ne sont pas répliquées dans Lakebase : ce sont des supports
-- d'investigation ponctuelle, consultés en SQL, pas des données d'application.
-- =============================================================================

-- --- Composants mouvementés absents du référentiel article -------------------
CREATE OR REPLACE TABLE {catalog}.{schema}.dq_articles_hors_referentiel
COMMENT 'Composants présents dans les mouvements mais absents de dim_article, classés par volume. À transmettre au responsable du référentiel.'
AS
SELECT
    f.child_itemid,
    COUNT(*)                                AS nb_lignes,
    COUNT(DISTINCT f.parent_itemid)         AS nb_parents,
    COUNT(DISTINCT f.parent_programme)      AS nb_programmes,
    MIN(f.semaine_debut)                    AS premiere_semaine,
    MAX(f.semaine_debut)                    AS derniere_semaine,
    SUM(f.conso_reelle)                     AS qty_consommee,
    SUM(f.conso_theorique)                  AS qty_theorique,
    -- Impact non valorisable : sans coût standard, ces lignes pèsent 0 € dans
    -- tous les classements financiers. C'est la mesure de ce qui est invisible.
    SUM(ABS(f.ecart_brut))                  AS ecart_absolu_non_valorise,
    CURRENT_TIMESTAMP()                     AS loaded_at
FROM {catalog}.{schema}.fact_ecart_backflush AS f
WHERE f.child_name IS NULL
GROUP BY f.child_itemid;

-- --- Articles parents produits sans nomenclature active ----------------------
CREATE OR REPLACE TABLE {catalog}.{schema}.dq_parents_sans_nomenclature
COMMENT 'Articles parents produits sans nomenclature active, classés par quantité. Leur production n''entre dans aucun calcul d''écart.'
AS
SELECT
    p.parent_itemid,
    MAX(p.parent_name)                      AS parent_name,
    MAX(p.parent_programme)                 AS parent_programme,
    COUNT(DISTINCT p.semaine_debut)         AS nb_semaines,
    MIN(p.semaine_debut)                    AS premiere_semaine,
    MAX(p.semaine_debut)                    AS derniere_semaine,
    SUM(p.qty_produite)                     AS qty_produite,
    CURRENT_TIMESTAMP()                     AS loaded_at
FROM {catalog}.{schema}.fact_production_parent AS p
LEFT JOIN (SELECT DISTINCT parent_itemid FROM {catalog}.{schema}.dim_nomenclature) AS n
  ON n.parent_itemid = p.parent_itemid
WHERE n.parent_itemid IS NULL
GROUP BY p.parent_itemid;
