# Architecture — Backflush Analytics

## 1. Chaîne de bout en bout

```
Dynamics 365 F&O
  ├─ emotors_data_platform.bronze_erp       invent_trans · invent_trans_origin · prod_table
  └─ emotors_data_champions.silver_erp_ye   silver_bom · silver_base_article
                    │
        ❶ Job « construire_modele_gold »  (serverless, quotidien 05h00 Europe/Paris)
                    │   src/sql/gold/*.sql exécutés dans l'ordre, puis contrôles qualité
                    ▼
  emotors_data_champions.backflush          Unity Catalog — dim_* · fact_* · agg_* · dq_controles
                    │
        ❷ Job « publier_lakebase »          staging + COPY + index + bascule atomique + GRANT
                    ▼
  Lakebase Postgres, schéma « backflush »   tables OLTP indexées + meta_ingestion
                    │
        ❸ Requêtes paramétrées, < 200 ms sur la volumétrie cible
                    ▼
  Databricks App « backflush-analytics »    FastAPI (Python 3.11) + React 19 (Vite)
```

## 2. Décisions structurantes

### 2.1 Pourquoi un job d'ingestion plutôt que des *synced tables* Lakebase

Les tables synchronisées managées sont plus simples, mais ne couvraient pas trois
besoins :

| Besoin | Synced tables | Job d'ingestion |
|---|---|---|
| Index composites et index d'expression (trigramme de recherche, tri par impact) | Limités | Complets, versionnés avec le code |
| Publication **atomique** de neuf tables cohérentes entre elles | Table par table | Bascule par table + dépendance stricte au job amont |
| Réattribution des droits après remplacement | Non applicable | Explicite, testée |

Le job vaut ~350 lignes de Python. Le compromis est assumé et documenté ici pour
qu'un successeur puisse revenir aux synced tables si le besoin d'index disparaît.

### 2.2 Bascule atomique (`src/jobs/sync_to_lakebase.py`)

```
DROP    backflush.t__stg            reliquat d'une exécution interrompue
CREATE  backflush.t__stg            DDL généré depuis lakebase_schema.py
COPY    → t__stg                    protocole COPY, ~10× plus rapide qu'INSERT
CREATE INDEX … ; ANALYZE            index construits sur table pleine
BEGIN
  DROP   backflush.t
  ALTER TABLE t__stg RENAME TO t
  ALTER  … RENAME CONSTRAINT / INDEX
  GRANT SELECT … TO <principal de service>
COMMIT
```

Trois propriétés :

1. **L'application ne voit jamais de table vide.** L'indisponibilité se réduit au
   commit, quelques millisecondes.
2. **Une exécution en échec laisse la production intacte.**
3. **Le GRANT final n'est pas cosmétique** : le renommage produit une table
   *neuve*, dont les privilèges sont vierges. L'oublier coupe l'application au
   lendemain de la mise en production — c'est le piège classique de ce motif.

### 2.3 Séparation logique / interface

| Couche | Contenu | Ne connaît pas |
|---|---|---|
| `app/server/domain/` | Filtres, indicateurs, dictionnaire des grilles | SQL, HTTP, React |
| `app/server/data/` | **Seul module écrivant du SQL** | HTTP, React |
| `app/server/services/` | Export Excel, assistant IA | HTTP |
| `app/server/api/` | Validation, codes HTTP, sérialisation | Règles métier |
| `app/client/` | Mise en forme et interaction | Définition des indicateurs |

Concrètement : la définition de « fiabilité du backflush » vit dans
`domain/metrics.py`, avec son unité, sa polarité et son texte d'aide. Le frontend
affiche ce qu'il reçoit. Ajouter un indicateur ne demande **aucune** modification
du React ; ajouter une colonne de grille non plus (`domain/dictionary.py`).

### 2.4 Sécurité des requêtes

Trois barrières successives :

1. **Validation Pydantic** — `Filtres` refuse une période inversée, un type
   d'écart inconnu, une recherche de plus de 80 caractères, une liste de plus de
   500 valeurs.
