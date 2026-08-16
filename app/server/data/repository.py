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
from collections.abc import Iterator
from datetime import date
from typing import Any

from psycopg.rows import dict_row

from app.server.core.errors import RequeteInvalideError
from app.server.core.lakebase import LakebasePool
from app.server.domain.dictionary import GRILLES
from app.server.domain.filters import Filtres, construire_predicat, expression_type_ecart
from app.server.domain.metrics import AgregatBrut

LOGGER = logging.getLogger("backflush.repository")

FACT = "fact_ecart_backflush"

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

#: Libellé de semaine ISO lisible (2026-S14), calculé en base pour rester
#: cohérent entre la grille, l'export et l'assistant.
SEMAINE_LIBELLE = "f.annee::text || '-S' || lpad(f.semaine::text, 2, '0')"

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
        """Une ligne par semaine — alimente la tendance et l'histogramme du slicer."""
        predicat = construire_predicat(filtres)
        sql = f"""
            SELECT f.semaine_debut,
                   f.annee,
                   f.semaine,
                   {SEMAINE_LIBELLE} AS semaine_libelle,
                   {MESURES.format(type_expr=expression_type_ecart())}
            FROM {FACT} f
            WHERE {predicat.sql}
            GROUP BY f.semaine_debut, f.annee, f.semaine
            ORDER BY f.semaine_debut
        """
        return self._fetch(sql, predicat.params)

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


    def totaux(self, filtres: Filtres) -> dict[str, Any]:
        """Totaux de la sélection ENTIÈRE, pour le pied de page des grilles.

        Ce ne sont pas les totaux de la page affichée : additionner cinquante
        lignes sur dix mille tromperait plus qu'il n'informerait. Les
        dénombrements distincts (parents, composants) ne sont pas non plus la
        somme des colonnes — ils sont recalculés sur toute la sélection, sans
        quoi un parent présent dans trois semaines serait compté trois fois.

        La quantité produite est dédoublonnée par (parent, semaine) avant
        sommation : elle est répétée sur chaque ligne de composant.
        """
        predicat = construire_predicat(filtres)
        sql = f"""
            WITH production AS (
                SELECT f.parent_itemid, f.semaine_debut,
                       MAX(f.qty_parent_produite) AS qty_semaine
                FROM {FACT} f
                WHERE {predicat.sql}
                GROUP BY f.parent_itemid, f.semaine_debut
            )
            SELECT {MESURES.format(type_expr=expression_type_ecart())},
                   (SELECT COALESCE(SUM(qty_semaine), 0) FROM production) AS qty_produite,
                   CASE WHEN COUNT(*) > 0
                        THEN (COUNT(*) - COUNT(*) FILTER (WHERE ({expression_type_ecart()}) <> 'Conforme'))::numeric
                             / COUNT(*) * 100 END AS taux_conformite,
                   CASE WHEN SUM(f.conso_theorique) > 0
                        THEN SUM(f.ecart_brut) / SUM(f.conso_theorique) * 100 END
                                                  AS ecart_pct_global
            FROM {FACT} f
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
        return {"production": production, "ecarts": ecarts}

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
        predicat = construire_predicat(filtres)
        params = {**predicat.params, "limite": taille, "decalage": (page - 1) * taille}

        # NULLS LAST systématique : une valeur manquante ne doit jamais occuper
        # la tête d'un classement par impact.
        ordre = f"{expressions[tri]} {sens.upper()} NULLS LAST"
        sql = _SQL_GRILLES[cle].format(
            fact=FACT,
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

        predicat = construire_predicat(filtres)
        params = {**predicat.params, "limite": _borne(limite, 1, 1_000_000), "decalage": 0}
        sql = _SQL_GRILLES[cle].format(
            fact=FACT,
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
        return self._fetch(
            "SELECT n.child_itemid, a.item_name AS child_name, a.categorie, "
            "       n.child_qty AS coef_bom, n.child_unitid AS unite, a.std_cost_price "
            "FROM dim_nomenclature n "
            "LEFT JOIN dim_article a ON a.item_id = n.child_itemid "
            "WHERE n.parent_itemid = %(parent)s "
            "ORDER BY n.child_qty DESC",
            {"parent": parent_itemid},
        )

    def parents_du_composant(self, child_itemid: str) -> list[dict[str, Any]]:
        return self._fetch(
            "SELECT n.parent_itemid, a.item_name AS parent_name, a.programme, "
            "       n.child_qty AS coef_bom "
            "FROM dim_nomenclature n "
            "LEFT JOIN dim_article a ON a.item_id = n.parent_itemid "
            "WHERE n.child_itemid = %(child)s "
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
