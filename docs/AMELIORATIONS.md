# Top 20 des améliorations — revue critique

Revue menée en double posture : **architecte data** (le modèle dit-il la vérité ?)
et **key-user exigeant** (puis-je décider et agir avec cet outil ?).

Les points sont classés par **valeur décisionnelle décroissante**, pas par
facilité. Les cinq premiers touchent à la **justesse des chiffres** : tant qu'ils
ne sont pas traités, tout le reste embellit une mesure discutable.

| # | Amélioration | Nature | Impact | Effort |
|---|---|---|---|---|
| 1 | Rapprochement par ordre de fabrication | Modèle | ⬤⬤⬤ | ⬤⬤⬤ |
| 2 | Prise en compte du rebut de nomenclature (`scrapvar`) | Modèle | ⬤⬤⬤ | ⬤ |
| 3 | Historisation de la nomenclature et du coût standard | Modèle | ⬤⬤⬤ | ⬤⬤ |
| 4 | Contrôle des unités et détection d'erreur de coefficient | Qualité | ⬤⬤⬤ | ⬤ |
| 5 | Axes société, site et entrepôt | Modèle | ⬤⬤ | ⬤⬤ |
| 6 | Boucle de qualification des écarts (write-back) | Métier | ⬤⬤⬤ | ⬤⬤ |
| 7 | Détection de dérive vs accident isolé | Analytique | ⬤⬤⬤ | ⬤⬤ |
| 8 | Rapprochement avec l'inventaire tournant | Analytique | ⬤⬤⬤ | ⬤⬤ |
| 9 | Alerting proactif et synthèse hebdomadaire | Métier | ⬤⬤ | ⬤ |
| 10 | Segmentation ABC/XYZ et matérialité dynamique | Analytique | ⬤⬤ | ⬤ |
| 11 | Ingestion incrémentale | Exploitation | ⬤⬤ | ⬤⬤ |
| 12 | Vues sauvegardées et partage d'analyses | Ergonomie | ⬤⬤ | ⬤ |
| 13 | Exports planifiés et abonnements | Ergonomie | ⬤⬤ | ⬤ |
| 14 | Virtualisation et tri multi-colonnes des grilles | Ergonomie | ⬤ | ⬤⬤ |
| 15 | Traçabilité jusqu'au mouvement de stock | Métier | ⬤⬤ | ⬤⬤ |
| 16 | Sécurité fine par programme et par site | Gouvernance | ⬤⬤ | ⬤⬤ |
| 17 | Observabilité applicative et audit d'usage | Exploitation | ⬤⬤ | ⬤ |
| 18 | Chaîne CI/CD et tests de non-régression chiffrés | Ingénierie | ⬤⬤⬤ | ⬤⬤ |
| 19 | Accessibilité WCAG 2.2 AA et parcours clavier complet | Ergonomie | ⬤ | ⬤ |
| 20 | Internationalisation et multi-devise | Ergonomie | ⬤ | ⬤⬤ |

---

## Justesse du chiffre

### 1. Rapprochement par ordre de fabrication plutôt que par semaine

**Le défaut.** L'analyse rapproche la production et la consommation d'une même
semaine calendaire. Or un OF lancé le jeudi et déclaré le lundi suivant place sa
consommation en semaine N et sa production en semaine N+1. Le modèle produit
alors une **surconsommation fictive** en N et une **non-consommation fictive** en
N+1, qui s'annulent sur le cumul mais polluent chaque semaine prise isolément —
et donc chaque alerte, chaque classement et chaque investigation.

C'est le principal biais résiduel du modèle. Il est structurel, pas marginal :
sur des cycles de fabrication courts et des séries fréquentes, il peut dominer le
signal recherché.

**La correction.** Introduire un grain `prod_id × composant`, en agrégeant tous
les mouvements d'un OF quelle que soit leur date, et rattacher l'OF à une semaine
par sa **date de clôture**. La maille hebdomadaire actuelle devient une
agrégation de ce grain, pas la maille de calcul.

**Toutes les colonnes nécessaires sont déjà disponibles** — et désormais
exposées par `v_src_prod_table` :

| Colonne | Rôle |
|---|---|
| `invent_trans_origin.referenceid` | Porte le `ProdId` des deux côtés du calcul |
| `prod_table.finisheddate` | Rattache l'OF à une semaine, sans ambiguïté |
| `prod_table.prodstatus` | Ne retenir que les OF clôturés, dont l'écart est définitif |
| `prod_table.bomid` | **La version de nomenclature réellement utilisée par l'OF** |

Ce dernier point résout au passage l'amélioration n° 3 pour la nomenclature :
plus besoin de choisir une version active de façon déterministe mais arbitraire,
l'OF dit laquelle il a consommée. Le contrôle `bom_multi_version` deviendrait
sans objet.

