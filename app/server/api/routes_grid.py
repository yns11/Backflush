"""Routes des grilles : pagination, tri et filtrage **côté serveur**.

Un tri ou un filtre côté client sur un extrait de page produit un classement
faux — c'est l'erreur la plus fréquente des tableaux de bord maison. Ici, la
grille ne trie jamais elle-même : elle demande une page triée.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel, Field

from app.server.api.deps import RepositoryDep, SettingsDep
from app.server.domain.filters import Filtres

routeur = APIRouter(prefix="/api/grilles", tags=["grilles"])


class RequeteGrille(BaseModel):
    """Corps d'une demande de page de grille."""

    filtres: Filtres = Field(default_factory=Filtres)
    tri: str | None = None
    sens: Literal["asc", "desc"] = "desc"
    page: int = Field(default=1, ge=1)
    taille: int = Field(default=50, ge=1, le=500)


@routeur.post("/{cle}", summary="Page d'une grille")
def page_grille(
    depot: RepositoryDep,
    settings: SettingsDep,
    cle: Literal["details", "composants", "programmes", "perimetres", "parents"],
    requete: RequeteGrille = Body(...),
) -> dict:
    page = depot.grille(
        cle,
        requete.filtres,
        tri=requete.tri,
        sens=requete.sens,
        page=requete.page,
        taille=requete.taille,
        taille_max=settings.page_size_max,
    )
    # Les totaux portent sur la SÉLECTION ENTIÈRE, pas sur la page : c'est le
    # seul total qui ait un sens sous les yeux d'un gestionnaire de stock.
    page["totaux"] = depot.totaux(requete.filtres)
    return page


@routeur.post("/{cle}/presse-papiers", summary="Extrait tabulé, prêt à coller dans Excel")
def presse_papiers(
    depot: RepositoryDep,
    settings: SettingsDep,
    cle: Literal["details", "composants", "programmes", "perimetres", "parents"],
    requete: RequeteGrille = Body(...),
    lignes_max: int = Query(default=5_000, ge=1, le=50_000),
) -> dict:
    """Retourne les lignes au format TSV.

    Le TSV se colle tel quel dans Excel, contrairement au CSV qui dépend du
    séparateur régional. Les nombres sont écrits avec la virgule décimale
    française, sans séparateur de milliers, pour être reconnus comme des nombres.
    """
    from app.server.domain.dictionary import GRILLES

    grille = GRILLES[cle]
    colonnes = [colonne for colonne in grille.colonnes if colonne.visible]
    flux = depot.flux_grille(cle, requete.filtres, tri=requete.tri, sens=requete.sens,
                             limite=min(lignes_max, settings.export_rows_max))

    lignes_tsv = ["\t".join(colonne.libelle for colonne in colonnes)]
    total = 0
    for ligne in flux:
        if total >= lignes_max:
            break
        lignes_tsv.append("\t".join(_texte(ligne.get(colonne.cle)) for colonne in colonnes))
        total += 1
    return {"tsv": "\n".join(lignes_tsv), "lignes": total, "colonnes": len(colonnes)}


def _texte(valeur: object) -> str:
    """Sérialise une valeur pour le presse-papiers, au format numérique français.

    Seuls les types réellement numériques sont convertis : une référence article
    comme ``0012`` est une chaîne et doit conserver ses zéros de tête.
    """
    if valeur is None:
        return ""
    if isinstance(valeur, bool):
        return "Oui" if valeur else "Non"
    if isinstance(valeur, int):
        return str(valeur)
    if isinstance(valeur, (float, Decimal)):
        return f"{float(valeur):.6f}".rstrip("0").rstrip(".").replace(".", ",") or "0"
    # Une tabulation ou un saut de ligne dans un libellé casserait la grille
    # collée : on les neutralise.
    return str(valeur).replace("\t", " ").replace("\n", " ").replace("\r", "")
