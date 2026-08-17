"""Tests de la source de faits effective — celle qui applique le paramétrage.

Trois familles de garanties, chacune correspondant à une façon dont ce mécanisme
peut casser en silence :

1. **Cohérence avec le schéma.** La liste de colonnes est recopiée dans
   ``app/`` parce que ``src/`` n'existe pas dans le conteneur Databricks Apps ;
   il faut donc un test qui la compare au schéma, sinon une colonne ajoutée
   disparaîtrait de toutes les requêtes sans un mot.
2. **Exactitude du recalcul.** Les formules du modèle gold sont rejouées ici :
   si elles changent d'un côté sans l'autre, les chiffres divergent sans erreur.
3. **Performance.** La table dérivée doit rester « remontable » par le
   planificateur. Un ``EXPLAIN`` le vérifie : sans cela, la régression se
   manifesterait par une application lente, un symptôme qui ne désigne jamais
   sa cause.
"""

from __future__ import annotations

import os
import re

import pytest

from app.server.data.faits import (
    COLONNES_FAIT,
    COLONNES_RECALCULEES,
    SOURCE_FAITS,
    TABLE_ARTICLE_EXCLU,
    TABLE_DETAIL,
    TABLE_NOMENCLATURE_PARAM,
)
from src.jobs.lakebase_schema import (
    FACT_ECART_BACKFLUSH,
    PARAM_ARTICLE_EXCLU,
    PARAM_NOMENCLATURE,
    TABLES_PARAM,
)

BASE_DISPONIBLE = bool(os.getenv("LAKEBASE_PG_URL"))
besoin_base = pytest.mark.skipif(
    not BASE_DISPONIBLE, reason="LAKEBASE_PG_URL non défini : test d'intégration ignoré."
)


class TestCoherenceAvecLeSchema:
    """Le contrat entre ``app/`` et ``src/``, qu'aucun import ne peut tenir."""

    def test_les_colonnes_suivent_le_schema(self) -> None:
        assert FACT_ECART_BACKFLUSH.column_names == COLONNES_FAIT, (
            "app/server/data/faits.py:COLONNES_FAIT a divergé du schéma Lakebase. "
            "Recopier FACT_ECART_BACKFLUSH.column_names — l'import direct est "
            "impossible, src/ n'existe pas dans le conteneur Databricks Apps."
        )

    def test_les_noms_de_tables_suivent_le_schema(self) -> None:
        assert FACT_ECART_BACKFLUSH.name == TABLE_DETAIL
        assert PARAM_ARTICLE_EXCLU.name == TABLE_ARTICLE_EXCLU
        assert PARAM_NOMENCLATURE.name == TABLE_NOMENCLATURE_PARAM

    def test_les_colonnes_recalculees_existent(self) -> None:
        inconnues = set(COLONNES_RECALCULEES) - set(COLONNES_FAIT)
        assert not inconnues, f"Colonnes recalculées absentes du schéma : {inconnues}"

    def test_les_tables_de_parametrage_ne_sont_pas_repliquees(self) -> None:
        """Elles doivent échapper à la bascule DROP + RENAME de l'ingestion."""
        from src.jobs.lakebase_schema import TABLES

        assert not set(TABLES_PARAM) & set(TABLES)
        for table in TABLES_PARAM:
            assert table.source == "", (
                f"{table.name} déclare une source Unity Catalog : elle serait "
                "publiée par le job, donc vidée à chaque exécution."
            )


class TestSourceFaits:
    def test_la_projection_expose_toutes_les_colonnes_une_fois(self) -> None:
        for colonne in COLONNES_FAIT:
            occurrences = len(re.findall(rf"\b(?:AS |b\.){colonne}\b", SOURCE_FAITS))
            assert occurrences >= 1, f"{colonne} absente de la projection."

    def test_les_lignes_desactivees_sont_ecartees(self) -> None:
        assert "COALESCE(o.active, TRUE)" in SOURCE_FAITS

    def test_les_references_exclues_sont_ecartees_des_deux_cotes(self) -> None:
        """Une exclusion vaut que la référence soit parent OU composant."""
        assert "x.item_id = b.child_itemid" in SOURCE_FAITS
        assert "x.item_id = b.parent_itemid" in SOURCE_FAITS

    def test_aucun_parametre_utilisateur_n_est_interpole(self) -> None:
        """La source est du SQL statique : aucun marqueur, aucune valeur."""
        assert "%(" not in SOURCE_FAITS

    def test_le_type_ecart_n_est_pas_recalcule(self) -> None:
        """Il est recalculé à la volée depuis le seuil actif, jamais lu ici."""
        assert "type_ecart" not in COLONNES_RECALCULEES


