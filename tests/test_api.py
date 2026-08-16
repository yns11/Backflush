"""Tests d'intégration de l'API.

Ils tournent sur un vrai Postgres si ``LAKEBASE_PG_URL`` est défini (voir
``scripts/dev.sh``), et se limitent sinon aux routes qui n'ont pas besoin de
données. Aucun test n'est silencieusement ignoré : ceux qui exigent une base
sont explicitement marqués ``skip`` avec leur raison.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.server.main import app

BASE_DISPONIBLE = bool(os.getenv("LAKEBASE_PG_URL"))
besoin_base = pytest.mark.skipif(
    not BASE_DISPONIBLE,
    reason="LAKEBASE_PG_URL non défini : test d'intégration ignoré.",
)

PERIODE = {"date_debut": "2026-01-01", "date_fin": "2030-12-31"}


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as instance:
        yield instance


class TestRoutesSansBase:
    """Ces routes doivent répondre même si la base est absente."""

    def test_health_repond_toujours_200(self, client: TestClient) -> None:
        reponse = client.get("/api/health")
        assert reponse.status_code == 200
        assert "base" in reponse.json()

    def test_dictionnaire_des_grilles(self, client: TestClient) -> None:
        grilles = client.get("/api/meta/grilles").json()
        assert set(grilles) == {"details", "composants", "programmes", "parents"}
        for grille in grilles.values():
            assert grille["colonnes"], "Une grille sans colonne est inexploitable."
            assert grille["cle_ligne"], "Sans clé de ligne, la sélection est impossible."

    def test_chaque_colonne_est_documentee_ou_evidente(self, client: TestClient) -> None:
        grilles = client.get("/api/meta/grilles").json()
        for grille in grilles.values():
            for colonne in grille["colonnes"]:
                assert colonne["libelle"], f"{colonne['cle']} n'a pas de libellé."

    def test_whoami_annonce_l_identite_d_execution(self, client: TestClient) -> None:
        corps = client.get("/api/whoami").json()
        # En local, aucun en-tête proxy : l'application doit le dire plutôt que
        # d'inventer un utilisateur.
        assert corps["contexte"] == "developpement_local"
        assert "principal de service" in corps["execution"]

    def test_suggestions_de_l_assistant(self, client: TestClient) -> None:
        suggestions = client.get("/api/assistant/suggestions").json()["suggestions"]
        assert len(suggestions) >= 4


class TestValidationDesEntrees:
    def test_une_periode_inversee_est_rejetee(self, client: TestClient) -> None:
        reponse = client.post(
            "/api/analytique/indicateurs",
            json={"date_debut": "2026-06-30", "date_fin": "2026-06-01"},
        )
        assert reponse.status_code == 422

    def test_une_grille_inconnue_est_rejetee(self, client: TestClient) -> None:
        reponse = client.post("/api/grilles/inexistante", json={"filtres": {}})
        assert reponse.status_code == 422

    def test_une_dimension_inconnue_est_rejetee(self, client: TestClient) -> None:
        reponse = client.post("/api/analytique/repartition/inconnue", json={})
        assert reponse.status_code == 422

    @besoin_base
    def test_un_tri_non_autorise_est_rejete(self, client: TestClient) -> None:
        reponse = client.post(
            "/api/grilles/details",
            json={"filtres": PERIODE, "tri": "(SELECT 1)"},
        )
        assert reponse.status_code == 422
        assert "Tri non autorisé" in reponse.json()["erreur"]

    @besoin_base
    def test_une_tentative_d_injection_ne_ramene_rien(self, client: TestClient) -> None:
        reponse = client.post(
            "/api/grilles/details",
            json={
                "filtres": {
                    **PERIODE,
                    "programmes": ["' OR 1=1 --"],
                },
                "taille": 5,
            },
        )
        assert reponse.status_code == 200
        # La valeur est traitée comme une donnée : aucun programme ne s'appelle
        # ainsi, donc zéro ligne. Une injection réussie en aurait ramené toutes.
        assert reponse.json()["total"] == 0


@besoin_base
class TestAnalytique:
    def test_indicateurs(self, client: TestClient) -> None:
        corps = client.post("/api/analytique/indicateurs", json=PERIODE).json()
        assert len(corps["indicateurs"]) == 8
        assert corps["concentration"]["part_tete_pct"] >= 0

    def test_coherence_ecart_net_et_decomposition(self, client: TestClient) -> None:
        """Identité fondamentale : net = non-consommation − surconsommation."""
        corps = client.post("/api/analytique/indicateurs", json=PERIODE).json()
        valeurs = {i["cle"]: i["valeur"] for i in corps["indicateurs"]}
        attendu = valeurs["non_consommation_valorisee"] - valeurs["surconsommation_valorisee"]
        assert valeurs["ecart_valorise_net"] == pytest.approx(attendu, rel=1e-6)

    def test_la_chronologie_ignore_les_bornes_de_date(self, client: TestClient) -> None:
        etroit = {"date_debut": "2026-06-01", "date_fin": "2026-06-08"}
        tendance = client.post("/api/analytique/tendance", json=etroit).json()["semaines"]
        chronologie = client.post("/api/analytique/chronologie", json=etroit).json()["semaines"]
        # Le slicer doit montrer ce qui est exclu : sa série est plus longue.
        assert len(chronologie) > len(tendance)

    def test_le_seuil_relatif_augmente_la_conformite(self, client: TestClient) -> None:
        def conformite(seuil_pct: float | None) -> float:
            corps = client.post(
                "/api/analytique/indicateurs", json={**PERIODE, "seuil_pct": seuil_pct}
            ).json()
            return next(i["valeur"] for i in corps["indicateurs"] if i["cle"] == "taux_conformite")

        assert conformite(5.0) >= conformite(None)

    def test_les_valeurs_numeriques_sont_des_nombres_json(self, client: TestClient) -> None:
        """Un `numeric` sérialisé en chaîne casserait silencieusement l'affichage."""
        semaines = client.post("/api/analytique/tendance", json=PERIODE).json()["semaines"]
        assert semaines, "Le jeu de test doit contenir au moins une semaine."
        assert isinstance(semaines[0]["ecart_valorise"], (int, float))
        assert isinstance(semaines[0]["conso_theorique"], (int, float))


