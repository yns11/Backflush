# Intégrer l'écart backflush aux campagnes d'inventaire

**Guide technique du chantier Backflush.** Ce que mesure l'écart backflush, comment
il est calculé, comment ses données sont structurées dans Unity Catalog, et comment
une application de campagnes d'inventaire peut les lire, les figer et s'en servir
pour expliquer une partie de l'écart entre stock ERP et stock compté.

| | |
|---|---|
| Source | `emotors_data_champions.backflush` |
| Grain de la table de faits | parent × composant × semaine ISO |
| Rafraîchissement | quotidien, 05:00 Europe/Paris |
| Début d'historique | 2026-03-30 |

> Ce document décrit l'existant côté Backflush et propose une intégration. Il ne
> modifie ni le modèle gold, ni l'application Backflush.

---

## 1. Ce qu'est l'écart backflush

En production, la sortie de stock des composants n'est pas saisie ligne à ligne.
Elle est **déduite automatiquement** de la déclaration de production : quand un ordre
de fabrication déclare 100 parents terminés, D365 retire du stock les composants
correspondants selon la nomenclature. C'est le *backflush*, ou déduction rétrospective.

Ce mécanisme repose sur une hypothèse : la consommation réelle est égale à la
consommation théorique. L'**écart backflush** est exactement la mesure de cette
hypothèse.

```
écart = consommation théorique − consommation réelle
      = (qté parent produite × coefficient nomenclature) − qté composant sortie du stock
```

Les deux termes sont mesurés sur des mouvements de stock réels, jamais sur une
intention : la production vient des entrées en stock déclarées, la consommation des
sorties enregistrées sur les lignes de nomenclature des OF.

### Un axe signé — c'est tout l'intérêt pour l'inventaire

| Signe | Type | Ce qui s'est passé | Conséquence sur le stock |
|---|---|---|---|
| `écart > 0` | **Non-consommation** | Le backflush a déduit *moins* que le théorique : le composant a quitté le magasin sans être entièrement enregistré. | Stock système **surévalué**. Au comptage, on trouvera *moins* que l'ERP. |
| `écart < 0` | **Surconsommation** | Le backflush a déduit *plus* que le théorique : rebut non déclaré, servitude hors nomenclature, coefficient erroné, correction manuelle. | Stock système **sous-évalué**. Au comptage, on trouvera *plus* que l'ERP. |

Entre les deux, une bande de tolérance : au-delà de `0,5` unité en valeur absolue
(paramètre `seuil_conformite` du pipeline), la ligne est classée `Non-consommation`
ou `Surconsommation` ; en deçà, `Conforme`. Ce seuil ne modifie jamais l'écart
lui-même, seulement son étiquette.

> **La relation à retenir.** Un écart backflush positif sur un composant prédit un
> écart d'inventaire **négatif** du même ordre de grandeur — et réciproquement.
> Toute la section 6 découle de cette inversion de signe.

---

## 2. Comment il est calculé

### 2.1 La production déclarée

Entrées en stock d'ordres de fabrication (`InventTrans` jointe à `InventTransOrigin`
sur `referencecategory = 2`, quantité positive), agrégées par article parent et par
semaine ISO, sur la date de mouvement **physique**. La semaine est datée par son
lundi (`semaine_debut`) ; l'année portée est l'année ISO, pas l'année civile.

### 2.2 La consommation réelle

Mouvements sur ligne de nomenclature d'OF (`referencecategory = 8`), rattachés au
parent via `ProdTable`, puis agrégés par parent × composant × semaine. Deux
conventions comptent :

- **Le signe est inversé à la source.** Les sorties, négatives dans l'ERP, sont
  stockées positives, pour que « théorique − réel » ait le sens attendu sans piège
  de signe en aval.
- **Les retours au stock sont nets, pas ignorés.** Un retour de composant est un
  dé-backflush ; il vient en déduction de la consommation de la semaine. Les ignorer
  ferait apparaître toute correction d'erreur comme une surconsommation permanente.
  Leur nombre est conservé (`nb_retours`).

### 2.3 La consommation théorique

La production de la semaine est explosée sur la nomenclature aplatie
(`dim_nomenclature`). Deux règles ont un effet direct sur les chiffres :