2. **Paramétrage systématique** — `construire_predicat` ne produit que des
   marqueurs `%(nom)s`. Les seuls fragments interpolés sont des expressions
   issues de **listes blanches** (colonne de tri, dimension d'agrégation). Un tri
   sur une colonne inconnue est rejeté par un `422` avant d'atteindre la base.
3. **Connexion en lecture seule** — chaque connexion physique est configurée en
   `default_transaction_read_only`, avec un `statement_timeout` de 25 s. Même une
   régression de code ne peut pas écrire dans la base analytique, ni bloquer le
   pool sur une requête pathologique.

Le test `test_une_tentative_d_injection_ne_ramene_rien` vérifie qu'une valeur
comme `' OR 1=1 --` est traitée en donnée : elle ne correspond à aucun programme,
donc zéro ligne.

### 2.5 Le seuil de conformité est un réglage, pas une constante

La colonne `type_ecart` matérialisée dans le modèle gold est calculée avec le
seuil de référence (±0,5 unité). L'application **ne la lit pas** : elle recalcule
le type à la volée à partir des seuils actifs. Un key-user peut donc déplacer la
tolérance sans relancer un job.

Deux tolérances, combinées en OU :

- **absolue**, en unités du composant — la règle métier documentée ;
- **relative**, en % du théorique — inactive par défaut. Elle répond à une
  limite réelle du seuil absolu : 0,5 unité sur une vis consommée par dizaines de
  milliers et 0,5 kg de résine ne mesurent pas la même chose.

### 2.6 Le périmètre est la maille de l'équivalent produit

Le **périmètre** est la ligne de production du parent fabriqué
(`silver_erp_ye.produits_fabriques.ligne_de_prod`). Un parent relève d'un seul
périmètre, un périmètre d'un seul programme : le programme reste donc obtenu par
somme, mais il n'est plus la maille de calcul.

Deux calculs se font sur le périmètre, et non sur le programme :

1. **L'uniformité du coefficient** (`dim_coef_perimetre`, ex-`dim_coef_programme`).
   Un composant peut avoir un coefficient 4 sur une ligne et 6 sur une autre du
   même programme : agrégé au programme, il apparaissait « non uniforme » et
   sortait de l'analyse, alors qu'il est parfaitement uniforme *dans sa ligne*.
2. **L'équivalent produit** (`écart ÷ coefficient`), qui n'a de sens que rapporté
   au volume produit d'une ligne. Cumulé sur deux lignes, il additionnerait des
   unités différentes — c'est la raison pour laquelle la vue synthétique impose
   un périmètre unique, plutôt que de le suggérer.

Conséquence sur le grain : `agg_ecart_hebdo_programme` descend au couple
programme × périmètre. Les dénombrements distincts qu'il porte (`nb_parents`,
`nb_composants`) ne sont dès lors **jamais additifs** entre périmètres et doivent
être recalculés depuis la table de faits.

Les articles sans ligne de production renseignée sont regroupés sous
`NON RENSEIGNE` plutôt qu'écartés : une donnée source incomplète doit rester
visible, sans quoi les totaux ne se réconcilient plus.

### 2.7 Le paramétrage du key-user s'applique à la LECTURE

Deux arbitrages sont laissés au key-user : **exclure une référence** de l'analyse
(consommable, article de transit, référence de test) et **désactiver ou corriger
une ligne de nomenclature** (nomenclature obsolète, substitution non tracée).
Ils vivent dans deux tables `param_*` que l'ingestion ne touche jamais.

Deux façons de les appliquer, et le choix a des conséquences :

| | Recalcul du modèle | **Application à la lecture** (retenu) |
|---|---|---|
| Délai de prise d'effet | La prochaine exécution du job | Immédiat |
| Réversibilité | Une exécution de plus | Un clic |
| Coût par requête | Nul | Une jointure sur deux petites tables |
| Risque | Arbitrage figé entre deux nuits | Divergence si les formules gold changent |

`data/faits.py` substitue au nom de la table de détail une **table dérivée** qui
joint le paramétrage et recalcule les colonnes concernées. Le reste du dépôt
continue d'écrire `FROM {FACT} f` sans rien savoir du mécanisme, et les
arbitrages atteignent donc aussi bien les indicateurs que l'export ou
l'assistant.

Le recalcul est exact, non approché : le modèle gold pose
`conso_theorique = qty_parent_produite × coef_bom` puis en dérive l'écart, sa
valorisation et l'équivalent produit. Substituer le coefficient et rejouer ces
formules redonne ce qu'aurait produit un recalcul complet. **C'est le point de
fragilité de ce choix** : toute évolution de ces formules côté gold doit être
répercutée dans `faits.py`, et `tests/test_faits.py` compare les deux.

Deux garde-fous complètent le dispositif :

- **Écriture bornée.** Le pool ouvre chaque connexion en
  `default_transaction_read_only` ; `connexion_ecriture()` lève ce verrou pour
  une transaction, et une seule. Les droits Postgres sont la barrière ultime :
  le principal de service ne reçoit `INSERT`/`UPDATE`/`DELETE` que sur les deux
  tables `param_*`.
