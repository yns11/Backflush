"""Tests de la maille « ordre de fabrication ».

Une table de plus décrivant la même matière ne vaut que si l'on sait exactement
ce qui doit coïncider avec l'existant et ce qui doit en différer. C'est tout
l'objet de ce fichier :

* **Ce qui doit coïncider** — le total des écarts, la consommation théorique, la
  consommation réelle et le volume produit. Descendre d'un cran ne crée ni ne
  détruit de matière ; une divergence signalerait que les règles d'agrégation
  ont pris deux chemins différents entre ``21_*`` et ``31_*``.
* **Ce qui doit différer** — la décomposition non-consommation / surconsommation,
  plus élevée des deux côtés à la maille OF. C'est le décalage des ordres à
  cheval sur deux semaines, qui cesse de se compenser entre lancements. Ce test
  échouerait si le jeu de démonstration cessait de produire ce cas, et l'écran
  perdrait alors tout intérêt sans que rien ne le signale.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.server.data.faits import COLONNES_FAIT_OF, SOURCE_FAITS_OF
from app.server.domain.dictionary import GRILLES
from app.server.main import app
from src.jobs.lakebase_schema import FACT_ECART_OF

BASE_DISPONIBLE = bool(os.getenv("LAKEBASE_PG_URL"))
besoin_base = pytest.mark.skipif(
    not BASE_DISPONIBLE, reason="LAKEBASE_PG_URL non défini : test d'intégration ignoré."
)

PERIODE = {"date_debut": "2026-01-01", "date_fin": "2030-12-31"}


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as instance:
        yield instance


class TestDefinition:
    def test_les_colonnes_suivent_le_schema(self) -> None:
        assert FACT_ECART_OF.column_names == COLONNES_FAIT_OF, (
            "app/server/data/faits.py:COLONNES_FAIT_OF a divergé du schéma."
        )

    def test_la_source_lit_bien_la_table_par_of(self) -> None:
        assert "FROM fact_ecart_of b" in SOURCE_FAITS_OF

    def test_le_meme_parametrage_s_applique(self) -> None:
        """Deux écrans voisins ne peuvent pas ignorer les mêmes exclusions."""
        assert "COALESCE(o.active, TRUE)" in SOURCE_FAITS_OF
        assert "param_article_exclu" in SOURCE_FAITS_OF

    def test_la_grille_porte_l_of_dans_sa_cle_de_ligne(self) -> None:
        """Sans l'OF dans la clé, deux lancements se confondraient à l'écran."""
        assert "prod_id" in GRILLES["details_of"].cle_ligne

    def test_la_grille_reprend_les_colonnes_du_detail(self) -> None:
        """Les deux grilles décrivent le même écart : mêmes libellés, mêmes formats."""
        detail = {c.cle: c for c in GRILLES["details"].colonnes}
        par_of = {c.cle: c for c in GRILLES["details_of"].colonnes}
        assert set(detail) <= set(par_of)
        for cle, colonne in detail.items():
            assert par_of[cle].libelle == colonne.libelle, cle
            assert par_of[cle].type == colonne.type, cle


@besoin_base
class TestReconciliation:
    @staticmethod
    def _totaux(client: TestClient, cle: str) -> dict:
        return client.post(
            f"/api/grilles/{cle}", json={"filtres": PERIODE, "taille": 1}
        ).json()["totaux"]

    def test_les_totaux_de_matiere_sont_identiques(self, client: TestClient) -> None:
        parent = self._totaux(client, "details")
        par_of = self._totaux(client, "details_of")
        for mesure in (
            "ecart_net", "ecart_valorise", "conso_theorique", "conso_reelle",
            "qty_produite",
        ):
            assert float(par_of[mesure]) == pytest.approx(float(parent[mesure]), rel=1e-6), (
                f"{mesure} diverge entre les deux mailles : elles décrivent "
                "pourtant les mêmes mouvements."
            )

    def test_la_decomposition_est_plus_elevee_a_la_maille_of(
        self, client: TestClient
    ) -> None:
        """Le biais de calage cesse de se compenser : c'est l'apport de la vue."""
        parent = self._totaux(client, "details")
        par_of = self._totaux(client, "details_of")
        assert float(par_of["non_consommation"]) > float(parent["non_consommation"])
        assert float(par_of["surconsommation"]) > float(parent["surconsommation"])
        # Les deux augmentent du MÊME montant : c'est de la matière déplacée
        # d'une semaine à l'autre, pas de la matière créée.
        supplement_nc = float(par_of["non_consommation"]) - float(parent["non_consommation"])
        supplement_sc = float(par_of["surconsommation"]) - float(parent["surconsommation"])
        assert supplement_nc == pytest.approx(supplement_sc, rel=1e-6)

    def test_la_maille_of_a_davantage_de_lignes(self, client: TestClient) -> None:
        detail = client.post(
            "/api/grilles/details", json={"filtres": PERIODE, "taille": 1}
        ).json()
        par_of = client.post(
            "/api/grilles/details_of", json={"filtres": PERIODE, "taille": 1}
        ).json()
        assert par_of["total"] > detail["total"]


@besoin_base
class TestGrilleParOf:
    def test_la_page_expose_les_colonnes_de_l_of(self, client: TestClient) -> None:
        ligne = client.post(
            "/api/grilles/details_of", json={"filtres": PERIODE, "taille": 1}
        ).json()["lignes"][0]
        assert ligne["prod_id"]
        assert ligne["prod_statut"]
        assert ligne["prod_bomid"]

    def test_le_tri_par_of_est_autorise(self, client: TestClient) -> None:
        corps = client.post(
            "/api/grilles/details_of",
            json={"filtres": PERIODE, "tri": "prod_id", "sens": "asc", "taille": 20},
        ).json()
        identifiants = [ligne["prod_id"] for ligne in corps["lignes"]]
        assert identifiants == sorted(identifiants)

    def test_les_filtres_transverses_s_appliquent(self, client: TestClient) -> None:
        options = client.get("/api/meta/filtres").json()
        programme = options["programmes"][0]
        corps = client.post(
            "/api/grilles/details_of",
            json={"filtres": {**PERIODE, "programmes": [programme]}, "taille": 20},
        ).json()
        assert corps["total"] > 0
        assert {ligne["parent_programme"] for ligne in corps["lignes"]} == {programme}

    def test_l_export_excel_fonctionne(self, client: TestClient) -> None:
        reponse = client.post(
            "/api/export/details_of.xlsx", json={"filtres": PERIODE, "lignes_max": 50}
        )
        assert reponse.status_code == 200
        assert reponse.content[:2] == b"PK"

    def test_les_totaux_portent_sur_la_bonne_source(self, client: TestClient) -> None:
        """Servir les totaux de la maille parent contredirait le corps du tableau."""
        page = client.post(
            "/api/grilles/details_of", json={"filtres": PERIODE, "taille": 10}
        ).json()
        assert page["totaux"]["nb_lignes"] == page["total"]
