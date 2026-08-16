/**
 * Types du contrat d'API.
 *
 * Ils décrivent ce que renvoie le backend ; ils ne dupliquent AUCUNE règle
 * métier. Les libellés d'indicateurs, les définitions de colonnes et les
 * formats viennent du serveur — le frontend ne fait que les mettre en forme.
 */

export type TypeEcart = 'Non-consommation' | 'Surconsommation' | 'Conforme'
export type StatutLigne = 'Nominal' | 'Hors nomenclature' | 'Sans consommation'
export type CleGrille = 'details' | 'composants' | 'programmes' | 'parents'

/** Sélection appliquée à toutes les vues. Miroir exact de `Filtres` côté Python. */
export interface Filtres {
  date_debut: string | null
  date_fin: string | null
  programmes: string[]
  categories: string[]
  types_ecart: TypeEcart[]
  statuts_ligne: StatutLigne[]
  parents: string[]
  composants: string[]
  recherche: string | null
  seuil_conformite: number
  seuil_pct: number | null
  impact_min: number | null
  coef_uniforme_uniquement: boolean
  exclure_conforme: boolean
}

export interface OptionsFiltres {
  programmes: string[]
  categories: string[]
  types_ecart: TypeEcart[]
  statuts_ligne: StatutLigne[]
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
    coef_bom: number | null
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
    coef_bom: number | null
    unite: string | null
    std_cost_price: number | null
  }>
}
