"""Dictionnaire des grilles et du modèle métier.

Deux usages, une seule source :

* **UI** — le frontend appelle ``/api/meta/grilles`` et construit ses colonnes à
  partir d'ici. Ajouter une colonne à une grille se fait donc en un seul endroit,
  côté serveur, sans toucher au React.
* **Sécurité** — la liste des colonnes triables est dérivée de ce dictionnaire.
  Un tri sur une colonne inconnue est rejeté avant d'atteindre la base.
* **Assistant IA** — :func:`contexte_metier` sérialise le modèle et les règles
  de calcul pour le prompt système.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

TypeColonne = Literal["texte", "entier", "decimal", "euro", "pourcent", "date", "booleen", "badge"]
Alignement = Literal["gauche", "droite", "centre"]


class Colonne(BaseModel):
    """Description d'une colonne de grille, indépendante de toute technologie d'affichage."""

    cle: str
    libelle: str
    type: TypeColonne
    alignement: Alignement = "gauche"
    #: Nombre de décimales pour les types numériques.
    decimales: int = 0
    #: Colonne triable côté serveur.
    triable: bool = True
    #: Colonne affichée par défaut (les autres restent accessibles via le sélecteur).
    visible: bool = True
    #: Largeur indicative en pixels.
    largeur: int = 120
    #: Définition métier, affichée au survol de l'en-tête.
    aide: str = ""


class Grille(BaseModel):
    """Définition complète d'une grille."""

    cle: str
    libelle: str
    description: str
    #: Clés composant l'identité d'une ligne (utilisé pour la sélection).
    cle_ligne: list[str]
    tri_defaut: str
    sens_defaut: Literal["asc", "desc"] = "desc"
    colonnes: list[Colonne]

    @property
    def colonnes_triables(self) -> set[str]:
        return {colonne.cle for colonne in self.colonnes if colonne.triable}


# ---------------------------------------------------------------------------
# Colonnes réutilisées
# ---------------------------------------------------------------------------
_SEMAINE = Colonne(
    cle="semaine_libelle", libelle="Semaine", type="texte", largeur=90, triable=False,
    aide="Semaine ISO du mouvement, au format AAAA-Sxx.",
)
_SEMAINE_DEBUT = Colonne(
    cle="semaine_debut", libelle="Lundi", type="date", largeur=100,
    aide="Lundi de la semaine ISO. Toutes les bornes de date portent sur ce champ.",
)
_ECART_BRUT = Colonne(
    cle="ecart_brut", libelle="Écart", type="decimal", alignement="droite", decimales=2, largeur=110,
    aide="Consommation théorique − consommation réelle, dans l'unité du composant.",
)
_ECART_VALORISE = Colonne(
    cle="ecart_valorise", libelle="Impact €", type="euro", alignement="droite", decimales=2,
    largeur=120,
    aide="Écart × coût standard du composant. Positif = valeur non déduite du stock.",
)