**Vérification.** Comparer, sur un trimestre, l'écart hebdomadaire actuel et
l'écart par OF ré-agrégé. La différence mesure exactement le biais de calage.

### 2. Prise en compte du taux de rebut de nomenclature

**Le défaut.** `scrapvar` (rebut prévu au paramétrage BOM) est ignoré. Un
composant dont la nomenclature prévoit 3 % de rebut apparaît en surconsommation
permanente de 3 % — un écart **attendu**, présenté comme une anomalie. Il occupe
la tête des classements et détourne l'attention des vrais problèmes.

**La correction.** `conso_theorique = qty_parent × coef_bom × (1 + scrapvar)` et
exposer deux mesures distinctes : écart *vs* nomenclature nette, et écart *vs*
nomenclature avec rebut. La seconde est celle qui doit déclencher une action.

**Coût.** Une colonne à remonter de `silver_bom`, une multiplication. C'est
l'amélioration au meilleur rapport valeur/effort de cette liste.

### 3. Historisation de la nomenclature et du coût standard

**Le défaut.** La nomenclature et le coût standard sont pris dans leur **dernier
état**. Un changement de coefficient au mois M réécrit rétroactivement l'écart
théorique de tous les mois passés ; une revalorisation de coût réécrit tout
l'historique financier. Concrètement : un tableau de bord exporté en juin ne se
retrouve plus en septembre, et personne ne sait pourquoi.

**La correction.** Historiser `dim_nomenclature` et `dim_article` en SCD type 2
(`valide_du` / `valide_au`), et joindre le fait sur la version **en vigueur à la
date du mouvement**. `silver_bom` porte déjà un `snapshot_date` : la matière
première existe.

**Bénéfice annexe.** Le différentiel de version devient auditable : « cet écart a
changé de nature le 12 mai, parce que le coefficient est passé de 4 à 5 ».

### 4. Contrôle des unités et détection d'erreur de coefficient

**Le défaut.** Le contrôle `ecart_aberrant` signale les écarts supérieurs à 100 %
du théorique, mais ne les qualifie pas. Or la cause la plus fréquente d'un écart
d'un ordre de grandeur n'est pas physique : c'est une **incohérence d'unité**
(nomenclature en KG, mouvements en G) ou un coefficient saisi avec une virgule
mal placée.

**La correction.** Trois contrôles ciblés :

- `unite_bom <> unite_article` — comparaison directe de `child_unitid` et
  `std_unit`, aujourd'hui non faite ;
- écart dont le **ratio réel/théorique est proche d'une puissance de dix**
  (0,001 · 0,1 · 10 · 1000) — signature d'une erreur d'unité, pas d'un rebut ;
- coefficient s'écartant de plus de trois écarts-types de la médiane du programme.

Ces lignes doivent être **exclues du classement financier** et routées vers une
file « paramétrage » : les mélanger aux écarts réels fausse la priorisation.

### 5. Axes société, site et entrepôt

**Le défaut.** `dataareaid` est filtré à l'ingestion et n'est pas conservé comme
axe ; le site et l'entrepôt ne sont pas remontés. Sur un groupe multi-sites, une
dérive locale est noyée dans un agrégat global, et une action corrective ne peut
pas être adressée à une équipe précise.

**La correction.** Ajouter `company`, `site_id`, `inventlocation_id` au grain du
fait, aux filtres et aux agrégats. Prérequis technique de la sécurité fine
(point 16) : sans axe site, pas de restriction par site.

---

## Décision et action

### 6. Boucle de qualification des écarts

**Le défaut.** L'outil montre les écarts ; il ne mémorise pas ce qu'on en a fait.
Chaque semaine, le key-user réexamine les mêmes lignes sans savoir lesquelles ont
déjà été instruites. Un tableau de bord sans mémoire fait refaire le même travail
indéfiniment.

**La correction.** Une table Lakebase **en écriture** (`qualification_ecart`) :
cause retenue, action décidée, responsable, échéance, statut, commentaire.
L'application est aujourd'hui volontairement en lecture seule ; ce point est le
seul qui justifie d'ouvrir une écriture, dans un schéma séparé et avec une
connexion dédiée, pour préserver la garantie structurelle actuelle.

**Effet immédiat.** Les indicateurs deviennent : « écart non qualifié », « écart
qualifié en attente d'action », « écart traité » — le vrai pilotage d'un plan
d'action, et une base d'apprentissage pour l'assistant.

### 7. Détection de dérive plutôt que d'accident isolé

**Le défaut.** Le classement est fait sur le montant de la période. Une référence
à −2 000 € toutes les semaines depuis six mois se classe derrière un accident
ponctuel à −15 000 €. Or la première est un problème de processus, la seconde un
incident déjà résolu.

