"""Tests des écrans de paramétrage — les seules routes qui écrivent.

L'enjeu n'est pas seulement « est-ce que ça marche » : c'est que le droit
d'écriture reste **borné**. Le pool est ouvert en lecture seule, la levée est
posée transaction par transaction, et les tables de paramétrage sont les seules
que le principal de service peut modifier. Ces tests vérifient les deux faces :
que l'écriture autorisée aboutit, et que l'écriture non autorisée échoue.
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

PERIODE = {"date_debut": "2026-01-01", "date_fin": "2030-12-31"}


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as instance:
        yield instance


@pytest.fixture
def nettoyer(client: TestClient):
    """Remet le paramétrage à zéro avant ET après chaque test.

    Avant aussi : un test précédent interrompu laisserait sinon un arbitrage en
    place, et le suivant échouerait pour une raison sans rapport avec lui.
    """
    def raz() -> None:
        pool = app.state.pool
        with pool.connexion_ecriture() as conn:
            conn.execute("DELETE FROM param_article_exclu")
            conn.execute("DELETE FROM param_nomenclature")

    raz()
    yield
    raz()


@besoin_base
class TestLectureDuReferentiel:
    def test_la_base_article_liste_les_references_avec_leur_impact(
        self, client: TestClient, nettoyer
    ) -> None:
        """Sur un référentiel vierge, aucune référence n'est exclue.

        La fixture ``nettoyer`` n'est pas décorative : sans elle, un arbitrage
        laissé par une mise au point manuelle ferait échouer ce test, et
        l'échec accuserait la lecture alors que le problème est l'état de la
        base.
        """
        page = client.get("/api/parametrage/articles?taille=5").json()
        assert page["total"] > 0
        ligne = page["lignes"][0]
        assert ligne["item_id"]
        assert ligne["exclu"] is False
        # L'impact est ce qui rend l'exclusion consciente : il doit être là.
        assert "impact_absolu" in ligne
        assert "nb_semaines_en_ecart" in ligne

    def test_le_classement_par_impact_est_decroissant(self, client: TestClient) -> None:
        lignes = client.get(
            "/api/parametrage/articles?tri=impact_absolu&sens=desc&taille=20"
        ).json()["lignes"]
        impacts = [float(ligne["impact_absolu"]) for ligne in lignes]
        assert impacts == sorted(impacts, reverse=True)

    def test_la_recherche_restreint_la_liste(self, client: TestClient) -> None:
        tous = client.get("/api/parametrage/articles?taille=1").json()["total"]
        filtre = client.get("/api/parametrage/articles?recherche=CMP-VIS&taille=1").json()
        assert 0 < filtre["total"] < tous
        assert "CMP-VIS" in client.get(
            "/api/parametrage/articles?recherche=CMP-VIS&taille=1"
        ).json()["lignes"][0]["item_id"]

    def test_la_nomenclature_expose_les_deux_coefficients(self, client: TestClient) -> None:
        ligne = client.get("/api/parametrage/nomenclature?taille=1").json()["lignes"][0]
        assert ligne["coef_origine"] is not None
        assert ligne["coef_effectif"] == ligne["coef_origine"]
        assert ligne["active"] is True
        assert ligne["coef_surcharge"] is False

    def test_un_tri_non_autorise_est_rejete(self, client: TestClient) -> None:
        reponse = client.get("/api/parametrage/articles?tri=(SELECT+1)")
        assert reponse.status_code == 422
        assert "Tri non autorisé" in reponse.json()["erreur"]


@besoin_base
class TestExclusionDArticles:
    def test_exclure_puis_reintegrer_est_reversible(
        self, client: TestClient, nettoyer
    ) -> None:
        reference = client.get("/api/parametrage/articles?taille=1").json()["lignes"][0]["item_id"]

        def impact() -> float:
            corps = client.post("/api/analytique/indicateurs", json=PERIODE).json()
            return next(
                i["valeur"] for i in corps["indicateurs"] if i["cle"] == "ecart_valorise_net"
            )

        avant = impact()
        client.post(
            "/api/parametrage/articles/exclusion",
            json={"item_ids": [reference], "exclu": True, "motif": "consommable"},
        )
        assert impact() != avant, "L'exclusion doit changer les chiffres immédiatement."

        client.post(
            "/api/parametrage/articles/exclusion",
            json={"item_ids": [reference], "exclu": False},
        )
        assert impact() == avant, "La réintégration doit rétablir exactement les chiffres."

    def test_le_motif_et_l_auteur_sont_conserves(self, client: TestClient, nettoyer) -> None:
        reference = client.get("/api/parametrage/articles?taille=1").json()["lignes"][0]["item_id"]
        client.post(
            "/api/parametrage/articles/exclusion",
            json={"item_ids": [reference], "exclu": True, "motif": "article de transit"},
            headers={"x-forwarded-email": "key.user@emotors.test"},
        )
        ligne = client.get(f"/api/parametrage/articles?recherche={reference}").json()["lignes"][0]
        assert ligne["exclu"] is True
        assert ligne["motif"] == "article de transit"
        assert ligne["modifie_par"] == "key.user@emotors.test"

    def test_l_auteur_vient_du_proxy_et_non_du_client(
        self, client: TestClient, nettoyer
    ) -> None:
        """Un auteur déclaré dans le corps de la requête ne tracerait rien."""
        reference = client.get("/api/parametrage/articles?taille=1").json()["lignes"][0]["item_id"]
        client.post(
            "/api/parametrage/articles/exclusion",
            json={"item_ids": [reference], "exclu": True, "modifie_par": "quelqu-un-d-autre"},
        )
        ligne = client.get(f"/api/parametrage/articles?recherche={reference}").json()["lignes"][0]
        assert ligne["modifie_par"] is None

    def test_le_filtre_d_etat_isole_les_exclusions(self, client: TestClient, nettoyer) -> None:
        reference = client.get("/api/parametrage/articles?taille=1").json()["lignes"][0]["item_id"]
        client.post(
            "/api/parametrage/articles/exclusion", json={"item_ids": [reference], "exclu": True}
        )
        exclus = client.get("/api/parametrage/articles?etat=exclus").json()
        assert exclus["total"] == 1
        assert exclus["lignes"][0]["item_id"] == reference

    def test_une_liste_vide_est_refusee(self, client: TestClient) -> None:
        reponse = client.post(
            "/api/parametrage/articles/exclusion", json={"item_ids": [], "exclu": True}
        )
        assert reponse.status_code == 422

    def test_les_doublons_ne_font_pas_echouer_le_lot(
        self, client: TestClient, nettoyer
    ) -> None:
        """Un ON CONFLICT échoue si la même clé apparaît deux fois dans le lot."""
        reference = client.get("/api/parametrage/articles?taille=1").json()["lignes"][0]["item_id"]
        reponse = client.post(
            "/api/parametrage/articles/exclusion",
            json={"item_ids": [reference, reference], "exclu": True},
        )
        assert reponse.status_code == 200
        assert reponse.json()["lignes"] == 1


@besoin_base
class TestSurchargeDeNomenclature:
    @staticmethod
    def _une_ligne(client: TestClient) -> dict:
        return client.get("/api/parametrage/nomenclature?taille=1").json()["lignes"][0]

    def test_corriger_le_coefficient_change_l_ecart(
        self, client: TestClient, nettoyer
    ) -> None:
        ligne = self._une_ligne(client)
        couple = {
            "parent_itemid": ligne["parent_itemid"],
            "child_itemid": ligne["child_itemid"],
        }
        client.post(
            "/api/parametrage/nomenclature/surcharge",
            json={"lignes": [couple], "active": True, "coef_bom": 99, "motif": "BOM obsolète"},
        )
        apres = self._une_ligne(client)
        assert apres["coef_effectif"] == 99
        assert apres["coef_origine"] == ligne["coef_origine"], (
            "Le coefficient de l'ERP ne doit jamais être modifié : sans lui, la "
            "correction n'est plus vérifiable."
        )
        assert apres["coef_surcharge"] is True

    def test_reinitialiser_retablit_l_erp(self, client: TestClient, nettoyer) -> None:
        ligne = self._une_ligne(client)
        couple = {
            "parent_itemid": ligne["parent_itemid"],
            "child_itemid": ligne["child_itemid"],
        }
        client.post(
            "/api/parametrage/nomenclature/surcharge",
            json={"lignes": [couple], "active": False, "coef_bom": 42},
        )
        client.post("/api/parametrage/nomenclature/reinitialisation", json={"lignes": [couple]})
        apres = self._une_ligne(client)
        assert apres["active"] is True
        assert apres["coef_surcharge"] is False
        assert apres["coef_effectif"] == ligne["coef_origine"]

    def test_un_coefficient_nul_ou_negatif_est_refuse(self, client: TestClient) -> None:
        ligne = self._une_ligne(client)
        for valeur in (0, -3):
            reponse = client.post(
                "/api/parametrage/nomenclature/surcharge",
                json={
                    "lignes": [
                        {
                            "parent_itemid": ligne["parent_itemid"],
                            "child_itemid": ligne["child_itemid"],
                        }
                    ],
                    "active": True,
                    "coef_bom": valeur,
                },
            )
            assert reponse.status_code == 422

    def test_le_resume_compte_les_arbitrages(self, client: TestClient, nettoyer) -> None:
        assert client.get("/api/parametrage/resume").json() == {
            "articles_exclus": 0,
            "lignes_desactivees": 0,
            "lignes_corrigees": 0,
        }
        ligne = self._une_ligne(client)
        client.post(
            "/api/parametrage/nomenclature/surcharge",
            json={
                "lignes": [
                    {
                        "parent_itemid": ligne["parent_itemid"],
                        "child_itemid": ligne["child_itemid"],
                    }
                ],
                "active": False,
            },
        )
        assert client.get("/api/parametrage/resume").json()["lignes_desactivees"] == 1


@besoin_base
class TestVerrouDeLectureSeule:
    """Le droit d'écrire doit rester l'exception, pas l'état par défaut."""

    def test_une_connexion_ordinaire_ne_peut_pas_ecrire(self) -> None:
        from app.server.core.errors import DonneesIndisponiblesError

        with pytest.raises(DonneesIndisponiblesError), app.state.pool.connection() as conn:
            conn.execute("DELETE FROM dq_controles")

    def test_la_connexion_d_ecriture_le_peut(self, nettoyer) -> None:
        with app.state.pool.connexion_ecriture() as conn:
            conn.execute(
                "INSERT INTO param_article_exclu (item_id, motif) VALUES ('TEST-VERROU', 'essai')"
            )
        with app.state.pool.connection() as conn:
            # Le pool renvoie des dictionnaires : la colonne est nommée.
            resultat = conn.execute(
                "SELECT count(*) AS n FROM param_article_exclu WHERE item_id = 'TEST-VERROU'"
            ).fetchone()
        assert resultat is not None and resultat["n"] == 1

    def test_le_verrou_est_rendu_apres_l_ecriture(self, nettoyer) -> None:
        """La levée vaut pour UNE transaction, pas pour la connexion entière."""
        from app.server.core.errors import DonneesIndisponiblesError

        with app.state.pool.connexion_ecriture() as conn:
            conn.execute("DELETE FROM param_article_exclu")

        with pytest.raises(DonneesIndisponiblesError), app.state.pool.connection() as conn:
            conn.execute("DELETE FROM dq_controles")
