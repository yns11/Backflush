"""Tests de l'amorce de ``sys.path`` des scripts de job.

Une tâche ``spark_python_task`` n'exécute pas un paquet Python : Databricks lit
le fichier et l'évalue. La racine du bundle ne figure alors pas dans
``sys.path``, et ``from src.jobs...`` échoue — alors que le même script
fonctionne en local via ``python -m``.

Ces tests reproduisent les trois modes d'exécution : en module, en fichier
depuis un répertoire quelconque, et par évaluation sans ``__file__`` défini.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
SCRIPTS = ("build_gold", "sync_to_lakebase")


@pytest.mark.parametrize("script", SCRIPTS)
class TestModesExecution:
    def test_execution_en_module(self, script: str) -> None:
        """``python -m src.jobs.<script>`` — le mode du développement local."""
        resultat = subprocess.run(
            [sys.executable, "-m", f"src.jobs.{script}", "--help"],
            cwd=RACINE, capture_output=True, text=True, timeout=60,
        )
        assert resultat.returncode == 0, resultat.stderr
        assert "--catalog" in resultat.stdout

    def test_execution_en_fichier_depuis_un_autre_repertoire(
        self, script: str, tmp_path: Path
    ) -> None:
        """Mode ``spark_python_task`` : fichier exécuté, répertoire courant quelconque.

        C'est le cas qui échouait en production par ModuleNotFoundError.
        """
        resultat = subprocess.run(
            [sys.executable, str(RACINE / "src" / "jobs" / f"{script}.py"), "--help"],
            cwd=tmp_path, capture_output=True, text=True, timeout=60,
        )
        assert resultat.returncode == 0, resultat.stderr
        assert "--catalog" in resultat.stdout

    def test_evaluation_sans_fichier_defini(self, script: str) -> None:
        """Évaluation dans des globales de notebook, où ``__file__`` est absent.

        C'est ainsi que Databricks exécute la tâche : le fichier est lu puis
        passé à ``exec``. L'amorce doit alors se rabattre sur ``sys.argv[0]`` ou
        sur le répertoire courant.
        """
        source = (RACINE / "src" / "jobs" / f"{script}.py").read_text(encoding="utf-8")
        globales: dict[str, object] = {"__name__": "module_evalue"}
        # `__file__` volontairement absent des globales.
        exec(compile(source, f"{script}.py", "exec"), globales)

        racine_detectee = globales.get("RACINE")
        assert isinstance(racine_detectee, Path)
        assert (racine_detectee / "src" / "jobs" / "sqlutil.py").is_file()


class TestRepertoireSql:
    def test_les_scripts_sql_sont_trouves_depuis_la_racine_detectee(self) -> None:
        """L'amorce doit aussi rendre les fichiers SQL localisables."""
        from src.jobs.build_gold import SQL_DIR, list_sql_files

        assert SQL_DIR.is_dir(), f"{SQL_DIR} est introuvable."
        assert len(list_sql_files()) >= 9


class TestEchecExplicite:
    def test_une_racine_absente_produit_un_message_actionnable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un bundle incomplet doit dire ce qui manque, pas lever un import obscur."""
        source = (RACINE / "src" / "jobs" / "build_gold.py").read_text(encoding="utf-8")
        # Isole l'amorce : aucune des trois pistes ne mène à src/jobs/sqlutil.py.
        amorce = source.split("RACINE = _amorcer_chemin_projet()")[0]
        globales: dict[str, object] = {"__name__": "module_evalue"}
        exec(compile(amorce, "amorce.py", "exec"), globales)

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", [str(tmp_path / "inexistant.py")])
        amorcer = globales["_amorcer_chemin_projet"]

        with pytest.raises(RuntimeError, match="Racine du projet introuvable"):
            amorcer()  # type: ignore[operator]