# ---------------------------------------------------------------------------
# Grilles
# ---------------------------------------------------------------------------
GRILLE_DETAIL = Grille(
    cle="details",
    libelle="Détail des écarts",
    description=(
        "Granularité d'audit : une ligne par article parent, composant et semaine. "
        "C'est le niveau auquel une investigation atelier se mène."
    ),
    cle_ligne=["semaine_debut", "parent_itemid", "child_itemid"],
    tri_defaut="ecart_valorise_absolu",
    colonnes=[
        _SEMAINE,
        _SEMAINE_DEBUT,
        Colonne(cle="parent_programme", libelle="Programme", type="texte", largeur=110,
                aide="Programme produit du parent (M3, M2BEV, …)."),
        Colonne(cle="parent_perimetre", libelle="Périmètre", type="texte", largeur=160,
                aide="Ligne de production du parent. Maille d'homogénéité du "
                     "coefficient de nomenclature."),
        Colonne(cle="parent_itemid", libelle="Réf. parent", type="texte", largeur=140),
        Colonne(cle="parent_name", libelle="Désignation parent", type="texte", largeur=200,
                visible=False),
        Colonne(cle="child_itemid", libelle="Réf. composant", type="texte", largeur=150),
        Colonne(cle="child_name", libelle="Désignation composant", type="texte", largeur=220),
        Colonne(cle="child_categorie", libelle="Catégorie", type="texte", largeur=110),
        Colonne(cle="coef_bom", libelle="Coef BOM", type="decimal", alignement="droite",
                decimales=4, largeur=100,
                aide="Quantité de composant par unité de parent, issue de la nomenclature active."),
        Colonne(cle="qty_parent_produite", libelle="Prod. parent", type="decimal",
                alignement="droite", decimales=0, largeur=110,
                aide="Quantité de parent entrée en stock sur la semaine."),
        Colonne(cle="conso_theorique", libelle="Théorique", type="decimal", alignement="droite",
                decimales=2, largeur=110, aide="Production parent × coefficient BOM."),
        Colonne(cle="conso_reelle", libelle="Réel", type="decimal", alignement="droite",
                decimales=2, largeur=110,
                aide="Quantité nette sortie du stock par le backflush (retours déduits)."),
        _ECART_BRUT,
        Colonne(cle="ecart_pct", libelle="Écart %", type="pourcent", alignement="droite",
                decimales=1, largeur=95,
                aide="Écart ÷ consommation théorique. Vide si le théorique est nul."),
        Colonne(cle="type_ecart", libelle="Type", type="badge", largeur=140,
                aide="Recalculé selon le seuil de conformité actif, pas figé à l'ingestion."),
        Colonne(cle="statut_ligne", libelle="Statut", type="badge", largeur=150, visible=False,
                aide="Nominal · Hors nomenclature (sortie sans ligne BOM) · "
                     "Sans consommation (BOM sans sortie sur la semaine)."),
        Colonne(cle="child_unite", libelle="Unité", type="texte", alignement="centre",
                largeur=70, visible=False),
        Colonne(cle="ecart_equivalent_produit", libelle="Éq. produit", type="decimal",
                alignement="droite", decimales=1, largeur=110,
                aide="Écart ÷ coefficient BOM : nombre d'unités parent équivalentes. "
                     "Calculé uniquement si le coefficient est uniforme sur le programme."),
        Colonne(cle="child_cout_standard", libelle="Coût std", type="euro", alignement="droite",
                decimales=4, largeur=100, visible=False),
        _ECART_VALORISE,
    ],
)

