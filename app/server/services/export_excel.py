"""Export Excel des grilles.

Contraintes prises en compte :

* le runtime Databricks Apps dispose de 6 Go de mémoire partagés par tous les
  utilisateurs — le classeur est donc écrit en mode ``write_only`` (openpyxl n'y
  conserve pas les lignes déjà écrites) et alimenté par un curseur serveur ;
* le fichier doit être **directement exploitable** par un key-user : formats
  numériques natifs (pas de texte, donc sommables), volets figés, filtre
  automatique, largeurs calées sur le dictionnaire de colonnes, et un onglet
  « Contexte » rappelant les filtres appliqués — un export sans son contexte est
  ininterprétable trois semaines plus tard.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.dimensions import ColumnDimension

from app.server.domain.dictionary import Colonne, Grille
from app.server.domain.filters import Filtres

LOGGER = logging.getLogger("backflush.export")

#: Séparateur de milliers : espace, conformément à l'usage français.
_MILLIERS = "# ##0"

_ENTETE_FOND = PatternFill("solid", fgColor="1F2937")
_ENTETE_POLICE = Font(color="FFFFFF", bold=True, size=10)
_ENTETE_ALIGNEMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)


def format_colonne(colonne: Colonne) -> str:
    """Format numérique Excel correspondant au type déclaré de la colonne."""
    if colonne.type in ("decimal", "euro"):
        base = _MILLIERS + ("." + "0" * colonne.decimales if colonne.decimales > 0 else "")
        return f'{base} "€"' if colonne.type == "euro" else base
    return {
        "entier": _MILLIERS,
        "pourcent": '0.0"%"',
        "date": "dd/mm/yyyy",
    }.get(colonne.type, "@")


def _valeur_excel(valeur: Any) -> Any:
    """Convertit une valeur SQL en type natif Excel.

    Les ``Decimal`` deviennent des flottants : laissés en texte, ils
    empêcheraient toute somme dans le tableur — le premier réflexe du key-user.
    Les horodatages perdent leur fuseau, qu'Excel ne sait pas représenter.
    """
    if isinstance(valeur, bool):
        return "Oui" if valeur else "Non"
    if isinstance(valeur, Decimal):
        return float(valeur)
    if isinstance(valeur, datetime):
        return valeur.replace(tzinfo=None)
    return valeur


def construire_classeur(
    grille: Grille,
    lignes: Iterable[dict[str, Any]],
    filtres: Filtres,
    *,
    colonnes_visibles: Sequence[str] | None = None,
    lignes_max: int = 100_000,
) -> io.BytesIO:
    """Produit le classeur en mémoire et retourne un flux positionné au début.

    :param colonnes_visibles: restreint et **ordonne** les colonnes exportées ;
        par défaut, celles marquées visibles dans le dictionnaire de la grille.
    """
    selection = _selectionner_colonnes(grille, colonnes_visibles)
    formats = [format_colonne(colonne) for colonne in selection]

    classeur = Workbook(write_only=True)
    feuille = classeur.create_sheet(title=_titre_onglet(grille.libelle))
    _preparer_colonnes(feuille, selection)

    entete = []
    for colonne in selection:
        cellule = WriteOnlyCell(feuille, value=colonne.libelle)
        cellule.fill = _ENTETE_FOND
        cellule.font = _ENTETE_POLICE
        cellule.alignment = _ENTETE_ALIGNEMENT
        entete.append(cellule)
    feuille.append(entete)

    nb_lignes = 0
    tronque = False
    for ligne in lignes:
        if nb_lignes >= lignes_max:
            tronque = True
            LOGGER.warning("Export tronqué à %d lignes (grille %s).", lignes_max, grille.cle)
            break
        cellules = []
        # strict=True : les deux listes sont construites ensemble ; une
        # divergence de longueur serait un bug, pas une donnée à tronquer.
        for colonne, format_ in zip(selection, formats, strict=True):
            cellule = WriteOnlyCell(feuille, value=_valeur_excel(ligne.get(colonne.cle)))
            cellule.number_format = format_
            cellules.append(cellule)
        feuille.append(cellules)
        nb_lignes += 1

    feuille.freeze_panes = "A2"
    if nb_lignes:
        feuille.auto_filter.ref = f"A1:{get_column_letter(len(selection))}{nb_lignes + 1}"

    _ajouter_onglet_contexte(classeur, grille, filtres, selection, nb_lignes, tronque)

    flux = io.BytesIO()
    classeur.save(flux)
    flux.seek(0)
    LOGGER.info("Export %s : %d ligne(s), %d colonne(s).", grille.cle, nb_lignes, len(selection))
    return flux


def _selectionner_colonnes(grille: Grille, demandees: Sequence[str] | None) -> list[Colonne]:
    par_cle = {colonne.cle: colonne for colonne in grille.colonnes}
    if not demandees:
        return [colonne for colonne in grille.colonnes if colonne.visible]
    # Les clés inconnues sont ignorées : un export ne doit pas échouer parce que
    # le frontend a transmis une colonne devenue obsolète.
    retenues = [par_cle[cle] for cle in demandees if cle in par_cle]
    return retenues or [colonne for colonne in grille.colonnes if colonne.visible]


def _preparer_colonnes(feuille, colonnes: Sequence[Colonne]) -> None:
    """Fixe les largeurs. À faire AVANT d'écrire la première ligne en write_only."""
    for index, colonne in enumerate(colonnes, start=1):
        lettre = get_column_letter(index)
        feuille.column_dimensions[lettre] = ColumnDimension(
            feuille, index=lettre,
            width=max(10.0, min(45.0, colonne.largeur / 7 + 4)),
            customWidth=True,
        )