- **Une seule version de nomenclature par parent.** Un parent peut porter plusieurs
  versions actives ; les cumuler multiplierait la consommation théorique. La version
  est choisie de façon déterministe : snapshot le plus récent, puis nom de version,
  puis `bomid`.
- **Les lignes multiples d'un même composant sont sommées** — deux emplacements de
  montage donnent un coefficient unique, jamais deux lignes.

### 2.4 La réconciliation

Théorique et réel sont rapprochés par une **jointure complète** (`FULL OUTER JOIN`)
sur (semaine, parent, composant). Une jointure interne masquerait les deux anomalies
les plus coûteuses en gestion de stock. La colonne `statut_ligne` les nomme :

| `statut_ligne` | Situation | Conséquence |
|---|---|---|
| `Nominal` | Composant prévu par la nomenclature et consommé. | Écart = différence des deux quantités. |
| `Hors nomenclature` | Composant sorti sur un OF sans ligne de nomenclature. | Théorique = 0, `coef_bom` nul : **100 % de surconsommation**. |
| `Sans consommation` | Composant prévu mais jamais sorti sur la semaine. | Réel = 0 : **100 % de non-consommation**. |

Ces trois statuts sont du signal, pas du bruit : les exclure d'une somme
supprimerait précisément les cas où le stock système a le plus dérivé.

### 2.5 Les mesures dérivées

```
ecart_brut               = conso_theorique − conso_reelle
ecart_pct                = ecart_brut / conso_theorique × 100   (NULL si théorique = 0)
ecart_valorise           = ecart_brut × child_cout_standard     (coût manquant traité comme 0)
ecart_equivalent_produit = ecart_brut / coef_bom                (si le coefficient est uniforme)
```

L'équivalent produit exprime l'écart en nombre d'unités parent « manquantes » vu
depuis ce composant. Il n'a de sens que si le coefficient est identique pour tous les
parents du **périmètre** (la ligne de production) — d'où le drapeau
`is_coef_uniforme`. Cette mesure n'a pas d'usage direct côté inventaire, qui raisonne
sur le composant.

---

## 3. L'architecture des données dans Unity Catalog

Tout est publié dans le schéma `emotors_data_champions.backflush`, en Delta, par un
job Databricks quotidien. Chaque table est reconstruite intégralement
(`CREATE OR REPLACE`) : ce n'est pas un flux incrémental, c'est un recalcul complet
de l'historique à chaque exécution — point important pour la section 5.

### 3.1 Tables publiées

| Table | Grain | Rôle pour le chantier |
|---|---|---|
| `fact_ecart_backflush` | parent × composant × semaine | **La source à utiliser.** Seule table portant à la fois l'écart et un axe temporel filtrable. |
| `fact_ecart_of` | OF × parent × composant × semaine | Même écart, décomposé par ordre de fabrication. Utile pour justifier une ligne devant l'atelier, pas pour le calcul. |
| `agg_ecart_composant` | composant × programme × périmètre | Cumul sur **tout** l'historique, sans axe date : **inutilisable** pour une période choisie. |
| `agg_ecart_hebdo_programme` | programme × semaine | Suivi de tendance. Pas d'axe composant. |
| `dim_article`, `dim_nomenclature` | article / (parent, composant) | Libellés, unités, coût standard, coefficients. |
| `dq_controles`, `dq_details` | contrôle | Contrôles qualité du pipeline. À consulter avant de figer une campagne. |

### 3.2 Colonnes de `fact_ecart_backflush`