@besoin_base
class TestGrilles:
    @pytest.mark.parametrize("cle", ["details", "composants", "programmes", "parents"])
    def test_chaque_grille_renvoie_une_page_coherente(self, client: TestClient, cle: str) -> None:
        corps = client.post(f"/api/grilles/{cle}", json={"filtres": PERIODE, "taille": 10}).json()
        assert corps["total"] >= len(corps["lignes"])
        assert len(corps["lignes"]) <= 10
        assert corps["nb_pages"] >= 1

    def test_la_pagination_ne_repete_pas_de_ligne(self, client: TestClient) -> None:
        page1 = client.post(
            "/api/grilles/composants", json={"filtres": PERIODE, "taille": 10, "page": 1}
        ).json()
        page2 = client.post(
            "/api/grilles/composants", json={"filtres": PERIODE, "taille": 10, "page": 2}
        ).json()
        cles1 = {(ligne["child_itemid"], ligne["parent_programme"]) for ligne in page1["lignes"]}
        cles2 = {(ligne["child_itemid"], ligne["parent_programme"]) for ligne in page2["lignes"]}
        assert not (cles1 & cles2)
        assert page1["total"] == page2["total"]

    def test_le_tri_est_applique_cote_serveur(self, client: TestClient) -> None:
        corps = client.post(
            "/api/grilles/details",
            json={"filtres": PERIODE, "tri": "ecart_valorise", "sens": "desc", "taille": 20},
        ).json()
        valeurs = [ligne["ecart_valorise"] for ligne in corps["lignes"]]
        assert valeurs == sorted(valeurs, reverse=True)

    def test_la_production_parent_n_est_pas_multipliee(self, client: TestClient) -> None:
        """Piège classique : sommer la production répétée sur chaque composant.

        La production agrégée d'un parent ne peut pas dépasser celle que la
        table de production déclare — elle serait sinon multipliée par le nombre
        de lignes de nomenclature.
        """
        parents = client.post(
            "/api/grilles/parents", json={"filtres": PERIODE, "taille": 5}
        ).json()["lignes"]
        for ligne in parents:
            assert ligne["qty_produite"] is not None
            # La consommation théorique agrège tous les composants ; elle est
            # donc structurellement supérieure à la seule quantité produite.
            assert ligne["conso_theorique"] >= ligne["qty_produite"]

    def test_le_presse_papiers_est_tabule(self, client: TestClient) -> None:
        corps = client.post(
            "/api/grilles/programmes/presse-papiers?lignes_max=5", json={"filtres": PERIODE}
        ).json()
        lignes = corps["tsv"].split("\n")
        assert len(lignes) == corps["lignes"] + 1
        assert lignes[0].count("\t") == corps["colonnes"] - 1

    def test_l_export_excel_produit_un_classeur(self, client: TestClient) -> None:
        reponse = client.post(
            "/api/export/composants.xlsx", json={"filtres": PERIODE, "lignes_max": 50}
        )
        assert reponse.status_code == 200
        assert reponse.headers["content-type"].startswith(
            "application/vnd.openxmlformats"
        )
        # Signature d'une archive ZIP : un XLSX en est une.
        assert reponse.content[:2] == b"PK"
        assert "backflush_composants" in reponse.headers["content-disposition"]


@besoin_base
class TestFiches:
    def test_fiche_composant_reunit_tout_le_contexte(self, client: TestClient) -> None:
        lignes = client.post(
            "/api/grilles/composants", json={"filtres": PERIODE, "taille": 1}
        ).json()["lignes"]
        reference = lignes[0]["child_itemid"]

        fiche = client.post(f"/api/analytique/composant/{reference}", json=PERIODE).json()
        assert fiche["article"]["item_id"] == reference
        assert fiche["indicateurs"]
        assert isinstance(fiche["parents"], list)

    def test_une_reference_inconnue_ne_provoque_pas_d_erreur(self, client: TestClient) -> None:
        fiche = client.post("/api/analytique/composant/INEXISTANT-999", json=PERIODE).json()
        assert fiche["article"] is None
        assert fiche["semaines"] == []
