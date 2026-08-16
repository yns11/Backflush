"""Définition et calcul des indicateurs — logique métier pure.

Ce module ne connaît ni SQL, ni HTTP, ni React : il transforme un agrégat brut
en une liste d'indicateurs prêts à afficher. C'est la **définition de référence**
des KPI ; le frontend se contente de mettre en forme ce qu'il reçoit (libellé,
unité, format, polarité, aide contextuelle), ce qui interdit toute divergence
entre la définition métier et l'affichage.

Il est entièrement testable sans base de données (voir ``tests/test_metrics.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

Format = Literal["entier", "decimal", "euro", "pourcent"]

#: Sens dans lequel une variation est favorable au métier.
SensFavorable = Literal["hausse", "baisse", "neutre"]


@dataclass(frozen=True)
class AgregatBrut:
    """Résultat d'agrégation renvoyé par la base, converti en flottants.

    Un agrégat vide (aucune ligne dans la sélection) est représenté par des
    zéros, jamais par ``None`` : les indicateurs restent affichables et le
    frontend n'a pas à gérer de cas particulier.
    """

    nb_lignes: int = 0
    nb_lignes_ecart: int = 0
    nb_parents: int = 0
    nb_composants: int = 0
    nb_programmes: int = 0
    nb_semaines: int = 0
    conso_theorique: float = 0.0
    conso_reelle: float = 0.0
    conso_theorique_valorisee: float = 0.0
    ecart_net: float = 0.0
    non_consommation: float = 0.0
    surconsommation: float = 0.0
    ecart_valorise: float = 0.0
    non_consommation_valorisee: float = 0.0
    surconsommation_valorisee: float = 0.0
    ecart_valorise_absolu: float = 0.0

    @classmethod
    def depuis_ligne(cls, ligne: dict | None) -> AgregatBrut:
        """Construit l'agrégat à partir d'une ligne SQL, en tolérant les NULL."""
        if not ligne:
            return cls()
        valeurs = {
            champ: _nombre(ligne.get(champ))
            for champ in cls.__dataclass_fields__
        }
        entiers = (
            "nb_lignes", "nb_lignes_ecart", "nb_parents",
            "nb_composants", "nb_programmes", "nb_semaines",
        )
        for champ in entiers:
            valeurs[champ] = int(valeurs[champ])
        return cls(**valeurs)


def _nombre(valeur: object) -> float:
    """Convertit une valeur SQL (``Decimal``, ``int``, ``None``) en flottant."""
    if valeur is None:
        return 0.0
    if isinstance(valeur, Decimal):
        return float(valeur)
    if isinstance(valeur, (int, float)):
        return float(valeur)
    return 0.0


class Indicateur(BaseModel):
    """Un KPI prêt à afficher, définition métier comprise."""

    cle: str
    libelle: str
    valeur: float
    unite: str
    format: Format
    sens_favorable: SensFavorable
    #: Variation relative par rapport à la période précédente, en points de %.
    variation_pct: float | None = None
    variation_absolue: float | None = None
    #: Définition affichée au survol : un chiffre sans définition n'est pas un KPI.
    aide: str


def _variation(courant: float, precedent: float | None) -> tuple[float | None, float | None]:
    """Retourne (variation relative en %, variation absolue).

    Une variation relative est indéfinie si la base est nulle : on renvoie
    ``None`` plutôt qu'un pourcentage infini ou un 0 trompeur.
    """
    if precedent is None:
        return None, None
    absolue = courant - precedent
    if precedent == 0:
        return None, absolue
    return (absolue / abs(precedent)) * 100, absolue


def taux_conformite(agregat: AgregatBrut) -> float:
    """Part des couples parent/composant/semaine dans la tolérance, en %."""
    if agregat.nb_lignes == 0:
        return 0.0
    return (agregat.nb_lignes - agregat.nb_lignes_ecart) / agregat.nb_lignes * 100


def ecart_pct_global(agregat: AgregatBrut) -> float:
    """Écart net rapporté à la consommation théorique, en %."""
    if agregat.conso_theorique == 0:
        return 0.0
    return agregat.ecart_net / agregat.conso_theorique * 100


def fiabilite_backflush(agregat: AgregatBrut) -> float:
    """Indice de fiabilité du backflush, en %.

    Analogue de l'« inventory accuracy » d'un WMS : on rapporte la **somme des
    écarts en valeur absolue** à la valeur théoriquement consommée. Prendre la
    valeur absolue est essentiel : une surconsommation de 10 k€ et une
    non-consommation de 10 k€ se compensent dans l'écart net alors qu'elles
    signalent deux dysfonctionnements bien réels.

    Borné à [0, 100] : au-delà de 100 % d'erreur, la précision de l'indicateur
    n'apporte plus rien à la décision.
    """
    if agregat.conso_theorique_valorisee <= 0:
        return 0.0
    ratio = agregat.ecart_valorise_absolu / agregat.conso_theorique_valorisee
    return max(0.0, min(100.0, (1 - ratio) * 100))


def construire_indicateurs(
    courant: AgregatBrut,
    precedent: AgregatBrut | None = None,
) -> list[Indicateur]:
    """Produit la liste ordonnée des indicateurs de la page de synthèse.

    L'ordre est celui de lecture : d'abord l'impact financier (le langage du
    comité de direction), puis sa décomposition, puis la qualité de la mesure.
    """
    def var(extracteur) -> tuple[float | None, float | None]:
        return _variation(extracteur(courant), extracteur(precedent) if precedent else None)

    definitions: list[tuple[str, str, float, str, Format, SensFavorable, str, tuple]] = [
        (
            "ecart_valorise_net", "Écart net valorisé",
            courant.ecart_valorise, "€", "euro", "neutre",
            "Somme signée des écarts × coût standard. Positif = valeur non déduite du "
            "stock (stock système surévalué) ; négatif = valeur consommée en excès.",
            var(lambda a: a.ecart_valorise),
        ),
        (
            "non_consommation_valorisee", "Non-consommation",
            courant.non_consommation_valorisee, "€", "euro", "baisse",
            "Valeur des composants prévus par la nomenclature mais non déduits du "
            "stock. Traduit un stock système supérieur au stock physique.",
            var(lambda a: a.non_consommation_valorisee),
        ),
        (
            "surconsommation_valorisee", "Surconsommation",
            courant.surconsommation_valorisee, "€", "euro", "baisse",
            "Valeur des composants sortis au-delà du théorique : rebut non déclaré, "
            "erreur de nomenclature, servitude non modélisée.",
            var(lambda a: a.surconsommation_valorisee),
        ),
        (
            "fiabilite_backflush", "Fiabilité du backflush",
            fiabilite_backflush(courant), "%", "pourcent", "hausse",
            "100 % − (somme des |écarts| valorisés ÷ consommation théorique valorisée). "
            "Les écarts de sens opposés ne se compensent pas.",
            var(fiabilite_backflush),
        ),
        (
            "taux_conformite", "Taux de conformité",
            taux_conformite(courant), "%", "pourcent", "hausse",
            "Part des couples parent/composant/semaine dont l'écart reste dans la "
            "tolérance paramétrée.",
            var(taux_conformite),
        ),
        (
            "ecart_pct_global", "Écart global",
            ecart_pct_global(courant), "%", "pourcent", "neutre",
            "Écart net rapporté à la consommation théorique, en quantité.",
            var(ecart_pct_global),
        ),
        (
            "nb_composants", "Références concernées",
            float(courant.nb_composants), "réf.", "entier", "neutre",
            "Nombre de composants distincts présents dans la sélection.",
            var(lambda a: float(a.nb_composants)),
        ),
        (
            "nb_lignes_ecart", "Lignes en écart",
            float(courant.nb_lignes_ecart), "lignes", "entier", "baisse",
            "Couples parent/composant/semaine hors tolérance. C'est le volume de "
            "travail d'investigation.",
            var(lambda a: float(a.nb_lignes_ecart)),
        ),
    ]

    return [
        Indicateur(
            cle=cle, libelle=libelle, valeur=valeur, unite=unite, format=format_,
            sens_favorable=sens, aide=aide,
            variation_pct=variation[0], variation_absolue=variation[1],
        )
        for cle, libelle, valeur, unite, format_, sens, aide, variation in definitions
    ]