GRILLE_COMPOSANTS = Grille(
    cle="composants",
    libelle="Références composant",
    description=(
        "Un composant peut être sain sur un programme et dériver sur un autre : "
        "l'agrégation conserve donc l'axe programme."
    ),
    # Le grain complet de la grille : le périmètre ne suffit pas à impliquer le
    # programme, car les parents dont la ligne de production est inconnue sont
    # tous regroupés sous « NON RENSEIGNE », tous programmes confondus.
    cle_ligne=["child_itemid", "parent_programme", "parent_perimetre"],
    tri_defaut="ecart_valorise_absolu",
    colonnes=[
        Colonne(cle="child_itemid", libelle="Réf. composant", type="texte", largeur=150),
        Colonne(cle="child_name", libelle="Désignation", type="texte", largeur=240),
        Colonne(cle="child_categorie", libelle="Catégorie", type="texte", largeur=110),
        Colonne(cle="parent_programme", libelle="Programme", type="texte", largeur=110),
        Colonne(cle="parent_perimetre", libelle="Périmètre", type="texte", largeur=160),
        Colonne(cle="coef_bom", libelle="Coef BOM", type="decimal", alignement="droite",
                decimales=4, largeur=100),
        Colonne(cle="is_coef_uniforme", libelle="Coef uniforme", type="booleen",
                alignement="centre", largeur=110,
                aide="Faux ⇒ l'écart en équivalent produit n'est pas calculable."),
        Colonne(cle="nb_parents", libelle="Parents", type="entier", alignement="droite",
                largeur=80),
        Colonne(cle="nb_semaines", libelle="Semaines", type="entier", alignement="droite",
                largeur=90, aide="Nombre de semaines avec activité sur la période filtrée."),
        Colonne(cle="nb_lignes_ecart", libelle="Lignes en écart", type="entier",
                alignement="droite", largeur=120),
        Colonne(cle="conso_theorique", libelle="Théorique", type="decimal", alignement="droite",
                decimales=1, largeur=110),
        Colonne(cle="conso_reelle", libelle="Réel", type="decimal", alignement="droite",
                decimales=1, largeur=110),
        Colonne(cle="ecart_net", libelle="Écart net", type="decimal", alignement="droite",
                decimales=1, largeur=110),
        Colonne(cle="ecart_pct_global", libelle="Écart %", type="pourcent", alignement="droite",
                decimales=1, largeur=95),
        Colonne(cle="non_consommation", libelle="Non-conso", type="decimal", alignement="droite",
                decimales=1, largeur=110, visible=False),
        Colonne(cle="surconsommation", libelle="Surconso", type="decimal", alignement="droite",
                decimales=1, largeur=110, visible=False),
        Colonne(cle="ecart_equivalent_produit", libelle="Éq. produit", type="decimal",
                alignement="droite", decimales=1, largeur=110),
        Colonne(cle="ecart_valorise", libelle="Impact net €", type="euro", alignement="droite",
                decimales=2, largeur=130),
        Colonne(cle="ecart_valorise_absolu", libelle="Impact absolu €", type="euro",
                alignement="droite", decimales=2, largeur=140,
                aide="Somme des |impacts| : mesure l'ampleur du désordre, sans compensation."),
    ],
)

GRILLE_PROGRAMMES = Grille(
    cle="programmes",
    libelle="Programmes",
    description="Agrégat par programme et semaine — la maille de pilotage hebdomadaire.",
    cle_ligne=["semaine_debut", "parent_programme"],
    tri_defaut="ecart_valorise_absolu",
    colonnes=[
        _SEMAINE,
        _SEMAINE_DEBUT,
        Colonne(cle="parent_programme", libelle="Programme", type="texte", largeur=130),
        Colonne(cle="nb_parents", libelle="Parents", type="entier", alignement="droite",
                largeur=90),
        Colonne(cle="nb_composants", libelle="Composants", type="entier", alignement="droite",
                largeur=110),
        Colonne(cle="nb_lignes", libelle="Lignes", type="entier", alignement="droite",
                largeur=90),
        Colonne(cle="nb_lignes_ecart", libelle="Lignes en écart", type="entier",
                alignement="droite", largeur=120),
        Colonne(cle="taux_conformite", libelle="Conformité", type="pourcent",
                alignement="droite", decimales=1, largeur=110),
        Colonne(cle="conso_theorique", libelle="Théorique", type="decimal", alignement="droite",
                decimales=0, largeur=120),
        Colonne(cle="conso_reelle", libelle="Réel", type="decimal", alignement="droite",
                decimales=0, largeur=120),
        Colonne(cle="ecart_net", libelle="Écart net", type="decimal", alignement="droite",
                decimales=1, largeur=110),
        Colonne(cle="non_consommation_valorisee", libelle="Non-conso €", type="euro",
                alignement="droite", decimales=0, largeur=130),
        Colonne(cle="surconsommation_valorisee", libelle="Surconso €", type="euro",
                alignement="droite", decimales=0, largeur=130),
        Colonne(cle="ecart_valorise", libelle="Impact net €", type="euro", alignement="droite",
                decimales=0, largeur=130),
        Colonne(cle="ecart_valorise_absolu", libelle="Impact absolu €", type="euro",
                alignement="droite", decimales=0, largeur=140),
    ],
)

