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
    "COLONNES_RECALCULEES",
    "SOURCE_FAITS",
    "TABLE_ARTICLE_EXCLU",
    "TABLE_DETAIL",
    "TABLE_NOMENCLATURE_PARAM",
]

TABLE_DETAIL = "fact_ecart_backflush"
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

#: Coefficient retenu : celui de la surcharge, sinon celui de la nomenclature.
_COEF = "COALESCE(o.coef_bom, b.coef_bom)"

#: Consommation théorique recalculée quand le coefficient est surchargé.
#: Sans surcharge, on conserve la valeur du modèle plutôt que de la recalculer :
#: une multiplication en numeric(38,6) n'est pas garantie de redonner au dernier
#: chiffre ce que Spark a écrit, et un écart de 10⁻⁶ ferait échouer le contrôle
#: de réconciliation agrégat / détail.
_CONSO_TH = (
    "CASE WHEN o.coef_bom IS NOT NULL "
    "THEN b.qty_parent_produite * o.coef_bom ELSE b.conso_theorique END"
)

_ECART = f"(({_CONSO_TH}) - b.conso_reelle)"

#: Colonnes dont la valeur est recalculée à la lecture. Les autres sont reprises
#: telles quelles. ``type_ecart`` n'y figure pas : l'application ne lit jamais la
#: colonne figée à l'ingestion, elle recalcule le type depuis le seuil actif
#: (``domain.filters.expression_type_ecart``).
COLONNES_RECALCULEES: dict[str, str] = {
    "coef_bom": _COEF,
    "conso_theorique": _CONSO_TH,
    "ecart_brut": _ECART,
    "ecart_pct": f"CASE WHEN ({_CONSO_TH}) > 0 THEN ({_ECART} / ({_CONSO_TH})) * 100 END",
    "ecart_valorise": f"{_ECART} * COALESCE(b.child_cout_standard, 0)",
    "ecart_equivalent_produit": (
        f"CASE WHEN b.is_coef_uniforme AND ({_COEF}) > 0 THEN {_ECART} / ({_COEF}) END"
    ),
}


def _projection() -> str:
    lignes = [
        f"           {COLONNES_RECALCULEES[nom]} AS {nom}"
        if nom in COLONNES_RECALCULEES
        else f"           b.{nom}"
        for nom in COLONNES_FAIT
    ]
    return ",\n".join(lignes).lstrip()


def construire_source() -> str:
    """Assemble la table dérivée. Extraite pour être lisible dans un test."""
    return f"""(
    SELECT {_projection()}
    FROM {TABLE_DETAIL} b
    LEFT JOIN {TABLE_NOMENCLATURE_PARAM} o
           ON o.parent_itemid = b.parent_itemid
          AND o.child_itemid  = b.child_itemid
    WHERE COALESCE(o.active, TRUE)
      AND NOT EXISTS (
              SELECT 1 FROM {TABLE_ARTICLE_EXCLU} x
              WHERE x.item_id = b.child_itemid OR x.item_id = b.parent_itemid
          )
)"""


#: Table dérivée à substituer au nom de la table de détail. Construite une fois
#: à l'import : c'est du SQL statique, sans paramètre ni valeur utilisateur.
SOURCE_FAITS = construire_source()
