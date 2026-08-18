"""Tests de l'axe « ordre de fabrication » : statuts, filtres, tiroir de contexte.

Trois familles de garanties, chacune correspondant à une façon dont cet axe peut
mentir sans lever d'erreur :

1. **Les libellés de statut.** Ils viennent d'une énumération D365 traduite à la
   main. Une traduction fausse ne casse rien : elle produit un filtre qui répond,
   des lignes qui s'affichent, et une lecture inversée — un ordre encore en cours
   présenté comme clôturé, donc un écart normal instruit comme une anomalie.
2. **Le cantonnement des deux filtres d'OF à leur source.** Les colonnes
   ``prod_id`` et ``prod_statut`` n'existent que sur ``fact_ecart_of``. Posées
   ailleurs, elles feraient échouer la requête ; ignorées silencieusement mais
   restées visibles à l'écran, elles feraient croire à un filtrage inexistant.
3. **Le contexte du tiroir.** Il doit ramener les ordres des DEUX côtés du
   mouvement — production déclarée et consommation déclarée — et déborder de la
   semaine cliquée. Sans ce débordement, tout écart de calage passe pour
   résiduel : c'est précisément la question à laquelle le tiroir sert à répondre.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from app.server.domain.filters import (
    CYCLE_STATUT_OF,
    Filtres,
    construire_predicat,
    rang_statut_of,
)
from app.server.main import app

RACINE = Path(__file__).resolve().parents[1]

BASE_DISPONIBLE = bool(os.getenv("LAKEBASE_PG_URL"))
besoin_base = pytest.mark.skipif(
    not BASE_DISPONIBLE, reason="LAKEBASE_PG_URL non défini : test d'intégration ignoré."
)

PERIODE = {"date_debut": "2026-01-01", "date_fin": "2030-12-31"}


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as instance:
        yield instance


# ---------------------------------------------------------------------------
# 1. Libellés de statut
# ---------------------------------------------------------------------------
class TestEnumerationDesStatuts:
    """Le SQL, le générateur et l'ordre d'affichage doivent dire la même chose."""

    #: Énumération `ProdStatus` de D365, telle qu'attendue dans `31_*`.
    ATTENDUE: ClassVar[dict[int, str]] = {
        0: "Aucun",
        1: "Créé",
        2: "Estimé",
        3: "Planifié",
        4: "Lancé",
        5: "Démarré",
        6: "Déclaré terminé",
        7: "Clôturé",
        8: "Annulé",
    }

    @staticmethod
    def _traduction_sql() -> dict[int, str]:
        """Extrait le CASE de traduction de `31_fact_ecart_of.sql`."""
        sql = (RACINE / "src/sql/gold/31_fact_ecart_of.sql").read_text(encoding="utf-8")
        bloc = re.search(
            r"WHEN pt\.prod_statut IS NULL THEN NULL(.*?)END\s+AS prod_statut",
            sql,
            re.S,
        )
        assert bloc, "Le CASE de traduction du statut d'OF est introuvable."
        return {
            int(code): libelle
            for code, libelle in re.findall(
                r"WHEN pt\.prod_statut = (\d+) THEN '([^']+)'", bloc.group(1)
            )
        }

    def test_la_traduction_sql_suit_l_enumeration_d365(self) -> None:
        assert self._traduction_sql() == self.ATTENDUE, (
            "La traduction de ProdStatus a divergé de l'énumération D365. "
            "Rappel : 4 = Lancé (Released), 5 = Démarré (StartedUp), "
            "6 = Déclaré terminé (ReportedFinished), 7 = Clôturé (Costed)."
        )

    def test_aucun_code_ne_tombe_dans_un_fourre_tout_muet(self) -> None:
        """Un statut non traduit doit se NOMMER, pour être filtrable et vu."""
        sql = (RACINE / "src/sql/gold/31_fact_ecart_of.sql").read_text(encoding="utf-8")
        assert "'Inconnu ('" in sql or "Inconnu (" in sql
        assert "ELSE 'Autre'" not in sql, (
            "Un « Autre » indifférencié rend indistinguables des statuts qui ne "
            "se lisent pas de la même façon."
        )

    def test_le_cycle_d_affichage_couvre_toute_l_enumeration(self) -> None:
        assert set(CYCLE_STATUT_OF) == set(self.ATTENDUE.values())

    def test_le_cycle_est_dans_l_ordre_des_codes(self) -> None:
        """L'ordre d'affichage est celui du cycle de vie, pas l'alphabétique."""
        attendu = [self.ATTENDUE[code] for code in sorted(self.ATTENDUE)]
        assert list(CYCLE_STATUT_OF) == attendu

    def test_un_statut_inconnu_est_classe_en_fin(self) -> None:
        connus = sorted(CYCLE_STATUT_OF, key=rang_statut_of)
        avec_intrus = sorted([*CYCLE_STATUT_OF, "Inconnu (12)"], key=rang_statut_of)
        assert avec_intrus[:-1] == connus
        assert avec_intrus[-1] == "Inconnu (12)"

    def test_le_generateur_n_invente_aucun_statut(self) -> None:
        from src.jobs.seed_demo_data import STATUTS_OF_DEMO

        libelles = {libelle for libelle, _, _ in STATUTS_OF_DEMO}
        assert libelles <= set(self.ATTENDUE.values()), (
            "Le jeu de démonstration produit un statut que le modèle gold ne "
            "produirait jamais : les écrans de développement mentiraient."
        )

    def test_seuls_les_ordres_termines_portent_une_date_de_cloture(self) -> None:
        """`finisheddate` n'existe qu'à partir de la déclaration de fin."""
        from src.jobs.seed_demo_data import STATUTS_OF_DEMO

        termines = {"Déclaré terminé", "Clôturé"}
        for libelle, _, cloture in STATUTS_OF_DEMO:
            assert cloture is (libelle in termines), libelle


