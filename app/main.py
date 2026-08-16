"""Point d'entrée du runtime Databricks Apps.

⚠️ Ce module existe pour une raison précise, ne pas le supprimer.

Databricks Apps déploie le **contenu** de ``source_code_path`` à la racine du
conteneur : ``app/server/`` devient ``/app/python/source_code/server/``. Le
paquet ``app`` n'existe donc plus à l'exécution, alors que tout le code
l'importe (``app.server.core.config``…). Sans ce module, uvicorn échoue au
démarrage sur ``ModuleNotFoundError: No module named 'app'``, chaque worker
meurt à la seconde, et le navigateur n'obtient qu'un « App Not Available ».

Deux corrections étaient possibles :

1. Renommer tous les imports en ``server.…``. Cela règle le symptôme mais casse
   le développement local et les tests, qui s'exécutent depuis la racine du
   dépôt où ``app`` est bien un paquet — et rend le code dépendant de l'endroit
   d'où on le lance.
2. Rétablir le paquet ``app`` à l'exécution, en le faisant pointer sur le
   dossier courant. La structure interne (``app.server.…``) reste alors valable
   dans les deux environnements, sans condition ni variante.

C'est la seconde qui est retenue ici. ``app.yaml`` lance donc ``main:app``, et
non ``app.server.main:app``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent


def _retablir_le_paquet_app() -> None:
    """Rend ``app`` importable, qu'il soit déployé ou non comme un paquet.

    Idempotent : uvicorn en mode multi-workers réimporte ce module dans chaque
    processus fils, et les tests peuvent l'importer alors que ``app`` est déjà
    chargé depuis la racine du dépôt.
    """
    if "app" in sys.modules:
        return

    initialisation = RACINE / "__init__.py"
    specification = importlib.util.spec_from_file_location(
        "app",
        initialisation,
        submodule_search_locations=[str(RACINE)],
    )
    if specification is None or specification.loader is None:  # pragma: no cover
        raise RuntimeError(
            "Impossible de reconstituer le paquet « app » à partir de "
            f"{RACINE}. Vérifiez que le contenu de app/ a bien été déployé "
            "(app.yaml, server/, requirements.txt doivent être à la racine)."
        )

    module = importlib.util.module_from_spec(specification)
    # L'enregistrement précède l'exécution : un import circulaire éventuel
    # retrouve alors un module partiellement initialisé plutôt qu'une erreur.
    sys.modules["app"] = module
    specification.loader.exec_module(module)


_retablir_le_paquet_app()

from app.server.main import app  # noqa: E402 — l'amorce doit précéder l'import

__all__ = ["app"]