**La correction.** Pour chaque `(composant, programme)`, une carte de contrôle
(moyenne mobile pondérée exponentiellement, bornes à ±3σ) et deux qualifications :

- **dérive** — n points consécutifs du même côté de la moyenne ;
- **accident** — un point hors bornes, précédé et suivi de points nominaux.

Une colonne `regime` dans `agg_ecart_composant`, un filtre dédié, et un
classement « dérives » distinct du classement « montants ». C'est le passage d'un
reporting descriptif à une analyse actionnable.

### 8. Rapprochement avec l'inventaire tournant

**Le défaut.** L'écart de backflush est une **hypothèse** sur le stock : il
prédit un écart entre stock système et stock physique. Rien ne vérifie cette
hypothèse. Une non-consommation de 50 k€ qui ne se retrouve pas à l'inventaire
signale une erreur du modèle, pas un problème d'atelier.

**La correction.** Ingérer les écarts d'inventaire (`InventJournalTrans`, journaux
de comptage) et croiser, par composant et par période :

| Backflush | Inventaire | Interprétation |
|---|---|---|
| Non-consommation | Écart négatif au comptage | **Confirmé** : le stock physique est bien inférieur au système |
| Non-consommation | Aucun écart | Suspect : backflush décalé, ou comptage sur une autre période |
| Surconsommation | Écart positif | **Confirmé** : rebut ou perte non déclarés |

Un indicateur de **taux de confirmation** donne enfin une mesure de la crédibilité
du modèle — et c'est l'argument qui fait adopter l'outil par le contrôle de gestion.

### 9. Alerting proactif et synthèse hebdomadaire

**Le défaut.** L'outil est en mode « pull » : il faut penser à le consulter. Un
écart de 80 k€ apparu lundi peut n'être vu que le vendredi.

**La correction.** Un job d'alerte après chaque ingestion :

- **immédiat** — toute référence dépassant un seuil de matérialité, ou tout
  contrôle qualité de sévérité ERREUR ;
- **hebdomadaire** — synthèse par programme, top 5 des dérives, évolution des
  indicateurs, envoyée au responsable de programme.

Les notifications portent un lien direct vers l'écran **déjà filtré** : les
filtres sont sérialisés dans l'URL, l'infrastructure est en place.

### 10. Segmentation ABC/XYZ et matérialité dynamique

**Le défaut.** Le seuil de matérialité est saisi manuellement, en euros absolus.
Un même seuil ne peut pas convenir à un aimant à 40 € et à une vis à 0,03 €.

**La correction.** Une classification ABC (valeur consommée) croisée avec XYZ
(régularité de la consommation), calculée à l'ingestion. Le seuil devient
relatif à la classe : 1 % de la valeur consommée pour un A, 5 % pour un C. Le
filtre « impact minimum » se remplace par « écart significatif pour sa classe ».

C'est la pratique standard en gestion de stock ; l'appliquer ici aligne l'outil
sur le vocabulaire des utilisateurs.

---

## Exploitation et ingénierie

### 11. Ingestion incrémentale

**Le défaut.** Chaque exécution reconstruit intégralement le modèle et republie
les neuf tables. Acceptable sur un historique de quelques mois ; intenable à
trois ans, tant en durée qu'en coût de calcul.

**La correction.** Fenêtre glissante de N semaines : `INSERT OVERWRITE` des seules
partitions concernées côté Delta, et `MERGE` sur les clés primaires côté Lakebase
plutôt que la bascule complète. Conserver la bascule atomique pour les
reconstructions complètes, déclenchées manuellement après un changement de règle.

**Prérequis.** Partitionner `fact_ecart_backflush` par `annee, semaine` et
définir la fenêtre de retraitement en fonction du délai de validation physique
observé (les mouvements antérieurs peuvent encore bouger).

### 12. Vues sauvegardées et partage d'analyses

Les filtres sont sérialisés dans l'URL — le partage par lien fonctionne déjà.
Manque la couche au-dessus : nommer une sélection (« Surconso M3 hors
paramétrage »), l'épingler, la partager à une équipe, la retrouver au prochain
comité. Une table Lakebase de préférences utilisateur suffit.

### 13. Exports planifiés et abonnements

L'export Excel est manuel. Un contrôleur de gestion veut recevoir son extraction
tous les lundis à 7h, avec ses filtres, sans se connecter. Un job réutilisant
`construire_classeur`, un dépôt sur volume Unity Catalog, un envoi par courriel :
la mécanique d'export est déjà factorisée.

### 14. Virtualisation et tri multi-colonnes des grilles

