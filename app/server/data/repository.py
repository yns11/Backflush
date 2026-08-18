"""Accès aux données Lakebase — **le seul module qui écrit du SQL**.

Règles tenues ici :

* toute valeur venant du client passe par un paramètre nommé psycopg
  (``%(nom)s``) ; jamais de concaténation ;
* les seuls fragments interpolés sont des **expressions issues de listes
  blanches** (colonne de tri, dimension d'agrégation), validées avant usage ;
* chaque grille renvoie son total en un seul aller-retour grâce à
  ``COUNT(*) OVER ()`` — pas de requête de comptage séparée ;
* les agrégats financiers sont calculés en base, jamais en Python : le volume
  reste côté serveur de données.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import date
from typing import Any

from psycopg.rows import dict_row

from app.server.core.errors import RequeteInvalideError
from app.server.core.lakebase import LakebasePool
from app.server.data.faits import SOURCE_FAITS, SOURCE_FAITS_OF
from app.server.data.faits import TABLE_ARTICLE_EXCLU as PARAM_EXCLUSION
from app.server.data.faits import TABLE_DETAIL as FACT_DETAIL
from app.server.data.faits import TABLE_NOMENCLATURE_PARAM as PARAM_NOMENCLATURE
from app.server.domain.dictionary import GRILLES
from app.server.domain.filters import (
    Filtres,
    construire_predicat,
    expression_type_ecart,
    rang_statut_of,
)
from app.server.domain.metrics import AgregatBrut

LOGGER = logging.getLogger("backflush.repository")

#: Source des faits. Ce n'est PAS le nom de la table de détail mais une table
#: dérivée qui applique le paramétrage du key-user (références exclues, lignes
#: de nomenclature désactivées ou corrigées) — voir ``data/faits.py``. Toutes
#: les requêtes analytiques écrivent ``FROM {FACT} f`` et bénéficient donc des
#: arbitrages sans avoir à les connaître.
FACT = SOURCE_FAITS

#: Bloc de mesures partagé par tous les agrégats. ``f`` est l'alias de la table
#: de détail. Chaque mesure est protégée par COALESCE : une sélection vide doit
#: produire des zéros, pas des NULL que le frontend devrait interpréter.
MESURES = """\
    COUNT(*)                                                    AS nb_lignes,
    COUNT(*) FILTER (WHERE ({type_expr}) <> 'Conforme')         AS nb_lignes_ecart,
    COUNT(DISTINCT f.parent_itemid)                             AS nb_parents,
    COUNT(DISTINCT f.child_itemid)                              AS nb_composants,
    COUNT(DISTINCT f.parent_programme)                          AS nb_programmes,
    COUNT(DISTINCT f.parent_perimetre)                          AS nb_perimetres,
    COUNT(DISTINCT f.semaine_debut)                             AS nb_semaines,
    COALESCE(SUM(f.conso_theorique), 0)                         AS conso_theorique,
    COALESCE(SUM(f.conso_reelle), 0)                            AS conso_reelle,
    COALESCE(SUM(f.conso_theorique * COALESCE(f.child_cout_standard, 0)), 0)
                                                                AS conso_theorique_valorisee,
    COALESCE(SUM(f.ecart_brut), 0)                              AS ecart_net,
    COALESCE(SUM(GREATEST(f.ecart_brut, 0)), 0)                 AS non_consommation,
    COALESCE(SUM(GREATEST(-f.ecart_brut, 0)), 0)                AS surconsommation,
    COALESCE(SUM(f.ecart_valorise), 0)                          AS ecart_valorise,
    COALESCE(SUM(GREATEST(f.ecart_valorise, 0)), 0)             AS non_consommation_valorisee,
    COALESCE(SUM(GREATEST(-f.ecart_valorise, 0)), 0)            AS surconsommation_valorisee,
    COALESCE(SUM(abs(f.ecart_valorise)), 0)                     AS ecart_valorise_absolu,
    -- Impact absolu en quantité. Ce n'est PAS |ecart_net| : les non-consommations
    -- et les surconsommations d'un même groupe se compenseraient, alors qu'elles
    -- s'ajoutent en volume d'anomalie. C'est la grandeur sur laquelle le
    -- classement est fait lorsque la mesure active est « quantite ».
    COALESCE(SUM(abs(f.ecart_brut)), 0)                         AS ecart_absolu,
    COALESCE(SUM(f.ecart_equivalent_produit), 0)                AS ecart_equivalent_produit"""

#: Noms des colonnes produites par :data:`MESURES`, extraits du bloc lui-même.
#:
#: Les recopier à la main aurait garanti l'oubli : une mesure ajoutée plus haut
#: aurait disparu des séries hebdomadaires sans erreur, laissant un ``null`` à
#: l'écran. Les lignes de commentaire sont retirées d'abord, sans quoi un
#: « ... AS ... » rédigé en français serait pris pour un alias.
_MESURES_NOMS: tuple[str, ...] = tuple(
    re.findall(
        r"\bAS\s+(\w+)",
        "\n".join(
            ligne for ligne in MESURES.splitlines() if not ligne.lstrip().startswith("--")
        ),
    )
)

#: Libellé de semaine ISO lisible (2026-S14), calculé en base pour rester
#: cohérent entre la grille, l'export et l'assistant.
SEMAINE_LIBELLE = "f.annee::text || '-S' || lpad(f.semaine::text, 2, '0')"

#: Calendrier CONTINU des semaines de la sélection.
#:
#: Attend une CTE ``mesures`` exposant ``semaine_debut`` pour la sélection
#: courante, et fournit une CTE ``calendrier`` d'une ligne par lundi.
#:
#: Trois règles, qui sont trois pièges évités :
#:
#: 1. **Une semaine sans mouvement produit une ligne**, sinon l'axe affiche
#:    « S24, S25, S27 » et l'arrêt de ligne de la S26 devient invisible — alors
#:    que c'est souvent l'information la plus utile de la série.
#: 2. **Les bornes sont bridées par l'étendue réelle des données.** Une sélection
#:    « 2026 → 2030 » sur un historique de vingt semaines produirait sinon 260
#:    colonnes vides. L'étendue est lue sur la table de détail brute : les
#:    semaines qui existent ne dépendent pas des références exclues.
#: 3. **Une sélection sans aucune ligne ne produit AUCUNE semaine.** Vingt
#:    semaines de zéros pour une référence inexistante ne renseignent sur rien ;
#:    c'est un état vide, et il doit se présenter comme tel.
_CALENDRIER = f"""etendue AS (
                SELECT MIN(semaine_debut) AS mini, MAX(semaine_debut) AS maxi
                FROM {FACT_DETAIL}
            ),
            bornes AS (
                SELECT GREATEST(
                           date_trunc('week', COALESCE(%(date_debut)s::date, e.mini))::date, e.mini
                       ) AS depart,
                       LEAST(
                           date_trunc('week', COALESCE(%(date_fin)s::date, e.maxi))::date, e.maxi
                       ) AS arrivee
                FROM etendue e
                WHERE EXISTS (SELECT 1 FROM mesures)
            ),
            calendrier AS (
                SELECT jour::date AS semaine_debut
                FROM bornes,
                     generate_series(bornes.depart, bornes.arrivee, interval '7 days') AS jour
                WHERE bornes.depart IS NOT NULL
            )"""

#: Expression d'impact selon la mesure choisie par l'utilisateur.
#:
#: L'application classe et trie par défaut en valeur (€). Basculer en quantité
#: ne doit pas se contenter de changer l'affichage : un classement des dix
#: premières références par euros n'est pas le même qu'en unités — une pièce à
#: 3 000 € et une visserie à 0,02 € ne se croisent jamais dans le même ordre.
#: Le tri suit donc la mesure, jusque dans la base.
IMPACT_SQL = {
    "valeur": "abs(f.ecart_valorise)",
    "quantite": "abs(f.ecart_brut)",
}


def expression_impact(mesure: str) -> str:
    """Expression d'agrégation du classement, pour la mesure demandée."""
    if mesure not in IMPACT_SQL:
        raise RequeteInvalideError(
            f"Mesure inconnue : {mesure}. Attendu : {', '.join(sorted(IMPACT_SQL))}."
        )
    return f"COALESCE(SUM({IMPACT_SQL[mesure]}), 0)"


