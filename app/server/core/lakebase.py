"""Pool de connexions Lakebase, avec rotation du jeton OAuth.

Trois contraintes propres à Lakebase dictent ce module :

1. **Le jeton OAuth expire au bout d'une heure.** Un mot de passe transmis au
   pool y reste figé pour toute sa durée de vie : passé une heure, chaque
   nouvelle connexion est refusée (« OAuth: User is not authorized »), et comme
   le pool recycle ses connexions, l'application cesse de servir. Le jeton est
   donc posé à **chaque connexion physique**, par une classe de connexion
   dédiée, et non écrit une fois pour toutes dans les paramètres du pool.
2. **La mise à l'échelle à zéro** réveille l'instance à la première connexion :
   ``check`` (pre-ping) évite de servir une connexion morte à une requête HTTP.
3. **L'application est en lecture seule.** Chaque connexion est configurée en
   ``default_transaction_read_only`` : même une régression de code ne peut pas
   écrire dans la base analytique. C'est une garantie structurelle, pas une
   convention.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.numeric import FloatLoader
from psycopg_pool import ConnectionPool

from app.server.core.config import Settings, diagnostic_environnement
from app.server.core.errors import ConfigurationError, DonneesIndisponiblesError

LOGGER = logging.getLogger("backflush.lakebase")

# Les colonnes `numeric` sont chargées en flottant plutôt qu'en `Decimal`.
#
# Ce n'est PAS une simplification de confort : sérialisé en JSON, un `Decimal`
# devient une CHAÎNE (« "12.000000" »), que le frontend doit alors reconvertir à
# chaque usage — un oubli produit silencieusement un « — » ou un NaN à l'écran.
# Comme JSON ne connaît de toute façon pas le type décimal, la valeur finirait
# en double côté navigateur : autant faire la conversion une seule fois, ici, de
# façon explicite. La précision d'un double (~15 chiffres significatifs) couvre
# très largement des quantités et des montants industriels.
#
# Corollaire assumé : cette application est en LECTURE SEULE. Aucun calcul
# monétaire à valeur juridique (facturation, comptabilité) n'y est produit ;
# les agrégations financières sont faites en `numeric` par Postgres, et seul le
# résultat est converti.
psycopg.adapters.register_loader("numeric", FloatLoader)

#: Chemin REST de génération d'un identifiant Lakebase. Relevé dans la source du
#: SDK Databricks : c'est le contrat réel du service, que la méthode typée ne
#: fait qu'envelopper. L'appeler directement rend le code indépendant de la
#: version du SDK embarquée dans le runtime.
REST_CREDENTIALS_POSTGRES = "/api/2.0/postgres/credentials"


class LakebasePool:
    """Encapsule un :class:`ConnectionPool` psycopg et la rotation du jeton."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: ConnectionPool | None = None
        self._kwargs: dict[str, Any] = {}
        self._workspace: Any | None = None
        # Jeton mis en cache, régénéré à l'usage. Le verrou évite qu'un pic de
        # connexions simultanées déclenche autant d'appels à l'API d'identité.
        self._verrou = threading.Lock()
        self._jeton: str | None = None
        self._jeton_obtenu_le: float = 0.0

    # -- Cycle de vie ------------------------------------------------------
    def open(self) -> None:
        """Ouvre le pool. Ne lève pas si la base n'est pas configurée.

        Une application qui démarre malgré une base absente reste
        diagnosticable : ``/api/health`` répond, les routes de données
        renvoient un 503 explicite.
        """
        settings = self._settings
        if not settings.base_de_donnees_configuree:
            # Le diagnostic est journalisé ici, et non laissé à la charge de
            # l'exploitant : « non configurée » sans plus de détail oblige à
            # aller inspecter l'application à la main, alors que la liste des
            # variables absentes tranche immédiatement entre les deux causes.
            diagnostic = diagnostic_environnement()
            LOGGER.warning(
                "Lakebase non configurée : les routes de données répondront 503. "
                "Variables injectées présentes : %s ; absentes : %s. "
                "Deux causes possibles : (1) la ressource « postgres » n'est pas "
                "attachée à l'application — vérifier avec « databricks apps get "
                "<application> -o json » le tableau resources ; (2) elle l'est, "
                "mais le déploiement en cours lui est ANTÉRIEUR — les variables "
                "sont injectées à la création du déploiement, un simple "
                "redémarrage ne suffit pas : relancer « databricks bundle run "
                "backflush_analytics ».",
                ", ".join(diagnostic["presentes"]) or "aucune",
                ", ".join(diagnostic["absentes"]),
            )
            return

        mode = settings.mode_connexion
        classe_connexion: type[psycopg.Connection] = psycopg.Connection
        if mode == "url_directe":
            conninfo = settings.lakebase_pg_url or ""
            self._kwargs = {}
        else:
            conninfo = ""
            self._kwargs = {
                "host": settings.pghost,
                "port": settings.pgport,
                "dbname": settings.pgdatabase,
                "user": settings.pguser or self._current_user(),
                "sslmode": "require",
            }
            if mode == "oauth_lakebase":
                # Le mot de passe n'est PAS figé ici : il est posé à chaque
                # connexion physique par la classe ci-dessous. Un jeton écrit
                # dans les paramètres du pool serait figé à l'ouverture, et
                # toute connexion ouverte après son expiration échouerait.
                classe_connexion = self._classe_connexion()
            else:
                # La ressource « postgres » de l'application a injecté un
                # identifiant. Il fonctionne, mais sa rotation appartient à la
                # plateforme : sans LAKEBASE_ENDPOINT, l'application ne peut pas
                # en produire un nouveau. On le signale plutôt que de le subir.
                self._kwargs["password"] = settings.pgpassword
                LOGGER.warning(
                    "Connexion par mot de passe injecté (PGPASSWORD). Définissez "
                    "LAKEBASE_ENDPOINT pour que l'application gère elle-même la "
                    "rotation du jeton."
                )

        self._pool = ConnectionPool(
            conninfo=conninfo,
            kwargs={**self._kwargs, "row_factory": dict_row} if self._kwargs
            else {"row_factory": dict_row},
            connection_class=classe_connexion,
            min_size=settings.pool_min_size,
            max_size=settings.pool_max_size,
            timeout=settings.pool_timeout_s,
            max_lifetime=1_800,          # recyclage régulier : aucune connexion ne vieillit indéfiniment
            check=ConnectionPool.check_connection,   # pre-ping : détecte le réveil après scale-to-zero
            configure=self._configure,
            open=False,
            name="lakebase",
        )
        self._pool.open(wait=False)
        LOGGER.info(
            "Pool Lakebase ouvert (min=%d, max=%d, schéma=%s).",
            settings.pool_min_size, settings.pool_max_size, settings.pg_schema,
        )

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None
        LOGGER.info("Pool Lakebase fermé.")

    # -- Accès -------------------------------------------------------------
    @property
    def disponible(self) -> bool:
        return self._pool is not None

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        """Fournit une connexion du pool, en lecture seule."""
        if self._pool is None:
            raise ConfigurationError()
        try:
            with self._pool.connection() as conn:
                yield conn
        except ConfigurationError:
            raise
        except psycopg.Error as exc:
            LOGGER.error("Erreur Lakebase : %s", exc)
            raise DonneesIndisponiblesError() from exc
        except Exception as exc:  # pool timeout, DNS, TLS…
            LOGGER.error("Lakebase injoignable : %s", exc)
            raise DonneesIndisponiblesError() from exc

    @contextmanager
    def connexion_ecriture(self) -> Iterator[psycopg.Connection]:
        """Connexion autorisée à écrire — **uniquement** sur le paramétrage.

        Le pool ouvre chaque connexion en ``default_transaction_read_only``
        (voir :meth:`_configure`) : c'est un verrou structurel, qui garantit
        qu'aucune régression de code ne peut altérer les données analytiques.
        Le paramétrage du key-user, lui, doit bien s'écrire quelque part.

        Plutôt que d'ouvrir un second pool en lecture-écriture — dont chaque
        requête de lecture profiterait aussi, ruinant la garantie — la levée est
        posée **par transaction**, par un ``SET TRANSACTION READ WRITE`` qui ne
        vaut que pour celle en cours. La portée du droit d'écriture est ainsi
        exactement le bloc ``with``, et les rares appelants sont trouvables d'un
        seul ``grep``.

        Les droits Postgres restent la barrière ultime : le principal de service
        ne reçoit ``INSERT``/``UPDATE``/``DELETE`` que sur les deux tables
        ``param_*`` (voir ``src/jobs/sync_to_lakebase.py``). Même cette
        transaction ne peut pas toucher aux tables de faits.
        """
        with self.connection() as conn:
            try:
                with conn.transaction():
                    # Doit être la PREMIÈRE instruction de la transaction :
                    # Postgres refuse de changer le mode d'une transaction déjà
                    # entamée.
                    conn.execute("SET TRANSACTION READ WRITE")
                    yield conn
            except psycopg.Error as exc:
                LOGGER.error("Écriture Lakebase refusée : %s", exc)
                raise

    def ping(self) -> dict[str, Any]:
        """Teste la connexion et retourne un diagnostic exploitable par /api/health."""
        if self._pool is None:
            return {
                "statut": "non_configure",
                "detail": "Aucune ressource Lakebase attachée.",
                # Exposé pour que /api/health suffise au diagnostic, sans avoir
                # à corréler avec les journaux. Noms de variables uniquement.
                **diagnostic_environnement(),
            }
        try:
            with self.connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1 AS ok")
                cur.fetchone()
            return {"statut": "ok"}
        except DonneesIndisponiblesError as exc:
            return {"statut": "indisponible", "detail": exc.message}

    # -- Jeton -------------------------------------------------------------
    def _classe_connexion(self) -> type[psycopg.Connection]:
        """Classe de connexion qui pose un jeton FRAIS à chaque ouverture.

        C'est le seul point d'injection fiable. Le pool résout ses paramètres
        de connexion à chaque ouverture physique, mais un mot de passe écrit
        dans ces paramètres à l'ouverture du pool y reste figé : après une
        heure, toute nouvelle connexion est refusée (« OAuth: User is not
        authorized »), et comme le pool recycle ses connexions, l'application
        finit par ne plus rien pouvoir servir.

        La classe gère aussi le refus d'un jeton mis en cache : elle l'invalide
        et retente **une** fois. L'application se rétablit donc seule après une
        expiration prématurée ou une révocation, sans attendre un redémarrage.
        """
        fournir = self._jeton_courant
        invalider = self._invalider_jeton

        class ConnexionLakebase(psycopg.Connection):
            @classmethod
            def connect(cls, conninfo: str = "", **kwargs: Any) -> Any:
                try:
                    return super().connect(conninfo, password=fournir(), **kwargs)
                except psycopg.OperationalError as exc:
                    if "not authorized" not in str(exc).lower():
                        raise
                    LOGGER.warning("Jeton Lakebase refusé ; régénération et nouvel essai.")
                    invalider()
                    return super().connect(conninfo, password=fournir(), **kwargs)

        return ConnexionLakebase

    def _jeton_courant(self) -> str:
        """Retourne le jeton en cache, ou en génère un s'il a fait son temps."""
        with self._verrou:
            age = time.monotonic() - self._jeton_obtenu_le
            if self._jeton is None or age >= self._settings.token_refresh_s:
                self._jeton = self._generate_token()
                self._jeton_obtenu_le = time.monotonic()
                LOGGER.info("Jeton Lakebase généré (valable ~1 h).")
            return self._jeton

    def _invalider_jeton(self) -> None:
        with self._verrou:
            self._jeton = None

    # -- Interne -----------------------------------------------------------
    def _configure(self, conn: psycopg.Connection) -> None:
        """Applique les garde-fous à chaque nouvelle connexion physique."""
        settings = self._settings
        with conn.cursor() as cur:
            # Une requête pathologique est annulée par le serveur plutôt que de
            # saturer le pool jusqu'au timeout HTTP.
            cur.execute(f"SET statement_timeout = {int(settings.statement_timeout_ms)}")
            # Verrou structurel : l'application ne peut pas écrire.
            cur.execute("SET default_transaction_read_only = on")
            cur.execute(f'SET search_path = "{settings.pg_schema}", public')
        conn.commit()

    def _workspace_client(self) -> Any:
        if self._workspace is None:
            from databricks.sdk import WorkspaceClient

            self._workspace = WorkspaceClient()
        return self._workspace

    def _current_user(self) -> str:
        return self._workspace_client().current_user.me().user_name

    def _generate_token(self) -> str:
        """Génère un identifiant Lakebase, quelle que soit la génération d'API du SDK.

        Deux générations coexistent, et la version embarquée dans le runtime
        Databricks Apps n'est pas forcément celle du poste de développement :
        ``w.postgres`` (projects / branches / endpoints, actuelle) et
        ``w.database`` (database instances, antérieure). On essaie la plus
        récente, puis l'autre. Sans ce repli, l'application démarre puis échoue
        sur un ``AttributeError`` qui ne dit ni pourquoi ni comment y remédier.
        """
        endpoint = self._settings.lakebase_endpoint
        if not endpoint:
            raise ConfigurationError("LAKEBASE_ENDPOINT est requis en mode OAuth.")

        workspace = self._workspace_client()
        tentatives: list[str] = []

        # 1. Méthode typée, si la version du SDK la porte.
        api_postgres = getattr(workspace, "postgres", None)
        if api_postgres is not None and hasattr(api_postgres, "generate_database_credential"):
            try:
                return api_postgres.generate_database_credential(endpoint=endpoint).token
            except Exception as exc:
                tentatives.append(f"w.postgres : {exc}")
        else:
            tentatives.append("w.postgres : absent de cette version du SDK")

        # 2. Appel REST direct — contrat réel du service, que la méthode typée
        #    ne fait qu'envelopper. `api_client.do` existe depuis les premières
        #    versions du SDK : cette piste est indépendante de la version.
        client = getattr(workspace, "api_client", None)
        if client is not None and hasattr(client, "do"):
            try:
                reponse = client.do("POST", REST_CREDENTIALS_POSTGRES, body={"endpoint": endpoint})
                jeton = (reponse or {}).get("token")
                if jeton:
                    LOGGER.info("Identifiant Lakebase obtenu par appel REST direct.")
                    return jeton
                tentatives.append("REST postgres : réponse sans jeton")
            except Exception as exc:
                tentatives.append(f"REST postgres : {exc}")

        # 3. Génération antérieure, pour les espaces restés sur les instances.
        instance = endpoint.split("/")[1] if "/" in endpoint else endpoint
        api_database = getattr(workspace, "database", None)
        if api_database is not None and hasattr(api_database, "generate_database_credential"):
            try:
                return api_database.generate_database_credential(
                    request_id=str(uuid4()), instance_names=[instance]
                ).token
            except Exception as exc:
                tentatives.append(f"w.database : {exc}")

        LOGGER.error("Génération d'identifiant Lakebase impossible : %s", " | ".join(tentatives))
        raise ConfigurationError(
            "Impossible d'obtenir un identifiant de connexion à Lakebase. "
            "Fournissez PGPASSWORD via la ressource « postgres » de l'application, "
            "ou relevez la version de databricks-sdk dans app/requirements.txt."
        )