| Colonne | Type | Signification |
|---|---|---|
| `semaine_debut` | `date` | Lundi de la semaine ISO. **Clé primaire** avec les deux articles. |
| `annee` / `semaine` | `int` | Année ISO et numéro de semaine ISO. |
| `parent_itemid` | `text` | Article parent fabriqué. **Clé primaire.** |
| `child_itemid` | `text` | Composant. **Clé primaire.** C'est l'axe de l'inventaire. |
| `parent_programme` | `text` | Programme du parent. Jamais NULL (`NON RENSEIGNE` à défaut). |
| `parent_perimetre` | `text` | Ligne de production du parent. Jamais NULL. |
| `parent_name`, `parent_categorie` | `text` | Libellé et catégorie du parent. |
| `child_name`, `child_categorie` | `text` | Libellé et catégorie du composant. |
| `child_programme` | `text` | Programme du composant (peut différer de celui du parent). |
| `child_unite` | `text` | **Unité de la quantité d'écart.** À reprendre telle quelle côté inventaire. |
| `coef_bom` | `numeric(38,6)` | Coefficient nomenclature retenu. NULL si `Hors nomenclature`. |
| `qty_parent_produite` | `numeric` | Production du parent sur la semaine. **Répétée sur chaque ligne composant : ne jamais la sommer.** |
| `conso_theorique` | `numeric` | Production × coefficient. |
| `conso_reelle` | `numeric` | Sorties nettes de retours, en valeur positive. |
| `ecart_brut` | `numeric` | **La mesure centrale.** Théorique − réel, additive sur toutes les dimensions. |
| `ecart_pct` | `numeric(12,4)` | Écart relatif, arrondi. NULL si théorique nul. Non additif. |
| `type_ecart` | `text` | Étiquette au seuil de 0,5 unité : Non-consommation / Surconsommation / Conforme. |
| `statut_ligne` | `text` | Nominal / Hors nomenclature / Sans consommation. |
| `child_cout_standard` | `numeric(20,6)` | Coût standard du composant. **Peut être NULL.** |
| `ecart_valorise` | `numeric` | Écart × coût standard, coût manquant traité comme 0. |
| `is_coef_uniforme` | `bool` | Coefficient identique sur tout le périmètre du parent. |
| `ecart_equivalent_produit` | `numeric` | Écart converti en unités parent, si coefficient uniforme. |
| `nb_transactions_conso` | `int` | Nombre de mouvements de consommation agrégés. |
| `nb_retours` | `int` | Nombre de mouvements de retour compris dans le net. |
| `loaded_at` | `timestamptz` | **Horodatage de construction de la table.** À enregistrer avec chaque lecture figée. |

### 3.3 Fraîcheur et paramètres du pipeline

- **Rafraîchissement :** tous les jours à 05:00, fuseau Europe/Paris.
- **Début d'historique :** `2026-03-30`. Aucun mouvement antérieur n'est chargé.
- **Seuil de conformité :** 0,5 unité, appliqué à l'étiquette seulement.
- **Mode de reconstruction :** remplacement complet. Une semaine passée *peut*
  changer d'un jour à l'autre.

---

## 4. Lire l'écart par composant entre deux dates

### 4.1 Choisir les bornes

La période pertinente n'est pas une fenêtre arbitraire : c'est l'intervalle entre
**le comptage précédent validé** pour l'article et le comptage courant. C'est sur cet
intervalle, et lui seul, que le stock système a pu dériver depuis le dernier point de
vérité.

La table est à la maille **semaine**. Une date de comptage tombant un mercredi ne peut
donc pas être respectée au jour près : il faut la ramener au lundi de sa semaine, et
accepter une imprécision pouvant aller jusqu'à six jours à chaque borne. Cette
imprécision doit être affichée à l'utilisateur, pas absorbée en silence.

```
borne_debut = lundi de la semaine du comptage précédent   (incluse)
borne_fin   = lundi de la semaine du comptage courant     (exclue)
```

Borne haute exclue : la semaine du comptage courant est en cours au moment du
comptage, elle n'est pas close. L'inclure ferait entrer dans le calcul des mouvements
postérieurs au comptage.

### 4.2 La requête

```sql
SELECT
    f.child_itemid                        AS item_id,
    MAX(f.child_name)                     AS libelle,
    MAX(f.child_unite)                    AS unite,

    SUM(f.ecart_brut)                     AS ecart_backflush_net,
    SUM(GREATEST(f.ecart_brut,  0))       AS non_consommation,
    SUM(GREATEST(-f.ecart_brut, 0))       AS surconsommation,

    SUM(f.conso_theorique)                AS conso_theorique,
    SUM(f.conso_reelle)                   AS conso_reelle,

    COUNT(DISTINCT f.parent_itemid)       AS nb_parents,
    COUNT(DISTINCT f.semaine_debut)       AS nb_semaines,
    MIN(f.semaine_debut)                  AS premiere_semaine,
    MAX(f.semaine_debut)                  AS derniere_semaine,
    MAX(f.loaded_at)                      AS source_loaded_at
FROM emotors_data_champions.backflush.fact_ecart_backflush AS f
WHERE f.semaine_debut >= :borne_debut     -- lundi, inclus
  AND f.semaine_debut <  :borne_fin       -- lundi, exclu
GROUP BY f.child_itemid
```

