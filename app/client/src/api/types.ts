/**
 * Types du contrat d'API.
 *
 * Ils décrivent ce que renvoie le backend ; ils ne dupliquent AUCUNE règle
 * métier. Les libellés d'indicateurs, les définitions de colonnes et les
 * formats viennent du serveur — le frontend ne fait que les mettre en forme.
 */

export type TypeEcart = 'Non-consommation' | 'Surconsommation' | 'Conforme'
export type StatutLigne = 'Nominal' | 'Hors nomenclature' | 'Sans consommation'
export type CleGrille =
  | 'details'
  | 'details_of'
  | 'composants'
  | 'programmes'
  | 'perimetres'
  | 'parents'

/** Mesure d'affichage et de classement : euros ou unités. */
export type Mesure = 'valeur' | 'quantite'

/** Sélection appliquée à toutes les vues. Miroir exact de `Filtres` côté Python. */
export interface Filtres {
  date_debut: string | null
  date_fin: string | null
  /**
   * Semaines retenues, désignées par leur lundi. Restriction énumérée, en
   * complément des bornes : celles-ci décrivent un intervalle continu et ne
   * savent pas exprimer « ces semaines-là, mais pas celle du milieu ». Utilisée
   * par le tiroir de contexte pour séparer les lignes qui composent le chiffre
   * cliqué de celles qui l'éclairent.
   */
  semaines_debut: string[]
  programmes: string[]
  perimetres: string[]
  categories: string[]
  types_ecart: TypeEcart[]
  statuts_ligne: StatutLigne[]
  parents: string[]
  composants: string[]
  /**
   * Axe de l'ordre de fabrication. N'a d'effet que sur la vue « Détail par
   * OF » : la table de détail à la maille parent a perdu l'OF au moment de son
   * agrégation. Ailleurs, le serveur les ignore et la barre de filtres les
   * vide — voir `BarreFiltres`.
   */
  ofs: string[]
  statuts_of: string[]
  recherche: string | null
  seuil_conformite: number
  seuil_pct: number | null
  impact_min: number | null
  coef_uniforme_uniquement: boolean
  exclure_conforme: boolean
}

export interface OptionsFiltres {
  programmes: string[]
  perimetres: string[]
  categories: string[]
  types_ecart: TypeEcart[]
  statuts_ligne: StatutLigne[]
  /** Statuts d'OF présents, classés dans l'ordre du cycle de vie D365. */
  statuts_of: string[]
  date_min: string | null
  date_max: string | null
  defauts: {
    date_debut: string | null
    date_fin: string | null
    seuil_conformite: number
    horizon_semaines: number
  }
}

export type FormatValeur = 'entier' | 'decimal' | 'euro' | 'pourcent'
export type SensFavorable = 'hausse' | 'baisse' | 'neutre'

export interface Indicateur {
  cle: string
  libelle: string
  valeur: number
  unite: string
  format: FormatValeur
  sens_favorable: SensFavorable
  variation_pct: number | null
  variation_absolue: number | null
  aide: string
}

export interface Concentration {
  nb_references: number
  tete: number
  impact_total: number
  impact_tete: number
  part_tete_pct: number
}

export interface ReponseIndicateurs {
  indicateurs: Indicateur[]
  concentration: Concentration
  comparaison_disponible: boolean
}

/** Une semaine de la série hebdomadaire. Toutes les mesures sont numériques. */
export interface SemaineAgregee {
  semaine_debut: string
  annee: number
  semaine: number
  semaine_libelle: string
  nb_lignes: number
  nb_lignes_ecart: number
  nb_parents: number
  nb_composants: number
  nb_programmes: number
  nb_perimetres: number
  nb_semaines: number
  conso_theorique: number
  conso_theorique_valorisee: number
  ecart_equivalent_produit: number
  conso_reelle: number
  ecart_net: number
  non_consommation: number
  surconsommation: number
  ecart_valorise: number
  non_consommation_valorisee: number
  surconsommation_valorisee: number
  ecart_valorise_absolu: number
  /** Somme des |écarts| en unités — pendant de `ecart_valorise_absolu` en quantité. */
  ecart_absolu: number
}

export interface LigneRepartition extends Omit<SemaineAgregee, 'semaine_debut' | 'annee' | 'semaine' | 'semaine_libelle'> {
  libelle: string
}

export interface LigneTopComposant extends LigneRepartition {
  child_itemid: string
  child_name: string | null
  child_categorie: string | null
  parent_programme: string
}

export type TypeColonne =
  | 'texte'
  | 'entier'
  | 'decimal'
  | 'euro'
  | 'pourcent'
  | 'date'
  | 'booleen'
  | 'badge'

export interface Colonne {
  cle: string
  libelle: string
  type: TypeColonne
  alignement: 'gauche' | 'droite' | 'centre'
  decimales: number
  triable: boolean
  visible: boolean
  largeur: number
  aide: string
}

export interface Grille {
  cle: CleGrille
  libelle: string
  description: string
  cle_ligne: string[]
  tri_defaut: string
  sens_defaut: 'asc' | 'desc'
  colonnes: Colonne[]
}

export type LigneGrille = Record<string, unknown>

export interface PageGrille {
  lignes: LigneGrille[]
  /** Totaux de la sélection entière — jamais ceux de la page affichée. */
  totaux: Record<string, number | null>
  total: number
  page: number
  taille: number
  nb_pages: number
  tri: string
  sens: 'asc' | 'desc'
}

export interface EtatIngestion {
  table_name: string
  row_count: number
  ended_at: string | null
  duration_ms: number
  status: string
  error_message: string | null
}

