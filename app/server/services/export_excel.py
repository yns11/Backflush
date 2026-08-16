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
from app.server.domain.synthese import SyntheseCroisee

LOGGER = logging.getLogger("backflush.export")

#: Séparateur de milliers : espace, conformément à l'usage français.
_MILLIERS = "# ##0"

_ENTETE_FOND = PatternFill("solid", fgColor="1F2937")
_ENTETE_POLICE = Font(color="FFFFFF", bold=True, size=10)
_ENTETE_ALIGNEMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)

_SECTION_FOND = PatternFill("solid", fgColor="E5E7EB")


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
    # AVANT le premier append : en mode write_only, la vue de la feuille est
    # sérialisée dès l'écriture de la première ligne ; fixer les volets ensuite
    # est silencieusement sans effet. L'``auto_filter``, lui, est écrit après les
    # données et peut donc attendre le décompte final.
    feuille.freeze_panes = "A2"

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
        *_lignes_filtres(filtres),
        ("", ""),
        ("DÉFINITION DES COLONNES", ""),
    ]
    lignes.extend((colonne.libelle, colonne.aide or "—") for colonne in colonnes)
    _ecrire_contexte(feuille, lignes)


def _lignes_filtres(filtres: Filtres) -> list[tuple[str, Any]]:
    """Rappel des filtres appliqués — un export sans son contexte est illisible."""
    return [
        ("FILTRES APPLIQUÉS", ""),
        ("Semaine du (lundi)", filtres.date_debut or "toutes"),
        ("Semaine au (lundi)", filtres.date_fin or "toutes"),
        ("Programmes", ", ".join(filtres.programmes) or "tous"),
        ("Périmètres", ", ".join(filtres.perimetres) or "tous"),
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
    ]


def _ecrire_contexte(feuille, lignes: Sequence[tuple[str, Any]]) -> None:
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


# =============================================================================
# Vue synthétique d'un périmètre — tableau croisé
# =============================================================================