- **Performance vérifiée.** La table dérivée n'est qu'une projection au-dessus
  d'une jointure : PostgreSQL la remonte dans la requête appelante, et les index
  de `fact_ecart_backflush` continuent de servir. Un test exécute un `EXPLAIN`
  et échoue si le balayage séquentiel revient — une régression se manifesterait
  sinon par une application lente, symptôme qui n'accuse jamais la bonne cause.

### 2.8 Réconciliation complète (`FULL OUTER JOIN`)

Le fait principal réconcilie l'attendu (production × nomenclature) et le réel
(sorties de stock) par une **jointure complète**. Une jointure interne masquerait
les deux anomalies les plus coûteuses :

| `statut_ligne` | Situation | Lecture métier |
|---|---|---|
| `Nominal` | Nomenclature et consommation présentes | Écart de mesure |
| `Hors nomenclature` | Composant sorti sans ligne BOM | Erreur de saisie d'OF, nomenclature obsolète, substitution non tracée |
| `Sans consommation` | Ligne BOM sans aucune sortie de la semaine | Backflush non exécuté, OF non clôturé |

### 2.9 La maille OF est une seconde lecture, jamais un axe de plus

`fact_ecart_of` porte le même écart que `fact_ecart_backflush`, avec l'ordre de
fabrication en plus. Le **total** est identique — descendre d'un cran ne crée ni
ne détruit de matière — mais la **décomposition** diffère : non-consommation et
surconsommation augmentent toutes deux du même montant, parce que le décalage
des ordres à cheval sur deux semaines cesse de se compenser entre lancements.
C'est le contrôle `reconciliation_of_detail` qui garde l'égalité des totaux.

Trois conséquences de conception :

* **Le basculement est explicite.** « Par parent » et « Par OF » sont deux
  lectures qui ne donnent pas les mêmes chiffres ; ajouter l'OF d'office aurait
  fait passer l'écart entre les deux écrans pour une incohérence.
* **Les critères `ofs` et `statuts_of` sont cantonnés à leur source.**
  `prod_id` et `prod_statut` n'existent pas sur la table à la maille parent :
  `construire_predicat(..., axe_of=True)` ne les pose que là où ils existent, et
  la barre de filtres les grise **et les vide** ailleurs. Grisés seuls, ils
  resteraient dans l'URL et laisseraient croire à un filtrage inexistant.
* **Le statut d'OF est une traduction, donc une dette.** `31_*` traduit
  l'énumération D365 `ProdStatus` (0 Aucun → 8 Annulé). Une traduction fausse ne
  casse rien : elle affiche « Clôturé » sur un ordre en cours et fait instruire
  comme anomalie un écart parfaitement normal. Le contrôle `of_statut_inconnu`
  remonte toute valeur non traduite plutôt que de la fondre dans un « Autre ».
  Dans le même esprit, la vue source ramène à `NULL` la sentinelle `1900-01-01`
  que D365 écrit dans `finisheddate` : laissée telle quelle, elle s'affiche
  comme une vraie date de clôture, et tout test `IS NOT NULL` compte les ordres
  en cours parmi les ordres terminés.

### 2.10 Le tiroir de contexte : une parenthèse, pas une navigation

Un chiffre du bloc « écart de prélèvement » de la vue synthétique vaut un
périmètre, un composant et une semaine. Il pose toujours la même question : cet
écart est-il *résiduel*, ou n'est-il que le décalage d'un ordre à cheval sur
deux semaines ? Le tableau croisé ne peut pas y répondre — la contrepartie est
dans la colonne d'à côté, mélangée à tous les autres ordres.

Cliquer le chiffre ouvre donc une grille par OF filtrée sur quatre critères
additifs : le périmètre, le composant, les ordres à l'origine des mouvements de
cette semaine, et **toutes** les semaines où ces ordres ont mouvementé ce
composant. Deux points de conception y sont contre-intuitifs mais nécessaires :

* **Les filtres globaux de sélection ne sont pas repris** (seules les tolérances
  le sont). Un « masquer les conformes » ou un type d'écart hérité de la barre
  retirerait de la liste un ordre qui porte la contrepartie, et le tiroir
  répondrait faux à la seule question qu'on lui pose.
* **Les critères vivent dans le tiroir**, jamais dans l'objet de filtres global :
  on retrouve son analyse intacte à la fermeture. C'est ce qui fait de ce
  drill-down une parenthèse et non un déplacement.

