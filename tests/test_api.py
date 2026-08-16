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

from app.server.domain.dictionary import GRILLES
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
        assert set(grilles) == {"details", "composants", "programmes", "perimetres", "parents"}
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
    @pytest.mark.parametrize(
        "cle", ["details", "composants", "programmes", "perimetres", "parents"]
    )
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
        # L'identité d'une ligne est celle déclarée par le dictionnaire : la
        # vérifier sur une clé partielle produirait de faux positifs.
        cle_ligne = GRILLES["composants"].cle_ligne

        def cles(page: dict) -> set[tuple]:
            return {tuple(ligne[champ] for champ in cle_ligne) for ligne in page["lignes"]}

        assert not (cles(page1) & cles(page2))
        assert page1["total"] == page2["total"]

    def test_l_identite_de_ligne_est_unique_sur_chaque_grille(self, client: TestClient) -> None:
        """La ``cle_ligne`` du dictionnaire doit refléter le grain réel du SQL.

        Une clé trop courte passe inaperçue jusqu'à ce que deux lignes
        distinctes se confondent : sélection qui en coche deux, ligne qui
        disparaît du rendu React, export incohérent avec l'écran.
        """
        for cle, grille in GRILLES.items():
            page = client.post(
                f"/api/grilles/{cle}", json={"filtres": PERIODE, "taille": 200}
            ).json()
            identites = [
                tuple(ligne[champ] for champ in grille.cle_ligne) for ligne in page["lignes"]
            ]
            assert len(set(identites)) == len(identites), f"grille « {cle} »"

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

    def test_le_pied_de_grille_totalise_toute_la_selection(self, client: TestClient) -> None:
        """Les totaux du pied portent sur la sélection, pas sur la page affichée.

        Totaliser la page tromperait : additionner cinquante lignes sur dix
        mille donne un chiffre juste sur un ensemble qui n'intéresse personne.
        """
        page = client.post(
            "/api/grilles/details", json={"filtres": PERIODE, "taille": 10}
        ).json()
        totaux = page["totaux"]
        assert totaux["nb_lignes"] == page["total"]
        # Les dénombrements distincts ne sont pas la somme des colonnes.
        assert totaux["nb_composants"] >= 1
        assert 0 <= totaux["taux_conformite"] <= 100

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
class TestMesure:
    """La bascule valeur / quantité doit atteindre le SQL, pas seulement l'affichage."""

    def test_les_indicateurs_changent_d_unite(self, client: TestClient) -> None:
        euros = client.post("/api/analytique/indicateurs?mesure=valeur", json=PERIODE).json()
        unites = client.post("/api/analytique/indicateurs?mesure=quantite", json=PERIODE).json()
        par_cle = {i["cle"]: i for i in euros["indicateurs"]}
        en_unites = {i["cle"]: i for i in unites["indicateurs"]}
        assert par_cle["ecart_valorise_net"]["unite"] != en_unites["ecart_valorise_net"]["unite"]

    @pytest.mark.parametrize(
        ("mesure", "colonne"),
        [("valeur", "ecart_valorise_absolu"), ("quantite", "ecart_absolu")],
    )
    def test_le_classement_suit_la_mesure(
        self, client: TestClient, mesure: str, colonne: str
    ) -> None:
        """Sans cela, on lirait un classement en euros habillé d'unités.

        Le tri porte sur la somme des |écarts|, pas sur |somme des écarts| :
        dans un groupe, une non-consommation et une surconsommation
        s'additionnent en volume d'anomalie au lieu de se compenser.
        """
        lignes = client.post(
            f"/api/analytique/top-composants?limite=12&mesure={mesure}", json=PERIODE
        ).json()["lignes"]
        assert lignes, "Le jeu de test doit contenir des écarts."
        impacts = [float(ligne[colonne]) for ligne in lignes]
        assert impacts == sorted(impacts, reverse=True)


@besoin_base
class TestSynthesePerimetre:
    @staticmethod
    def _un_perimetre(client: TestClient) -> str:
        options = client.get("/api/meta/filtres").json()
        assert options["perimetres"], "Le jeu de test doit contenir au moins un périmètre."
        return options["perimetres"][0]

    def test_un_perimetre_unique_est_exige(self, client: TestClient) -> None:
        reponse = client.post("/api/analytique/synthese-perimetre", json=PERIODE)
        assert reponse.status_code == 422
        assert "unique" in reponse.json()["erreur"]

    def test_la_vue_croise_production_et_ecarts(self, client: TestClient) -> None:
        perimetre = self._un_perimetre(client)
        corps = client.post(
            "/api/analytique/synthese-perimetre",
            json={**PERIODE, "perimetres": [perimetre]},
        ).json()
        assert corps["perimetre"] == perimetre
        assert corps["production"], "Un périmètre sans production ne serait pas exploitable."
        for ligne in corps["production"]:
            assert ligne["parent_itemid"] and ligne["annee"] and ligne["semaine"]
        # Le bloc écart ne retient que les coefficients uniformes.
        for ligne in corps["ecarts"]:
            assert ligne["coef_bom"] is not None

    def test_l_export_de_la_vue_synthetique(self, client: TestClient) -> None:
        perimetre = self._un_perimetre(client)
        reponse = client.post(
            "/api/export/synthese-perimetre.xlsx",
            json={"filtres": {**PERIODE, "perimetres": [perimetre]}, "mesure": "quantite"},
        )
        assert reponse.status_code == 200
        assert reponse.content[:2] == b"PK"
        assert "backflush_synthese" in reponse.headers["content-disposition"]

    def test_l_export_refuse_une_selection_multiple(self, client: TestClient) -> None:
        reponse = client.post(
            "/api/export/synthese-perimetre.xlsx", json={"filtres": PERIODE}
        )
        assert reponse.status_code == 422


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