GRILLE_PERIMETRES = Grille(
    cle="perimetres",
    libelle="Périmètres",
    description=(
        "Agrégat par périmètre (ligne de production) et semaine. Maille "
        "intermédiaire entre le programme et la référence : c'est le niveau où "
        "le coefficient de nomenclature est homogène, donc où l'écart en "
        "équivalent produit a un sens."
    ),
    cle_ligne=["semaine_debut", "parent_perimetre"],
    tri_defaut="ecart_valorise_absolu",
    colonnes=[
        _SEMAINE,
        _SEMAINE_DEBUT,
        Colonne(cle="parent_perimetre", libelle="Périmètre", type="texte", largeur=180),
        Colonne(cle="parent_programme", libelle="Programme", type="texte", largeur=120),
        Colonne(cle="nb_parents", libelle="Parents", type="entier", alignement="droite",
                largeur=90),
        Colonne(cle="nb_composants", libelle="Composants", type="entier", alignement="droite",
                largeur=110),
        Colonne(cle="nb_lignes", libelle="Lignes", type="entier", alignement="droite",
                largeur=90),
        Colonne(cle="nb_lignes_ecart", libelle="Lignes en écart", type="entier",
                alignement="droite", largeur=120),
        Colonne(cle="taux_conformite", libelle="Conformité", type="pourcent",
                alignement="droite", decimales=1, largeur=110),
        Colonne(cle="qty_produite", libelle="Production", type="decimal", alignement="droite",
                decimales=0, largeur=110,
                aide="Quantité de parents produits sur le périmètre et la semaine."),
        Colonne(cle="conso_theorique", libelle="Théorique", type="decimal", alignement="droite",
                decimales=0, largeur=120),
        Colonne(cle="conso_reelle", libelle="Réel", type="decimal", alignement="droite",
                decimales=0, largeur=120),
        Colonne(cle="ecart_net", libelle="Écart net", type="decimal", alignement="droite",
                decimales=1, largeur=110),
        Colonne(cle="ecart_equivalent_produit", libelle="Équiv. produit", type="decimal",
                alignement="droite", decimales=1, largeur=130,
                aide="Écart converti en unités de produit fini, sur les composants "
                     "à coefficient uniforme dans le périmètre."),
        Colonne(cle="non_consommation_valorisee", libelle="Non-conso €", type="euro",
                alignement="droite", decimales=0, largeur=130),
        Colonne(cle="surconsommation_valorisee", libelle="Surconso €", type="euro",
                alignement="droite", decimales=0, largeur=130),
        Colonne(cle="ecart_valorise", libelle="Impact net €", type="euro", alignement="droite",
                decimales=0, largeur=130),
        Colonne(cle="ecart_valorise_absolu", libelle="Impact absolu €", type="euro",
                alignement="droite", decimales=0, largeur=140),
    ],
)

GRILLE_PARENTS = Grille(
    cle="parents",
    libelle="Articles parents",
    description="Agrégat par article parent — utile pour cibler une ligne de production.",
    cle_ligne=["parent_itemid"],
    tri_defaut="ecart_valorise_absolu",
    colonnes=[
        Colonne(cle="parent_itemid", libelle="Réf. parent", type="texte", largeur=150),
        Colonne(cle="parent_name", libelle="Désignation", type="texte", largeur=240),
        Colonne(cle="parent_programme", libelle="Programme", type="texte", largeur=120),
        Colonne(cle="parent_perimetre", libelle="Périmètre", type="texte", largeur=170),
        Colonne(cle="nb_composants", libelle="Composants", type="entier", alignement="droite",
                largeur=110),
        Colonne(cle="nb_semaines", libelle="Semaines", type="entier", alignement="droite",
                largeur=90),
        Colonne(cle="qty_produite", libelle="Production", type="decimal", alignement="droite",
                decimales=0, largeur=110,
                aide="Somme des quantités de parent produites sur la période."),
        Colonne(cle="nb_lignes_ecart", libelle="Lignes en écart", type="entier",
                alignement="droite", largeur=120),
        Colonne(cle="conso_theorique", libelle="Théorique", type="decimal", alignement="droite",
                decimales=0, largeur=120),
        Colonne(cle="conso_reelle", libelle="Réel", type="decimal", alignement="droite",
                decimales=0, largeur=120),
        Colonne(cle="ecart_net", libelle="Écart net", type="decimal", alignement="droite",
                decimales=1, largeur=110),
        Colonne(cle="ecart_valorise", libelle="Impact net €", type="euro", alignement="droite",
                decimales=0, largeur=130),
        Colonne(cle="ecart_valorise_absolu", libelle="Impact absolu €", type="euro",
                alignement="droite", decimales=0, largeur=140),
    ],
)

