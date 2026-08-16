"""Contrôles avant déploiement, exécutés à chaque passage de la suite.

Le vérificateur vit dans ``.claude/skills/databricks-livraison/`` : il est
réutilisable d'un projet à l'autre. L'attacher ici lui donne ce qu'un script
d'exploitation n'a pas — il tourne à chaque modification du bundle, et pas
seulement quand quelqu'un pense à le lancer avant de déployer.

Chaque anomalie qu'il détecte correspond à une panne réellement subie sur ce
projet, dont le symptôme désignait la mauvaise cause.
"""

from __future__ import annotations

import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE / ".claude" / "skills" / "databricks-livraison"))

from verifier_bundle import verifier  # noqa: E402 — le chemin doit précéder l'import


def test_le_bundle_ne_porte_aucune_anomalie_connue() -> None:
    bloquantes = [anomalie for anomalie in verifier(RACINE) if anomalie.bloquant]
    assert not bloquantes, "\n\n" + "\n\n".join(str(a) for a in bloquantes)