`ecart_backflush_net` est la valeur à reprendre pour le recalcul. Les deux composantes
`non_consommation` et `surconsommation` ne servent pas au calcul mais à
l'interprétation : un net proche de zéro peut recouvrir des milliers d'unités qui se
compensent.

**Trois erreurs à ne pas commettre :**

1. **Filtrer sur `type_ecart <> 'Conforme'`** — cela écarterait des milliers de petits
   écarts dont la somme n'est pas petite. On somme `ecart_brut`, toutes lignes
   confondues.
2. **Filtrer sur `statut_ligne = 'Nominal'`** — cela supprimerait exactement les cas
   de dérive maximale (§ 2.4).
3. **Sommer `qty_parent_produite`** — elle est répétée sur chaque ligne composant du
   parent ; sa somme n'a aucun sens.

### 4.3 Restreindre à un sous-ensemble d'articles

```sql
  AND f.child_itemid IN (:liste_articles)
  -- ou, si la campagne est bornée par ligne de production :
  AND f.parent_perimetre IN (:liste_perimetres)
```

Attention à la seconde forme : elle borne par le **périmètre du parent**, pas par un
lieu de stockage. Un composant commun consommé par deux lignes de production n'y
apparaîtra que partiellement. Voir la limite n° 1 en section 7.

---

## 5. Stocker le résultat dans le Lakebase inventaire

### 5.1 Figer plutôt que requêter à la volée

La table gold est reconstruite entièrement chaque nuit. Une correction de
nomenclature, un mouvement saisi en retard, une mise à jour de coût standard :
**l'écart d'une semaine passée peut changer**. Si l'application d'inventaire relit
Unity Catalog à chaque affichage, deux consultations de la même campagne à quinze
jours d'intervalle donneront des chiffres différents, et un écart résiduel validé par
un contrôleur deviendra infalsifiable.

La valeur doit donc être **lue une fois, écrite dans le Lakebase de l'application, et
rattachée à la campagne**, avec ses bornes et sa date de lecture. Un rafraîchissement
explicite reste possible tant que la campagne est ouverte ; il devient interdit une
fois la campagne clôturée.

### 5.2 Table proposée

```sql
CREATE TABLE campagne_ecart_backflush (
    campagne_id          bigint        NOT NULL
                                       REFERENCES campagne(id) ON DELETE CASCADE,
    item_id              text          NOT NULL,

    -- Bornes effectivement appliquées, en lundis ISO. Stockées avec la valeur :
    -- sans elles, le chiffre n'est plus interprétable ni auditable.
    borne_debut          date          NOT NULL,
    borne_fin            date          NOT NULL,

    unite                text,
    ecart_backflush_net  numeric(20,6) NOT NULL,
    non_consommation     numeric(20,6) NOT NULL,
    surconsommation      numeric(20,6) NOT NULL,
    conso_theorique      numeric(20,6),
    conso_reelle         numeric(20,6),
    nb_parents           integer,
    nb_semaines          integer,

    -- Traçabilité : fraîcheur de la source au moment de la lecture, et instant
    -- de la lecture elle-même. Les deux sont nécessaires pour rejouer un écart.
    source_loaded_at     timestamptz,
    rafraichi_le         timestamptz   NOT NULL DEFAULT now(),

    PRIMARY KEY (campagne_id, item_id)
);

CREATE INDEX idx_ceb_campagne ON campagne_ecart_backflush (campagne_id);
```

### 5.3 Écriture idempotente

Le rafraîchissement doit pouvoir être relancé sans créer de doublon ni laisser de
ligne obsolète : un `INSERT … ON CONFLICT` par article, encadré d'une suppression des
articles qui ne sont plus dans le résultat, le tout dans une transaction unique.

