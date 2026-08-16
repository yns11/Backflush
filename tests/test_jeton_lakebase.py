"""Tests de la génération d'identifiant Lakebase.

Deux générations d'API coexistent dans le SDK Databricks — ``w.postgres``
(projects / branches / endpoints) et ``w.database`` (database instances) — et la
version embarquée dans un environnement de job ou dans le runtime Databricks
Apps n'est pas forcément celle du poste de développement.

Sans repli, l'absence de l'une produit un ``AttributeError`` sec, qui ne dit ni
pourquoi ni comment y remédier. Ces tests verrouillent le repli et la qualité du
diagnostic.
"""

from __future__ import annotations

import pytest

from src.jobs.sync_to_lakebase import generer_jeton_lakebase, version_sdk

ENDPOINT = "projects/backflush/branches/production/endpoints/primary"


class ApiPostgres:
    def __init__(self, jeton: str = "jeton-postgres", erreur: Exception | None = None) -> None:
        self._jeton, self._erreur = jeton, erreur
        self.appels: list[str] = []

    def generate_database_credential(self, endpoint: str):
        self.appels.append(endpoint)
        if self._erreur:
            raise self._erreur
        return type("Identifiant", (), {"token": self._jeton})()


class ApiDatabase:
    def __init__(self, jeton: str = "jeton-database", erreur: Exception | None = None) -> None:
        self._jeton, self._erreur = jeton, erreur
        self.appels: list[dict] = []

    def generate_database_credential(self, request_id: str, instance_names: list[str]):
        self.appels.append({"request_id": request_id, "instance_names": instance_names})
        if self._erreur:
            raise self._erreur
        return type("Identifiant", (), {"token": self._jeton})()


def workspace(**apis) -> object:
    """Client factice n'exposant que les API demandées."""
    return type("WorkspaceFactice", (), apis)()


class TestGenerationDuJeton:
    def test_la_generation_actuelle_est_privilegiee(self) -> None:
        postgres, database = ApiPostgres(), ApiDatabase()
        jeton = generer_jeton_lakebase(workspace(postgres=postgres, database=database), ENDPOINT)

        assert jeton == "jeton-postgres"
        assert postgres.appels == [ENDPOINT]
        assert database.appels == [], "L'API antérieure ne doit pas être sollicitée inutilement."

    def test_repli_quand_l_api_postgres_est_absente(self) -> None:
        """Cas rencontré en production : SDK trop ancien pour `w.postgres`."""
        database = ApiDatabase()
        jeton = generer_jeton_lakebase(workspace(database=database), ENDPOINT)

        assert jeton == "jeton-database"
        # Le chemin de ressource est converti en identifiant de projet.
        assert database.appels[0]["instance_names"] == ["backflush"]
        assert database.appels[0]["request_id"], "Un identifiant de requête est attendu."

    def test_repli_quand_l_api_postgres_echoue(self) -> None:
        postgres = ApiPostgres(erreur=RuntimeError("endpoint indisponible"))
        database = ApiDatabase()
        jeton = generer_jeton_lakebase(workspace(postgres=postgres, database=database), ENDPOINT)

        assert jeton == "jeton-database"

    def test_aucune_api_disponible_produit_un_diagnostic_complet(self) -> None:
        with pytest.raises(RuntimeError) as erreur:
            generer_jeton_lakebase(workspace(), ENDPOINT)

        message = str(erreur.value)
        assert ENDPOINT in message
        assert "w.postgres" in message and "w.database" in message
        assert "databricks-sdk" in message, "Le remède doit être nommé."
        assert "PGPASSWORD" in message, "L'échappatoire doit être nommée."
        assert "Version du SDK" in message

    def test_les_deux_echecs_sont_rapportes(self) -> None:
        postgres = ApiPostgres(erreur=RuntimeError("403 interdit"))
        database = ApiDatabase(erreur=RuntimeError("instance inconnue"))

        with pytest.raises(RuntimeError) as erreur:
            generer_jeton_lakebase(workspace(postgres=postgres, database=database), ENDPOINT)

        message = str(erreur.value)
        assert "403 interdit" in message
        assert "instance inconnue" in message

    def test_un_endpoint_sans_chemin_reste_utilisable(self) -> None:
        database = ApiDatabase()
        generer_jeton_lakebase(workspace(database=database), "mon-instance")
        assert database.appels[0]["instance_names"] == ["mon-instance"]


class TestVersionSdk:
    def test_la_version_est_toujours_lisible(self) -> None:
        """Le diagnostic ne doit jamais échouer, SDK installé ou non."""
        assert isinstance(version_sdk(), str)
        assert version_sdk()