#: Dimensions autorisées pour une répartition. Liste blanche stricte.
DIMENSIONS = {
    "programme": "f.parent_programme",
    "perimetre": "f.parent_perimetre",
    "categorie": "f.child_categorie",
    "statut": "f.statut_ligne",
    "type": None,          # remplacé par l'expression dynamique du type d'écart
}

#: Expressions de tri autorisées, par grille. Toute clé absente est refusée.
_TRI_COMMUN = {
    "semaine_debut": "f.semaine_debut",
    "parent_programme": "f.parent_programme",
    "parent_perimetre": "f.parent_perimetre",
    "ecart_valorise": "ecart_valorise",
    "ecart_valorise_absolu": "ecart_valorise_absolu",
    "ecart_net": "ecart_net",
}

TRI_SQL: dict[str, dict[str, str]] = {
    "details": {
        "semaine_debut": "f.semaine_debut",
        "parent_programme": "f.parent_programme",
        "parent_perimetre": "f.parent_perimetre",
        "parent_itemid": "f.parent_itemid",
        "parent_name": "f.parent_name",
        "child_itemid": "f.child_itemid",
        "child_name": "f.child_name",
        "child_categorie": "f.child_categorie",
        "child_unite": "f.child_unite",
        "coef_bom": "f.coef_bom",
        "qty_parent_produite": "f.qty_parent_produite",
        "conso_theorique": "f.conso_theorique",
        "conso_reelle": "f.conso_reelle",
        "ecart_brut": "f.ecart_brut",
        "ecart_pct": "f.ecart_pct",
        "statut_ligne": "f.statut_ligne",
        "ecart_equivalent_produit": "f.ecart_equivalent_produit",
        "child_cout_standard": "f.child_cout_standard",
        "ecart_valorise": "f.ecart_valorise",
        "ecart_valorise_absolu": "abs(f.ecart_valorise)",
        "type_ecart": "type_ecart",
    },
    "composants": {
        **_TRI_COMMUN,
        "child_itemid": "f.child_itemid",
        "child_name": "child_name",
        "child_categorie": "child_categorie",
        "coef_bom": "coef_bom",
        "is_coef_uniforme": "is_coef_uniforme",
        "nb_parents": "nb_parents",
        "nb_semaines": "nb_semaines",
        "nb_lignes_ecart": "nb_lignes_ecart",
        "conso_theorique": "conso_theorique",
        "conso_reelle": "conso_reelle",
        "ecart_pct_global": "ecart_pct_global",
        "non_consommation": "non_consommation",
        "surconsommation": "surconsommation",
        "ecart_equivalent_produit": "ecart_equivalent_produit",
    },
    "programmes": {
        **_TRI_COMMUN,
        "nb_parents": "nb_parents",
        "nb_composants": "nb_composants",
        "nb_lignes": "nb_lignes",
        "nb_lignes_ecart": "nb_lignes_ecart",
        "taux_conformite": "taux_conformite",
        "conso_theorique": "conso_theorique",
        "conso_reelle": "conso_reelle",
        "non_consommation_valorisee": "non_consommation_valorisee",
        "surconsommation_valorisee": "surconsommation_valorisee",
    },
    "perimetres": {
        **_TRI_COMMUN,
        "nb_parents": "nb_parents",
        "nb_composants": "nb_composants",
        "nb_lignes": "nb_lignes",
        "nb_lignes_ecart": "nb_lignes_ecart",
        "taux_conformite": "taux_conformite",
        "qty_produite": "qty_produite",
        "conso_theorique": "conso_theorique",
        "conso_reelle": "conso_reelle",
        "ecart_equivalent_produit": "ecart_equivalent_produit",
        "non_consommation_valorisee": "non_consommation_valorisee",
        "surconsommation_valorisee": "surconsommation_valorisee",
    },
    "parents": {
        **_TRI_COMMUN,
        "parent_itemid": "f.parent_itemid",
        "parent_name": "parent_name",
        "nb_composants": "nb_composants",
        "nb_semaines": "nb_semaines",
        "qty_produite": "qty_produite",
        "nb_lignes_ecart": "nb_lignes_ecart",
        "conso_theorique": "conso_theorique",
        "conso_reelle": "conso_reelle",
    },
}