```sql
INSERT INTO campagne_ecart_backflush AS c (
    campagne_id, item_id, borne_debut, borne_fin, unite,
    ecart_backflush_net, non_consommation, surconsommation,
    conso_theorique, conso_reelle, nb_parents, nb_semaines,
    source_loaded_at, rafraichi_le)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (campagne_id, item_id) DO UPDATE SET
    borne_debut         = EXCLUDED.borne_debut,
    borne_fin           = EXCLUDED.borne_fin,
    unite               = EXCLUDED.unite,
    ecart_backflush_net = EXCLUDED.ecart_backflush_net,
    non_consommation    = EXCLUDED.non_consommation,
    surconsommation     = EXCLUDED.surconsommation,
    conso_theorique     = EXCLUDED.conso_theorique,
    conso_reelle        = EXCLUDED.conso_reelle,
    nb_parents          = EXCLUDED.nb_parents,
    nb_semaines         = EXCLUDED.nb_semaines,
    source_loaded_at    = EXCLUDED.source_loaded_at,
    rafraichi_le        = now();
```

> **Absence de donnée ≠ écart nul.** Un article sans ligne dans le résultat n'a pas un
> écart de zéro : il n'a **pas de mesure**. Trois cas se confondraient sinon — article
> jamais consommé en production, article hors du périmètre du modèle, période
> antérieure au `2026-03-30`. Ces cas doivent rester `NULL` jusqu'à l'affichage, où ils
> s'écrivent « non mesuré » et non « 0 ».

---

## 6. Recalculer l'écart d'inventaire

### 6.1 Conventions

Une seule convention de signe doit régner dans l'application. Celle retenue ici est la
convention d'inventaire usuelle, du point de vue du magasin :

```
ecart_inventaire = qty_comptee − qty_erp
```

Négatif = il manque du stock par rapport à l'ERP. Positif = il y en a plus. L'écart
backflush suit la convention inverse (§ 1) : c'est précisément pourquoi il entre dans
la formule avec un signe changé.

### 6.2 Les trois grandeurs

```
ecart_net_sans_backflush = qty_comptee − qty_erp
part_backflush           = − ecart_backflush_net
ecart_residuel           = ecart_net_sans_backflush − part_backflush
                         = (qty_comptee − qty_erp) + ecart_backflush_net
```

`part_backflush` est la fraction de l'écart d'inventaire que le backflush explique,
exprimée dans la même convention que l'écart d'inventaire. `ecart_residuel` est ce qui
reste inexpliqué : c'est lui, et non l'écart brut, qui doit déclencher une
investigation.

### 6.3 Taux d'explication

Un ratio simple `part_backflush / ecart_net` est trompeur : il dépasse 100 % dès que le
backflush sur-explique, et n'a pas de signe interprétable quand les deux grandeurs sont
de sens opposés. La formulation par réduction de l'écart est plus sûre :

```
taux_explication = 1 − |ecart_residuel| / |ecart_net_sans_backflush|
```

Indéfini (NULL) si l'écart net est nul. Vaut 1 quand le backflush explique exactement
l'écart, 0 quand il n'apporte rien, et devient **négatif** quand la prise en compte du
backflush *creuse* l'écart au lieu de le réduire — un signal en soi, à afficher et non
à masquer par un plancher à zéro.

### 6.4 Exemples chiffrés

| Cas | ERP | Compté | Écart net | Écart backflush | Part backflush | Résiduel | Taux |
|---|---:|---:|---:|---:|---:|---:|---:|
| Expliqué | 1 200 | 1 150 | −50 | +42 | −42 | −8 | 84 % |
| Sur-expliqué | 800 | 790 | −10 | +26 | −26 | +16 | −60 % |
| Aggravé | 1 200 | 1 150 | −50 | −30 | +30 | −80 | −60 % |
| Non mesuré | 640 | 618 | −22 | — | — | — | — |