Le contexte est calculé côté serveur (`Repository.contexte_of`) : les deux côtés
du mouvement — production déclarée et consommation déclarée — remontent d'une
seule requête, parce que `fact_ecart_of` naît d'une jointure complète.

**Deux blocs disjoints**, sur le modèle du `1. PRODUCTION` / `2. ÉCART DE
PRÉLÈVEMENT` du tableau croisé, parce qu'ils ne répondent pas à la même
question :

| Bloc | Portée | Ce qu'il dit |
|---|---|---|
| 1. Lignes du chiffre cliqué | la semaine du clic | ce qui **fait** le chiffre — le total de son pied de page est exactement l'écart affiché dans le bandeau |
| 2. Autres semaines des mêmes ordres | les semaines voisines | ce qui l'**éclaire** — un écart de signe opposé ici annule celui du bloc 1 |

L'égalité « total du bloc 1 = chiffre cliqué » est ce qui rend le tiroir
vérifiable, et elle impose que les deux blocs ne se recouvrent pas. D'où le
filtre `semaines_debut`, une restriction **énumérée** : la semaine du clic est
le plus souvent au milieu de l'étendue, et des bornes continues ne savent pas
l'exclure du second bloc.

Le second bloc n'existe que s'il a quelque chose à montrer. Son absence n'est
pas un vide à combler mais une **réponse** : ces ordres n'ont bougé nulle part
ailleurs, l'écart est résiduel à cette maille — et c'est écrit ainsi, plutôt
qu'affiché comme un tableau vide.

### 2.11 Performance

| Mécanisme | Effet |
|---|---|
| `COUNT(*) OVER ()` | Total et page dans un seul aller-retour |
| Index composites `(dimension, semaine_debut)` | Les filtres les plus fréquents portent tous une borne de date |
| Index GIN trigramme sur une expression | Recherche plein texte indexée ; le repository réutilise l'expression **à l'identique**, sinon l'index est ignoré |
| Index `(semaine_debut, abs(ecart_valorise) DESC)` | Le tri par impact, cas dominant, n'exige pas de tri en mémoire |
| Curseur serveur pour l'export | 100 000 lignes exportables dans un runtime de 6 Go |
| `ANALYZE` avant la bascule | La première requête après ingestion planifie sur des statistiques fraîches |
| Compression gzip des réponses | Une page de 500 lignes passe d'environ 400 ko à 80 ko |

### 2.12 Chargement des `numeric` en flottant

`psycopg.adapters.register_loader("numeric", FloatLoader)` — sérialisé en JSON,
un `Decimal` devient une **chaîne**, que le frontend doit reconvertir à chaque
usage ; un oubli produit silencieusement un « — » à l'écran. JSON n'ayant pas de
type décimal, la valeur finit de toute façon en double dans le navigateur : la
conversion est faite une seule fois, explicitement. Les agrégations financières
restent calculées en `numeric` par Postgres ; seul le résultat est converti.

## 3. Assistant IA — outils plutôt que texte-vers-SQL

Un assistant générant du SQL libre sur une base de production cumule trois
risques : requêtes non bornées, jointures fausses affirmées avec assurance,
surface d'injection. Le modèle ne peut appeler ici qu'un **catalogue fermé de
neuf fonctions** — les mêmes que celles qui alimentent les écrans, donc déjà
paramétrées, bornées et testées.

| Garantie | Mise en œuvre |
|---|---|
| Traçabilité | Chaque réponse expose les outils appelés, leurs paramètres et le nombre de lignes lues, dans un bloc dépliable |
| Ancrage | Les outils héritent des filtres de l'écran ; le modèle ne peut que les restreindre, et sa surcharge repasse par la validation Pydantic |
| Bornes | 5 tours d'outils maximum, 50 lignes par outil, historique tronqué à 10 messages |
| Identité d'exécution | Affichée : la base est lue par le **principal de service** de l'application, pas par l'identité du visiteur |
| Réserve | Avertissement « généré par IA, vérifiez avant décision » sur chaque réponse ; le prompt système impose de distinguer constatation et hypothèse, et de rappeler les limites du modèle (pas de rebut, maille hebdomadaire, coûts manquants) |

## 4. Data-visualisation

La palette est **validée par script**, pas à l'œil : bandes de luminosité,
plancher de chroma, séparation sous simulation des daltonismes, contraste sur la
surface — dans les deux thèmes.