def _ajouter_onglet_contexte(
    classeur: Workbook,
    grille: Grille,
    filtres: Filtres,
    colonnes: Sequence[Colonne],
    nb_lignes: int,
    tronque: bool,
) -> None:
    """Trace des paramètres d'extraction — indispensable à toute relecture."""
    feuille = classeur.create_sheet(title="Contexte")
    feuille.column_dimensions["A"] = ColumnDimension(feuille, index="A", width=34, customWidth=True)
    feuille.column_dimensions["B"] = ColumnDimension(feuille, index="B", width=95, customWidth=True)

    lignes: list[tuple[str, Any]] = [
        ("Extraction", grille.libelle),
        ("Description", grille.description),
        ("Généré le", datetime.now().replace(microsecond=0)),
        ("Lignes exportées", nb_lignes),
        ("Export tronqué", "Oui — affinez vos filtres" if tronque else "Non"),
        ("", ""),
        ("FILTRES APPLIQUÉS", ""),
        ("Semaine du (lundi)", filtres.date_debut or "toutes"),
        ("Semaine au (lundi)", filtres.date_fin or "toutes"),
        ("Programmes", ", ".join(filtres.programmes) or "tous"),
        ("Catégories", ", ".join(filtres.categories) or "toutes"),
        ("Types d'écart", ", ".join(filtres.types_ecart) or "tous"),
        ("Statuts de ligne", ", ".join(filtres.statuts_ligne) or "tous"),
        ("Réf. parent", ", ".join(filtres.parents) or "toutes"),
        ("Réf. composant", ", ".join(filtres.composants) or "toutes"),
        ("Recherche", filtres.recherche or "—"),
        ("Seuil de conformité (unités)", filtres.seuil_conformite),
        ("Seuil relatif (%)", filtres.seuil_pct if filtres.seuil_pct is not None else "inactif"),
        ("Impact minimum (€)", filtres.impact_min if filtres.impact_min is not None else "—"),
        ("Coef. uniforme uniquement", "Oui" if filtres.coef_uniforme_uniquement else "Non"),
        ("Lignes conformes exclues", "Oui" if filtres.exclure_conforme else "Non"),
        ("", ""),
        ("DÉFINITION DES COLONNES", ""),
    ]
    lignes.extend((colonne.libelle, colonne.aide or "—") for colonne in colonnes)

    gras = Font(bold=True)
    for libelle, valeur in lignes:
        cellule_libelle = WriteOnlyCell(feuille, value=libelle)
        if libelle.isupper() and libelle:
            cellule_libelle.font = gras
        cellule_valeur = WriteOnlyCell(feuille, value=_valeur_excel(valeur))
        cellule_valeur.alignment = Alignment(wrap_text=True, vertical="top")
        feuille.append([cellule_libelle, cellule_valeur])


def _titre_onglet(libelle: str) -> str:
    """Excel refuse les onglets de plus de 31 caractères et certains symboles."""
    interdits = set('[]:*?/\\')
    propre = "".join(caractere for caractere in libelle if caractere not in interdits)
    return propre[:31] or "Export"


def nom_fichier(grille: Grille, filtres: Filtres) -> str:
    """Nom de fichier autodescriptif : contenu, période, horodatage."""
    debut = filtres.date_debut.isoformat() if filtres.date_debut else "debut"
    fin = filtres.date_fin.isoformat() if filtres.date_fin else "fin"
    horodatage = datetime.now().strftime("%Y%m%d-%H%M")
    return f"backflush_{grille.cle}_{debut}_{fin}_{horodatage}.xlsx"