Le deuxième cas est celui qui coûte le plus cher à mal interpréter : le backflush
annonce 26 unités de non-consommation, l'inventaire n'en trouve que 10 qui manquent.
Soit une partie de la non-consommation a déjà été corrigée par un ajustement, soit les
bornes de période sont mal calées, soit le coefficient de nomenclature est faux. Le
troisième cas dit autre chose encore : la surconsommation aurait dû faire trouver
*plus* de stock, on en trouve moins — les deux anomalies sont indépendantes et
s'additionnent.

### 6.5 Où l'afficher

- **Vue dédiée « Backflush »** — une ligne par article de la campagne, triée par
  `|ecart_residuel|` décroissant, avec les bornes de période en en-tête et la fraîcheur
  de la source. C'est l'écran d'arbitrage.
- **Colonnes ajoutées aux vues existantes** (feuille de comptage, écarts, validation) —
  au minimum `part_backflush` et `ecart_residuel`, en gardant l'écart net brut visible :
  le remplacer silencieusement changerait le sens d'un chiffre que les utilisateurs
  connaissent.
- **Indicateur de campagne** — part de l'écart total valorisé expliquée par le
  backflush, et **nombre d'articles non mesurés**. Ce dernier chiffre est la mesure de
  couverture du dispositif ; sans lui, le taux d'explication global est flatteur.

---

## 7. Limites et pièges

Par ordre décroissant d'impact sur le chantier.

### 1. Il n'existe aucun axe société, site ou magasin — *bloquant*

Le modèle backflush ne conserve ni `dataareaid`, ni site, ni entrepôt : la société est
filtrée à l'ingestion, les dimensions de stockage ne sont pas reprises. L'écart d'un
composant est un **total tous lieux confondus**.

Or une campagne d'inventaire porte presque toujours sur un magasin. Rapprocher un écart
backflush multi-sites d'un comptage mono-magasin produirait un résiduel faux, sans
qu'aucun contrôle ne le signale. Trois issues, à arbitrer explicitement :

- restreindre les campagnes concernées à un périmètre mono-site ;
- n'activer la fonctionnalité que pour les articles dont le stock est physiquement unique ;
- faire évoluer le pipeline backflush pour porter la dimension de stockage jusqu'à la
  table de faits — c'est un changement de grain, donc de clé primaire.

### 2. La maille est hebdomadaire, pas journalière — *structurel*

Les bornes de période sont ramenées au lundi. Sur un article à forte rotation,
l'imprécision aux deux bornes peut représenter plusieurs jours de consommation —
largement de quoi dominer l'écart mesuré. L'imprécision doit être affichée (les dates
réellement appliquées, pas celles demandées). Un calage au jour près supposerait de
descendre au niveau des mouvements bronze, hors du périmètre de la table gold.

### 3. L'écart n'explique un manquant que si la consommation théorique a réellement eu lieu — *structurel*

L'écart backflush mesure une différence entre deux enregistrements, pas une réalité
physique. Si le coefficient de nomenclature est faux, la consommation théorique est
fausse, et l'écart existe sans qu'aucune pièce n'ait bougé. Un écart backflush important
sur un article dont le résiduel d'inventaire reste élevé doit d'abord faire suspecter la
nomenclature — usage légitime du dispositif, à condition de ne pas présenter l'écart
comme une explication acquise.

### 4. Le paramétrage de l'application Backflush n'existe pas dans Unity Catalog — *structurel*

