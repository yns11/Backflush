"""Paramétrage du key-user : exclusions d'articles et surcharges de nomenclature.

**Seul module de l'application qui écrit dans Lakebase.** Toutes les autres
requêtes sont en lecture, garanties par le mode ``default_transaction_read_only``
du pool ; ce module lève la garantie transaction par transaction, via
``LakebasePool.connexion_ecriture()``.

Deux arbitrages sont possibles, et ils ne se valent pas :

* **Exclure une référence** — elle disparaît de l'analyse. À réserver aux cas où
  la référence n'a rien à y faire (consommable de production, article de
  transit, référence de test). Ce n'est pas un moyen de faire disparaître un
  écart gênant : l'écran affiche donc, en face de chaque référence, l'impact
  qu'elle porte et le nombre de semaines concernées.
* **Corriger une ligne de nomenclature** — la désactiver, ou substituer son
  coefficient. C'est la réponse aux nomenclatures fausses ou obsolètes, qui
  produisent un écart entièrement fictif.

Dans les deux cas l'arbitrage est **réversible** et **motivé** : un paramétrage
anonyme et sans justification devient, six mois plus tard, une anomalie que
personne n'ose toucher.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from psycopg.rows import dict_row

from app.server.core.errors import RequeteInvalideError
from app.server.core.lakebase import LakebasePool
from app.server.data.faits import TABLE_ARTICLE_EXCLU, TABLE_NOMENCLATURE_PARAM

LOGGER = logging.getLogger("backflush.parametrage")

#: Bornes de pagination des écrans de paramétrage.
TAILLE_MAX = 500

#: Nombre maximal de lignes traitées en une action par lot. Une action non bornée
#: transformerait un clic distrait en modification de tout le référentiel.
LOT_MAX = 1_000

#: Tris autorisés — liste blanche stricte, comme pour les grilles analytiques.
TRI_ARTICLES = {
    "item_id": "a.item_id",
    "item_name": "a.item_name",
    "categorie": "a.categorie",
    "programme": "a.programme",
    "perimetre": "a.perimetre",
    "type_produit": "a.type_produit",
    "std_cost_price": "a.std_cost_price",
    "impact_absolu": "impact_absolu",
    "nb_semaines_en_ecart": "nb_semaines_en_ecart",
    "exclu": "exclu",
}

TRI_NOMENCLATURE = {
    "parent_itemid": "n.parent_itemid",
    "child_itemid": "n.child_itemid",
    "parent_name": "parent_name",
    "child_name": "child_name",
    "perimetre": "perimetre",
    "coef_origine": "n.child_qty",
    "coef_effectif": "coef_effectif",
    "active": "active",
    "modifie_le": "o.modifie_le",
}


class DepotParametrage:
    """Lecture et écriture des tables de paramétrage."""

    def __init__(self, pool: LakebasePool) -> None:
        self._pool = pool

    # -- Lecture -----------------------------------------------------------
    def articles(
        self,
        *,
        recherche: str | None = None,
        etat: str = "tous",
        tri: str = "impact_absolu",
        sens: str = "desc",
        page: int = 1,
        taille: int = 50,
    ) -> dict[str, Any]:
        """Base article, enrichie de l'impact que porte chaque référence.

        L'impact vient de l'agrégat par composant, pas de la table de détail :
        parcourir sept millions de lignes pour afficher une page de cinquante
        références serait hors de proportion. L'agrégat, lui, tient en quelques
        milliers de lignes.
        """
        ordre = self._ordre(TRI_ARTICLES, tri, sens)
        conditions, params = self._filtre_recherche(
            recherche, ("a.item_id", "a.item_name", "a.categorie")
        )
        if etat == "exclus":
            conditions.append("x.item_id IS NOT NULL")
        elif etat == "inclus":
            conditions.append("x.item_id IS NULL")
        elif etat != "tous":
            raise RequeteInvalideError(f"État inconnu : {etat}.")

        taille, page, params = self._pagination(taille, page, params)
        sql = f"""
            WITH impact AS (
                SELECT child_itemid,
                       SUM(abs(ecart_valorise_total)) AS impact_absolu,
                       SUM(nb_semaines_en_ecart)      AS nb_semaines_en_ecart
                FROM agg_ecart_composant
                GROUP BY child_itemid
            )
            SELECT a.item_id,
                   a.item_name,
                   a.categorie,
                   a.programme,
                   a.perimetre,
                   a.type_produit,
                   a.std_cost_price,
                   a.std_unit,
                   COALESCE(i.impact_absolu, 0)       AS impact_absolu,
                   COALESCE(i.nb_semaines_en_ecart, 0) AS nb_semaines_en_ecart,
                   (x.item_id IS NOT NULL)            AS exclu,
                   x.motif,
                   x.modifie_par,
                   x.modifie_le,
                   COUNT(*) OVER ()                   AS _total
            FROM dim_article a
            LEFT JOIN {TABLE_ARTICLE_EXCLU} x ON x.item_id = a.item_id
            LEFT JOIN impact i                ON i.child_itemid = a.item_id
            WHERE {" AND ".join(conditions)}
            ORDER BY {ordre}, a.item_id
            LIMIT %(limite)s OFFSET %(decalage)s
        """
        return self._page(sql, params, page, taille)

    def nomenclature(
        self,
        *,
        recherche: str | None = None,
        etat: str = "tous",
        tri: str = "parent_itemid",
        sens: str = "asc",
        page: int = 1,
        taille: int = 50,
    ) -> dict[str, Any]:
        """Nomenclature active, avec sa surcharge éventuelle.

        Le coefficient d'origine et le coefficient effectif sont exposés
        côte à côte : une correction qu'on ne peut pas comparer à la valeur
        d'origine n'est plus vérifiable.
        """
        ordre = self._ordre(TRI_NOMENCLATURE, tri, sens)
        conditions, params = self._filtre_recherche(
            recherche, ("n.parent_itemid", "n.child_itemid", "pa.item_name", "ca.item_name")
        )
        if etat == "surchargees":
            conditions.append("o.parent_itemid IS NOT NULL")
        elif etat == "desactivees":
            conditions.append("o.active IS FALSE")
        elif etat != "tous":
            raise RequeteInvalideError(f"État inconnu : {etat}.")

        taille, page, params = self._pagination(taille, page, params)
        sql = f"""
            SELECT n.parent_itemid,
                   n.child_itemid,
                   n.bomid,
                   pa.item_name                       AS parent_name,
                   ca.item_name                       AS child_name,
                   pa.perimetre                       AS perimetre,
                   ca.std_unit                        AS unite,
                   n.child_qty                        AS coef_origine,
                   COALESCE(o.coef_bom, n.child_qty)  AS coef_effectif,
                   COALESCE(o.active, TRUE)           AS active,
                   (o.coef_bom IS NOT NULL)           AS coef_surcharge,
                   o.motif,
                   o.modifie_par,
                   o.modifie_le,
                   COUNT(*) OVER ()                   AS _total
            FROM dim_nomenclature n
            LEFT JOIN {TABLE_NOMENCLATURE_PARAM} o
                   ON o.parent_itemid = n.parent_itemid AND o.child_itemid = n.child_itemid
            LEFT JOIN dim_article pa ON pa.item_id = n.parent_itemid
            LEFT JOIN dim_article ca ON ca.item_id = n.child_itemid
            WHERE {" AND ".join(conditions)}
            ORDER BY {ordre}, n.parent_itemid, n.child_itemid
            LIMIT %(limite)s OFFSET %(decalage)s
        """
        return self._page(sql, params, page, taille)

    def resume(self) -> dict[str, Any]:
        """Compte des arbitrages en vigueur — affiché en permanence dans l'app.

        Un paramétrage actif mais invisible produit des chiffres que personne ne
        sait expliquer. Ce résumé est la contrepartie du droit d'exclure.
        """
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM {TABLE_ARTICLE_EXCLU}")
            exclus = (cur.fetchone() or {}).get("n", 0)
            cur.execute(
                f"SELECT COUNT(*) FILTER (WHERE active IS FALSE)   AS desactivees, "
                f"       COUNT(*) FILTER (WHERE coef_bom IS NOT NULL) AS corrigees "
                f"FROM {TABLE_NOMENCLATURE_PARAM}"
            )
            nomenclature = cur.fetchone() or {}
        return {
            "articles_exclus": int(exclus or 0),
            "lignes_desactivees": int(nomenclature.get("desactivees") or 0),
            "lignes_corrigees": int(nomenclature.get("corrigees") or 0),
        }

    # -- Écriture ----------------------------------------------------------
    def exclure_articles(
        self, item_ids: list[str], *, motif: str | None, utilisateur: str | None
    ) -> int:
        """Exclut des références, ou met à jour le motif de celles déjà exclues."""
        valeurs = self._lot(item_ids)
        with self._pool.connexion_ecriture() as conn:
            conn.execute(
                f"""
                INSERT INTO {TABLE_ARTICLE_EXCLU} (item_id, motif, modifie_par, modifie_le)
                SELECT item, %(motif)s, %(par)s, %(le)s FROM unnest(%(items)s::text[]) AS item
                ON CONFLICT (item_id) DO UPDATE
                    SET motif = EXCLUDED.motif,
                        modifie_par = EXCLUDED.modifie_par,
                        modifie_le = EXCLUDED.modifie_le
                """,
                {"items": valeurs, "motif": motif, "par": utilisateur, "le": _maintenant()},
            )
        LOGGER.info("%d référence(s) exclue(s) par %s.", len(valeurs), utilisateur or "inconnu")
        return len(valeurs)

    def reintegrer_articles(self, item_ids: list[str]) -> int:
        """Réintègre des références dans l'analyse."""
        valeurs = self._lot(item_ids)
        with self._pool.connexion_ecriture() as conn:
            conn.execute(
                f"DELETE FROM {TABLE_ARTICLE_EXCLU} WHERE item_id = ANY(%(items)s::text[])",
                {"items": valeurs},
            )
        LOGGER.info("%d référence(s) réintégrée(s).", len(valeurs))
        return len(valeurs)

    def surcharger_nomenclature(
        self,
        lignes: list[tuple[str, str]],
        *,
        active: bool,
        coef_bom: float | None,
        motif: str | None,
        utilisateur: str | None,
    ) -> int:
        """Désactive ou corrige des lignes de nomenclature.

        ``coef_bom`` à ``None`` conserve le coefficient d'origine : c'est ce qui
        permet de désactiver une ligne sans avoir à en inventer un.
        """
        if coef_bom is not None and coef_bom <= 0:
            raise RequeteInvalideError(
                "Un coefficient de nomenclature doit être strictement positif : "
                "à zéro, la consommation théorique disparaît sans que la ligne "
                "soit pour autant retirée de l'analyse. Désactivez-la plutôt."
            )
        couples = self._lot(lignes)
        parents = [parent for parent, _ in couples]
        enfants = [enfant for _, enfant in couples]
        with self._pool.connexion_ecriture() as conn:
            conn.execute(
                f"""
                INSERT INTO {TABLE_NOMENCLATURE_PARAM}
                       (parent_itemid, child_itemid, active, coef_bom, motif, modifie_par, modifie_le)
                SELECT p, c, %(active)s, %(coef)s::numeric, %(motif)s, %(par)s, %(le)s
                FROM unnest(%(parents)s::text[], %(enfants)s::text[]) AS t(p, c)
                ON CONFLICT (parent_itemid, child_itemid) DO UPDATE
                    SET active = EXCLUDED.active,
                        coef_bom = EXCLUDED.coef_bom,
                        motif = EXCLUDED.motif,
                        modifie_par = EXCLUDED.modifie_par,
                        modifie_le = EXCLUDED.modifie_le
                """,
                {
                    "parents": parents, "enfants": enfants, "active": active,
                    "coef": coef_bom, "motif": motif,
                    "par": utilisateur, "le": _maintenant(),
                },
            )
        LOGGER.info(
            "%d ligne(s) de nomenclature surchargée(s) par %s (active=%s, coef=%s).",
            len(couples), utilisateur or "inconnu", active, coef_bom,
        )
        return len(couples)

    def reinitialiser_nomenclature(self, lignes: list[tuple[str, str]]) -> int:
        """Supprime la surcharge : la ligne redevient celle de l'ERP."""
        couples = self._lot(lignes)
        with self._pool.connexion_ecriture() as conn:
            conn.execute(
                f"""
                DELETE FROM {TABLE_NOMENCLATURE_PARAM} o
                USING unnest(%(parents)s::text[], %(enfants)s::text[]) AS t(p, c)
                WHERE o.parent_itemid = t.p AND o.child_itemid = t.c
                """,
                {
                    "parents": [parent for parent, _ in couples],
                    "enfants": [enfant for _, enfant in couples],
                },
            )
        LOGGER.info("%d surcharge(s) de nomenclature levée(s).", len(couples))
        return len(couples)

    # -- Interne -----------------------------------------------------------
    @staticmethod
    def _ordre(autorises: dict[str, str], tri: str, sens: str) -> str:
        if tri not in autorises:
            raise RequeteInvalideError(f"Tri non autorisé sur « {tri} ».")
        if sens not in ("asc", "desc"):
            raise RequeteInvalideError("Le sens de tri doit valoir « asc » ou « desc ».")
        return f"{autorises[tri]} {sens.upper()} NULLS LAST"

    @staticmethod
    def _filtre_recherche(
        recherche: str | None, colonnes: tuple[str, ...]
    ) -> tuple[list[str], dict[str, Any]]:
        conditions = ["TRUE"]
        params: dict[str, Any] = {}
        terme = (recherche or "").strip()
        if terme:
            clauses = " OR ".join(f"coalesce({colonne},'') ILIKE %(recherche)s" for colonne in colonnes)
            conditions.append(f"({clauses})")
            params["recherche"] = f"%{terme}%"
        return conditions, params

    @staticmethod
    def _pagination(
        taille: int, page: int, params: dict[str, Any]
    ) -> tuple[int, int, dict[str, Any]]:
        taille = max(1, min(int(taille), TAILLE_MAX))
        page = max(1, int(page))
        return taille, page, {**params, "limite": taille, "decalage": (page - 1) * taille}

    def _page(
        self, sql: str, params: dict[str, Any], page: int, taille: int
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            lignes = [dict(ligne) for ligne in cur.fetchall()]
        total = int(lignes[0]["_total"]) if lignes else 0
        for ligne in lignes:
            ligne.pop("_total", None)
        return {
            "lignes": lignes,
            "total": total,
            "page": page,
            "taille": taille,
            "nb_pages": (total + taille - 1) // taille if taille else 0,
        }

    @staticmethod
    def _lot(elements: list[Any]) -> list[Any]:
        if not elements:
            raise RequeteInvalideError("Aucune ligne sélectionnée.")
        if len(elements) > LOT_MAX:
            raise RequeteInvalideError(
                f"Action limitée à {LOT_MAX} lignes ; {len(elements)} demandées."
            )
        # Dédoublonnage en conservant l'ordre : un doublon ferait échouer un
        # INSERT ... ON CONFLICT (« ligne affectée deux fois »).
        vus: dict[Any, None] = {}
        for element in elements:
            vus.setdefault(element, None)
        return list(vus)


def _maintenant() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)
