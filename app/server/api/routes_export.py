"""Route d'export Excel."""

from __future__ import annotations

import urllib.parse
from typing import Literal

from fastapi import APIRouter, Body
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.server.api.deps import RepositoryDep, SettingsDep
from app.server.domain.dictionary import GRILLES
from app.server.domain.filters import Filtres
from app.server.services.export_excel import construire_classeur, nom_fichier

routeur = APIRouter(prefix="/api/export", tags=["export"])

TYPE_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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
    cle: Literal["details", "composants", "programmes", "parents"],
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

    nom = nom_fichier(grille, requete.filtres)
    # RFC 5987 : le nom peut contenir des accents ; on fournit les deux formes.
    disposition = (
        f"attachment; filename=\"{nom}\"; "
        f"filename*=UTF-8''{urllib.parse.quote(nom)}"
    )
    return StreamingResponse(
        classeur,
        media_type=TYPE_XLSX,
        headers={"Content-Disposition": disposition, "Cache-Control": "no-store"},
    )