#: La grille par OF trie comme la grille de détail, plus l'axe de l'ordre de
#: fabrication. Dérivée plutôt que recopiée : les deux décrivent le même écart,
#: et un tri disponible d'un côté seulement passerait pour un bug.
TRI_SQL["details_of"] = {
    **TRI_SQL["details"],
    "prod_id": "f.prod_id",
    "prod_statut": "f.prod_statut",
    "prod_date_cloture": "f.prod_date_cloture",
}

#: Source de faits par grille.
#:
#: Toutes les grilles lisent la table de détail à la maille parent, sauf celle
#: par ordre de fabrication. Les deux sources appliquent le même paramétrage :
#: les deux écrans doivent parler des mêmes lignes.
SOURCE_PAR_GRILLE: dict[str, str] = {"details_of": SOURCE_FAITS_OF}


def source_de(cle: str) -> str:
    """Table (dérivée) que doit lire une grille donnée."""
    return SOURCE_PAR_GRILLE.get(cle, FACT)


def porte_axe_of(cle: str) -> bool:
    """La source de cette grille expose-t-elle ``prod_id`` et ``prod_statut`` ?

    Gouverne les filtres « Numéro OF » et « Statut OF » : ailleurs, ces colonnes
    n'existent pas et les référencer ferait échouer la requête. La réponse se
    déduit de la table lue, jamais d'une liste de clés tenue à part — une grille
    ajoutée sur la source par OF hériterait alors du filtre sans qu'on y pense,
    et une grille retirée n'en garderait pas la trace.
    """
    return source_de(cle) is SOURCE_FAITS_OF