GRILLES: dict[str, Grille] = {
    grille.cle: grille
    for grille in (
        GRILLE_DETAIL, GRILLE_COMPOSANTS, GRILLE_PROGRAMMES,
        GRILLE_PERIMETRES, GRILLE_PARENTS,
    )
}


# ---------------------------------------------------------------------------
# Contexte métier pour l'assistant IA
# ---------------------------------------------------------------------------
REGLES_METIER = """\
DÉFINITION DE L'ÉCART DE BACKFLUSH
  écart = consommation théorique − consommation réelle
        = (quantité de parent produite × coefficient de nomenclature)
          − quantité de composant sortie du stock
  écart > 0 → « Non-consommation » : le stock système est surévalué ; le
              backflush n'a pas déduit tout ce que la nomenclature prévoyait.
  écart < 0 → « Surconsommation »  : plus de composant est sorti que prévu
              (rebut non déclaré, erreur de nomenclature, servitude, vol).
  |écart| ≤ seuil → « Conforme ». Seuil par défaut : 0,5 unité, réglable.

STATUT DE LIGNE (complémentaire du type d'écart)
  Nominal            : la nomenclature et la consommation existent toutes deux.
  Hors nomenclature  : composant sorti sur un OF sans ligne BOM correspondante.
                       100 % de surconsommation ; signale une erreur de saisie
                       d'ordre de fabrication ou une nomenclature obsolète.
  Sans consommation  : ligne BOM sans aucune sortie sur la semaine.
                       100 % de non-consommation ; typiquement un backflush non
                       exécuté ou un OF non clôturé.

VALORISATION
  impact € = écart × coût standard du composant. Un composant sans coût standard
  contribue pour 0 € : l'impact réel est donc SOUS-ESTIMÉ, ce que signale le
  contrôle qualité « composant_sans_cout_standard ».

ÉQUIVALENT PRODUIT FABRIQUÉ
  éq. produit = écart ÷ coefficient BOM. Nombre d'unités de parent « manquantes »
  ou « en trop » vues depuis ce composant. Calculé UNIQUEMENT si le coefficient
  est uniforme pour tous les parents du programme (is_coef_uniforme = vrai) ;
  sinon la division n'a pas de sens physique et la valeur est vide.

PÉRIMÈTRE ET LIMITES CONNUES
  - L'analyse est hebdomadaire (semaine ISO, lundi), par plage de dates, et NON
    par ordre de fabrication : un OF à cheval sur deux semaines répartit sa
    production et sa consommation sur les deux.
  - Les mouvements sont datés par invent_trans.datephysical.
  - Le taux de rebut de nomenclature (scrapvar) n'est PAS pris en compte : une
    surconsommation peut donc correspondre à un rebut prévu au paramétrage.
  - Une seule version de nomenclature active est retenue par article parent.
  - Les retours de composant au stock viennent en déduction de la consommation
    de la semaine (consommation nette).
"""


def contexte_metier() -> str:
    """Description compacte du modèle et des règles, pour le prompt système."""
    lignes = [REGLES_METIER, "", "GRILLES DISPONIBLES ET LEURS COLONNES", ""]
    for grille in GRILLES.values():
        lignes.append(f"* {grille.cle} — {grille.libelle} : {grille.description}")
        lignes.append(
            "  colonnes : " + ", ".join(colonne.cle for colonne in grille.colonnes)
        )
    return "\n".join(lignes)
