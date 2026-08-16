"""Job ❷ — Publication du modèle gold Unity Catalog dans Lakebase Postgres.

Stratégie : **chargement en table de transit puis bascule atomique**.

Pour chaque table ::

    1. DROP  <table>__stg          (reliquat d'une exécution interrompue)
    2. CREATE <table>__stg          (DDL généré par lakebase_schema)
    3. COPY   → <table>__stg        (protocole COPY, ~10× plus rapide qu'INSERT)
    4. CREATE INDEX sur <table>__stg + ANALYZE
    5. transaction : DROP <table> ; RENAME <table>__stg → <table> ;
                     renommage des index et de la contrainte ; re-GRANT

Pourquoi cette stratégie plutôt qu'un TRUNCATE + INSERT :

* les lecteurs de l'application ne voient jamais de table vide ni partielle —
  l'indisponibilité se réduit au commit (quelques millisecondes) ;
* les index sont construits sur une table déjà remplie, ce qui est nettement
  plus rapide que de les maintenir pendant le chargement ;
* une exécution qui échoue laisse la table de production intacte.

⚠️ Le renommage crée une NOUVELLE table : les privilèges de l'ancienne sont
perdus. Le re-GRANT de l'étape 5 est obligatoire, pas cosmétique.

Exemple d'appel ::

    python -m src.jobs.sync_to_lakebase \
        --catalog emotors_data_champions --schema backflush \
        --pg-host inst-xxx.database.cloud.databricks.com \
        --pg-database databricks_postgres \
        --lakebase-endpoint projects/<id>/branches/production/endpoints/<ep> \
        --app-role "backflush-analytics"
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg import sql as pgsql

from src.jobs.lakebase_schema import (
    META_INGESTION,
    SCHEMA,
    TABLES,
    Table,
    create_indexes_sql,
    create_table_sql,
)
from src.jobs.sqlutil import validate_identifier

LOGGER = logging.getLogger("backflush.sync_to_lakebase")

#: Suffixe de la table de transit.
STAGING_SUFFIX = "__stg"

#: Fréquence de journalisation pendant le COPY.
LOG_EVERY = 100_000


# ---------------------------------------------------------------------------
# Connexion
# ---------------------------------------------------------------------------
def connect(args: argparse.Namespace) -> psycopg.Connection:
    """Ouvre une connexion Lakebase.

    Deux modes, dans cet ordre :

    * ``LAKEBASE_PG_URL`` — développement local / Postgres de test ;
    * OAuth Databricks — jeton d'une heure généré pour l'endpoint Lakebase.
      Le job dure moins d'une heure par construction ; en cas de volumétrie
      exceptionnelle, relancer par sous-ensemble de tables (``--tables``).
    """
    url = os.getenv("LAKEBASE_PG_URL")
    if url:
        LOGGER.info("Connexion Lakebase via LAKEBASE_PG_URL (mode local).")
        return psycopg.connect(url, autocommit=False)

    if not (args.pg_host and args.lakebase_endpoint):
        raise SystemExit(
            "Connexion impossible : définissez LAKEBASE_PG_URL, ou passez "
            "--pg-host et --lakebase-endpoint."
        )

    from databricks.sdk import WorkspaceClient

    workspace = WorkspaceClient()
    token = workspace.postgres.generate_database_credential(
        endpoint=args.lakebase_endpoint
    ).token
    user = args.pg_user or workspace.current_user.me().user_name

    LOGGER.info("Connexion Lakebase %s/%s en tant que %s", args.pg_host, args.pg_database, user)
    return psycopg.connect(
        host=args.pg_host,
        port=args.pg_port,
        dbname=args.pg_database,
        user=user,
        password=token,
        sslmode="require",
        autocommit=False,
    )


# ---------------------------------------------------------------------------
# Publication d'une table
# ---------------------------------------------------------------------------
def effective_indexes(table: Table, *, allow_gin: bool) -> tuple:
    """Index réellement créés, en tenant compte de la disponibilité de pg_trgm."""
    if allow_gin:
        return tuple(table.indexes)
    return tuple(index for index in table.indexes if index.method != "gin")


def publish_table(
    conn: psycopg.Connection,
    spark,
    table: Table,
    *,
    catalog: str,
    gold_schema: str,
    pg_schema: str,
    roles: Sequence[str],
    allow_gin: bool = True,
) -> int:
    """Charge une table gold dans Lakebase et bascule atomiquement. Retourne le nb de lignes."""
    source_fqn = f"{catalog}.{gold_schema}.{table.source}"
    staging = table.name + STAGING_SUFFIX
    indexes = effective_indexes(table, allow_gin=allow_gin)

    LOGGER.info("[%s] lecture de %s", table.name, source_fqn)
    dataframe = spark.table(source_fqn).select(*table.column_names)

    with conn.cursor() as cur:
        cur.execute(
            pgsql.SQL("DROP TABLE IF EXISTS {}.{} CASCADE").format(
                pgsql.Identifier(pg_schema), pgsql.Identifier(staging)
            )
        )
        cur.execute(create_table_sql(table, name=staging, schema=pg_schema))
    conn.commit()

    row_count = _copy_rows(conn, dataframe, table, staging, pg_schema)

    # Index construits APRÈS le chargement : bien plus rapide que de les
    # maintenir ligne à ligne pendant le COPY.
    with conn.cursor() as cur:
        for statement in create_indexes_sql(
            _with_indexes(table, indexes), name=staging, schema=pg_schema
        ):
            cur.execute(statement)
        # Statistiques fraîches dès la bascule : sans ANALYZE, la première
        # requête de l'application planifie sur des estimations par défaut et
        # part en balayage séquentiel.
        cur.execute(
            pgsql.SQL("ANALYZE {}.{}").format(
                pgsql.Identifier(pg_schema), pgsql.Identifier(staging)
            )
        )
    conn.commit()

    _swap(conn, table, staging, pg_schema, roles, indexes)
    LOGGER.info("[%s] publiée — %d ligne(s)", table.name, row_count)
    return row_count


def _with_indexes(table: Table, indexes: tuple) -> Table:
    """Copie de ``table`` restreinte au jeu d'index fourni."""
    return replace(table, indexes=indexes)


