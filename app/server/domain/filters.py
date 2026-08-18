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

#: Cycle de vie d'un ordre de fabrication D365 (`ProdStatus`), dans l'ORDRE du
#: cycle et non dans l'ordre alphabétique.
#:
#: Sert à classer les options du filtre « Statut OF ». Un tri alphabétique
#: donnerait « Annulé, Aucun, Clôturé, Créé, Démarré… » : une liste où l'on ne
#: peut pas lire d'un coup d'œil ce qui est terminé et ce qui ne l'est pas,
#: alors que c'est la seule question que ce filtre sert à poser.
#:
#: La liste est un ORDRE d'affichage, pas une contrainte de validation : un
#: statut absent (valeur D365 non traduite, remontée par le contrôle
#: `of_statut_inconnu`) reste sélectionnable et est simplement classé en fin.
CYCLE_STATUT_OF: tuple[str, ...] = (
    "Aucun",
    "Créé",
    "Estimé",
    "Planifié",
    "Lancé",
    "Démarré",
    "Déclaré terminé",
    "Clôturé",
    "Annulé",
)


def rang_statut_of(statut: str) -> tuple[int, str]:
    """Clé de tri d'un statut d'OF : sa place dans le cycle, puis son libellé."""
    try:
        return (CYCLE_STATUT_OF.index(statut), "")
    except ValueError:
        return (len(CYCLE_STATUT_OF), statut)


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

    #: Semaines retenues, désignées par leur lundi. Restriction ÉNUMÉRÉE, en
    #: complément des bornes : celles-ci décrivent un intervalle continu, et ne
    #: savent donc pas exprimer « ces semaines-là, mais pas celle du milieu ».
    #:
    #: C'est ce qu'exige le tiroir de contexte, qui sépare les lignes composant
    #: le chiffre cliqué (la semaine du clic) de celles qui l'éclairent (les
    #: autres semaines des mêmes ordres). Sans énumération, les deux blocs se
    #: recouvriraient et le premier ne se sommerait plus au chiffre affiché.
    #:
    #: Vide = aucune restriction, comme tous les autres filtres de liste. Se
    #: combine en ET avec ``date_debut``/``date_fin`` lorsque les deux sont
    #: posés.
    semaines_debut: list[date] = Field(default_factory=list, max_length=LISTE_MAX)

    programmes: list[str] = Field(default_factory=list, max_length=LISTE_MAX)
    #: Périmètres — les lignes de production. Axe d'analyse plus fin que le
    #: programme, et seul niveau où le coefficient de nomenclature est homogène.
    perimetres: list[str] = Field(default_factory=list, max_length=LISTE_MAX)
    categories: list[str] = Field(default_factory=list, max_length=LISTE_MAX)
    types_ecart: list[TypeEcart] = Field(default_factory=list)
    statuts_ligne: list[StatutLigne] = Field(default_factory=list)
    parents: list[str] = Field(default_factory=list, max_length=LISTE_MAX)
    composants: list[str] = Field(default_factory=list, max_length=LISTE_MAX)

    #: Axe de l'ordre de fabrication. Ces deux critères n'existent QUE sur
    #: ``fact_ecart_of`` : la table de détail à la maille parent a perdu l'OF au
    #: moment de son GROUP BY. :func:`construire_predicat` les ignore donc hors
    #: de cette source — voir son paramètre ``axe_of``.
    ofs: list[str] = Field(default_factory=list, max_length=LISTE_MAX)
    statuts_of: list[str] = Field(default_factory=list, max_length=LISTE_MAX)

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

    @field_validator(
        "programmes", "perimetres", "categories", "parents", "composants",
        "ofs", "statuts_of",
    )
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
            return self.model_copy(
                update={"date_debut": None, "date_fin": None, "semaines_debut": []}
            )
        duree = self.date_fin - self.date_debut
        return self.model_copy(update={
            "date_debut": self.date_debut - duree - timedelta(days=7),
            "date_fin": self.date_debut - timedelta(days=7),
            # La liste énumérée désigne des semaines de la période COURANTE :
            # la reporter telle quelle sur la période précédente ne
            # sélectionnerait rien, et la variation afficherait une chute de
            # 100 % là où il n'y a qu'une incohérence de bornes.
            "semaines_debut": [],
        })

    def sans_dates(self) -> Filtres:
        """Mêmes filtres, sans AUCUNE borne temporelle (histogramme du slicer).

        La liste énumérée de semaines en est une : la laisser réduirait
        l'histogramme aux seules semaines retenues, alors qu'il est là pour
        montrer ce que la sélection écarte.
        """
        return self.model_copy(
            update={"date_debut": None, "date_fin": None, "semaines_debut": []}
        )


@dataclass(frozen=True)
class Predicat:
    """Fragment ``WHERE`` paramétré."""

    #: Conditions jointes par ``AND``. Toujours au moins ``TRUE``.
    sql: str
    #: Paramètres nommés à passer tels quels à psycopg.
    params: dict[str, Any]


def construire_predicat(
    filtres: Filtres, alias: str = "f", *, axe_of: bool = False
) -> Predicat:
    """Traduit :class:`Filtres` en fragment SQL paramétré pour la table de détail.

    :param alias: alias de la table de faits dans la requête appelante.
    :param axe_of: la source lue porte-t-elle l'axe de l'ordre de fabrication ?

    ``axe_of`` gouverne les deux critères ``ofs`` et ``statuts_of``, et rien
    d'autre. Il vaut ``False`` par défaut parce que la source par défaut est
    ``fact_ecart_backflush``, où les colonnes ``prod_id`` et ``prod_statut``
    n'existent pas : les référencer y ferait échouer la requête avec une erreur
    de colonne inconnue, sur des écrans qui n'ont rien demandé.

    L'alternative — refuser la requête quand un filtre d'OF est posé hors de sa
    source — a été écartée : la barre de filtres vide et grise ces deux critères
    dès qu'on quitte la vue « Détail par OF », de sorte que le cas ne se produit
    en pratique que si un lien partagé porte encore les paramètres. Une erreur
    serait alors une impasse ; les ignorer laisse l'écran fonctionner, et
    l'utilisateur voit que les deux champs sont vides.
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
    if filtres.semaines_debut:
        conditions.append(f"{alias}.semaine_debut = ANY(%(semaines_debut)s)")
        params["semaines_debut"] = list(filtres.semaines_debut)

    for champ, colonne, cle in (
        (filtres.programmes, "parent_programme", "programmes"),
        (filtres.perimetres, "parent_perimetre", "perimetres"),
        (filtres.categories, "child_categorie", "categories"),
        (filtres.parents, "parent_itemid", "parents"),
        (filtres.composants, "child_itemid", "composants"),
        (filtres.statuts_ligne, "statut_ligne", "statuts_ligne"),
    ):
        if champ:
            conditions.append(f"{alias}.{colonne} = ANY(%({cle})s)")
            params[cle] = list(champ)

    # Axe OF : seulement là où les colonnes existent (cf. `axe_of`).
    if axe_of:
        for champ, colonne, cle in (
            (filtres.ofs, "prod_id", "ofs"),
            (filtres.statuts_of, "prod_statut", "statuts_of"),
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