class Repository:
    """Façade de lecture sur Lakebase."""

    def __init__(self, pool: LakebasePool) -> None:
        self._pool = pool

    # -- Primitives --------------------------------------------------------
    def _fetch(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def _fetch_one(self, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
        lignes = self._fetch(sql, params)
        return lignes[0] if lignes else None

    def _stream(
        self, sql: str, params: dict[str, Any], taille_lot: int = 5_000
    ) -> Iterator[dict[str, Any]]:
        """Parcourt un résultat volumineux sans le charger entièrement en mémoire.

        Le curseur est **nommé**, donc matérialisé côté serveur : Postgres ne
        renvoie que ``taille_lot`` lignes à la fois. C'est ce qui permet
        d'exporter cent mille lignes dans un runtime limité à 6 Go.
        """
        with (
            self._pool.connection() as conn,
            conn.cursor(name="export", row_factory=dict_row) as cur,
        ):
            cur.itersize = taille_lot
            cur.execute(sql, params)
            yield from cur

    # -- Métadonnées -------------------------------------------------------
    def options_filtres(self) -> dict[str, Any]:
        """Valeurs disponibles pour alimenter les listes de filtres."""
        programmes = self._fetch(
            "SELECT DISTINCT parent_programme AS valeur FROM fact_ecart_backflush "
            "WHERE parent_programme IS NOT NULL ORDER BY 1", {},
        )
        perimetres = self._fetch(
            "SELECT DISTINCT parent_perimetre AS valeur FROM fact_ecart_backflush "
            "WHERE parent_perimetre IS NOT NULL ORDER BY 1", {},
        )
        categories = self._fetch(
            "SELECT DISTINCT child_categorie AS valeur FROM fact_ecart_backflush "
            "WHERE child_categorie IS NOT NULL ORDER BY 1", {},
        )
        # Statuts d'OF réellement présents, classés dans l'ordre du cycle de vie
        # D365 et non par ordre alphabétique : la question posée à ce filtre est
        # toujours « où en est l'ordre », jamais « comment s'appelle son statut ».
        # Un statut non traduit (cf. contrôle `of_statut_inconnu`) reste dans la
        # liste, en fin — le masquer rendrait ses lignes infiltrables.
        statuts_of = self._fetch(
            "SELECT DISTINCT prod_statut AS valeur FROM fact_ecart_of "
            "WHERE prod_statut IS NOT NULL", {},
        )
        bornes = self._fetch_one(
            "SELECT MIN(semaine_debut) AS debut, MAX(semaine_debut) AS fin "
            "FROM fact_ecart_backflush", {},
        ) or {}
        return {
            "programmes": [ligne["valeur"] for ligne in programmes],
            "perimetres": [ligne["valeur"] for ligne in perimetres],
            "categories": [ligne["valeur"] for ligne in categories],
            "types_ecart": ["Non-consommation", "Surconsommation", "Conforme"],
            "statuts_ligne": ["Nominal", "Hors nomenclature", "Sans consommation"],
            "statuts_of": sorted(
                (ligne["valeur"] for ligne in statuts_of), key=rang_statut_of
            ),
            "date_min": bornes.get("debut"),
            "date_max": bornes.get("fin"),
        }

    def fraicheur(self) -> dict[str, Any]:
        """État de la dernière ingestion et contrôles qualité en anomalie."""
        ingestion = self._fetch(
            "SELECT table_name, row_count, ended_at, duration_ms, status, error_message "
            "FROM meta_ingestion ORDER BY table_name", {},
        )
        controles = self._fetch(
            "SELECT controle, severite, domaine, valeur, en_anomalie, message "
            "FROM dq_controles ORDER BY "
            "CASE severite WHEN 'ERREUR' THEN 0 WHEN 'ALERTE' THEN 1 ELSE 2 END, controle", {},
        )
        derniere = max(
            (ligne["ended_at"] for ligne in ingestion if ligne.get("ended_at")), default=None
        )
        return {
            "derniere_ingestion": derniere,
            "tables": ingestion,
            "controles": controles,
            "en_echec": [ligne["table_name"] for ligne in ingestion if ligne["status"] != "SUCCES"],
        }

    # -- Agrégats ----------------------------------------------------------
    def agregat(self, filtres: Filtres) -> AgregatBrut:
        """Agrégat global de la sélection, base des indicateurs de synthèse."""
        predicat = construire_predicat(filtres)
        sql = (
            f"SELECT {MESURES.format(type_expr=expression_type_ecart())} "
            f"FROM {FACT} f WHERE {predicat.sql}"
        )
        return AgregatBrut.depuis_ligne(self._fetch_one(sql, predicat.params))

    def serie_hebdomadaire(self, filtres: Filtres) -> list[dict[str, Any]]:
        """Une ligne par semaine — alimente la tendance et la piste du slicer.

        L'axe est **continu** : une semaine sans mouvement produit une ligne à
        zéro plutôt que d'être omise. Sans cela, l'axe affichait
        « S24, S25, S27, S28 » et une interruption de production se lisait comme
        une semaine ordinaire — l'absence devenait invisible, alors que c'est
        souvent le fait le plus intéressant de la série.
        """
        predicat = construire_predicat(filtres)
        sql = f"""
            WITH mesures AS (
                SELECT f.semaine_debut,
                       {MESURES.format(type_expr=expression_type_ecart())}
                FROM {FACT} f
                WHERE {predicat.sql}
                GROUP BY f.semaine_debut
            ),
            {_CALENDRIER}
            SELECT c.semaine_debut,
                   EXTRACT(isoyear FROM c.semaine_debut)::int  AS annee,
                   EXTRACT(week    FROM c.semaine_debut)::int  AS semaine,
                   EXTRACT(isoyear FROM c.semaine_debut)::text || '-S'
                       || lpad(EXTRACT(week FROM c.semaine_debut)::text, 2, '0')
                                                              AS semaine_libelle,
                   {", ".join(f"COALESCE(m.{nom}, 0) AS {nom}" for nom in _MESURES_NOMS)}
            FROM calendrier c
            LEFT JOIN mesures m ON m.semaine_debut = c.semaine_debut
            ORDER BY c.semaine_debut
        """
        params = {**predicat.params, "date_debut": filtres.date_debut, "date_fin": filtres.date_fin}
        return self._fetch(sql, params)

    def semaines_periode(self, filtres: Filtres) -> list[dict[str, Any]]:
        """Calendrier continu des semaines de la sélection, sans les mesures.

        Sert d'ossature aux restitutions croisées : les colonnes du tableau
        doivent couvrir la période demandée, y compris les semaines sans
        production — un trou dans l'axe se lit comme une semaine qui n'a jamais
        existé.
        """
        predicat = construire_predicat(filtres)
        sql = f"""
            WITH mesures AS (
                SELECT DISTINCT f.semaine_debut
                FROM {FACT} f
                WHERE {predicat.sql}
            ),
            {_CALENDRIER}
            SELECT c.semaine_debut,
                   EXTRACT(isoyear FROM c.semaine_debut)::int AS annee,
                   EXTRACT(week    FROM c.semaine_debut)::int AS semaine
            FROM calendrier c
            ORDER BY 1
        """
        params = {**predicat.params, "date_debut": filtres.date_debut, "date_fin": filtres.date_fin}
        return self._fetch(sql, params)

    def repartition(
        self, filtres: Filtres, dimension: str, limite: int = 20, mesure: str = "valeur",
    ) -> list[dict[str, Any]]:
        """Agrégat par dimension (programme, périmètre, catégorie, type, statut)."""
        if dimension not in DIMENSIONS:
            raise RequeteInvalideError(
                f"Dimension inconnue : {dimension}. Attendu : {', '.join(sorted(DIMENSIONS))}."
            )
        expression = DIMENSIONS[dimension] or f"({expression_type_ecart()})"
        classement = expression_impact(mesure)
        predicat = construire_predicat(filtres)
        params = {**predicat.params, "limite": _borne(limite, 1, 200)}
        sql = f"""
            SELECT {expression} AS libelle,
                   {MESURES.format(type_expr=expression_type_ecart())}
            FROM {FACT} f
            WHERE {predicat.sql}
            GROUP BY 1
            ORDER BY {classement} DESC
            LIMIT %(limite)s
        """
        return self._fetch(sql, params)

    def top_composants(
        self, filtres: Filtres, limite: int = 10, mesure: str = "valeur",
    ) -> list[dict[str, Any]]:
        """Composants classés par impact absolu, en valeur ou en quantité."""
        classement = expression_impact(mesure)
        predicat = construire_predicat(filtres)
        params = {**predicat.params, "limite": _borne(limite, 1, 100)}
        sql = f"""
            SELECT f.child_itemid,
                   MAX(f.child_name)      AS child_name,
                   MAX(f.child_categorie) AS child_categorie,
                   f.parent_programme,
                   {MESURES.format(type_expr=expression_type_ecart())}
            FROM {FACT} f
            WHERE {predicat.sql}
            GROUP BY f.child_itemid, f.parent_programme
            ORDER BY {classement} DESC
            LIMIT %(limite)s
        """
        return self._fetch(sql, params)

    def concentration(
        self, filtres: Filtres, tete: int = 10, mesure: str = "valeur",
    ) -> dict[str, Any]:
        """Part des ``tete`` premières références dans l'impact absolu total.

        Un chiffre de concentration élevé est une bonne nouvelle opérationnelle :
        il signifie que corriger quelques références résout l'essentiel du
        problème. C'est l'équivalent d'une analyse ABC de stock.
        """
        classement = expression_impact(mesure)
        predicat = construire_predicat(filtres)
        params = {**predicat.params, "tete": _borne(tete, 1, 100)}
        sql = f"""
            WITH par_composant AS (
                SELECT f.child_itemid,
                       {classement} AS impact
                FROM {FACT} f
                WHERE {predicat.sql}
                GROUP BY f.child_itemid
            ),
            classe AS (
                SELECT impact, ROW_NUMBER() OVER (ORDER BY impact DESC) AS rang
                FROM par_composant
            )
            SELECT COALESCE(SUM(impact), 0)                                  AS impact_total,
                   COALESCE(SUM(impact) FILTER (WHERE rang <= %(tete)s), 0)  AS impact_tete,
                   COUNT(*)                                                  AS nb_references
            FROM classe
        """
        ligne = self._fetch_one(sql, params) or {}
        total = float(ligne.get("impact_total") or 0)
        tete_valeur = float(ligne.get("impact_tete") or 0)
        return {
            "nb_references": int(ligne.get("nb_references") or 0),
            "tete": params["tete"],
            "impact_total": total,
            "impact_tete": tete_valeur,
            "part_tete_pct": (tete_valeur / total * 100) if total else 0.0,
        }


    def totaux(self, filtres: Filtres, cle: str = "details") -> dict[str, Any]:
        """Totaux de la sélection ENTIÈRE, pour le pied de page des grilles.

        Ce ne sont pas les totaux de la page affichée : additionner cinquante
        lignes sur dix mille tromperait plus qu'il n'informerait. Les
        dénombrements distincts (parents, composants) ne sont pas non plus la
        somme des colonnes — ils sont recalculés sur toute la sélection, sans
        quoi un parent présent dans trois semaines serait compté trois fois.

        La grille lue est passée en argument : la vue par ordre de fabrication
        n'a ni la même source ni le même nombre de lignes, et lui servir les
        totaux de la maille parent afficherait un pied en contradiction avec le
        corps du tableau.

        La quantité produite est dédoublonnée par (parent, semaine) avant
        sommation : elle est répétée sur chaque ligne de composant.
        """
        predicat = construire_predicat(filtres, axe_of=porte_axe_of(cle))
        source = source_de(cle)
        # La quantité produite est portée par chaque ligne de composant : elle
        # est dédoublonnée sur la clé de production de la maille lue, faute de
        # quoi elle serait multipliée par le nombre de lignes de nomenclature.
        cle_production = (
            "f.prod_id, f.parent_itemid, f.semaine_debut"
            if cle == "details_of"
            else "f.parent_itemid, f.semaine_debut"
        )
        sql = f"""
            WITH production AS (
                SELECT {cle_production},
                       MAX(f.qty_parent_produite) AS qty_semaine
                FROM {source} f
                WHERE {predicat.sql}
                GROUP BY {cle_production}
            )
            SELECT {MESURES.format(type_expr=expression_type_ecart())},
                   (SELECT COALESCE(SUM(qty_semaine), 0) FROM production) AS qty_produite,
                   CASE WHEN COUNT(*) > 0
                        THEN (COUNT(*) - COUNT(*) FILTER (WHERE ({expression_type_ecart()}) <> 'Conforme'))::numeric
                             / COUNT(*) * 100 END AS taux_conformite,
                   CASE WHEN SUM(f.conso_theorique) > 0
                        THEN SUM(f.ecart_brut) / SUM(f.conso_theorique) * 100 END
                                                  AS ecart_pct_global
            FROM {source} f
            WHERE {predicat.sql}
        """
        return self._fetch_one(sql, predicat.params) or {}

    # -- Vue synthétique d'un périmètre ------------------------------------
    def synthese_perimetre(self, filtres: Filtres, mesure: str = "quantite") -> dict[str, Any]:
        """Production par parent et écarts par composant, croisés aux semaines.

        Restitution en tableau croisé, à l'image du rapport de pilotage de
        l'atelier : une ligne par parent produit, une colonne par semaine, puis
        une ligne par composant en écart.

        Le bloc « écart » ne retient que les composants à **coefficient
        uniforme** dans le périmètre : ce sont les seuls dont l'écart se
        convertit en équivalent produit sans ambiguïté. Les autres restent
        visibles dans les grilles de détail — les masquer ici évite d'afficher
        une conversion qui n'a pas de sens physique.
        """
        if mesure not in IMPACT_SQL:
            raise RequeteInvalideError(f"Mesure inconnue : {mesure}.")
        predicat = construire_predicat(filtres)

        # La production est portée par la ligne de composant : on la
        # dédoublonne par (parent, semaine) avant toute sommation.
        production = self._fetch(f"""
            WITH parsemaine AS (
                SELECT f.parent_itemid,
                       f.semaine_debut,
                       f.annee,
                       f.semaine,
                       MAX(f.parent_name)          AS parent_name,
                       MAX(f.qty_parent_produite)  AS qty_produite
                FROM {FACT} f
                WHERE {predicat.sql}
                GROUP BY f.parent_itemid, f.semaine_debut, f.annee, f.semaine
            )
            SELECT p.parent_itemid,
                   p.parent_name,
                   p.semaine_debut,
                   p.annee,
                   p.semaine,
                   p.qty_produite,
                   p.qty_produite * COALESCE(a.std_cost_price, 0) AS valeur_produite,
                   b.bomid
            FROM parsemaine p
            LEFT JOIN dim_article a ON a.item_id = p.parent_itemid
            LEFT JOIN (
                SELECT parent_itemid, MIN(bomid) AS bomid
                FROM dim_nomenclature GROUP BY parent_itemid
            ) b ON b.parent_itemid = p.parent_itemid
            ORDER BY p.parent_itemid, p.semaine_debut
        """, predicat.params)

        ecarts = self._fetch(f"""
            SELECT f.child_itemid,
                   MAX(f.child_name)                        AS child_name,
                   MIN(f.coef_bom)                          AS coef_bom,
                   f.semaine_debut,
                   f.annee,
                   f.semaine,
                   COALESCE(SUM(f.ecart_equivalent_produit), 0) AS ecart_equivalent_produit,
                   COALESCE(SUM(f.ecart_valorise), 0)           AS ecart_valorise,
                   COALESCE(SUM(f.ecart_brut), 0)               AS ecart_brut
            FROM {FACT} f
            WHERE {predicat.sql} AND f.is_coef_uniforme
            GROUP BY f.child_itemid, f.semaine_debut, f.annee, f.semaine
            ORDER BY f.child_itemid, f.semaine_debut
        """, predicat.params)
        # Les colonnes du tableau croisé viennent du calendrier, pas des lignes
        # rapportées : une semaine d'arrêt de ligne doit apparaître comme une
        # colonne vide, non disparaître de l'axe.
        return {
            "production": production,
            "ecarts": ecarts,
            "semaines": self.semaines_periode(filtres),
        }

    def contexte_of(
        self,
        *,
        perimetre: str,
        composant: str,
        annee: int,
        semaine: int,
        max_ofs: int = 400,
    ) -> dict[str, Any]:
        """Ordres de fabrication derrière UNE cellule d'écart de la vue synthétique.

        Une cellule du bloc « écart de prélèvement » vaut un périmètre, une
        référence composant et une semaine. La question qu'elle pose est
        toujours la même : cet écart est-il *résiduel*, ou n'est-il que le
        décalage d'un OF à cheval sur deux semaines ? On ne peut y répondre
        qu'en descendant à la maille de l'ordre, et en regardant AUSSI les
        semaines voisines.

        La réponse tient en deux ensembles :

        1. **Les ordres.** Tous ceux qui ont mouvementé ce composant cette
           semaine-là — côté consommation déclarée comme côté production
           déclarée. Les deux cas remontent d'une seule requête parce que
           ``fact_ecart_of`` naît d'une jointure complète : un OF qui a produit
           sans consommer y a une ligne (théorique sans réel), un OF qui a
           consommé sans produire aussi (réel sans théorique).
        2. **Leurs semaines.** Toutes celles où ces mêmes ordres ont mouvementé
           ce composant, et pas seulement celle du clic — c'est précisément ce
           débordement qui distingue un écart résiduel d'un artefact de calage.

        Aucun filtre utilisateur n'est appliqué ici, et c'est délibéré : un
        « type d'écart » ou un « masquer les conformes » hérité de la barre
        globale retirerait de la liste des ordres qui expliquent le chiffre, et
        le tiroir répondrait faux à la seule question qu'on lui pose. Le
        paramétrage du référentiel, lui, s'applique — il vient de la source
        dérivée, et la vue synthétique compte déjà avec lui.

        :param max_ofs: garde-fou. Au-delà, la liste est tronquée et
            ``tronque`` le signale ; sans lui, une semaine anormale enverrait
            des milliers d'identifiants dans un filtre ``IN``.
        """
        cle = {
            "perimetre": perimetre,
            "composant": composant,
            "annee": annee,
            "semaine": semaine,
        }

        ordres = self._fetch(f"""
            SELECT DISTINCT f.prod_id
            FROM {SOURCE_FAITS_OF} f
            WHERE f.parent_perimetre = %(perimetre)s
              AND f.child_itemid     = %(composant)s
              AND f.annee            = %(annee)s
              AND f.semaine          = %(semaine)s
              AND f.prod_id IS NOT NULL
            ORDER BY 1
            LIMIT %(limite)s
        """, {**cle, "limite": max_ofs + 1})
        tronque = len(ordres) > max_ofs
        prod_ids = [ligne["prod_id"] for ligne in ordres[:max_ofs]]

        # La cellule cliquée elle-même, à la maille parent : le tiroir doit
        # pouvoir afficher le chiffre d'où l'on vient. Sans lui, rien ne
        # garantit à l'utilisateur qu'il regarde la bonne case.
        origine = self._fetch_one(f"""
            SELECT MAX(f.child_name)                            AS child_name,
                   MAX(f.child_unite)                           AS child_unite,
                   MIN(f.semaine_debut)                         AS semaine_debut,
                   COALESCE(SUM(f.ecart_brut), 0)               AS ecart_brut,
                   COALESCE(SUM(f.ecart_equivalent_produit), 0) AS ecart_equivalent_produit,
                   COALESCE(SUM(f.ecart_valorise), 0)           AS ecart_valorise
            FROM {FACT} f
            WHERE f.parent_perimetre = %(perimetre)s
              AND f.child_itemid     = %(composant)s
              AND f.annee            = %(annee)s
              AND f.semaine          = %(semaine)s
        """, cle) or {}

        semaines: list[dict[str, Any]] = []
        if prod_ids:
            semaines = self._fetch(f"""
                SELECT DISTINCT f.semaine_debut, f.annee, f.semaine
                FROM {SOURCE_FAITS_OF} f
                WHERE f.prod_id      = ANY(%(prod_ids)s)
                  AND f.child_itemid = %(composant)s
                ORDER BY f.semaine_debut
            """, {"prod_ids": prod_ids, "composant": composant})

        return {
            "perimetre": perimetre,
            "composant": composant,
            "child_name": origine.get("child_name"),
            "child_unite": origine.get("child_unite"),
            "annee": annee,
            "semaine": semaine,
            "semaine_debut": origine.get("semaine_debut"),
            "ecart_brut": origine.get("ecart_brut"),
            "ecart_equivalent_produit": origine.get("ecart_equivalent_produit"),
            "ecart_valorise": origine.get("ecart_valorise"),
            "ofs": prod_ids,
            "tronque": tronque,
            "semaines": semaines,
            # Bornes prêtes à poser dans les filtres du tiroir. Repli sur la
            # semaine cliquée si aucun ordre n'est trouvé : mieux vaut un tiroir
            # qui montre une semaine vide qu'un tiroir sans bornes, qui
            # ouvrirait tout l'historique.
            "date_debut": (
                semaines[0]["semaine_debut"] if semaines else origine.get("semaine_debut")
            ),
            "date_fin": (
                semaines[-1]["semaine_debut"] if semaines else origine.get("semaine_debut")
            ),
        }

    # -- Grilles -----------------------------------------------------------
    def grille(
        self,
        cle: str,
        filtres: Filtres,
        *,
        tri: str | None = None,
        sens: str = "desc",
        page: int = 1,
        taille: int = 50,
        taille_max: int = 500,
    ) -> dict[str, Any]:
        """Page d'une grille, triée et filtrée côté serveur."""
        grille = GRILLES.get(cle)
        if grille is None:
            raise RequeteInvalideError(f"Grille inconnue : {cle}.")

        tri = tri or grille.tri_defaut
        expressions = TRI_SQL[cle]
        if tri not in expressions:
            raise RequeteInvalideError(
                f"Tri non autorisé sur « {tri} » pour la grille « {cle} »."
            )
        if sens not in ("asc", "desc"):
            raise RequeteInvalideError("Le sens de tri doit valoir « asc » ou « desc ».")

        taille = _borne(taille, 1, taille_max)
        page = max(1, page)
        predicat = construire_predicat(filtres, axe_of=porte_axe_of(cle))
        params = {**predicat.params, "limite": taille, "decalage": (page - 1) * taille}

        # NULLS LAST systématique : une valeur manquante ne doit jamais occuper
        # la tête d'un classement par impact.
        ordre = f"{expressions[tri]} {sens.upper()} NULLS LAST"
        sql = _SQL_GRILLES[cle].format(
            fact=source_de(cle),
            mesures=MESURES.format(type_expr=expression_type_ecart()),
            type_expr=expression_type_ecart(),
            semaine_libelle=SEMAINE_LIBELLE,
            predicat=predicat.sql,
            ordre=ordre,
        )
        lignes = self._fetch(sql, params)
        total = int(lignes[0]["_total"]) if lignes else 0
        for ligne in lignes:
            ligne.pop("_total", None)
        return {
            "lignes": lignes,
            "total": total,
            "page": page,
            "taille": taille,
            "nb_pages": (total + taille - 1) // taille if taille else 0,
            "tri": tri,
            "sens": sens,
        }

    def flux_grille(
        self, cle: str, filtres: Filtres, *, tri: str | None = None,
        sens: str = "desc", limite: int = 100_000,
    ) -> Iterator[dict[str, Any]]:
        """Itère toutes les lignes d'une grille pour l'export, sans pagination.

        Utilise un curseur serveur : l'App ne matérialise jamais l'intégralité du
        résultat en mémoire (contrainte de 6 Go du runtime Databricks Apps).
        """
        grille = GRILLES.get(cle)
        if grille is None:
            raise RequeteInvalideError(f"Grille inconnue : {cle}.")
        tri = tri or grille.tri_defaut
        expressions = TRI_SQL[cle]
        if tri not in expressions:
            raise RequeteInvalideError(f"Tri non autorisé sur « {tri} ».")
        if sens not in ("asc", "desc"):
            raise RequeteInvalideError("Le sens de tri doit valoir « asc » ou « desc ».")

        predicat = construire_predicat(filtres, axe_of=porte_axe_of(cle))
        params = {**predicat.params, "limite": _borne(limite, 1, 1_000_000), "decalage": 0}
        sql = _SQL_GRILLES[cle].format(
            fact=source_de(cle),
            mesures=MESURES.format(type_expr=expression_type_ecart()),
            type_expr=expression_type_ecart(),
            semaine_libelle=SEMAINE_LIBELLE,
            predicat=predicat.sql,
            ordre=f"{expressions[tri]} {sens.upper()} NULLS LAST",
        )
        for ligne in self._stream(sql, params):
            ligne.pop("_total", None)
            yield ligne

    # -- Fiches (utilisées par l'assistant et le panneau latéral) ----------
    def fiche_article(self, item_id: str) -> dict[str, Any] | None:
        return self._fetch_one(
            "SELECT item_id, item_name, item_description, categorie, item_group_id, "
            "item_group_label, programme, std_cost_price, std_unit "
            "FROM dim_article WHERE item_id = %(item_id)s", {"item_id": item_id},
        )

    def nomenclature_du_parent(self, parent_itemid: str) -> list[dict[str, Any]]:
        """Nomenclature active d'un parent, **surcharges comprises**.

        Le coefficient exposé est celui qui sert réellement au calcul, et les
        lignes désactivées sont retirées : afficher la valeur de l'ERP dans le
        tiroir pendant que l'analyse en utilise une autre créerait deux vérités
        à l'écran, sans moyen de savoir laquelle est la bonne.
        """
        return self._fetch(
            "SELECT n.child_itemid, a.item_name AS child_name, a.categorie, "
            "       COALESCE(o.coef_bom, n.child_qty) AS coef_bom, "
            "       n.child_qty AS coef_erp, "
            "       (o.coef_bom IS NOT NULL) AS coef_surcharge, "
            "       n.child_unitid AS unite, a.std_cost_price "
            f"FROM dim_nomenclature n "
            f"LEFT JOIN {PARAM_NOMENCLATURE} o "
            "       ON o.parent_itemid = n.parent_itemid AND o.child_itemid = n.child_itemid "
            "LEFT JOIN dim_article a ON a.item_id = n.child_itemid "
            "WHERE n.parent_itemid = %(parent)s AND COALESCE(o.active, TRUE) "
            f"  AND NOT EXISTS (SELECT 1 FROM {PARAM_EXCLUSION} x WHERE x.item_id = n.child_itemid) "
            "ORDER BY COALESCE(o.coef_bom, n.child_qty) DESC",
            {"parent": parent_itemid},
        )

    def parents_du_composant(self, child_itemid: str) -> list[dict[str, Any]]:
        """Parents consommant ce composant, surcharges comprises (cf. ci-dessus)."""
        return self._fetch(
            "SELECT n.parent_itemid, a.item_name AS parent_name, a.programme, a.perimetre, "
            "       COALESCE(o.coef_bom, n.child_qty) AS coef_bom, "
            "       (o.coef_bom IS NOT NULL) AS coef_surcharge "
            f"FROM dim_nomenclature n "
            f"LEFT JOIN {PARAM_NOMENCLATURE} o "
            "       ON o.parent_itemid = n.parent_itemid AND o.child_itemid = n.child_itemid "
            "LEFT JOIN dim_article a ON a.item_id = n.parent_itemid "
            "WHERE n.child_itemid = %(child)s AND COALESCE(o.active, TRUE) "
            f"  AND NOT EXISTS (SELECT 1 FROM {PARAM_EXCLUSION} x WHERE x.item_id = n.parent_itemid) "
            "ORDER BY a.programme, n.parent_itemid",
            {"child": child_itemid},
        )

    def historique_composant(
        self, child_itemid: str, filtres: Filtres
    ) -> list[dict[str, Any]]:
        """Série hebdomadaire d'un composant — support du drill-through le plus fin."""
        cible = filtres.model_copy(update={"composants": [child_itemid]})
        return self.serie_hebdomadaire(cible)

    def semaines_disponibles(self) -> list[date]:
        lignes = self._fetch(
            "SELECT DISTINCT semaine_debut FROM fact_ecart_backflush ORDER BY 1", {}
        )
        return [ligne["semaine_debut"] for ligne in lignes]


def _borne(valeur: int, minimum: int, maximum: int) -> int:
    """Contraint un entier dans un intervalle — protège la base des valeurs extrêmes."""
    return max(minimum, min(int(valeur), maximum))


# ---------------------------------------------------------------------------
# Requêtes des grilles
# ---------------------------------------------------------------------------
# Chaque requête expose `_total` via COUNT(*) OVER () : le nombre total de lignes
# (ou de groupes) est obtenu dans le même aller-retour que la page.
_SQL_GRILLES: dict[str, str] = {
    "details": """
        SELECT f.semaine_debut,
               f.annee,
               f.semaine,
               {semaine_libelle}                AS semaine_libelle,
               f.parent_programme,
               f.parent_perimetre,
               f.parent_itemid,
               f.parent_name,
               f.child_itemid,
               f.child_name,
               f.child_categorie,
               f.child_unite,
               f.coef_bom,
               f.qty_parent_produite,
               f.conso_theorique,
               f.conso_reelle,
               f.ecart_brut,
               f.ecart_pct,
               ({type_expr})                    AS type_ecart,
               f.statut_ligne,
               f.ecart_equivalent_produit,
               f.child_cout_standard,
               f.ecart_valorise,
               abs(f.ecart_valorise)            AS ecart_valorise_absolu,
               COUNT(*) OVER ()                 AS _total
        FROM {fact} f
        WHERE {predicat}
        ORDER BY {ordre}, f.semaine_debut DESC, f.parent_itemid, f.child_itemid
        LIMIT %(limite)s OFFSET %(decalage)s
    """,
    # Même projection que « details », plus l'axe de l'ordre de fabrication.
    # Le SQL est distinct plutôt que paramétré : les deux grilles ne lisent pas
    # la même table, et un template commun aurait fait dépendre la plus utilisée
    # d'une abstraction au service de la seconde.
    "details_of": """
        SELECT f.semaine_debut,
               f.annee,
               f.semaine,
               {semaine_libelle}                AS semaine_libelle,
               f.prod_id,
               f.prod_statut,
               f.prod_bomid,
               f.prod_date_cloture,
               f.parent_programme,
               f.parent_perimetre,
               f.parent_itemid,
               f.parent_name,
               f.child_itemid,
               f.child_name,
               f.child_categorie,
               f.child_unite,
               f.coef_bom,
               f.qty_parent_produite,
               f.conso_theorique,
               f.conso_reelle,
               f.ecart_brut,
               f.ecart_pct,
               ({type_expr})                    AS type_ecart,
               f.statut_ligne,
               f.ecart_equivalent_produit,
               f.child_cout_standard,
               f.ecart_valorise,
               abs(f.ecart_valorise)            AS ecart_valorise_absolu,
               COUNT(*) OVER ()                 AS _total
        FROM {fact} f
        WHERE {predicat}
        ORDER BY {ordre}, f.semaine_debut DESC, f.prod_id, f.child_itemid
        LIMIT %(limite)s OFFSET %(decalage)s
    """,
    "composants": """
        SELECT f.child_itemid,
               MAX(f.child_name)                AS child_name,
               MAX(f.child_categorie)           AS child_categorie,
               f.parent_programme,
               f.parent_perimetre,
               MIN(f.coef_bom)                  AS coef_bom,
               bool_and(f.is_coef_uniforme)     AS is_coef_uniforme,
               {mesures},
               CASE WHEN SUM(f.conso_theorique) > 0
                    THEN SUM(f.ecart_brut) / SUM(f.conso_theorique) * 100 END
                                                AS ecart_pct_global,
               COUNT(*) OVER ()                 AS _total
        FROM {fact} f
        WHERE {predicat}
        GROUP BY f.child_itemid, f.parent_programme, f.parent_perimetre
        ORDER BY {ordre}, f.child_itemid
        LIMIT %(limite)s OFFSET %(decalage)s
    """,
    "programmes": """
        SELECT f.semaine_debut,
               f.annee,
               f.semaine,
               {semaine_libelle}                AS semaine_libelle,
               f.parent_programme,
               {mesures},
               CASE WHEN COUNT(*) > 0
                    THEN (COUNT(*) - COUNT(*) FILTER (WHERE ({type_expr}) <> 'Conforme'))::numeric
                         / COUNT(*) * 100 END   AS taux_conformite,
               COUNT(*) OVER ()                 AS _total
        FROM {fact} f
        WHERE {predicat}
        GROUP BY f.semaine_debut, f.annee, f.semaine, f.parent_programme
        ORDER BY {ordre}, f.semaine_debut DESC, f.parent_programme
        LIMIT %(limite)s OFFSET %(decalage)s
    """,
    "perimetres": """
        WITH production AS (
            -- Même précaution que pour la grille « parents » : la quantité
            -- produite est répétée sur chaque ligne de composant. On la
            -- dédoublonne par (périmètre, parent, semaine) avant de sommer,
            -- sinon la production serait multipliée par le nombre de composants.
            SELECT parent_perimetre, semaine_debut, SUM(qty_semaine) AS qty_produite
            FROM (
                SELECT f.parent_perimetre,
                       f.semaine_debut,
                       f.parent_itemid,
                       MAX(f.qty_parent_produite) AS qty_semaine
                FROM {fact} f
                WHERE {predicat}
                GROUP BY f.parent_perimetre, f.semaine_debut, f.parent_itemid
            ) parparent
            GROUP BY parent_perimetre, semaine_debut
        )
        SELECT f.semaine_debut,
               f.annee,
               f.semaine,
               {semaine_libelle}                AS semaine_libelle,
               f.parent_perimetre,
               MAX(f.parent_programme)          AS parent_programme,
               MAX(p.qty_produite)              AS qty_produite,
               {mesures},
               CASE WHEN COUNT(*) > 0
                    THEN (COUNT(*) - COUNT(*) FILTER (WHERE ({type_expr}) <> 'Conforme'))::numeric
                         / COUNT(*) * 100 END   AS taux_conformite,
               COUNT(*) OVER ()                 AS _total
        FROM {fact} f
        LEFT JOIN production p
               ON p.parent_perimetre = f.parent_perimetre
              AND p.semaine_debut    = f.semaine_debut
        WHERE {predicat}
        GROUP BY f.semaine_debut, f.annee, f.semaine, f.parent_perimetre
        ORDER BY {ordre}, f.semaine_debut DESC, f.parent_perimetre
        LIMIT %(limite)s OFFSET %(decalage)s
    """,
    "parents": """
        WITH production AS (
            -- La production du parent est répétée sur chaque ligne de composant :
            -- la sommer directement la multiplierait par le nombre de composants.
            -- On prend donc le maximum par semaine avant de sommer sur la période.
            SELECT parent_itemid, SUM(qty_semaine) AS qty_produite
            FROM (
                SELECT f.parent_itemid,
                       f.semaine_debut,
                       MAX(f.qty_parent_produite) AS qty_semaine
                FROM {fact} f
                WHERE {predicat}
                GROUP BY f.parent_itemid, f.semaine_debut
            ) parsemaine
            GROUP BY parent_itemid
        )
        SELECT f.parent_itemid,
               MAX(f.parent_name)               AS parent_name,
               f.parent_programme,
               f.parent_perimetre,
               MAX(p.qty_produite)              AS qty_produite,
               {mesures},
               COUNT(*) OVER ()                 AS _total
        FROM {fact} f
        LEFT JOIN production p ON p.parent_itemid = f.parent_itemid
        WHERE {predicat}
        GROUP BY f.parent_itemid, f.parent_programme, f.parent_perimetre
        ORDER BY {ordre}, f.parent_itemid
        LIMIT %(limite)s OFFSET %(decalage)s
    """,
}