def _copy_rows(
    conn: psycopg.Connection,
    dataframe,
    table: Table,
    staging: str,
    pg_schema: str,
) -> int:
    """Écrit les lignes du DataFrame dans la table de transit via COPY."""
    columns = pgsql.SQL(", ").join(pgsql.Identifier(name) for name in table.column_names)
    statement = pgsql.SQL("COPY {}.{} ({}) FROM STDIN").format(
        pgsql.Identifier(pg_schema), pgsql.Identifier(staging), columns
    )

    written = 0
    started = time.monotonic()
    with conn.cursor() as cur, cur.copy(statement) as copy:
        # toLocalIterator() rapatrie les partitions une à une : l'empreinte
        # mémoire du driver reste bornée même sur une table volumineuse.
        for row in dataframe.toLocalIterator():
            copy.write_row(tuple(row))
            written += 1
            if written % LOG_EVERY == 0:
                LOGGER.info("[%s] %d lignes copiées…", table.name, written)
    conn.commit()

    elapsed = time.monotonic() - started
    LOGGER.info(
        "[%s] COPY terminé : %d lignes en %.1f s (%.0f lignes/s)",
        table.name, written, elapsed, written / elapsed if elapsed else 0.0,
    )
    return written


def _swap(
    conn: psycopg.Connection,
    table: Table,
    staging: str,
    pg_schema: str,
    roles: Sequence[str],
    indexes: tuple,
) -> None:
    """Bascule transactionnelle transit → production, index et droits compris."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            pgsql.SQL("DROP TABLE IF EXISTS {}.{} CASCADE").format(
                pgsql.Identifier(pg_schema), pgsql.Identifier(table.name)
            )
        )
        cur.execute(
            pgsql.SQL("ALTER TABLE {}.{} RENAME TO {}").format(
                pgsql.Identifier(pg_schema), pgsql.Identifier(staging),
                pgsql.Identifier(table.name),
            )
        )

        if table.primary_key:
            cur.execute(
                pgsql.SQL("ALTER TABLE {}.{} RENAME CONSTRAINT {} TO {}").format(
                    pgsql.Identifier(pg_schema), pgsql.Identifier(table.name),
                    pgsql.Identifier(f"{staging}_pkey"), pgsql.Identifier(f"{table.name}_pkey"),
                )
            )

        for index in indexes:
            cur.execute(
                pgsql.SQL("ALTER INDEX {}.{} RENAME TO {}").format(
                    pgsql.Identifier(pg_schema),
                    pgsql.Identifier(f"ix_{staging}_{index.suffix}"),
                    pgsql.Identifier(f"ix_{table.name}_{index.suffix}"),
                )
            )

        # Le renommage a produit une table neuve : sans ce GRANT, le principal
        # de service de l'application perd l'accès à la table publiée.
        for role in roles:
            cur.execute(
                pgsql.SQL("GRANT SELECT ON {}.{} TO {}").format(
                    pgsql.Identifier(pg_schema), pgsql.Identifier(table.name),
                    pgsql.Identifier(role),
                )
            )


# ---------------------------------------------------------------------------
# Journal d'ingestion
# ---------------------------------------------------------------------------
def ensure_meta_table(conn: psycopg.Connection, pg_schema: str) -> None:
    with conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{pg_schema}"')
        cur.execute(
            create_table_sql(META_INGESTION, schema=pg_schema).replace(
                "CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1
            )
        )
    conn.commit()


def record_ingestion(
    conn: psycopg.Connection,
    pg_schema: str,
    *,
    table_name: str,
    source_table: str,
    row_count: int,
    started_at: datetime,
    ended_at: datetime,
    status: str,
    error_message: str | None,
    run_id: str,
    roles: Sequence[str],
) -> None:
    duration_ms = int((ended_at - started_at).total_seconds() * 1000)
    with conn.cursor() as cur:
        cur.execute(
            pgsql.SQL(
                """
                INSERT INTO {}.{} (table_name, source_table, row_count, started_at,
                                   ended_at, duration_ms, status, error_message, run_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (table_name) DO UPDATE SET
                    source_table  = EXCLUDED.source_table,
                    row_count     = EXCLUDED.row_count,
                    started_at    = EXCLUDED.started_at,
                    ended_at      = EXCLUDED.ended_at,
                    duration_ms   = EXCLUDED.duration_ms,
                    status        = EXCLUDED.status,
                    error_message = EXCLUDED.error_message,
                    run_id        = EXCLUDED.run_id
                """
            ).format(pgsql.Identifier(pg_schema), pgsql.Identifier(META_INGESTION.name)),
            (table_name, source_table, row_count, started_at, ended_at,
             duration_ms, status, error_message, run_id),
        )
        for role in roles:
            cur.execute(
                pgsql.SQL("GRANT SELECT ON {}.{} TO {}").format(
                    pgsql.Identifier(pg_schema), pgsql.Identifier(META_INGESTION.name),
                    pgsql.Identifier(role),
                )
            )
    conn.commit()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="emotors_data_champions")
    parser.add_argument("--schema", default="backflush", help="Schéma gold Unity Catalog.")
    parser.add_argument("--pg-schema", default=SCHEMA, help="Schéma Postgres cible.")
    parser.add_argument("--pg-host", default=os.getenv("PGHOST"))
    parser.add_argument("--pg-port", type=int, default=int(os.getenv("PGPORT", "5432")))
    parser.add_argument("--pg-database", default=os.getenv("PGDATABASE", "databricks_postgres"))
    parser.add_argument("--pg-user", default=os.getenv("PGUSER"))
    parser.add_argument("--lakebase-endpoint", default=os.getenv("LAKEBASE_ENDPOINT"))
    parser.add_argument(
        "--app-role",
        action="append",
        default=[],
        dest="app_roles",
        help="Rôle Postgres recevant SELECT après la bascule (répétable). "
             "En général le client_id du principal de service de l'application.",
    )
    parser.add_argument(
        "--tables",
        default="",
        help="Sous-ensemble de tables à publier (séparées par des virgules). "
             "Vide = toutes.",
    )
    parser.add_argument("--run-id", default=os.getenv("DATABRICKS_RUN_ID", "local"))
    return parser


def select_tables(names: str) -> tuple[Table, ...]:
    if not names.strip():
        return TABLES
    wanted = {name.strip() for name in names.split(",") if name.strip()}
    known = {table.name for table in TABLES}
    unknown = wanted - known
    if unknown:
        raise SystemExit(f"Tables inconnues : {', '.join(sorted(unknown))}")
    return tuple(table for table in TABLES if table.name in wanted)


def run(spark, args: argparse.Namespace) -> dict[str, int]:
    catalog = validate_identifier(args.catalog, label="catalog")
    gold_schema = validate_identifier(args.schema, label="schema")
    pg_schema = validate_identifier(args.pg_schema, label="pg_schema")
    # Les rôles ne sont PAS validés comme identifiants : un principal de service
    # Lakebase porte un client_id de la forme « 1a2b-... », qui n'est pas un
    # identifiant SQL nu. psycopg.sql.Identifier le met entre guillemets de façon
    # sûre — c'est la protection, pas une validation par expression régulière.
    roles = list(args.app_roles)
    tables = select_tables(args.tables)

    results: dict[str, int] = {}
    with connect(args) as conn:
        ensure_meta_table(conn, pg_schema)
        allow_gin = ensure_extensions(conn)
        for table in tables:
            started_at = datetime.now(UTC)
            try:
                count = publish_table(
                    conn, spark, table,
                    catalog=catalog, gold_schema=gold_schema,
                    pg_schema=pg_schema, roles=roles, allow_gin=allow_gin,
                )
                status, error_message = "SUCCES", None
            except Exception as exc:
                conn.rollback()
                record_ingestion(
                    conn, pg_schema, table_name=table.name, source_table=table.source,
                    row_count=-1, started_at=started_at, ended_at=datetime.now(UTC),
                    status="ECHEC", error_message=str(exc)[:2000], run_id=args.run_id, roles=roles,
                )
                LOGGER.exception("[%s] échec de la publication", table.name)
                raise

            record_ingestion(
                conn, pg_schema, table_name=table.name, source_table=table.source,
                row_count=count, started_at=started_at, ended_at=datetime.now(UTC),
                status=status, error_message=error_message, run_id=args.run_id, roles=roles,
            )
            results[table.name] = count
    return results


def ensure_extensions(conn: psycopg.Connection) -> bool:
    """Active pg_trgm si les droits le permettent. Retourne sa disponibilité.

    Sans pg_trgm, la recherche plein texte reste fonctionnelle (``ILIKE``) mais
    n'est plus indexée, et les index GIN correspondants sont simplement omis.
    On préfère une application plus lente à une application en panne.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        conn.commit()
        return True
    except psycopg.Error as exc:
        conn.rollback()
        LOGGER.warning(
            "pg_trgm indisponible (%s). La recherche restera fonctionnelle mais non "
            "indexée ; les index GIN de recherche sont ignorés.", exc,
        )
        return False


def main(argv: Iterable[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
        stream=sys.stdout,
    )
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)

    from pyspark.sql import SparkSession

    spark = SparkSession.builder.getOrCreate()
    results = run(spark, args)
    total: Any = sum(results.values())
    LOGGER.info("Publication terminée : %d table(s), %s ligne(s).", len(results), f"{total:,}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
