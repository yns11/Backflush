"""Tâches Databricks du pipeline « écarts backflush ».

Deux jobs :

* :mod:`src.jobs.build_gold`        — construit le modèle gold dans Unity Catalog.
* :mod:`src.jobs.sync_to_lakebase`  — publie le modèle gold dans Lakebase Postgres.

Le module :mod:`src.jobs.sqlutil` est volontairement sans dépendance à Spark :
il est testable en local (voir ``tests/test_sqlutil.py``).
"""
