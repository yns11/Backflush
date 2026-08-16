"""Tests des définitions d'indicateurs — logique métier pure, sans base."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.server.domain.metrics import (
    AgregatBrut,
    construire_indicateurs,
    ecart_pct_global,
    fiabilite_backflush,
    taux_conformite,
)


class TestAgregatBrut:
    def test_agregat_vide_ne_produit_que_des_zeros(self) -> None:
        agregat = AgregatBrut.depuis_ligne(None)
        assert agregat.nb_lignes == 0
        assert agregat.ecart_valorise == 0.0

    def test_convertit_les_decimaux_et_absorbe_les_nuls(self) -> None:
        agregat = AgregatBrut.depuis_ligne(
            {"nb_lignes": 10, "ecart_valorise": Decimal("1234.56"), "conso_theorique": None}
        )
        assert agregat.nb_lignes == 10
        assert agregat.ecart_valorise == pytest.approx(1234.56)
        assert agregat.conso_theorique == 0.0

    def test_les_compteurs_restent_entiers(self) -> None:
        agregat = AgregatBrut.depuis_ligne({"nb_lignes": Decimal("42")})
        assert isinstance(agregat.nb_lignes, int)


class TestTauxConformite:
    def test_aucune_ligne_donne_zero(self) -> None:
        assert taux_conformite(AgregatBrut()) == 0.0

    def test_calcule_la_part_des_lignes_dans_la_tolerance(self) -> None:
        agregat = AgregatBrut(nb_lignes=200, nb_lignes_ecart=50)
        assert taux_conformite(agregat) == pytest.approx(75.0)

    def test_toutes_les_lignes_en_ecart_donnent_zero(self) -> None:
        assert taux_conformite(AgregatBrut(nb_lignes=10, nb_lignes_ecart=10)) == 0.0


class TestEcartPctGlobal:
    def test_theorique_nul_ne_divise_pas_par_zero(self) -> None:
        assert ecart_pct_global(AgregatBrut(ecart_net=100.0)) == 0.0

    def test_rapporte_l_ecart_au_theorique(self) -> None:
        agregat = AgregatBrut(conso_theorique=1000.0, ecart_net=-25.0)
        assert ecart_pct_global(agregat) == pytest.approx(-2.5)


class TestFiabiliteBackflush:
    def test_sans_valorisation_l_indicateur_est_nul(self) -> None:
        assert fiabilite_backflush(AgregatBrut(ecart_valorise_absolu=500.0)) == 0.0

    def test_aucun_ecart_donne_cent_pour_cent(self) -> None:
        agregat = AgregatBrut(conso_theorique_valorisee=10_000.0, ecart_valorise_absolu=0.0)
        assert fiabilite_backflush(agregat) == pytest.approx(100.0)

    def test_les_ecarts_opposes_ne_se_compensent_pas(self) -> None:
        """Le cœur de l'indicateur : c'est la SOMME DES VALEURS ABSOLUES.

        Une non-consommation de 500 € et une surconsommation de 500 € donnent un
        écart net nul, alors que deux dysfonctionnements bien réels se sont
        produits. La fiabilité doit les compter tous les deux.
        """
        agregat = AgregatBrut(
            conso_theorique_valorisee=10_000.0,
            ecart_valorise=0.0,             # ils se compensent…
            ecart_valorise_absolu=1_000.0,  # …mais pas en valeur absolue
        )
        assert fiabilite_backflush(agregat) == pytest.approx(90.0)

    def test_l_indicateur_est_borne_a_zero(self) -> None:
        agregat = AgregatBrut(conso_theorique_valorisee=100.0, ecart_valorise_absolu=500.0)
        assert fiabilite_backflush(agregat) == 0.0


class TestConstruireIndicateurs:
    def test_produit_les_huit_indicateurs_attendus(self) -> None:
        indicateurs = construire_indicateurs(AgregatBrut())
        cles = [indicateur.cle for indicateur in indicateurs]
        assert cles == [
            "ecart_valorise_net",
            "non_consommation_valorisee",
            "surconsommation_valorisee",
            "fiabilite_backflush",
            "taux_conformite",
            "ecart_pct_global",
            "nb_composants",
            "nb_lignes_ecart",
        ]

    def test_chaque_indicateur_porte_sa_definition(self) -> None:
        for indicateur in construire_indicateurs(AgregatBrut()):
            assert indicateur.aide, f"{indicateur.cle} n'a pas d'aide contextuelle."
            assert indicateur.unite, f"{indicateur.cle} n'a pas d'unité."

    def test_sans_periode_precedente_aucune_variation(self) -> None:
        for indicateur in construire_indicateurs(AgregatBrut(nb_lignes=10)):
            assert indicateur.variation_pct is None
            assert indicateur.variation_absolue is None

    def test_calcule_la_variation_relative(self) -> None:
        courant = AgregatBrut(ecart_valorise=1_500.0)
        precedent = AgregatBrut(ecart_valorise=1_000.0)
        indicateur = construire_indicateurs(courant, precedent)[0]
        assert indicateur.variation_pct == pytest.approx(50.0)
        assert indicateur.variation_absolue == pytest.approx(500.0)

    def test_une_base_nulle_ne_produit_pas_de_variation_infinie(self) -> None:
        indicateur = construire_indicateurs(
            AgregatBrut(ecart_valorise=1_500.0), AgregatBrut(ecart_valorise=0.0)
        )[0]
        assert indicateur.variation_pct is None
        assert indicateur.variation_absolue == pytest.approx(1_500.0)

    def test_la_variation_est_relative_a_la_valeur_absolue(self) -> None:
        """Depuis -1 000 € vers -500 €, l'écart se réduit de moitié.

        Rapporter à la valeur signée donnerait -50 %, ce qui se lirait comme une
        aggravation alors que la situation s'améliore.
        """
        indicateur = construire_indicateurs(
            AgregatBrut(ecart_valorise=-500.0), AgregatBrut(ecart_valorise=-1_000.0)
        )[0]
        assert indicateur.variation_pct == pytest.approx(50.0)
        assert indicateur.variation_absolue == pytest.approx(500.0)

    def test_les_polarites_sont_coherentes_avec_le_metier(self) -> None:
        polarites = {i.cle: i.sens_favorable for i in construire_indicateurs(AgregatBrut())}
        # Un écart net signé n'est ni bon ni mauvais dans l'absolu : neutre.
        assert polarites["ecart_valorise_net"] == "neutre"
        # Fiabilité et conformité : plus c'est haut, mieux c'est.
        assert polarites["fiabilite_backflush"] == "hausse"
        assert polarites["taux_conformite"] == "hausse"
        # Volumes d'anomalie : plus c'est bas, mieux c'est.
        assert polarites["non_consommation_valorisee"] == "baisse"
        assert polarites["surconsommation_valorisee"] == "baisse"
        assert polarites["nb_lignes_ecart"] == "baisse"
