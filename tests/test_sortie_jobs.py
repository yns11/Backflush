"""Tests de la sortie des scripts de job.

Régression coûteuse : une tâche Databricks évalue le fichier dans un noyau
IPython, où ``SystemExit`` remonte comme une exception ordinaire et fait échouer
la tâche — **même avec le code 0**. Le job journalisait « Modèle gold construit
avec succès », puis la tâche passait en échec dans la foulée, tous les travaux
avals étant sautés.

Le défaut est invisible en local : ``python -m src.jobs.build_gold`` interprète
``SystemExit(0)`` comme une terminaison normale. Seule une vérification
explicite le retient.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
SCRIPTS = ("build_gold", "sync_to_lakebase", "seed_demo_data")


def _source(script: str) -> str:
    return (RACINE / "src" / "jobs" / f"{script}.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("script", SCRIPTS)
class TestPointDEntree:
    def test_le_bloc_principal_ne_leve_pas_systemexit(self, script: str) -> None:
        """Le garde ``__main__`` doit appeler ``main()``, jamais la lever."""
        arbre = ast.parse(_source(script))
        gardes = [
            noeud for noeud in arbre.body
            if isinstance(noeud, ast.If) and "__main__" in ast.dump(noeud.test)
        ]
        assert len(gardes) == 1, f"{script} devrait avoir exactement un bloc __main__."

        for noeud in ast.walk(gardes[0]):
            if isinstance(noeud, ast.Raise):
                leve = ast.dump(noeud)
                assert "SystemExit" not in leve, (
                    f"{script} lève SystemExit dans son bloc __main__ : la tâche "
                    f"Databricks échouera même en cas de succès."
                )

    def test_le_bloc_principal_appelle_bien_main(self, script: str) -> None:
        arbre = ast.parse(_source(script))
        garde = next(
            noeud for noeud in arbre.body
            if isinstance(noeud, ast.If) and "__main__" in ast.dump(noeud.test)
        )
        appels = [
            noeud.func.id
            for noeud in ast.walk(garde)
            if isinstance(noeud, ast.Call) and isinstance(noeud.func, ast.Name)
        ]
        assert "main" in appels, f"{script} n'appelle pas main() dans son bloc __main__."

    def test_main_ne_renvoie_pas_de_code_de_retour(self, script: str) -> None:
        """Un code de retour laisserait croire qu'il est encore interprété.

        Les échecs remontent par exception, ce qui produit de toute façon un
        code non nul en ligne de commande.
        """
        arbre = ast.parse(_source(script))
        fonction = next(
            noeud for noeud in ast.walk(arbre)
            if isinstance(noeud, ast.FunctionDef) and noeud.name == "main"
        )
        for noeud in ast.walk(fonction):
            if isinstance(noeud, ast.Return):
                assert noeud.value is None, (
                    f"main() de {script} renvoie une valeur : elle n'est plus utilisée."
                )


class TestConditionsDErreur:
    """Une condition d'erreur doit lever une exception nommée, pas SystemExit.

    Sous Databricks, une ``SystemExit`` se présente comme « SystemExit: 1 » sans
    message exploitable, là où une ``RuntimeError`` affiche sa cause.
    """

    @pytest.mark.parametrize("script", SCRIPTS)
    def test_aucun_systemexit_leve_dans_le_module(self, script: str) -> None:
        arbre = ast.parse(_source(script))
        for noeud in ast.walk(arbre):
            if isinstance(noeud, ast.Raise) and "SystemExit" in ast.dump(noeud):
                pytest.fail(
                    f"{script} lève SystemExit : préférer une exception nommée, "
                    f"dont le message survit à l'exécution sous Databricks."
                )