| Rôle | Clair | Sombre |
|---|---|---|
| Séries catégorielles | `#2a78d6` `#eb6834` `#1baf7a` `#eda100` | `#3987e5` `#d95926` `#199e70` `#c98500` |
| Divergent (polarité de l'écart) | bleu ↔ `#e34948`, milieu `#f0efec` | bleu ↔ `#e66767`, milieu `#383835` |
| Statuts (réservés) | `#0ca30c` `#fab219` `#ec835a` `#d03b3b` | identiques |

Résultats : pire paire adjacente ΔE 9,1 (protan) en clair, 8,4 en sombre — au
delà du seuil de 8. Trois teintes claires passent sous 3:1 de contraste : la
règle de relief s'applique, elles portent toujours une étiquette directe et sont
doublées par la vue tabulaire.

Règles de notation appliquées :

- **Jamais de double axe.** Quantités et euros n'ont pas la même unité : la
  tendance hebdomadaire empile deux panneaux alignés sur le même axe temporel
  plutôt que de superposer deux échelles, ce qui suggérerait des croisements
  inexistants.
- **Échelles passant par zéro**, sinon les variations sont visuellement exagérées.
- **Scénario de référence texturé** : la consommation théorique est hachurée, la
  réelle est pleine. La distinction survit à l'impression et au daltonisme.
- **Le divergent n'est pas un code bien/mal** : non-consommation et
  surconsommation sont deux anomalies de sens opposé. Les couleurs de statut
  restent réservées à l'alerte de matérialité.
- **Tri par magnitude de la valeur encodée** : les barres montrent l'impact net
  signé, elles sont donc ordonnées par |impact net| — conserver l'ordre serveur
  (par impact absolu) produirait un classement visuellement incohérent.
- **L'unité se déclare, elle ne se devine pas.** Un composant de graphique ne
  peut pas savoir si les nombres qu'on lui passe sont des euros ou des pièces :
  `BarresDivergentes` exige donc un `formater`, et `TendanceHebdo` un
  `enValeur`, tous deux sans valeur par défaut. Le défaut qui existait — un
  repli en euros — a produit exactement ce qu'un repli produit : des barres qui
  suivaient fidèlement la bascule valeur/quantité, et des étiquettes qui
  restaient libellées « k€ ». Un graphique juste et une légende fausse est le
  pire des deux, parce qu'il ne se remarque pas. Rendre ces propriétés
  obligatoires transforme l'oubli en erreur de compilation, ce qu'aucun test
  d'affichage ne garantirait aussi complètement.

## 5. États obligatoires

Toute vue de données traite **chargement / vide / erreur / partiel** — un panneau
blanc ne dit pas à l'utilisateur s'il doit attendre, recommencer, ou conclure que
le chiffre est nul. Le composant `VueDonnees` factorise cet aiguillage.

Le bandeau de qualité n'apparaît que lorsqu'il a quelque chose à dire :
contrôle en anomalie, ingestion en échec, ou données de plus de 30 heures.

## 6. Volumétrie et dimensionnement

| Grandeur | Ordre attendu | Marge |
|---|---|---|
| `fact_ecart_backflush` | parents × composants × semaines — ~10⁵–10⁶ lignes/an | Index couvrants ; `COUNT(*) OVER ()` reste sous la seconde |
| Pool de connexions | 2 workers × 6 = 12 au plus | Très en deçà du plafond d'un endpoint Lakebase |
| Page de grille | 500 lignes maximum | `PAGE_SIZE_MAX` |
| Export | 100 000 lignes maximum | `EXPORT_ROWS_MAX`, curseur serveur, onglet « Contexte » signalant une troncature |

## 7. Ce que le modèle ne dit pas

Limites héritées de la source, rappelées dans l'interface, dans l'export et dans
le prompt de l'assistant :

- L'analyse est **hebdomadaire et par plage de dates**, pas par ordre de
  fabrication : un OF à cheval sur deux semaines répartit sa production et sa
  consommation sur les deux, ce qui crée un écart de calage sans anomalie réelle.
- Le **taux de rebut de nomenclature** (`scrapvar`) n'est pas pris en compte :
  une surconsommation peut correspondre à un rebut prévu au paramétrage.
- **Une seule version de nomenclature active** est retenue par article parent ;
  les parents multi-versions sont comptés par `dq_bom_multi_version`.
- Un composant **sans coût standard** contribue pour 0 € : l'impact financier est
  donc sous-estimé, ce que signale le contrôle `composant_sans_cout_standard`.
- Les mouvements sont datés par `invent_trans.datephysical` : une validation
  physique tardive décale l'écart d'une semaine.
