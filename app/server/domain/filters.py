"""Modèle de filtres et construction des prédicats SQL.

Toute la sélection de données de l'application passe par :class:`Filtres`. Le
frontend n'écrit jamais de SQL : il envoie un objet de filtres, validé par
Pydantic, que :func:`construire_predicat` traduit en fragment ``WHERE``
**entièrement paramétré**. Aucune valeur saisie par l'utilisateur n'est
concaténée dans la requête.

Le type d'écart est recalculé à la volée à partir du seuil de conformité, et non
lu dans la colonne ``type_ecart`` figée à l'ingestion : le seuil est un réglage
métier que le key-user doit pouvoir déplacer sans relancer un job.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

#: Valeurs autorisées pour le type d'écart (recalculé dynamiquement).
TypeEcart = Literal["Non-consommation", "Surconsommation", "Conforme"]

#: Valeurs autorisées pour le statut de ligne (figé à l'ingestion).
StatutLigne = Literal["Nominal", "Hors nomenclature", "Sans consommation"]

#: Longueur maximale d'un terme de recherche — borne le coût du trigramme.
RECHERCHE_MAX = 80

#: Nombre maximal d'éléments dans un filtre multi-valeurs.
LISTE_MAX = 500

#: Expression de recherche plein texte. DOIT rester identique à
#: ``src.jobs.lakebase_schema.SEARCH_EXPRESSION`` pour que l'index GIN trigramme
#: soit retenu par le planificateur.
SEARCH_EXPRESSION_TEMPLATE = (
    "(coalesce({a}.child_itemid,'') || ' ' || coalesce({a}.child_name,'') || ' ' "
    "|| coalesce({a}.parent_itemid,'') || ' ' || coalesce({a}.parent_name,''))"
)


def expression_type_ecart(alias: str = "f") -> str:
    """Expression SQL du type d'écart.

    Deux tolérances, combinées en OU :

    * ``%(seuil)s`` — tolérance **absolue**, en unités du composant. C'est la
      règle métier de référence (±0,5 unité par défaut).
    * ``%(seuil_pct)s`` — tolérance **relative**, en % du théorique, optionnelle.
      Indispensable dès que les volumes sont hétérogènes : 0,5 unité sur une vis
      consommée par dizaines de milliers n'a pas le même sens que 0,5 kg de
      résine. Vaut ``NULL`` (donc inactive) tant que le key-user ne l'active pas.

    Les deux paramètres sont TOUJOURS présents dans le jeu de paramètres, même
    inactifs : une expression SQL stable se met mieux en cache côté planificateur.
    """
    # Les transtypages explicites ne sont pas décoratifs : sur un paramètre lié à
    # NULL, Postgres ne peut pas inférer le type et rejette « $n IS NOT NULL ».
    return (
        f"CASE "
        f"WHEN abs({alias}.ecart_brut) <= %(seuil)s::numeric THEN 'Conforme' "
        f"WHEN %(seuil_pct)s::numeric IS NOT NULL AND {alias}.ecart_pct IS NOT NULL "
        f"     AND abs({alias}.ecart_pct) <= %(seuil_pct)s::numeric THEN 'Conforme' "
        f"WHEN {alias}.ecart_brut > 0 THEN 'Non-consommation' "
        f"ELSE 'Surconsommation' END"
    )


class Filtres(BaseModel):
    """Sélection appliquée à toutes les vues analytiques.

    Toutes les bornes sont **inclusives**. ``date_debut``/``date_fin`` portent
    sur ``semaine_debut`` (le lundi de la semaine ISO), jamais sur la date de
    mouvement : la maille d'analyse est la semaine.
    """

    date_debut: date | None = None
    date_fin: date | None = None

    programmes: list[str] = Field(default_factory=list, max_length=LISTE_MAX)
    categories: list[str] = Field(default_factory=list, max_length=LISTE_MAX)
    types_ecart: list[TypeEcart] = Field(default_factory=list)
    statuts_ligne: list[StatutLigne] = Field(default_factory=list)
    parents: list[str] = Field(default_factory=list, max_length=LISTE_MAX)
    composants: list[str] = Field(default_factory=list, max_length=LISTE_MAX)

    recherche: str | None = Field(default=None, max_length=RECHERCHE_MAX)

    seuil_conformite: float = Field(
        default=0.5, ge=0, le=1_000_000,
        description="Tolérance absolue en unités : |écart| <= seuil ⇒ ligne conforme.",
    )
    seuil_pct: float | None = Field(
        default=None, ge=0, le=100,
        description=(
            "Tolérance relative en % du théorique, combinée en OU avec la tolérance "
            "absolue. Inactive par défaut, conformément à la règle métier de référence."
        ),
    )
    impact_min: float | None = Field(
        default=None, ge=0,
        description="Ne conserver que les lignes dont |écart valorisé| atteint ce montant.",
    )
    coef_uniforme_uniquement: bool = Field(
        default=False,
        description="Ne conserver que les lignes dont l'équivalent produit est calculable.",
    )
    exclure_conforme: bool = Field(
        default=False, description="Masquer les lignes conformes (bruit de mesure)."
    )

    @field_validator("recherche")
    @classmethod
    def _nettoyer_recherche(cls, valeur: str | None) -> str | None:
        if valeur is None:
            return None
        nettoye = valeur.strip()
        return nettoye or None

    @field_validator("programmes", "categories", "parents", "composants")
    @classmethod
    def _nettoyer_liste(cls, valeurs: list[str]) -> list[str]:
        # Dédoublonnage en conservant l'ordre : un doublon allongerait le tableau
        # transmis à Postgres sans changer le résultat.
        vus: dict[str, None] = {}
        for valeur in valeurs:
            propre = valeur.strip()
            if propre:
                vus.setdefault(propre, None)
        return list(vus)

    @model_validator(mode="after")
    def _verifier_bornes(self) -> Filtres:
        if self.date_debut and self.date_fin and self.date_debut > self.date_fin:
            raise ValueError("date_debut doit précéder date_fin.")
        return self

    # -- Utilitaires métier ------------------------------------------------
    def periode_precedente(self) -> Filtres:
        """Mêmes filtres, décalés sur la période immédiatement antérieure.

        Sert au calcul des variations de KPI. La période précédente a exactement
        la même longueur ; sans dates bornées, la comparaison n'a pas de sens et
        la méthode retourne une période vide.
        """
        if not (self.date_debut and self.date_fin):
            return self.model_copy(update={"date_debut": None, "date_fin": None})
        duree = self.date_fin - self.date_debut
        return self.model_copy(update={
            "date_debut": self.date_debut - duree - timedelta(days=7),
            "date_fin": self.date_debut - timedelta(days=7),
        })

    def sans_dates(self) -> Filtres:
        """Mêmes filtres, sans bornes temporelles (pour l'histogramme du slicer)."""
        return self.model_copy(update={"date_debut": None, "date_fin": None})


@dataclass(frozen=True)
class Predicat:
    """Fragment ``WHERE`` paramétré."""

    #: Conditions jointes par ``AND``. Toujours au moins ``TRUE``.
    sql: str
    #: Paramètres nommés à passer tels quels à psycopg.
    params: dict[str, Any]


def construire_predicat(filtres: Filtres, alias: str = "f") -> Predicat:
    """Traduit :class:`Filtres` en fragment SQL paramétré pour la table de détail.

    :param alias: alias de ``fact_ecart_backflush`` dans la requête appelante.
    """
    conditions: list[str] = []
    params: dict[str, Any] = {
        "seuil": filtres.seuil_conformite,
        "seuil_pct": filtres.seuil_pct,
    }

    if filtres.date_debut:
        conditions.append(f"{alias}.semaine_debut >= %(date_debut)s")
        params["date_debut"] = filtres.date_debut
    if filtres.date_fin:
        conditions.append(f"{alias}.semaine_debut <= %(date_fin)s")
        params["date_fin"] = filtres.date_fin

    for champ, colonne, cle in (
        (filtres.programmes, "parent_programme", "programmes"),
        (filtres.categories, "child_categorie", "categories"),
        (filtres.parents, "parent_itemid", "parents"),
        (filtres.composants, "child_itemid", "composants"),
        (filtres.statuts_ligne, "statut_ligne", "statuts_ligne"),
    ):
        if champ:
            conditions.append(f"{alias}.{colonne} = ANY(%({cle})s)")
            params[cle] = list(champ)

    if filtres.types_ecart:
        conditions.append(f"({expression_type_ecart(alias)}) = ANY(%(types_ecart)s)")
        params["types_ecart"] = list(filtres.types_ecart)

    if filtres.exclure_conforme:
        conditions.append(f"({expression_type_ecart(alias)}) <> 'Conforme'")

    if filtres.impact_min is not None:
        conditions.append(f"abs({alias}.ecart_valorise) >= %(impact_min)s")
        params["impact_min"] = filtres.impact_min

    if filtres.coef_uniforme_uniquement:
        conditions.append(f"{alias}.is_coef_uniforme")

    if filtres.recherche:
        conditions.append(
            f"{SEARCH_EXPRESSION_TEMPLATE.format(a=alias)} ILIKE %(recherche)s"
        )
        # Le motif est construit ici, jamais côté client : les métacaractères
        # LIKE saisis par l'utilisateur sont neutralisés.
        params["recherche"] = f"%{_echapper_like(filtres.recherche)}%"

    return Predicat(sql=" AND ".join(conditions) if conditions else "TRUE", params=params)


def _echapper_like(terme: str) -> str:
    """Neutralise ``%``, ``_`` et ``\\`` pour qu'ils soient cherchés littéralement."""
    return terme.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
