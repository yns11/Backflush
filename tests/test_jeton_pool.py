"""Tests de la rotation du jeton Lakebase dans le pool de connexions.

Panne observée en production : après une période d'inactivité, toutes les
routes de données répondent 503, et le journal empile
« OAuth: User is not authorized » sur chaque tentative de connexion.

Cause : le mot de passe était transmis au pool à son ouverture. Un
``ConnectionPool`` conserve les paramètres qu'on lui donne ; un fil
d'arrière-plan qui met à jour un dictionnaire *lu avant* la construction du
pool n'a donc aucun effet. Le mécanisme de rotation existait, il était inerte —
et rien ne le disait, puisque tout fonctionne pendant la première heure.

Ces tests verrouillent le seul point d'injection fiable : la connexion
physique.
"""

from __future__ import annotations

import itertools
from typing import Any

import psycopg
import pytest

from app.server.core.config import Settings
from app.server.core.lakebase import LakebasePool


def _settings(**surcharges: Any) -> Settings:
    base: dict[str, Any] = {
        "lakebase_pg_url": None,
        "pghost": "ep-test.database.cloud.databricks.com",
        "pgport": 5432,
        "pgdatabase": "databricks_postgres",
        "pguser": "principal-de-service",
        "pgpassword": None,
        "lakebase_endpoint": "projects/p/branches/production/endpoints/primary",
    }
    return Settings(**{**base, **surcharges})


@pytest.fixture
def jetons() -> itertools.count:
    """Fournit « jeton-1 », « jeton-2 »… à chaque génération."""
    return itertools.count(1)


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch, jetons: itertools.count) -> LakebasePool:
    lakebase = LakebasePool(_settings())
    monkeypatch.setattr(
        LakebasePool, "_generate_token", lambda self: f"jeton-{next(jetons)}"
    )
    return lakebase


class ServeurFactice:
    """Serveur Postgres simulé : retient les jetons présentés, peut les refuser."""

    def __init__(self) -> None:
        self.presentes: list[str] = []
        self.refuser: set[str] = set()


@pytest.fixture
def serveur(monkeypatch: pytest.MonkeyPatch) -> ServeurFactice:
    factice = ServeurFactice()

    @classmethod  # type: ignore[misc]
    def faux_connect(cls, conninfo: str = "", **kwargs: Any) -> object:
        motdepasse = kwargs.get("password", "")
        factice.presentes.append(motdepasse)
        if motdepasse in factice.refuser:
            raise psycopg.OperationalError("ERROR:  OAuth: User is not authorized")
        return object()

    monkeypatch.setattr(psycopg.Connection, "connect", faux_connect)
    return factice


class TestJetonPoseAChaqueConnexion:
    def test_le_jeton_est_regenere_une_fois_la_fenetre_ecoulee(
        self, pool: LakebasePool, serveur: ServeurFactice,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Le cœur du défaut : une connexion tardive doit obtenir un jeton neuf."""
        horloge = [0.0]
        monkeypatch.setattr("app.server.core.lakebase.time.monotonic", lambda: horloge[0])
        classe = pool._classe_connexion()

        classe.connect()
        horloge[0] = pool._settings.token_refresh_s + 1     # une demi-heure plus tard
        classe.connect()

        assert serveur.presentes == ["jeton-1", "jeton-2"], (
            "Une connexion ouverte après l'expiration doit présenter un jeton neuf."
        )

    def test_le_jeton_est_mutualise_entre_connexions_rapprochees(
        self, pool: LakebasePool, serveur: ServeurFactice,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Un pic de connexions ne doit pas déclencher autant d'appels d'API."""
        monkeypatch.setattr("app.server.core.lakebase.time.monotonic", lambda: 0.0)
        classe = pool._classe_connexion()

        for _ in range(5):
            classe.connect()

        assert serveur.presentes == ["jeton-1"] * 5


class TestReprisSurRefus:
    def test_un_jeton_refuse_est_regenere_puis_retente(
        self, pool: LakebasePool, serveur: ServeurFactice,
    ) -> None:
        """Auto-rétablissement : sans cela, il faut redémarrer l'application."""
        serveur.refuser.add("jeton-1")

        pool._classe_connexion().connect()

        assert serveur.presentes == ["jeton-1", "jeton-2"]

    def test_un_refus_persistant_finit_par_remonter(
        self, pool: LakebasePool, serveur: ServeurFactice,
    ) -> None:
        """Un seul nouvel essai : ne pas marteler l'API d'identité."""
        serveur.refuser.update({"jeton-1", "jeton-2"})

        with pytest.raises(psycopg.OperationalError):
            pool._classe_connexion().connect()

        assert len(serveur.presentes) == 2

    def test_une_erreur_etrangere_au_jeton_remonte_sans_nouvel_essai(
        self, pool: LakebasePool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Régénérer un jeton ne répare pas un réseau coupé : ne pas masquer."""
        tentatives = []

        @classmethod  # type: ignore[misc]
        def faux_connect(cls, conninfo: str = "", **kwargs: Any) -> object:
            tentatives.append(kwargs.get("password"))
            raise psycopg.OperationalError("connection timed out")

        monkeypatch.setattr(psycopg.Connection, "connect", faux_connect)

        with pytest.raises(psycopg.OperationalError, match="timed out"):
            pool._classe_connexion().connect()

        assert len(tentatives) == 1


class TestParametresDuPool:
    """Ce que le pool reçoit à sa construction — là où le défaut se logeait."""

    @pytest.fixture
    def pool_construit(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        captures: dict[str, Any] = {}

        class PoolFactice:
            check_connection = staticmethod(lambda conn: None)

            def __init__(self, conninfo: str = "", **kwargs: Any) -> None:
                captures.update(kwargs, conninfo=conninfo)

            def open(self, wait: bool = True) -> None:
                pass

        monkeypatch.setattr("app.server.core.lakebase.ConnectionPool", PoolFactice)
        return captures

    def test_aucun_mot_de_passe_n_est_fige_dans_le_pool(
        self, pool_construit: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(LakebasePool, "_generate_token", lambda self: "jeton")
        LakebasePool(_settings()).open()

        assert "password" not in pool_construit["kwargs"], (
            "Un mot de passe transmis au pool y reste figé : après expiration, "
            "toute nouvelle connexion est refusée jusqu'au redémarrage."
        )
        assert issubclass(pool_construit["connection_class"], psycopg.Connection)
        assert pool_construit["connection_class"] is not psycopg.Connection

    def test_le_mot_de_passe_injecte_reste_transmis_tel_quel(
        self, pool_construit: dict[str, Any],
    ) -> None:
        """Sans endpoint, la plateforme fournit un mot de passe : rien à générer."""
        LakebasePool(_settings(lakebase_endpoint=None, pgpassword="fourni")).open()

        assert pool_construit["kwargs"]["password"] == "fourni"
        assert pool_construit["connection_class"] is psycopg.Connection
