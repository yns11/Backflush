-- =============================================================================
-- 00 — Schéma cible du modèle « écarts de consommation backflush »
-- =============================================================================
-- Placeholders substitués par src/jobs/build_gold.py :
--   {catalog}          catalogue cible          ex. emotors_data_champions
--   {schema}           schéma cible             ex. backflush
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}
COMMENT 'Modèle analytique des écarts de consommation composant issus du backflush de production (source : Dynamics 365 F&O). Rafraîchi quotidiennement par le job backflush_gold.';
