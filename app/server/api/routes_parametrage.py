"""Routes de paramétrage : base article et nomenclature.

Ce sont les seules routes de l'application qui **modifient** quelque chose. Trois
garde-fous, en plus de la validation Pydantic :

* les actions sont bornées en volume (``LOT_MAX``) ;
* l'auteur est celui du proxy Databricks Apps, jamais un champ du corps de la
  requête — un auteur déclaré par le client ne trace rien ;
* les écritures passent par ``connexion_ecriture()``, qui lève le verrou de
  lecture seule pour la seule transaction en cours.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Body
from pydantic import BaseModel, Field

from app.server.api.deps import ParametrageDep, UtilisateurDep

routeur = APIRouter(prefix="/api/parametrage", tags=["parametrage"])

#: Longueur maximale d'un motif. Assez pour une phrase, pas pour un rapport.
MOTIF_MAX = 300


def _auteur(utilisateur: dict) -> str | None:
    """Identité à tracer : l'e-mail du proxy, à défaut le nom d'utilisateur."""
    return utilisateur.get("email") or utilisateur.get("utilisateur")


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------
@routeur.get("/resume", summary="Nombre d'arbitrages en vigueur")
def resume(depot: ParametrageDep) -> dict:
    return depot.resume()


@routeur.get("/articles", summary="Base article et exclusions")
def articles(
    depot: ParametrageDep,
    recherche: str | None = None,
    etat: Literal["tous", "exclus", "inclus"] = "tous",
    tri: str = "impact_absolu",
    sens: Literal["asc", "desc"] = "desc",
    page: int = 1,
    taille: int = 50,
) -> dict:
    return depot.articles(
        recherche=recherche, etat=etat, tri=tri, sens=sens, page=page, taille=taille
    )


@routeur.get("/nomenclature", summary="Nomenclature active et surcharges")
def nomenclature(
    depot: ParametrageDep,
    recherche: str | None = None,
    etat: Literal["tous", "surchargees", "desactivees"] = "tous",
    tri: str = "parent_itemid",
    sens: Literal["asc", "desc"] = "asc",
    page: int = 1,
    taille: int = 50,
) -> dict:
    return depot.nomenclature(
        recherche=recherche, etat=etat, tri=tri, sens=sens, page=page, taille=taille
    )


# ---------------------------------------------------------------------------
# Écriture
# ---------------------------------------------------------------------------
class RequeteExclusion(BaseModel):
    item_ids: list[str] = Field(min_length=1)
    #: ``True`` exclut, ``False`` réintègre. Une seule route pour les deux sens :
    #: l'écran propose une bascule, pas deux boutons qui s'ignorent.
    exclu: bool = True
    motif: str | None = Field(default=None, max_length=MOTIF_MAX)


@routeur.post("/articles/exclusion", summary="Exclure ou réintégrer des références")
def basculer_exclusion(
    depot: ParametrageDep,
    utilisateur: UtilisateurDep,
    requete: RequeteExclusion = Body(...),
) -> dict:
    if requete.exclu:
        nombre = depot.exclure_articles(
            requete.item_ids, motif=requete.motif, utilisateur=_auteur(utilisateur)
        )
    else:
        nombre = depot.reintegrer_articles(requete.item_ids)
    return {"lignes": nombre, "exclu": requete.exclu}


class LigneNomenclature(BaseModel):
    parent_itemid: str = Field(min_length=1)
    child_itemid: str = Field(min_length=1)


class RequeteSurcharge(BaseModel):
    lignes: list[LigneNomenclature] = Field(min_length=1)
    #: ``False`` retire la ligne du calcul d'écart.
    active: bool = True
    #: Coefficient de substitution ; ``None`` conserve celui de l'ERP.
    coef_bom: float | None = Field(default=None, gt=0)
    motif: str | None = Field(default=None, max_length=MOTIF_MAX)


@routeur.post("/nomenclature/surcharge", summary="Désactiver ou corriger des lignes")
def surcharger(
    depot: ParametrageDep,
    utilisateur: UtilisateurDep,
    requete: RequeteSurcharge = Body(...),
) -> dict:
    couples = [(ligne.parent_itemid, ligne.child_itemid) for ligne in requete.lignes]
    nombre = depot.surcharger_nomenclature(
        couples,
        active=requete.active,
        coef_bom=requete.coef_bom,
        motif=requete.motif,
        utilisateur=_auteur(utilisateur),
    )
    return {"lignes": nombre}


class RequeteReinitialisation(BaseModel):
    lignes: list[LigneNomenclature] = Field(min_length=1)


@routeur.post("/nomenclature/reinitialisation", summary="Rétablir la nomenclature d'origine")
def reinitialiser(
    depot: ParametrageDep, requete: RequeteReinitialisation = Body(...)
) -> dict:
    couples = [(ligne.parent_itemid, ligne.child_itemid) for ligne in requete.lignes]
    return {"lignes": depot.reinitialiser_nomenclature(couples)}