def construire_classeur_synthese(
    modele: SyntheseCroisee,
    filtres: Filtres,
    *,
    perimetre: str,
    en_valeur: bool,
) -> io.BytesIO:
    """Produit le classeur du tableau croisé production / écart.

    Différence délibérée avec l'écran : **les zéros sont écrits**. À l'écran,
    laisser la cellule vide allège une grille où l'essentiel des croisements est
    nul ; dans un classeur destiné au calcul, une case vide oblige à écrire des
    ``SI(ESTVIDE(...))`` partout. Le classeur porte donc des ``0`` numériques, et
    les cellules restées vides ne le sont que là où la référence n'existait pas
    encore dans la semaine considérée — c'est-à-dire nulle part, le pivot
    remplissant toute la matrice.
    """
    format_ = f'{_MILLIERS}.00 "€"' if en_valeur else f"{_MILLIERS}.00"
    unite = "€" if en_valeur else "unités"

    classeur = Workbook(write_only=True)
    feuille = classeur.create_sheet(title=_titre_onglet(f"Synthèse {perimetre}"))

    largeurs = [22.0, 38.0, 14.0] + [11.0] * len(modele.semaines) + [14.0]
    for index, largeur in enumerate(largeurs, start=1):
        lettre = get_column_letter(index)
        feuille.column_dimensions[lettre] = ColumnDimension(
            feuille, index=lettre, width=largeur, customWidth=True
        )

    # Volets figés sur l'en-tête ET les trois colonnes d'identification : au-delà
    # d'une dizaine de semaines, une ligne défilée sans sa référence est illisible.
    # À poser avant le premier append (cf. construire_classeur).
    feuille.freeze_panes = "D2"

    libelles = ["Référence", "Désignation", "BOM / Coef."]
    libelles += [semaine.libelle for semaine in modele.semaines]
    libelles.append("Total")
    entete = []
    for libelle in libelles:
        cellule = WriteOnlyCell(feuille, value=libelle)
        cellule.fill = _ENTETE_FOND
        cellule.font = _ENTETE_POLICE
        cellule.alignment = _ENTETE_ALIGNEMENT
        entete.append(cellule)
    feuille.append(entete)

    def section(titre: str) -> None:
        cellules = [WriteOnlyCell(feuille, value=titre)]
        cellules += [WriteOnlyCell(feuille, value=None) for _ in libelles[1:]]
        for cellule in cellules:
            cellule.fill = _SECTION_FOND
            cellule.font = Font(bold=True)
        feuille.append(cellules)

    def ligne_chiffree(
        reference: str,
        designation: str,
        complement: str,
        valeurs: dict[str, float],
        total: float,
        *,
        gras: bool = False,
    ) -> None:
        cellules = [
            WriteOnlyCell(feuille, value=reference),
            WriteOnlyCell(feuille, value=designation),
            WriteOnlyCell(feuille, value=complement),
        ]
        for semaine in modele.semaines:
            cellule = WriteOnlyCell(feuille, value=float(valeurs.get(semaine.cle, 0.0)))
            cellule.number_format = format_
            cellules.append(cellule)
        cellule_total = WriteOnlyCell(feuille, value=float(total))
        cellule_total.number_format = format_
        cellules.append(cellule_total)
        if gras:
            for cellule in cellules:
                cellule.font = Font(bold=True)
        feuille.append(cellules)

    section(f"1. PRODUCTION ({unite})")
    for ligne in modele.production:
        ligne_chiffree(
            ligne.reference, ligne.designation, ligne.complement, ligne.valeurs, ligne.total
        )
    ligne_chiffree(
        "TOTAL", "Total production", "",
        modele.total_production, modele.total_production_periode, gras=True,
    )

    section(
        f"2. ÉCART DE PRÉLÈVEMENT ({'€' if en_valeur else 'équivalent produit'})"
    )
    for ligne in modele.ecarts:
        ligne_chiffree(
            ligne.reference, ligne.designation, ligne.complement, ligne.valeurs, ligne.total
        )

    contexte = classeur.create_sheet(title="Contexte")
    contexte.column_dimensions["A"] = ColumnDimension(contexte, index="A", width=34, customWidth=True)
    contexte.column_dimensions["B"] = ColumnDimension(contexte, index="B", width=95, customWidth=True)
    _ecrire_contexte(contexte, [
        ("Extraction", "Vue synthétique d'un périmètre"),
        ("Périmètre", perimetre),
        ("Mesure", "Valeur (€)" if en_valeur else "Quantité (unités)"),
        ("Généré le", datetime.now().replace(microsecond=0)),
        ("Semaines", len(modele.semaines)),
        ("Parents produits", len(modele.production)),
        ("Composants en écart", len(modele.ecarts)),
        ("Écart / volume produit (%)", round(modele.part_ecart_pct, 2)),
        ("", ""),
        *_lignes_filtres(filtres),
        ("", ""),
        ("LECTURE", ""),
        ("1. PRODUCTION", "Une ligne par parent fabriqué du périmètre, puis le total."),
        (
            "2. ÉCART DE PRÉLÈVEMENT",
            "Composants à coefficient uniforme uniquement. En quantité, l'écart est "
            "exprimé en équivalent produit fabriqué : écart ÷ coefficient de "
            "nomenclature, donc directement comparable au volume produit ci-dessus.",
        ),
        (
            "Signe",
            "Positif = non-consommation (théorique > réel). "
            "Négatif = surconsommation (réel > théorique).",
        ),
    ])

    flux = io.BytesIO()
    classeur.save(flux)
    flux.seek(0)
    LOGGER.info(
        "Export synthèse périmètre %s : %d semaine(s), %d parent(s), %d composant(s).",
        perimetre, len(modele.semaines), len(modele.production), len(modele.ecarts),
    )
    return flux


def nom_fichier_synthese(perimetre: str, filtres: Filtres) -> str:
    """Nom de fichier autodescriptif pour la vue synthétique."""
    debut = filtres.date_debut.isoformat() if filtres.date_debut else "debut"
    fin = filtres.date_fin.isoformat() if filtres.date_fin else "fin"
    horodatage = datetime.now().strftime("%Y%m%d-%H%M")
    propre = "".join(c if c.isalnum() else "-" for c in perimetre).strip("-")[:40]
    return f"backflush_synthese_{propre or 'perimetre'}_{debut}_{fin}_{horodatage}.xlsx"