class TestSentinelleDeDateDeCloture:
    """D365 n'écrit jamais NULL dans `finisheddate` : il y écrit 1900-01-01."""

    def test_la_vue_source_neutralise_la_sentinelle(self) -> None:
        vue = (RACINE / "src/sql/gold/01_src_views.sql").read_text(encoding="utf-8")
        bloc = vue.split("v_src_prod_table")[1]
        assert "1901-01-01" in bloc, (
            "La sentinelle 1900-01-01 de finisheddate doit être ramenée à NULL "
            "dans la vue source. Laissée telle quelle, elle s'affiche comme une "
            "vraie date de clôture et fait passer les OF en cours pour terminés."
        )
        # La borne est un « supérieur à », pas une égalité : selon le fuseau, la
        # sentinelle peut arriver au 31/12/1899.
        assert re.search(r">\s*TIMESTAMP\s*'1901-01-01", bloc)


@besoin_base
class TestStatutsServisALApplication:
    def test_les_options_exposent_les_statuts_d_of(self, client: TestClient) -> None:
        options = client.get("/api/meta/filtres").json()
        assert options["statuts_of"], "Aucun statut d'OF servi aux filtres."
        assert set(options["statuts_of"]) <= set(CYCLE_STATUT_OF)

    def test_les_options_sont_dans_l_ordre_du_cycle(self, client: TestClient) -> None:
        statuts = client.get("/api/meta/filtres").json()["statuts_of"]
        assert statuts == sorted(statuts, key=rang_statut_of)


# ---------------------------------------------------------------------------
# 2. Cantonnement des filtres d'OF
# ---------------------------------------------------------------------------
class TestPredicat:
    def test_les_criteres_d_of_sont_ignores_hors_de_leur_axe(self) -> None:
        filtres = Filtres(ofs=["OF-1"], statuts_of=["Clôturé"])
        predicat = construire_predicat(filtres)
        assert "prod_id" not in predicat.sql
        assert "prod_statut" not in predicat.sql
        assert "ofs" not in predicat.params
        assert "statuts_of" not in predicat.params

    def test_ils_s_appliquent_sur_l_axe_of(self) -> None:
        filtres = Filtres(ofs=["OF-1", "OF-2"], statuts_of=["Clôturé"])
        predicat = construire_predicat(filtres, axe_of=True)
        assert "f.prod_id = ANY(%(ofs)s)" in predicat.sql
        assert "f.prod_statut = ANY(%(statuts_of)s)" in predicat.sql
        assert predicat.params["ofs"] == ["OF-1", "OF-2"]
        assert predicat.params["statuts_of"] == ["Clôturé"]

    def test_aucune_valeur_n_est_concatenee_dans_le_sql(self) -> None:
        """Les identifiants d'OF sont une saisie libre : jamais interpolés."""
        filtres = Filtres(ofs=["OF-1'; DROP TABLE fact_ecart_of; --"])
        predicat = construire_predicat(filtres, axe_of=True)
        assert "DROP TABLE" not in predicat.sql
        assert predicat.params["ofs"] == ["OF-1'; DROP TABLE fact_ecart_of; --"]

    def test_la_source_decide_seule_du_port_de_l_axe(self) -> None:
        from app.server.data.repository import porte_axe_of

        assert porte_axe_of("details_of")
        for cle in ("details", "programmes", "perimetres", "composants"):
            assert not porte_axe_of(cle), cle


