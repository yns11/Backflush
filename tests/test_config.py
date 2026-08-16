"""Tests de la configuration, en particulier la détection du mode de connexion.

Trois modes coexistent selon la façon dont Lakebase est attaché. Se tromper de
mode produit soit une application qui refuse de démarrer, soit une application
qui perd sa connexion au bout d'une heure — d'où ces tests.
"""

from __future__ import annotations

from app.server.core.config import Settings


def _settings(**surcharges) -> Settings:
    """Construit une configuration en neutralisant l'environnement ambiant.

    Sans cette neutralisation, un ``LAKEBASE_PG_URL`` exporté pour les tests
    d'intégration ferait passer tous les cas en mode « url_directe ».
    """
    base = {
        "lakebase_pg_url": None,
        "pghost": None,
        "pguser": None,
        "lakebase_endpoint": None,
        "pgpassword": None,
    }
    return Settings(**{**base, **surcharges})


class TestModeConnexion:
    def test_sans_rien_la_base_n_est_pas_configuree(self) -> None:
        settings = _settings()
        assert settings.mode_connexion == "non_configure"
        assert settings.base_de_donnees_configuree is False

    def test_url_directe_pour_le_developpement_local(self) -> None:
        settings = _settings(lakebase_pg_url="postgresql://u@localhost/db")
        assert settings.mode_connexion == "url_directe"
        assert settings.base_de_donnees_configuree is True

    def test_hote_et_endpoint_donnent_le_mode_oauth(self) -> None:
        settings = _settings(pghost="ep-x.database.cloud.databricks.com",
                             lakebase_endpoint="projects/p/branches/b/endpoints/e")
        assert settings.mode_connexion == "oauth_lakebase"

    def test_hote_et_mot_de_passe_injecte_sans_endpoint(self) -> None:
        """Cas de la ressource « postgres » qui ne fournit pas d'endpoint."""
        settings = _settings(pghost="ep-x.database.cloud.databricks.com", pgpassword="jeton")
        assert settings.mode_connexion == "mot_de_passe_injecte"
        assert settings.base_de_donnees_configuree is True

    def test_l_endpoint_prime_sur_le_mot_de_passe_injecte(self) -> None:
        """Priorité voulue : seul le mode OAuth garantit la rotation du jeton.

        Un mot de passe injecté au démarrage expire au bout d'une heure sans que
        l'application puisse en produire un nouveau.
        """
        settings = _settings(pghost="ep-x", lakebase_endpoint="projects/p/branches/b/endpoints/e",
                             pgpassword="jeton")
        assert settings.mode_connexion == "oauth_lakebase"

    def test_un_hote_seul_ne_suffit_pas(self) -> None:
        assert _settings(pghost="ep-x").base_de_donnees_configuree is False

    def test_l_url_directe_prime_sur_tout(self) -> None:
        settings = _settings(lakebase_pg_url="postgresql://u@localhost/db",
                             pghost="ep-x", lakebase_endpoint="projects/p/branches/b/endpoints/e")
        assert settings.mode_connexion == "url_directe"


class TestResume:
    def test_le_resume_n_expose_aucun_secret(self) -> None:
        settings = _settings(
            pghost="ep-x.database.cloud.databricks.com",
            pgpassword="JETON-TRES-SECRET",
            lakebase_pg_url="postgresql://utilisateur:motdepasse@hote/db",
        )
        rendu = str(settings.resume())
        assert "JETON-TRES-SECRET" not in rendu
        assert "motdepasse" not in rendu
        assert "ep-x.database" not in rendu

    def test_le_resume_annonce_le_mode(self) -> None:
        resume = _settings(pghost="h", lakebase_endpoint="projects/p/branches/b/endpoints/e").resume()
        assert resume["mode_connexion"] == "oauth_lakebase"
