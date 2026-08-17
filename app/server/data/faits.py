"""Source de faits **effective** : la table de détail vue à travers le paramétrage.

Le key-user peut exclure une référence de l'analyse et désactiver ou corriger
une ligne de nomenclature (écrans « Base article » et « Nomenclature »). Ces
arbitrages doivent produire leur effet partout — indicateurs, graphiques,
grilles, export, assistant — et immédiatement.

Deux façons de faire, et le choix compte :

1. **Recalculer le modèle** à chaque arbitrage. Le résultat serait matérialisé,
   donc rapide, mais il faudrait relancer un job pour voir l'effet d'une case
   cochée, et l'arbitrage deviendrait irréversible entre deux exécutions.
2. **Appliquer les surcharges à la lecture**, ici. C'est ce qui est fait : la
   constante ``FACT`` du dépôt n'est plus un nom de table mais une table
   dérivée, qui joint la table de détail aux deux tables de paramétrage et
   recalcule les colonnes concernées. Le reste du dépôt continue d'écrire
   ``FROM {FACT} f`` sans rien savoir de ce mécanisme.

Le recalcul est EXACT, et non une approximation : le modèle gold pose
``conso_theorique = qty_parent_produite × coef_bom`` puis en dérive tout le
reste (``src/sql/gold/30_fact_ecart_backflush.sql``). Substituer le coefficient
et rejouer ces trois formules redonne donc précisément ce qu'aurait produit un
recalcul complet. Toute évolution de ces formules côté gold doit être répercutée
ici — c'est le prix de ce choix, et ``tests/test_faits.py`` le rappelle.

Performance : la table dérivée n'est qu'une projection au-dessus d'une jointure
externe et d'une anti-jointure, sans agrégat ni ``LIMIT``. Le planificateur
PostgreSQL la « remonte » (subquery pull-up) dans la requête appelante, si bien
que les prédicats de date et de dimension continuent d'attaquer les index de
``fact_ecart_backflush``. Ce n'est pas une supposition : ``test_faits.py``
exécute un ``EXPLAIN`` et échoue si le balayage séquentiel revient.
"""

from __future__ import annotations

__all__ = [
    "COLONNES_FAIT",
    "COLONNES_FAIT_OF",
    "COLONNES_RECALCULEES",
    "SOURCE_FAITS",
    "SOURCE_FAITS_OF",
    "TABLE_ARTICLE_EXCLU",
    "TABLE_DETAIL",
    "TABLE_DETAIL_OF",
    "TABLE_NOMENCLATURE_PARAM",
]

TABLE_DETAIL = "fact_ecart_backflush"
TABLE_DETAIL_OF = "fact_ecart_of"
TABLE_ARTICLE_EXCLU = "param_article_exclu"
TABLE_NOMENCLATURE_PARAM = "param_nomenclature"

#: Colonnes de la table de détail, dans l'ordre du schéma.
#:
#: Recopiées ici plutôt qu'importées de ``src.jobs.lakebase_schema`` : Databricks
#: Apps déploie le CONTENU de ``app/`` à la racine du conteneur, où ``src/``
#: n'existe pas. Un import du schéma passerait tous les tests en local puis
#: échouerait au démarrage en production — la panne exacte que ce projet a déjà
#: connue. ``tests/test_faits.py`` compare cette liste au schéma et échoue si
#: les deux divergent.
COLONNES_FAIT: tuple[str, ...] = (
    "semaine_debut",
    "parent_itemid",
    "child_itemid",
    "annee",
    "semaine",
    "parent_programme",
    "parent_perimetre",
    "parent_name",
    "parent_categorie",
    "child_name",
    "child_categorie",
    "child_programme",
    "child_unite",
    "coef_bom",
    "qty_parent_produite",
    "conso_reelle",
    "conso_theorique",
    "ecart_brut",
    "ecart_pct",
    "type_ecart",
    "statut_ligne",
    "child_cout_standard",
    "ecart_valorise",
    "is_coef_uniforme",
    "ecart_equivalent_produit",
    "nb_transactions_conso",
    "nb_retours",
    "loaded_at",
)

#: Condition d'existence d'une surcharge de coefficient sur la ligne.
_SURCHARGE = "o.coef_bom IS NOT NULL"


def _si_surcharge(recalcul: str, colonne: str) -> str:
    """Recalcule uniquement quand un coefficient a été substitué.

    Sans surcharge, la valeur du modèle est reprise TELLE QUELLE plutôt que
    recalculée à l'identique. Ce n'est pas une optimisation : les colonnes du
    modèle sont typées (``numeric(12,4)`` pour un pourcentage, ``numeric(38,6)``
    pour une quantité) et rejouer la formule produit une valeur légèrement plus
    précise, donc différente. L'application se comporterait alors autrement
    selon qu'un paramétrage existe ou non quelque part dans la base — et le
    contrôle de réconciliation agrégat / détail, calé sur les valeurs écrites,
    finirait par diverger d'un centième.

    Conséquence recherchée : tant que personne ne paramètre rien, les chiffres
    sont au bit près ceux d'avant l'introduction de ce mécanisme.
    """
    return f"CASE WHEN {_SURCHARGE} THEN {recalcul} ELSE b.{colonne} END"


