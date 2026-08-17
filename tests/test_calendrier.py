"""Tests de la continuité de l'axe temporel.

Une semaine sans mouvement doit apparaître, sinon un arrêt de ligne se lit
comme une semaine ordinaire. Mais la continuité a deux bords, et les deux ont
déjà produit des régressions :

* une plage large sur un historique court générerait des centaines de colonnes
  vides — l'axe doit être bridé par l'étendue réelle des données ;
* une sélection sans aucune ligne ne doit produire AUCUNE semaine, et non un
  calendrier de zéros pour une référence qui n'existe pas.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.server.main import app

BASE_DISPONIBLE = bool(os.getenv("LAKEBASE_PG_URL"))
besoin_base = pytest.mark.skipif(
    not BASE_DISPONIBLE, reason="LAKEBASE_PG_URL non défini : test d'intégration ignoré."
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as instance:
        yield instance


def _semaines(client: TestClient, filtres: dict) -> list[dict]:
    return client.post("/api/analytique/tendance", json=filtres).json()["semaines"]


@besoin_base
class TestAxeContinu:
    def test_l_axe_ne_saute_aucune_semaine(self, client: TestClient) -> None:
        """Chaque lundi doit suivre le précédent de sept jours exactement."""
        from datetime import date, timedelta
        from itertools import pairwise

        semaines = _semaines(client, {})
        assert len(semaines) > 2
        lundis = [date.fromisoformat(s["semaine_debut"]) for s in semaines]
        for precedent, suivant in pairwise(lundis):
            assert suivant - precedent == timedelta(days=7), (
                f"Trou dans l'axe entre {precedent} et {suivant}."
            )

    def test_une_semaine_sans_mouvement_est_presente_et_a_zero(
        self, client: TestClient, connexion
    ) -> None:
        """Creuse un trou dans les faits, vérifie l'axe, puis remet tout en place.

        La suppression doit être VALIDÉE : l'application lit par une autre
        connexion, et ne verrait rien d'une transaction ouverte. Une annulation
        ne suffit donc pas à réparer — les lignes sont mises de côté dans une
        table temporaire et réinsérées quoi qu'il arrive. Sans cela, ce test
        ampute durablement le jeu local et fait échouer, plus tard et ailleurs,
        des tests qui n'y sont pour rien.
        """
        semaines = _semaines(client, {})
        cible = semaines[len(semaines) // 2]["semaine_debut"]

        with connexion.cursor() as cur:
            cur.execute(
                "CREATE TEMP TABLE sauvegarde_semaine AS "
                "SELECT * FROM fact_ecart_backflush WHERE semaine_debut = %s",
                (cible,),
            )
            cur.execute(
                "DELETE FROM fact_ecart_backflush WHERE semaine_debut = %s", (cible,)
            )
        connexion.commit()
        try:
            apres = _semaines(client, {})
            trouvee = next(s for s in apres if s["semaine_debut"] == cible)
            assert trouvee["nb_lignes"] == 0
            assert trouvee["ecart_valorise"] == 0
            assert len(apres) == len(semaines), "La semaine vide doit rester une colonne."
        finally:
            with connexion.cursor() as cur:
                cur.execute(
                    "INSERT INTO fact_ecart_backflush SELECT * FROM sauvegarde_semaine"
                )
                cur.execute("DROP TABLE sauvegarde_semaine")
            connexion.commit()

    def test_une_plage_plus_large_que_l_historique_est_bridee(
        self, client: TestClient
    ) -> None:
        reference = _semaines(client, {})
        large = _semaines(client, {"date_debut": "2020-01-01", "date_fin": "2035-12-31"})
        assert len(large) == len(reference), (
            "Les bornes doivent être ramenées à l'étendue réelle des données ; "
            "sinon quinze ans de colonnes vides."
        )

    def test_une_selection_vide_ne_produit_aucune_semaine(self, client: TestClient) -> None:
        vide = _semaines(
            client,
            {"date_debut": "2026-01-01", "date_fin": "2030-12-31", "composants": ["INEXISTANT"]},
        )
        assert vide == []

    def test_la_periode_demandee_est_respectee(self, client: TestClient) -> None:
        toutes = _semaines(client, {})
        debut = toutes[2]["semaine_debut"]
        fin = toutes[6]["semaine_debut"]
        restreinte = _semaines(client, {"date_debut": debut, "date_fin": fin})
        assert [s["semaine_debut"] for s in restreinte] == [
            s["semaine_debut"] for s in toutes[2:7]
        ]


@besoin_base
class TestCalendrierDeLaVueSynthetique:
    def test_les_colonnes_couvrent_toute_la_periode(self, client: TestClient) -> None:
        perimetre = client.get("/api/meta/filtres").json()["perimetres"][0]
        toutes = _semaines(client, {})
        filtres = {
            "date_debut": toutes[0]["semaine_debut"],
            "date_fin": toutes[-1]["semaine_debut"],
            "perimetres": [perimetre],
        }
        corps = client.post("/api/analytique/synthese-perimetre", json=filtres).json()
        assert len(corps["semaines"]) == len(toutes)
        # Les semaines produites sont un sous-ensemble des colonnes, jamais
        # l'inverse : une ligne de production sans colonne serait invisible.
        colonnes = {(s["annee"], s["semaine"]) for s in corps["semaines"]}
        for ligne in corps["production"]:
            assert (ligne["annee"], ligne["semaine"]) in colonnes