Au-delà de 250 lignes par page, le DOM devient lourd sur des postes modestes.
Une virtualisation par fenêtre (rendu des seules lignes visibles) rendrait des
pages de 1 000 lignes fluides. Manquent aussi un tri secondaire (« par programme,
puis par impact »), le figement de la première colonne au défilement horizontal,
et le redimensionnement des colonnes à la souris. Aucun n'est bloquant ; tous
sont demandés dès que la grille devient l'écran principal de travail.

### 15. Traçabilité jusqu'au mouvement de stock

Le drill-through s'arrête à la ligne `parent × composant × semaine`. Pour
instruire, il faut descendre au **mouvement** : date exacte, OF, quantité,
emplacement, lot, opérateur. Sans cela, l'investigation quitte l'outil pour
Dynamics et l'outil cesse d'être le point d'entrée.

Conserver `invent_trans.recid` dans un fait de détail, exposé à la demande
(pas répliqué en masse dans Lakebase) via une requête directe sur l'entrepôt SQL.

### 16. Sécurité fine par programme et par site

L'application interroge Lakebase avec son principal de service : **tout
utilisateur y voit tout**. Acceptable pour un pilote interne, discutable dès que
l'outil est ouvert à des équipes de programmes concurrents ou à des partenaires.

Deux niveaux, dans cet ordre :

1. filtrage applicatif à partir d'une table d'habilitations `(utilisateur,
   programme, site)`, l'identité venant de `x-forwarded-email` ;
2. politiques RLS Postgres adossées aux rôles Lakebase, pour que la restriction
   soit portée par la base et non par le code.

### 17. Observabilité applicative et audit d'usage

Aujourd'hui : des journaux et un en-tête `X-Duree-Ms`. Insuffisant pour exploiter.

- Métriques par route (latence p50/p95, taux d'erreur, saturation du pool),
  exposées et historisées ;
- journal d'audit — qui a exporté quoi, quand, avec quels filtres : exigence
  courante dès qu'un export contient des données de coût ;
- suivi de l'assistant : nombre d'appels, outils utilisés, coût par question,
  taux de réponses sans conclusion — le seul moyen de savoir s'il sert vraiment.

### 18. Chaîne CI/CD et tests de non-régression chiffrés

La couverture actuelle (101 tests) porte sur la logique métier, les filtres et
l'API. Manquent trois maillons :

- **intégration continue** — lint, tests, compilation du frontend et
  `databricks bundle validate` à chaque proposition de modification ;
- **tests de bout en bout** Playwright sur les parcours critiques : drill-through,
  export, changement de plage, copie presse-papiers. Les scénarios ont déjà été
  exécutés manuellement pendant la validation ; il reste à les figer ;
- **tests chiffrés de non-régression** — un jeu de données figé, avec des totaux
  attendus vérifiés à chaque exécution. C'est le seul filet qui empêche une
  optimisation SQL de changer silencieusement un résultat. Sur un outil
  financier, c'est le test qui compte le plus.

### 19. Accessibilité WCAG 2.2 AA et parcours clavier complet

Les fondations sont posées : rôles ARIA, `aria-sort` sur les en-têtes, poignées
du slicer opérables au clavier, texte jamais porteur de la seule couleur,
`prefers-reduced-motion` respecté. Restent à traiter : le piège de focus du
tiroir latéral, les annonces `aria-live` sur les changements de page de grille,
un audit de contraste sur les états de survol, et une vérification au lecteur
d'écran. À faire avant tout déploiement large — c'est une obligation, pas une
finition.

### 20. Internationalisation et multi-devise

L'interface est en français, avec des formats français codés dans `lib/format.ts`.
Sur un groupe international, il faudra externaliser les libellés (les définitions
d'indicateurs venant déjà du serveur, le gros du travail y est), gérer le format
de date et de nombre par locale, et surtout la **devise** : la valorisation est
en euros implicites. Une usine hors zone euro exige un taux de change, daté, et
une devise portée par la donnée plutôt que par le libellé.

---

## Ce qui n'est volontairement pas dans cette liste

- **Migration vers les *synced tables* Lakebase.** Elles simplifieraient le job,
  mais coûteraient le contrôle des index et la bascule atomique multi-tables
  (voir `ARCHITECTURE.md` §2.1). À reconsidérer seulement si le besoin d'index
  disparaît.
- **Passage à une bibliothèque de graphiques.** Les composants SVG maison font
  environ 400 lignes et respectent des règles de notation qu'aucune bibliothèque
  n'applique par configuration. Les remplacer serait une régression.
- **Ajout d'indicateurs supplémentaires en page de synthèse.** Huit tuiles est
  déjà la limite haute de ce qui se lit d'un coup d'œil. Toute mesure
  supplémentaire doit remplacer une existante, ou vivre dans une grille.