Les exclusions d'articles et les surcharges de coefficients saisies dans l'application
Backflush sont stockées dans *son* Lakebase et appliquées à la lecture. Unity Catalog
n'en sait rien. Les chiffres lus par l'application inventaire sont donc les chiffres
**bruts du modèle**, et peuvent différer de ceux affichés à l'écran Backflush. Il faut
choisir : assumer et documenter cette différence, ou exposer le paramétrage (vue ou
point d'accès dédié) pour que les deux applications parlent des mêmes nombres.

### 5. Une semaine passée peut changer — *vigilance*

Le job reconstruit tout chaque nuit à 05:00. Mouvements saisis en retard, correction de
nomenclature, mise à jour de coût standard : l'historique n'est pas figé. D'où la règle
de la section 5 — lire une fois, écrire, horodater. Un écart recalculé après clôture de
campagne doit être traité comme une nouvelle mesure, jamais comme une correction de
l'ancienne.

### 6. La nomenclature est un instantané appliqué au passé — *vigilance*

`dim_nomenclature` ne conserve qu'une version active par parent, sans historisation. La
consommation théorique des semaines passées est donc recalculée avec la nomenclature
*d'aujourd'hui*. Une évolution de nomenclature en cours de période déplace mécaniquement
l'écart de tout l'historique du parent concerné.

### 7. Unités, coûts et arrondis — *vigilance*

La quantité d'écart est exprimée dans `child_unite` : elle doit être comparée à un
comptage dans la même unité, sans conversion implicite. `child_cout_standard` peut être
NULL, auquel cas `ecart_valorise` vaut 0 — un total valorisé sous-estime donc
silencieusement. Si la campagne affiche des montants, compter les articles sans coût et
l'indiquer. Enfin `ecart_pct` est arrondi à quatre décimales et n'est pas additif : tout
pourcentage global se recalcule à partir des sommes.

### 8. Avant 2026-03-30, il n'y a rien — *vigilance*

L'historique commence au 30 mars 2026. Toute période antérieure, ou débordant avant
cette date, renvoie une mesure partielle. L'application doit détecter le cas (comparer
`borne_debut` au début d'historique) et le signaler comme période incomplète plutôt que
de livrer un écart tronqué.

---

## 8. Séquence de mise en œuvre

Les jalons dans l'ordre où ils lèvent le risque.

1. **Trancher la question du site.** Limite n° 1. Déterminer si les campagnes visées
   sont mono-site, ou si le pipeline backflush doit porter la dimension de stockage.
   Toute la suite en dépend, y compris la clé de la table de destination.
2. **Ouvrir l'accès en lecture.** `SELECT` sur
   `emotors_data_champions.backflush.fact_ecart_backflush` pour le principal de service
   de l'application inventaire, et un SQL Warehouse dimensionné pour une requête agrégée
   ponctuelle — pas pour un usage interactif.
3. **Valider les signes sur un article pilote.** Prendre un article à écart backflush
   franc, comparer au dernier inventaire connu, vérifier que le résiduel se réduit.
   Faire ce contrôle avant d'écrire une ligne de code d'affichage : une inversion de
   signe est indétectable une fois l'écran en place.
4. **Créer la table de destination et le rafraîchissement.** Lecture bornée, écriture
   idempotente en transaction unique, horodatage de la source. Rafraîchissement autorisé
   tant que la campagne est ouverte, interdit après clôture.
5. **Calculer et exposer les trois grandeurs** dans le domaine métier de l'application,
   pas dans les vues, pour qu'un seul jeu de formules serve la vue dédiée, les colonnes
   ajoutées et l'export.
6. **Traiter le non-mesuré comme un état de première classe.** NULL de bout en bout,
   affiché « non mesuré », compté dans un indicateur de couverture de campagne.
7. **Rapprocher un inventaire complet avant généralisation.** Sur une campagne réelle
   close, mesurer la distribution du taux d'explication. Elle dira si le dispositif
   tient, et sur quelles familles d'articles il ne tient pas — information plus utile
   qu'un taux moyen.

---

## Références dans ce dépôt

| Sujet | Fichier |
|---|---|
| Calcul de l'écart | `src/sql/gold/30_fact_ecart_backflush.sql` |
| Production déclarée | `src/sql/gold/20_fact_production_parent.sql` |
| Consommation réelle | `src/sql/gold/21_fact_consommation_composant.sql` |
| Sélection de la version de nomenclature | `src/sql/gold/11_dim_nomenclature.sql` |
| Écart à la maille OF | `src/sql/gold/31_fact_ecart_of.sql` |
| Contrôles qualité | `src/sql/gold/90_dq_controles.sql` |
| Paramètres du pipeline | `databricks.yml` (`date_from`, `seuil_conformite`) |
| Planification du job | `resources/backflush_pipeline.job.yml` |
| Limites connues du modèle | `docs/AMELIORATIONS.md` |

Les chiffres cités (seuil de conformité, début d'historique, heure de rafraîchissement)
sont les paramètres du pipeline au moment de la rédaction ; ils sont configurables au
niveau du bundle Databricks et doivent être revérifiés avant mise en production.