@besoin_base
class TestFiltresDOfSurLesGrilles:
    """Le filtre doit RESTREINDRE sur sa grille et ne RIEN faire ailleurs."""

    @staticmethod
    def _page(client: TestClient, cle: str, filtres: dict) -> dict:
        reponse = client.post(f"/api/grilles/{cle}", json={"filtres": {**PERIODE, **filtres}})
        assert reponse.status_code == 200, reponse.text
        return reponse.json()

    def test_un_numero_d_of_restreint_la_grille_par_of(self, client: TestClient) -> None:
        reference = self._page(client, "details_of", {})
        assert reference["total"] > 0
        of = reference["lignes"][0]["prod_id"]

        cible = self._page(client, "details_of", {"ofs": [of]})
        assert 0 < cible["total"] < reference["total"]
        assert {ligne["prod_id"] for ligne in cible["lignes"]} == {of}

    def test_un_statut_restreint_la_grille_par_of(self, client: TestClient) -> None:
        reference = self._page(client, "details_of", {})
        cible = self._page(client, "details_of", {"statuts_of": ["Clôturé"]})
        assert 0 < cible["total"] < reference["total"]
        assert {ligne["prod_statut"] for ligne in cible["lignes"]} == {"Clôturé"}

    def test_le_meme_filtre_ne_touche_pas_la_grille_par_parent(
        self, client: TestClient
    ) -> None:
        """Ni erreur, ni filtrage fantôme : la grille est rigoureusement la même."""
        reference = self._page(client, "details", {})
        avec_of = self._page(client, "details", {"ofs": ["OF-INEXISTANT"], "statuts_of": ["Créé"]})
        assert avec_of["total"] == reference["total"]

    def test_le_pied_de_totaux_suit_le_filtre(self, client: TestClient) -> None:
        """Un pied qui ignorerait le filtre contredirait le corps du tableau.

        Les totaux voyagent dans la réponse de la grille, mais ils sont calculés
        par une requête distincte sur la sélection entière : rien ne garantit a
        priori qu'ils reçoivent le même prédicat que les lignes.
        """
        restreint = self._page(client, "details_of", {"statuts_of": ["Clôturé"]})
        complet = self._page(client, "details_of", {})
        assert restreint["totaux"]["nb_lignes"] < complet["totaux"]["nb_lignes"]
        assert restreint["totaux"]["nb_lignes"] == restreint["total"]

    def test_l_export_suit_le_filtre(self, client: TestClient) -> None:
        reponse = client.post(
            "/api/grilles/details_of/presse-papiers",
            json={"filtres": {**PERIODE, "statuts_of": ["Clôturé"]}},
        )
        assert reponse.status_code == 200, reponse.text
        assert reponse.json()["lignes"] > 0


