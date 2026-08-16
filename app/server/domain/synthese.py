"""Mise en tableau croisé de la vue synthétique d'un périmètre.

Le dépôt renvoie des lignes « longues » (une ligne par référence × semaine) ;
la restitution d'atelier est « large » (une ligne par référence, une colonne par
semaine). Le pivot est fait ici, hors de l'API comme de l'export, pour deux
raisons :

* il est identique à celui du navigateur — le classeur et l'écran doivent
  montrer exactement les mêmes chiffres, dans le même ordre ;
* il est testable sans base ni tableur.

Une différence assumée subsiste entre les deux supports : à l'écran, une valeur
nulle laisse la cellule vide (sur trente références × treize semaines, les zéros
noieraient l'information) ; dans le classeur, elle vaut ``0``, car un tableau
destiné au calcul ne doit pas obliger le key-user à traiter les cases vides.
Cette décision n'appartient donc pas au pivot : il conserve les zéros, et chaque
support choisit sa présentation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

__all__ = ["LigneCroisee", "Semaine", "SyntheseCroisee", "croiser"]


def _nombre(valeur: Any) -> float:
    """Convertit une valeur SQL en flottant, ``None`` compris."""
    if valeur is None:
        return 0.0
    if isinstance(valeur, Decimal):
        return float(valeur)
    return float(valeur)


@dataclass(frozen=True)
class Semaine:
    """Colonne du tableau croisé : clé triable et libellé court."""

    cle: str
    libelle: str

    @classmethod
    def depuis(cls, annee: Any, numero: Any) -> Semaine:
        an = int(annee or 0)
        no = int(numero or 0)
        return cls(cle=f"{an}-{no:02d}", libelle=f"S{no:02d}")


@dataclass
class LigneCroisee:
    """Une référence, ses valeurs par semaine et son total de période."""

    reference: str
    designation: str
    #: Identifiant de nomenclature (production) ou coefficient (écart).
    complement: str
    valeurs: dict[str, float] = field(default_factory=dict)
    total: float = 0.0

    def ajouter(self, cle_semaine: str, valeur: float) -> None:
        self.valeurs[cle_semaine] = self.valeurs.get(cle_semaine, 0.0) + valeur
        self.total += valeur


@dataclass
class SyntheseCroisee:
    """Modèle complet du tableau : colonnes, deux blocs de lignes, totaux."""

    semaines: list[Semaine]
    production: list[LigneCroisee]
    ecarts: list[LigneCroisee]
    total_production: dict[str, float]
    total_production_periode: float
    #: Somme des |écarts| rapportée au volume produit, en pourcentage.
    part_ecart_pct: float


def _format_coef(valeur: Any) -> str:
    """Coefficient de nomenclature, en notation française, sans zéros inutiles."""
    if valeur is None:
        return "—"
    texte = f"{_nombre(valeur):.4f}".rstrip("0").rstrip(".")
    return (texte or "0").replace(".", ",")


def croiser(
    production: Iterable[Mapping[str, Any]],
    ecarts: Iterable[Mapping[str, Any]],
    *,
    en_valeur: bool,
) -> SyntheseCroisee:
    """Croise les lignes longues du dépôt en un tableau référence × semaine.

    :param en_valeur: mesure d'analyse. En euros, les deux blocs portent des
        montants ; en quantité, la production est en pièces fabriquées et
        l'écart en **équivalent produit** — la seule grandeur qui se compare au
        volume produit affiché au-dessus.
    """
    semaines: dict[str, Semaine] = {}

    def colonne(ligne: Mapping[str, Any]) -> str:
        semaine = Semaine.depuis(ligne.get("annee"), ligne.get("semaine"))
        semaines.setdefault(semaine.cle, semaine)
        return semaine.cle

    parents: dict[str, LigneCroisee] = {}
    total_production: dict[str, float] = {}
    for ligne in production:
        cle = colonne(ligne)
        valeur = _nombre(ligne.get("valeur_produite" if en_valeur else "qty_produite"))
        reference = str(ligne.get("parent_itemid") or "—")
        entree = parents.get(reference)
        if entree is None:
            entree = LigneCroisee(
                reference=reference,
                designation=str(ligne.get("parent_name") or "—"),
                complement=str(ligne.get("bomid") or "—"),
            )
            parents[reference] = entree
        entree.ajouter(cle, valeur)
        total_production[cle] = total_production.get(cle, 0.0) + valeur

    composants: dict[str, LigneCroisee] = {}
    for ligne in ecarts:
        cle = colonne(ligne)
        valeur = _nombre(
            ligne.get("ecart_valorise" if en_valeur else "ecart_equivalent_produit")
        )
        reference = str(ligne.get("child_itemid") or "—")
        entree = composants.get(reference)
        if entree is None:
            entree = LigneCroisee(
                reference=reference,
                designation=str(ligne.get("child_name") or "—"),
                complement=_format_coef(ligne.get("coef_bom")),
            )
            composants[reference] = entree
        entree.ajouter(cle, valeur)

    total_periode = sum(total_production.values())
    ecart_absolu = sum(abs(entree.total) for entree in composants.values())

    return SyntheseCroisee(
        semaines=sorted(semaines.values(), key=lambda semaine: semaine.cle),
        # Le classement se fait sur le volume pour la production (les gros
        # porteurs d'abord) et sur l'écart ABSOLU pour les composants : le signe
        # dit la nature de l'anomalie, pas sa gravité.
        production=sorted(parents.values(), key=lambda entree: -entree.total),
        ecarts=sorted(composants.values(), key=lambda entree: -abs(entree.total)),
        total_production=total_production,
        total_production_periode=total_periode,
        part_ecart_pct=(ecart_absolu / total_periode * 100) if total_periode else 0.0,
    )
