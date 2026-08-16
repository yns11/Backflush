"""Routes analytiques : indicateurs, tendances, répartitions, concentration.

Toutes les routes sont en ``POST`` : l'objet de filtres est structuré (listes,
dates, seuils) et dépasserait rapidement la longueur maximale d'une URL, tout en
polluant les journaux d'accès avec des références article.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Body

from app.server.api.deps import RepositoryDep
from app.server.core.errors import RequeteInvalideError
from app.server.domain.filters import Filtres
from app.server.domain.metrics import construire_indicateurs

routeur = APIRouter(prefix="/api/analytique", tags=["analytique"])

#: Mesure de classement et d'affichage. « valeur » raisonne en euros, « quantite »
#: en unités : les deux classements diffèrent, et c'est précisément l'intérêt de
#: la bascule.
Mesure = Literal["valeur", "quantite"]


@routeur.post("/indicateurs", summary="Indicateurs de synthèse et variation")
def indicateurs(
    depot: RepositoryDep, filtres: Filtres = Body(...), mesure: Mesure = "valeur",
) -> dict:
    """Indicateurs de la période, comparés à la période précédente de même durée."""
    courant = depot.agregat(filtres)
    # Sans bornes de date, « la période précédente » n'a pas de définition : on
    # n'affiche alors aucune variation plutôt qu'une comparaison arbitraire.
    precedent = depot.agregat(filtres.periode_precedente()) if filtres.date_debut else None
    return {
        "indicateurs": [
            ind.model_dump() for ind in construire_indicateurs(courant, precedent, mesure)
        ],
        "agregat": courant.__dict__,
        "concentration": depot.concentration(filtres, mesure=mesure),
        "comparaison_disponible": precedent is not None,
    }


@routeur.post("/tendance", summary="Série hebdomadaire de la sélection")
def tendance(depot: RepositoryDep, filtres: Filtres = Body(...)) -> dict:
    return {"semaines": depot.serie_hebdomadaire(filtres)}


@routeur.post("/chronologie", summary="Histogramme complet pour le slicer temporel")
def chronologie(depot: RepositoryDep, filtres: Filtres = Body(...)) -> dict:
    """Série hebdomadaire **sans borne de date**.

    Le slicer doit montrer tout l'historique disponible pour que l'utilisateur
    voie ce qu'il exclut ; seules les autres dimensions du filtre s'appliquent.
    """
    return {"semaines": depot.serie_hebdomadaire(filtres.sans_dates())}


@routeur.post("/repartition/{dimension}", summary="Agrégat par dimension")
def repartition(
    depot: RepositoryDep,
    dimension: Literal["programme", "perimetre", "categorie", "type", "statut"],
    filtres: Filtres = Body(...),
    limite: int = 20,
    mesure: Mesure = "valeur",
) -> dict:
    return {
        "dimension": dimension,
        "mesure": mesure,
        "lignes": depot.repartition(filtres, dimension, limite, mesure),
    }


@routeur.post("/top-composants", summary="Composants par impact financier absolu")
def top_composants(
    depot: RepositoryDep,
    filtres: Filtres = Body(...),
    limite: int = 10,
    mesure: Mesure = "valeur",
) -> dict:
    return {"mesure": mesure, "lignes": depot.top_composants(filtres, limite, mesure)}


@routeur.post("/synthese-perimetre", summary="Vue synthétique d'un périmètre")
def synthese_perimetre(
    depot: RepositoryDep, filtres: Filtres = Body(...), mesure: Mesure = "quantite",
) -> dict:
    """Tableau croisé production × semaine et écart × semaine, pour UN périmètre.

    La restriction à un périmètre unique n'est pas une commodité d'affichage :
    l'écart en équivalent produit se rapporte au volume produit de la ligne. Le
    cumuler sur deux lignes de production reviendrait à additionner des unités
    différentes.
    """
    if len(filtres.perimetres) != 1:
        raise RequeteInvalideError(
            "La vue synthétique porte sur un périmètre unique : "
            f"{len(filtres.perimetres)} sélectionné(s)."
        )
    return {"perimetre": filtres.perimetres[0], "mesure": mesure,
            **depot.synthese_perimetre(filtres, mesure)}


@routeur.post("/composant/{child_itemid}", summary="Fiche complète d'un composant")
def fiche_composant(
    depot: RepositoryDep, child_itemid: str, filtres: Filtres = Body(...)
) -> dict:
    """Tout ce qu'il faut pour instruire une référence, en un seul appel."""
    cible = filtres.model_copy(update={"composants": [child_itemid]})
    return {
        "article": depot.fiche_article(child_itemid),
        "indicateurs": [ind.model_dump() for ind in construire_indicateurs(depot.agregat(cible))],
        "semaines": depot.serie_hebdomadaire(cible),
        "parents": depot.parents_du_composant(child_itemid),
    }


@routeur.post("/parent/{parent_itemid}", summary="Fiche complète d'un article parent")
def fiche_parent(depot: RepositoryDep, parent_itemid: str, filtres: Filtres = Body(...)) -> dict:
    cible = filtres.model_copy(update={"parents": [parent_itemid]})
    return {
        "article": depot.fiche_article(parent_itemid),
        "indicateurs": [ind.model_dump() for ind in construire_indicateurs(depot.agregat(cible))],
        "semaines": depot.serie_hebdomadaire(cible),
        "nomenclature": depot.nomenclature_du_parent(parent_itemid),
    }