export interface ControleQualite {
  controle: string
  severite: 'ERREUR' | 'ALERTE' | 'INFO'
  domaine: string
  valeur: number
  en_anomalie: boolean
  message: string
}

export interface Fraicheur {
  derniere_ingestion: string | null
  tables: EtatIngestion[]
  controles: ControleQualite[]
  en_echec: string[]
}

export interface Sante {
  application: Record<string, unknown>
  base: { statut: string; detail?: string }
}

export interface AppelOutil {
  outil: string
  arguments: Record<string, unknown>
  nb_lignes: number | null
  erreur: string | null
}

export interface ReponseAssistant {
  reponse: string
  appels: AppelOutil[]
  avertissement: string
  modele: string
}

export interface FicheArticle {
  item_id: string
  item_name: string | null
  item_description: string | null
  categorie: string | null
  item_group_id: string | null
  item_group_label: string | null
  programme: string | null
  std_cost_price: number | null
  std_unit: string | null
}

export interface FicheComposant {
  article: FicheArticle | null
  indicateurs: Indicateur[]
  semaines: SemaineAgregee[]
  parents: Array<{
    parent_itemid: string
    parent_name: string | null
    programme: string | null
    perimetre: string | null
    /** Coefficient EFFECTIF : celui de l'ERP, ou sa correction si elle existe. */
    coef_bom: number | null
    coef_surcharge: boolean
  }>
}

export interface FicheParent {
  article: FicheArticle | null
  indicateurs: Indicateur[]
  semaines: SemaineAgregee[]
  nomenclature: Array<{
    child_itemid: string
    child_name: string | null
    categorie: string | null
    /** Coefficient EFFECTIF : celui de l'ERP, ou sa correction si elle existe. */
    coef_bom: number | null
    coef_erp: number | null
    coef_surcharge: boolean
    unite: string | null
    std_cost_price: number | null
  }>
}

/** Une ligne du bloc « production » de la vue synthétique d'un périmètre. */
export interface LigneProductionSynthese {
  parent_itemid: string
  parent_name: string | null
  bomid: string | null
  semaine_debut: string
  annee: number
  semaine: number
  qty_produite: number
  valeur_produite: number
}

/** Une ligne du bloc « écart de prélèvement » de la vue synthétique. */
export interface LigneEcartSynthese {
  child_itemid: string
  child_name: string | null
  coef_bom: number | null
  semaine_debut: string
  annee: number
  semaine: number
  ecart_equivalent_produit: number
  ecart_valorise: number
  ecart_brut: number
}

/** Une semaine du calendrier de la période, mouvementée ou non. */
export interface SemaineCalendrier {
  semaine_debut: string
  annee: number
  semaine: number
}

/**
 * Contexte d'investigation d'une cellule d'écart : les ordres de fabrication
 * derrière le chiffre, et toutes les semaines où ils ont mouvementé ce
 * composant. Le débordement au-delà de la semaine cliquée est l'information
 * utile — c'est ce qui distingue un écart résiduel d'un décalage de calage.
 */
export interface ContexteOf {
  perimetre: string
  composant: string
  child_name: string | null
  child_unite: string | null
  annee: number
  semaine: number
  semaine_debut: string | null
  ecart_brut: number | null
  ecart_equivalent_produit: number | null
  ecart_valorise: number | null
  ofs: string[]
  /** Vrai si la liste d'OF a été écrêtée : le tiroir doit le dire. */
  tronque: boolean
  semaines: SemaineCalendrier[]
  date_debut: string | null
  date_fin: string | null
}

export interface SynthesePerimetre {
  perimetre: string
  mesure: Mesure
  production: LigneProductionSynthese[]
  ecarts: LigneEcartSynthese[]
  /** Colonnes du tableau croisé : toute la période, trous compris. */
  semaines: SemaineCalendrier[]
}

// ---------------------------------------------------------------------------
// Paramétrage — base article et nomenclature
// ---------------------------------------------------------------------------

export interface LigneArticle {
  item_id: string
  item_name: string | null
  categorie: string | null
  programme: string | null
  perimetre: string | null
  type_produit: string | null
  std_cost_price: number | null
  std_unit: string | null
  /** Impact absolu porté par la référence — à lire AVANT de l'exclure. */
  impact_absolu: number
  nb_semaines_en_ecart: number
  exclu: boolean
  motif: string | null
  modifie_par: string | null
  modifie_le: string | null
}

export interface LigneNomenclatureParam {
  parent_itemid: string
  child_itemid: string
  bomid: string | null
  parent_name: string | null
  child_name: string | null
  perimetre: string | null
  unite: string | null
  coef_origine: number | null
  coef_effectif: number | null
  active: boolean
  coef_surcharge: boolean
  motif: string | null
  modifie_par: string | null
  modifie_le: string | null
}

export interface PageParametrage<T> {
  lignes: T[]
  total: number
  page: number
  taille: number
  nb_pages: number
}

export interface ResumeParametrage {
  articles_exclus: number
  lignes_desactivees: number
  lignes_corrigees: number
}

/**
 * Résultat du diagnostic de l'assistant : une étape par cause possible, dans
 * l'ordre où elles s'excluent. Chaque étape porte son propre verdict — un
 * diagnostic n'a d'intérêt que s'il dit où la chaîne casse.
 */
export interface EtapeDiagnostic {
  etape: string
  ok: boolean
  message: string
  /** Geste de correction, quand l'étape a échoué. */
  remede?: string
  /** Noms de endpoints à reprendre, quand celui configuré est introuvable. */
  endpoints_disponibles?: string[]
}

export interface DiagnosticAssistant {
  endpoint: string
  ok: boolean
  etapes: EtapeDiagnostic[]
}