@besoin_base
class TestRecalculExact:
    """Le recalcul doit redonner EXACTEMENT ce qu'aurait produit le job gold."""

    @staticmethod
    def _lire(connexion, parent: str, enfant: str) -> dict:
        """Première ligne de la source EFFECTIVE, en dictionnaire."""
        from psycopg.rows import dict_row

        with connexion.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT * FROM {SOURCE_FAITS} f "
                "WHERE f.parent_itemid = %s AND f.child_itemid = %s "
                "ORDER BY f.semaine_debut LIMIT 1",
                (parent, enfant),
            )
            return dict(cur.fetchone())

    def test_sans_surcharge_les_valeurs_sont_celles_du_modele(self, connexion) -> None:
        with connexion.cursor() as cur:
            cur.execute(
                f"SELECT parent_itemid, child_itemid FROM {TABLE_DETAIL} "
                "WHERE statut_ligne = 'Nominal' LIMIT 1"
            )
            parent, enfant = cur.fetchone()
        effectif = self._lire(connexion, parent, enfant)
        from psycopg.rows import dict_row

        with connexion.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT * FROM {TABLE_DETAIL} "
                "WHERE parent_itemid = %s AND child_itemid = %s "
                "ORDER BY semaine_debut LIMIT 1",
                (parent, enfant),
            )
            brut = dict(cur.fetchone())
        for colonne in COLONNES_RECALCULEES:
            assert effectif[colonne] == brut[colonne], colonne

    def test_un_coefficient_surcharge_recalcule_toute_la_chaine(self, connexion) -> None:
        with connexion.cursor() as cur:
            cur.execute(
                f"SELECT parent_itemid, child_itemid, coef_bom FROM {TABLE_DETAIL} "
                "WHERE statut_ligne = 'Nominal' AND coef_bom > 0 "
                "AND child_cout_standard IS NOT NULL LIMIT 1"
            )
            parent, enfant, coef = cur.fetchone()
        avant = self._lire(connexion, parent, enfant)

        with connexion.cursor() as cur:
            cur.execute(
                f"INSERT INTO {TABLE_NOMENCLATURE_PARAM} "
                "(parent_itemid, child_itemid, active, coef_bom) VALUES (%s, %s, TRUE, %s) "
                "ON CONFLICT (parent_itemid, child_itemid) DO UPDATE SET coef_bom = EXCLUDED.coef_bom",
                (parent, enfant, float(coef) * 2),
            )
        apres = self._lire(connexion, parent, enfant)

        # Les numeric de Postgres reviennent en Decimal : on compare en flottant,
        # la précision décimale n'étant pas l'objet du test.
        valeur = lambda ligne, cle: float(ligne[cle])  # noqa: E731

        assert valeur(apres, "coef_bom") == float(coef) * 2
        # conso_theorique = qty_parent_produite × coef : doubler le coefficient
        # double le théorique, et l'écart le suit exactement.
        assert valeur(apres, "conso_theorique") == pytest.approx(
            valeur(avant, "conso_theorique") * 2
        )
        assert valeur(apres, "ecart_brut") == pytest.approx(
            valeur(apres, "conso_theorique") - valeur(apres, "conso_reelle")
        )
        assert valeur(apres, "ecart_valorise") == pytest.approx(
            valeur(apres, "ecart_brut") * valeur(apres, "child_cout_standard")
        )

    def test_une_ligne_desactivee_disparait(self, connexion) -> None:
        with connexion.cursor() as cur:
            cur.execute(f"SELECT parent_itemid, child_itemid FROM {TABLE_DETAIL} LIMIT 1")
            parent, enfant = cur.fetchone()
            cur.execute(
                f"INSERT INTO {TABLE_NOMENCLATURE_PARAM} (parent_itemid, child_itemid, active) "
                "VALUES (%s, %s, FALSE) "
                "ON CONFLICT (parent_itemid, child_itemid) DO UPDATE SET active = FALSE",
                (parent, enfant),
            )
            cur.execute(
                f"SELECT count(*) FROM {SOURCE_FAITS} f "
                "WHERE f.parent_itemid = %s AND f.child_itemid = %s",
                (parent, enfant),
            )
            assert cur.fetchone()[0] == 0

    def test_une_reference_exclue_disparait_comme_composant(self, connexion) -> None:
        with connexion.cursor() as cur:
            cur.execute(f"SELECT child_itemid FROM {TABLE_DETAIL} LIMIT 1")
            (enfant,) = cur.fetchone()
            cur.execute(
                f"INSERT INTO {TABLE_ARTICLE_EXCLU} (item_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (enfant,),
            )
            cur.execute(
                f"SELECT count(*) FROM {SOURCE_FAITS} f WHERE f.child_itemid = %s", (enfant,)
            )
            assert cur.fetchone()[0] == 0

    def test_une_reference_exclue_disparait_comme_parent(self, connexion) -> None:
        with connexion.cursor() as cur:
            cur.execute(f"SELECT parent_itemid FROM {TABLE_DETAIL} LIMIT 1")
            (parent,) = cur.fetchone()
            cur.execute(
                f"INSERT INTO {TABLE_ARTICLE_EXCLU} (item_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (parent,),
            )
            cur.execute(
                f"SELECT count(*) FROM {SOURCE_FAITS} f WHERE f.parent_itemid = %s", (parent,)
            )
            assert cur.fetchone()[0] == 0


@besoin_base
class TestPlanDExecution:
    """Garde-fou de performance : la table dérivée ne doit pas coûter un balayage.

    C'est le risque propre à ce choix de conception. Une régression se
    manifesterait par une application lente sur la volumétrie réelle — un
    symptôme qui n'accuse jamais la bonne cause, et que la volumétrie de
    développement ne révèle pas.
    """

    def test_le_filtre_de_date_attaque_encore_l_index(self, connexion) -> None:
        from app.server.domain.filters import Filtres, construire_predicat

        filtres = Filtres(date_debut="2026-05-01", date_fin="2026-05-31")
        predicat = construire_predicat(filtres)
        with connexion.cursor() as cur:
            cur.execute(f"ANALYZE {TABLE_DETAIL}")
            cur.execute(
                f"EXPLAIN SELECT count(*) FROM {SOURCE_FAITS} f WHERE {predicat.sql}",
                predicat.params,
            )
            plan = "\n".join(ligne[0] for ligne in cur.fetchall())

        assert "Index Scan" in plan or "Bitmap Index Scan" in plan, plan
        assert f"Seq Scan on {TABLE_DETAIL}" not in plan, (
            "La table dérivée n'est plus remontée par le planificateur : chaque "
            f"requête balaie {TABLE_DETAIL} en entier.\n{plan}"
        )