# ---------------------------------------------------------------------------
# 3. Contexte du tiroir
# ---------------------------------------------------------------------------
@besoin_base
class TestContexteOf:
    @staticmethod
    def _cellule(client: TestClient) -> dict:
        """Une cellule d'écart réelle de la vue synthétique."""
        perimetre = client.get("/api/meta/filtres").json()["perimetres"][0]
        corps = client.post(
            "/api/analytique/synthese-perimetre",
            json={**PERIODE, "perimetres": [perimetre]},
        ).json()
        assert corps["ecarts"], "Aucun écart dans la vue synthétique du périmètre."
        ligne = corps["ecarts"][0]
        return {
            "perimetre": perimetre,
            "composant": ligne["child_itemid"],
            "annee": ligne["annee"],
            "semaine": ligne["semaine"],
        }

    @staticmethod
    def _contexte(client: TestClient, cellule: dict) -> dict:
        reponse = client.post("/api/analytique/contexte-of", json=cellule)
        assert reponse.status_code == 200, reponse.text
        return reponse.json()

    def test_la_cellule_ramene_des_ordres(self, client: TestClient) -> None:
        contexte = self._contexte(client, self._cellule(client))
        assert contexte["ofs"], "Une cellule en écart doit avoir au moins un ordre."
        assert contexte["tronque"] is False

    def test_les_ordres_ont_tous_mouvemente_ce_composant_cette_semaine(
        self, client: TestClient, connexion
    ) -> None:
        cellule = self._cellule(client)
        contexte = self._contexte(client, cellule)
        with connexion.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT prod_id FROM fact_ecart_of "
                "WHERE parent_perimetre = %s AND child_itemid = %s "
                "AND annee = %s AND semaine = %s",
                (cellule["perimetre"], cellule["composant"],
                 cellule["annee"], cellule["semaine"]),
            )
            attendus = {ligne[0] for ligne in cur.fetchall()}
        assert set(contexte["ofs"]) == attendus

    def test_les_deux_cotes_du_mouvement_sont_couverts(
        self, client: TestClient, connexion
    ) -> None:
        """Production déclarée SANS consommation, et l'inverse : les deux comptent.

        C'est la garantie que la jointure complète est bien exploitée. Une
        jointure interne ne remonterait que les ordres ayant les deux côtés, et
        l'ordre qui a produit sans consommer — celui qui porte justement l'écart
        — resterait invisible.
        """
        with connexion.cursor() as cur:
            # Une cellule où coexistent un ordre sans consommation et un autre
            # hors nomenclature ou nominal : le cas discriminant.
            cur.execute(
                "SELECT parent_perimetre, child_itemid, annee, semaine "
                "FROM fact_ecart_of "
                "GROUP BY parent_perimetre, child_itemid, annee, semaine "
                "HAVING count(DISTINCT statut_ligne) > 1 AND count(DISTINCT prod_id) > 1 "
                "LIMIT 1"
            )
            trouve = cur.fetchone()
        if trouve is None:
            pytest.skip("Le jeu courant n'a pas de cellule à statuts de ligne mixtes.")

        perimetre, composant, annee, semaine = trouve
        contexte = self._contexte(client, {
            "perimetre": perimetre, "composant": composant,
            "annee": annee, "semaine": semaine,
        })
        with connexion.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT statut_ligne FROM fact_ecart_of "
                "WHERE prod_id = ANY(%s) AND child_itemid = %s "
                "AND annee = %s AND semaine = %s",
                (contexte["ofs"], composant, annee, semaine),
            )
            statuts = {ligne[0] for ligne in cur.fetchall()}
        assert len(statuts) > 1, (
            "Le contexte n'a ramené qu'un seul côté du mouvement : la jointure "
            "complète n'est pas exploitée."
        )

    def test_les_semaines_couvrent_toute_l_activite_des_ordres(
        self, client: TestClient, connexion
    ) -> None:
        """Le débordement hors de la semaine cliquée est la raison d'être du tiroir."""
        cellule = self._cellule(client)
        contexte = self._contexte(client, cellule)
        with connexion.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT annee, semaine FROM fact_ecart_of "
                "WHERE prod_id = ANY(%s) AND child_itemid = %s",
                (contexte["ofs"], cellule["composant"]),
            )
            attendues = {(ligne[0], ligne[1]) for ligne in cur.fetchall()}
        rendues = {(s["annee"], s["semaine"]) for s in contexte["semaines"]}
        assert rendues == attendues
        assert (cellule["annee"], cellule["semaine"]) in rendues

    def test_les_bornes_encadrent_toutes_les_semaines(self, client: TestClient) -> None:
        contexte = self._contexte(client, self._cellule(client))
        debuts = [s["semaine_debut"] for s in contexte["semaines"]]
        assert contexte["date_debut"] == min(debuts)
        assert contexte["date_fin"] == max(debuts)

    def test_le_chiffre_rendu_est_celui_de_la_cellule(
        self, client: TestClient, connexion
    ) -> None:
        """Le bandeau du tiroir doit afficher le chiffre d'où l'on vient."""
        cellule = self._cellule(client)
        contexte = self._contexte(client, cellule)
        with connexion.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(ecart_brut), 0) FROM fact_ecart_backflush "
                "WHERE parent_perimetre = %s AND child_itemid = %s "
                "AND annee = %s AND semaine = %s",
                (cellule["perimetre"], cellule["composant"],
                 cellule["annee"], cellule["semaine"]),
            )
            (attendu,) = cur.fetchone()
        assert float(contexte["ecart_brut"]) == pytest.approx(float(attendu))

    def test_une_cellule_sans_mouvement_ne_casse_pas(self, client: TestClient) -> None:
        """Cellule vide : réponse vide, pas d'erreur, et surtout pas de bornes nulles."""
        cellule = self._cellule(client)
        contexte = self._contexte(
            client, {**cellule, "composant": "COMPOSANT-INEXISTANT"}
        )
        assert contexte["ofs"] == []
        assert contexte["semaines"] == []

    def test_les_filtres_globaux_ne_sont_pas_pris_en_compte(
        self, client: TestClient
    ) -> None:
        """La route n'accepte QUE les coordonnées de la cellule.

        Un objet de filtres accepté ici serait tôt ou tard alimenté par la barre
        globale, et un « masquer les conformes » retirerait de la liste les
        ordres qui portent la contrepartie de l'écart.
        """
        reponse = client.post(
            "/api/analytique/contexte-of",
            json={**self._cellule(client), "exclure_conforme": True},
        )
        assert reponse.status_code == 200
        # Le champ surnuméraire est simplement ignoré : la réponse est identique.
        assert reponse.json()["ofs"] == self._contexte(client, self._cellule(client))["ofs"]

    def test_les_coordonnees_hors_bornes_sont_refusees(self, client: TestClient) -> None:
        reponse = client.post(
            "/api/analytique/contexte-of",
            json={"perimetre": "X", "composant": "Y", "annee": 2026, "semaine": 99},
        )
        assert reponse.status_code == 422
