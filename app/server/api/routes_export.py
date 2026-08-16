"""Route d'export Excel."""

from __future__ import annotations

import urllib.parse
from typing import Literal

from fastapi import APIRouter, Body
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.server.api.deps import RepositoryDep, SettingsDep
from app.server.core.errors import RequeteInvalideError
from app.server.domain.dictionary import GRILLES
from app.server.domain.filters import Filtres
from app.server.domain.synthese import croiser
from app.server.services.export_excel import (
    construire_classeur,
    construire_classeur_synthese,
    nom_fichier,
    nom_fichier_synthese,
)

routeur = APIRouter(prefix="/api/export", tags=["export"])

TYPE_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _piece_jointe(nom: str) -> dict[str, str]:
    """En-têtes de téléchargement. RFC 5987 : le nom peut porter des accents."""
    disposition = (
        f"attachment; filename=\"{nom}\"; "
        f"filename*=UTF-8''{urllib.parse.quote(nom)}"
    )
    return {"Content-Disposition": disposition, "Cache-Control": "no-store"}


# ⚠️ Ordre de déclaration significatif : « /{cle}.xlsx » est un motif qui
# capturerait aussi « /synthese-perimetre.xlsx ». FastAPI retient la première
# route déclarée qui correspond, et la validation du Literal rejetterait alors
# la requête par un 422 déroutant. Les chemins littéraux passent donc AVANT.
class RequeteSynthese(BaseModel):
    filtres: Filtres = Field(default_factory=Filtres)
    mesure: Literal["valeur", "quantite"] = "quantite"


@routeur.post("/synthese-perimetre.xlsx", summary="Export Excel de la vue synthétique")
def exporter_synthese(
    depot: RepositoryDep, requete: RequeteSynthese = Body(...)
) -> StreamingResponse:
    """Tableau croisé production / écart d'un périmètre, en classeur.

    Le pivot est celui de l'écran (``domain.synthese.croiser``) : le classeur et
    l'application montrent les mêmes chiffres dans le même ordre. Seule la
    présentation des zéros diffère — l'écran les laisse vides pour la lisibilité,
    le classeur les écrit pour rester directement calculable.
    """
    if len(requete.filtres.perimetres) != 1:
        raise RequeteInvalideError(
            "La vue synthétique porte sur un périmètre unique : "
            f"{len(requete.filtres.perimetres)} sélectionné(s)."
        )
    donnees = depot.synthese_perimetre(requete.filtres, requete.mesure)
    modele = croiser(
        donnees["production"], donnees["ecarts"], en_valeur=requete.mesure == "valeur"
    )
    perimetre = requete.filtres.perimetres[0]
    classeur = construire_classeur_synthese(
        modele, requete.filtres, perimetre=perimetre, en_valeur=requete.mesure == "valeur"
    )
    return StreamingResponse(
        classeur,
        media_type=TYPE_XLSX,
        headers=_piece_jointe(nom_fichier_synthese(perimetre, requete.filtres)),
    )


class RequeteExport(BaseModel):
    filtres: Filtres = Field(default_factory=Filtres)
    tri: str | None = None
    sens: Literal["asc", "desc"] = "desc"
    #: Colonnes à exporter, dans l'ordre souhaité. Vide = colonnes visibles.
    colonnes: list[str] = Field(default_factory=list)
    #: Plafond de lignes, borné par la configuration serveur.
    lignes_max: int = Field(default=100_000, ge=1)


@routeur.post("/{cle}.xlsx", summary="Export Excel d'une grille")
def exporter(
    depot: RepositoryDep,
    settings: SettingsDep,
    cle: Literal["details", "composants", "programmes", "perimetres", "parents"],
    requete: RequeteExport = Body(...),
) -> StreamingResponse:
    """Construit le classeur et le renvoie en pièce jointe.

    Le classeur est produit en mémoire puis servi en une fois : openpyxl ne
    sait pas écrire un XLSX en flux continu (l'archive ZIP a besoin de son
    index final). La consommation reste bornée par ``export_rows_max`` et par
    le mode ``write_only``.
    """
    grille = GRILLES[cle]
    plafond = min(requete.lignes_max, settings.export_rows_max)
    lignes = depot.flux_grille(
        cle, requete.filtres, tri=requete.tri, sens=requete.sens, limite=plafond
    )
    classeur = construire_classeur(
        grille, lignes, requete.filtres,
        colonnes_visibles=requete.colonnes or None, lignes_max=plafond,
    )

    return StreamingResponse(
        classeur,
        media_type=TYPE_XLSX,
        headers=_piece_jointe(nom_fichier(grille, requete.filtres)),
    )
