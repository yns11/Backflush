"""Tests du démarrage de l'application dans la disposition Databricks Apps.

Databricks Apps déploie le **contenu** de ``app/`` à la racine du conteneur :
``app/server/`` devient ``<racine>/server/``. Le paquet ``app`` n'existe donc
plus, alors que tout le code l'importe.

En production, cela s'est traduit par ``ModuleNotFoundError: No module named
'app'`` répété à l'infini — chaque worker uvicorn mourait à la seconde et le
navigateur n'obtenait qu'un « App Not Available ». Les tests qui importent
``app.server.main`` depuis la racine du dépôt ne voient rien : leur disposition
n'est pas celle du conteneur.

Ces tests reproduisent donc la disposition réelle, dans un sous-processus dont
le ``sys.path`` ne contient pas la racine du dépôt.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

RACINE = Path(__file__).resolve().parents[1]
DOSSIER_APP = RACINE / "app"
RESSOURCE_APP = RACINE / "resources" / "backflush_app.yml"


def manifeste() -> dict:
    return yaml.safe_load((DOSSIER_APP / "app.yaml").read_text(encoding="utf-8"))


def cible_uvicorn() -> str:
    """Retourne la cible « module:attribut » déclarée dans app.yaml."""
    commande = manifeste()["command"]
    assert commande[0] == "uvicorn", "Le manifeste ne lance plus uvicorn."
    return commande[1]


def ressources_declarees() -> dict[str, str]:
    """Nom → type de chaque ressource attachée à l'application par le bundle."""
    bundle = yaml.safe_load(RESSOURCE_APP.read_text(encoding="utf-8"))
    application = bundle["resources"]["apps"]["backflush_analytics"]
    types = {}
    for ressource in application["resources"]:
        cles = [cle for cle in ressource if cle not in ("name", "description")]
        assert len(cles) == 1, f"Ressource {ressource['name']} : type ambigu {cles}."
        types[ressource["name"]] = cles[0]
    return types


def injections() -> dict[str, str]:
    """Variable d'environnement → nom de ressource, pour chaque `valueFrom`."""
    return {
        entree["name"]: entree["valueFrom"]
        for entree in manifeste()["env"]
        if "valueFrom" in entree
    }


class TestManifeste:
    def test_la_cible_ne_prefixe_pas_par_le_paquet_app(self) -> None:
        """« app.server.main:app » est le chemin du dépôt, pas celui du conteneur."""
        module = cible_uvicorn().split(":")[0]
        assert not module.startswith("app."), (
            "Le paquet « app » n'existe pas dans le conteneur : son CONTENU est "
            "déployé à la racine. Utiliser « main:app »."
        )

    def test_le_module_cible_est_bien_livre(self) -> None:
        module, _, attribut = cible_uvicorn().partition(":")
        fichier = DOSSIER_APP / (module.replace(".", "/") + ".py")
        assert fichier.is_file(), f"{fichier} est déclaré dans app.yaml mais absent."
        assert attribut == "app"


class TestInjectionDesRessources:
    """Contrat entre le bundle (qui attache) et app.yaml (qui réclame).

    Une ressource attachée n'injecte pas tout d'elle-même. Pour Lakebase, la
    plateforme fournit PGHOST/PGPORT/PGDATABASE/PGUSER/PGSSLMODE
    automatiquement, mais **jamais LAKEBASE_ENDPOINT** : il faut le réclamer par
    un `valueFrom`. L'oubli ne casse rien au démarrage — l'application se lance,
    puis répond 503 sur toutes les routes de données, ce qui ressemble à s'y
    méprendre à une ressource non attachée. D'où ce test.
    """

    def test_chaque_valueFrom_designe_une_ressource_existante(self) -> None:
        declarees = ressources_declarees()
        for variable, ressource in injections().items():
            assert ressource in declarees, (
                f"{variable} réclame la ressource « {ressource} », absente du "
                f"bundle (déclarées : {sorted(declarees)})."
            )

    def test_le_endpoint_lakebase_est_explicitement_reclame(self) -> None:
        noms_postgres = [
            nom for nom, type_ in ressources_declarees().items() if type_ == "postgres"
        ]
        if not noms_postgres:
            pytest.skip("Aucune ressource Lakebase attachée à l'application.")

        assert injections().get("LAKEBASE_ENDPOINT") in noms_postgres, (
            "Sans « LAKEBASE_ENDPOINT / valueFrom: <ressource postgres> », "
            "l'application reçoit l'hôte mais aucun moyen de générer un jeton : "
            "elle démarre et répond 503 sur toutes les routes de données."
        )


@pytest.fixture(scope="module")
def racine_deployee(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Reproduit /app/python/source_code : le contenu de app/, déballé."""
    cible = tmp_path_factory.mktemp("source_code")
    shutil.copytree(
        DOSSIER_APP, cible, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "client", "static"),
    )
    return cible


class TestDemarrageDansLaDispositionDeployee:
    """L'application doit s'importer avec app/ déballé à la racine."""

    def _executer(self, racine: Path, code: str) -> subprocess.CompletedProcess:
        environnement = dict(os.environ)
        # Ni la racine du dépôt, ni le répertoire courant : seule la disposition
        # déployée doit permettre l'import.
        environnement["PYTHONPATH"] = ""
        environnement["PYTHONSAFEPATH"] = ""     # `python -c` n'ajoute pas cwd
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=racine, env=environnement, capture_output=True, text=True,
        )

    def test_le_paquet_app_est_absent_de_cette_disposition(
        self, racine_deployee: Path
    ) -> None:
        """Garde-fou : sans amorce, l'import échoue bien — le test a du sens."""
        resultat = self._executer(
            racine_deployee, "import sys; sys.path.insert(0, '.'); import app.server.main"
        )
        assert resultat.returncode != 0
        assert "No module named 'app'" in resultat.stderr

    def test_le_module_declare_expose_l_application(self, racine_deployee: Path) -> None:
        module, _, attribut = cible_uvicorn().partition(":")
        resultat = self._executer(
            racine_deployee,
            "import sys; sys.path.insert(0, '.'); "
            f"import {module} as m; "
            f"objet = getattr(m, {attribut!r}); "
            "assert objet.__class__.__name__ == 'FastAPI', objet; "
            "print('OK')",
        )
        assert resultat.returncode == 0, resultat.stderr
        assert "OK" in resultat.stdout
