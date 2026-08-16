"""Tests du modèle de filtres et du constructeur de prédicats SQL.

C'est la frontière de sécurité de l'application : tout ce qui vient du client y
passe. Les tests vérifient à la fois la correction métier et l'absence de
concaténation de valeurs dans le SQL.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.server.domain.filters import (
    Filtres,
    construire_predicat,
    expression_type_ecart,
)


class TestValidation:
    def test_valeurs_par_defaut(self) -> None:
        filtres = Filtres()
        assert filtres.seuil_conformite == 0.5
        assert filtres.seuil_pct is None
        assert filtres.programmes == []

    def test_refuse_une_periode_inversee(self) -> None:
        with pytest.raises(ValidationError):
            Filtres(date_debut=date(2026, 6, 1), date_fin=date(2026, 5, 1))

    def test_refuse_un_type_d_ecart_inconnu(self) -> None:
        with pytest.raises(ValidationError):
            Filtres(types_ecart=["Inventé"])  # type: ignore[list-item]

    def test_refuse_un_seuil_negatif(self) -> None:
        with pytest.raises(ValidationError):
            Filtres(seuil_conformite=-1)

    def test_borne_la_longueur_de_la_recherche(self) -> None:
        with pytest.raises(ValidationError):
            Filtres(recherche="x" * 500)

    def test_normalise_la_recherche_vide(self) -> None:
        assert Filtres(recherche="   ").recherche is None

    def test_dedoublonne_les_listes_en_conservant_l_ordre(self) -> None:
        filtres = Filtres(programmes=["M3", " M3 ", "M2BEV", "M3"])
        assert filtres.programmes == ["M3", "M2BEV"]


class TestPeriodePrecedente:
    def test_meme_duree_immediatement_anterieure(self) -> None:
        filtres = Filtres(date_debut=date(2026, 6, 1), date_fin=date(2026, 6, 29))
        precedente = filtres.periode_precedente()
        assert precedente.date_fin == date(2026, 5, 25)     # lundi précédent
        assert precedente.date_debut == date(2026, 4, 27)
        # Durée identique : la comparaison est honnête.
        assert (precedente.date_fin - precedente.date_debut) == (
            filtres.date_fin - filtres.date_debut
        )

    def test_sans_bornes_la_comparaison_est_vide(self) -> None:
        precedente = Filtres().periode_precedente()
        assert precedente.date_debut is None and precedente.date_fin is None

    def test_conserve_les_autres_criteres(self) -> None:
        filtres = Filtres(
            date_debut=date(2026, 6, 1), date_fin=date(2026, 6, 29), programmes=["M3"]
        )
        assert filtres.periode_precedente().programmes == ["M3"]


class TestConstruirePredicat:
    def test_sans_filtre_le_predicat_est_toujours_vrai(self) -> None:
        predicat = construire_predicat(Filtres())
        assert predicat.sql == "TRUE"
        # Les deux seuils sont toujours transmis : l'expression SQL de type
        # d'écart les référence systématiquement.
        assert set(predicat.params) == {"seuil", "seuil_pct"}

    def test_les_dates_deviennent_des_parametres_lies(self) -> None:
        predicat = construire_predicat(
            Filtres(date_debut=date(2026, 6, 1), date_fin=date(2026, 6, 29))
        )
        assert "f.semaine_debut >= %(date_debut)s" in predicat.sql
        assert predicat.params["date_debut"] == date(2026, 6, 1)

    def test_les_listes_utilisent_any_et_un_tableau(self) -> None:
        predicat = construire_predicat(Filtres(programmes=["M3", "M2BEV"]))
        assert "f.parent_programme = ANY(%(programmes)s)" in predicat.sql
        assert predicat.params["programmes"] == ["M3", "M2BEV"]

    def test_aucune_valeur_client_n_apparait_dans_le_sql(self) -> None:
        """Garde-fou anti-injection : la requête ne contient que des marqueurs."""
        filtres = Filtres(
            programmes=["'; DROP TABLE fact_ecart_backflush; --"],
            recherche="100% _test_",
            composants=["CMP-001"],
            impact_min=1_000,
        )
        predicat = construire_predicat(filtres)
        assert "DROP TABLE" not in predicat.sql
        assert "CMP-001" not in predicat.sql
        assert "100%" not in predicat.sql

    def test_les_metacaracteres_like_sont_neutralises(self) -> None:
        predicat = construire_predicat(Filtres(recherche="100%_x"))
        # Cherché littéralement, et non comme un joker.
        assert predicat.params["recherche"] == r"%100\%\_x%"

    def test_l_antislash_est_echappe_en_premier(self) -> None:
        predicat = construire_predicat(Filtres(recherche=r"a\b"))
        assert predicat.params["recherche"] == r"%a\\b%"

    def test_exclure_conforme_utilise_l_expression_dynamique(self) -> None:
        predicat = construire_predicat(Filtres(exclure_conforme=True))
        assert "<> 'Conforme'" in predicat.sql
        assert "%(seuil)s" in predicat.sql

    def test_le_filtre_de_materialite_porte_sur_la_valeur_absolue(self) -> None:
        predicat = construire_predicat(Filtres(impact_min=500))
        assert "abs(f.ecart_valorise) >= %(impact_min)s" in predicat.sql

    def test_l_alias_est_propage(self) -> None:
        predicat = construire_predicat(Filtres(programmes=["M3"]), alias="e")
        assert "e.parent_programme" in predicat.sql


class TestExpressionTypeEcart:
    def test_reference_les_deux_seuils(self) -> None:
        expression = expression_type_ecart()
        assert "%(seuil)s::numeric" in expression
        assert "%(seuil_pct)s::numeric" in expression

    def test_produit_les_trois_valeurs_metier(self) -> None:
        expression = expression_type_ecart()
        for valeur in ("'Conforme'", "'Non-consommation'", "'Surconsommation'"):
            assert valeur in expression

    def test_la_tolerance_absolue_prime_sur_le_signe(self) -> None:
        """L'ordre des branches compte : la conformité est testée en premier.

        Sinon une ligne à +0,2 unité serait classée « Non-consommation » avant
        même que la tolérance ne soit évaluée.
        """
        expression = expression_type_ecart()
        assert expression.index("'Conforme'") < expression.index("'Non-consommation'")