#: Coefficient retenu : celui de la surcharge, sinon celui de la nomenclature.
_COEF = "COALESCE(o.coef_bom, b.coef_bom)"

_CONSO_TH = _si_surcharge("b.qty_parent_produite * o.coef_bom", "conso_theorique")
_ECART = f"(({_CONSO_TH}) - b.conso_reelle)"

#: Colonnes dont la valeur est recalculée à la lecture. Les autres sont reprises
#: telles quelles. ``type_ecart`` n'y figure pas : l'application ne lit jamais la
#: colonne figée à l'ingestion, elle recalcule le type depuis le seuil actif
#: (``domain.filters.expression_type_ecart``).
COLONNES_RECALCULEES: dict[str, str] = {
    "coef_bom": _COEF,
    "conso_theorique": _CONSO_TH,
    "ecart_brut": _si_surcharge(_ECART, "ecart_brut"),
    "ecart_pct": _si_surcharge(
        f"CASE WHEN ({_CONSO_TH}) > 0 THEN ({_ECART} / ({_CONSO_TH})) * 100 END",
        "ecart_pct",
    ),
    "ecart_valorise": _si_surcharge(
        f"{_ECART} * COALESCE(b.child_cout_standard, 0)", "ecart_valorise"
    ),
    "ecart_equivalent_produit": _si_surcharge(
        f"CASE WHEN b.is_coef_uniforme AND ({_COEF}) > 0 THEN {_ECART} / ({_COEF}) END",
        "ecart_equivalent_produit",
    ),
}


#: Colonnes de la table de détail PAR ORDRE DE FABRICATION.
#:
#: Même grain que la précédente, plus l'OF — et sans les colonnes que la maille
#: OF ne porte pas (`child_programme`). Recopiée ici pour la même raison, et
#: vérifiée par le même test.
COLONNES_FAIT_OF: tuple[str, ...] = (
    "semaine_debut",
    "prod_id",
    "parent_itemid",
    "child_itemid",
    "annee",
    "semaine",
    "prod_bomid",
    "prod_date_cloture",
    "prod_statut",
    "parent_programme",
    "parent_perimetre",
    "parent_name",
    "parent_categorie",
    "child_name",
    "child_categorie",
    "child_unite",
    "coef_bom",
    "qty_parent_produite",
    "conso_reelle",
    "conso_theorique",
    "ecart_brut",
    "ecart_pct",
    "type_ecart",
    "statut_ligne",
    "child_cout_standard",
    "ecart_valorise",
    "is_coef_uniforme",
    "ecart_equivalent_produit",
    "nb_transactions_conso",
    "nb_retours",
    "loaded_at",
)


def _projection(colonnes: tuple[str, ...]) -> str:
    lignes = [
        f"           {COLONNES_RECALCULEES[nom]} AS {nom}"
        if nom in COLONNES_RECALCULEES
        else f"           b.{nom}"
        for nom in colonnes
    ]
    return ",\n".join(lignes).lstrip()


def construire_source(table: str, colonnes: tuple[str, ...]) -> str:
    """Assemble la table dérivée. Extraite pour être lisible dans un test.

    Le même paramétrage s'applique aux deux mailles : sans cela, l'écran de
    détail par OF afficherait des références que l'utilisateur a exclues, et ses
    totaux ne correspondraient plus à ceux de l'écran voisin.
    """
    return f"""(
    SELECT {_projection(colonnes)}
    FROM {table} b
    LEFT JOIN {TABLE_NOMENCLATURE_PARAM} o
           ON o.parent_itemid = b.parent_itemid
          AND o.child_itemid  = b.child_itemid
    WHERE COALESCE(o.active, TRUE)
      AND NOT EXISTS (
              SELECT 1 FROM {TABLE_ARTICLE_EXCLU} x
              WHERE x.item_id = b.child_itemid OR x.item_id = b.parent_itemid
          )
)"""


#: Tables dérivées à substituer aux noms de tables. Construites une fois à
#: l'import : c'est du SQL statique, sans paramètre ni valeur utilisateur.
SOURCE_FAITS = construire_source(TABLE_DETAIL, COLONNES_FAIT)
SOURCE_FAITS_OF = construire_source(TABLE_DETAIL_OF, COLONNES_FAIT_OF)
