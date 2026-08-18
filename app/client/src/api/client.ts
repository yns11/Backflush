/**
 * Client HTTP de l'API.
 *
 * Un seul endroit connaît les chemins, la sérialisation et le traitement des
 * erreurs. Les composants n'appellent jamais `fetch` directement : ils
 * consomment ces fonctions typées via React Query.
 */

import type {
  CleGrille,
  ContexteOf,
  DiagnosticAssistant,
  FicheComposant,
  FicheParent,
  Filtres,
  Fraicheur,
  Grille,
  LigneArticle,
  LigneGrille,
  LigneNomenclatureParam,
  LigneRepartition,
  LigneTopComposant,
  OptionsFiltres,
  PageGrille,
  PageParametrage,
  ReponseAssistant,
  ReponseIndicateurs,
  Mesure,
  ResumeParametrage,
  Sante,
  SemaineAgregee,
  SynthesePerimetre,
} from './types'

/** Paramètres communs aux deux écrans de paramétrage. */
export interface RequeteParametrage {
  recherche?: string | null
  etat?: string
  tri?: string
  sens?: 'asc' | 'desc'
  page?: number
  taille?: number
  [cle: string]: unknown
}

/** Sérialise une requête en chaîne d'URL, en omettant les valeurs absentes. */
function encoder(params: Record<string, unknown>): string {
  const query = new URLSearchParams()
  for (const [cle, valeur] of Object.entries(params)) {
    if (valeur !== null && valeur !== undefined && valeur !== '') query.set(cle, String(valeur))
  }
  return query.toString()
}

/** Erreur d'API portant le code HTTP et le message métier renvoyé par le serveur. */
export class ErreurApi extends Error {
  constructor(
    message: string,
    readonly statut: number,
    readonly type?: string,
    /**
     * Cause technique, quand le serveur a jugé utile de la transmettre.
     * Présente sur les pannes de configuration — nom de endpoint, droits,
     * paramètre refusé — absente sur les erreurs de données.
     */
    readonly detail?: string,
  ) {
    super(message)
    this.name = 'ErreurApi'
  }

  /** Vrai lorsque la cause est structurelle (base absente ou injoignable). */
  get estIndisponibilite(): boolean {
    return this.statut === 503
  }
}

async function appeler<T>(chemin: string, options: RequestInit = {}): Promise<T> {
  let reponse: Response
  try {
    reponse = await fetch(chemin, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(options.headers ?? {}),
      },
    })
  } catch {
    // Réseau coupé, application redémarrée : message actionnable plutôt que
    // le « Failed to fetch » brut du navigateur.
    throw new ErreurApi("Le serveur est injoignable. Vérifiez votre connexion.", 0)
  }

  if (!reponse.ok) {
    const corps = await reponse.json().catch(() => null)
    const message =
      (corps && typeof corps === 'object' && 'erreur' in corps
        ? String((corps as { erreur: unknown }).erreur)
        : null) ?? `Erreur ${reponse.status}`
    const lire = (cle: string): string | undefined =>
      corps && typeof corps === 'object' && cle in corps
        ? String((corps as Record<string, unknown>)[cle])
        : undefined
    throw new ErreurApi(message, reponse.status, lire('type'), lire('detail'))
  }

  return (await reponse.json()) as T
}

function poster<T>(chemin: string, corps: unknown): Promise<T> {
  return appeler<T>(chemin, { method: 'POST', body: JSON.stringify(corps) })
}

export const api = {
  sante: () => appeler<Sante>('/api/health'),

  optionsFiltres: () => appeler<OptionsFiltres>('/api/meta/filtres'),

  grilles: () => appeler<Record<CleGrille, Grille>>('/api/meta/grilles'),

  fraicheur: () => appeler<Fraicheur>('/api/meta/fraicheur'),

  indicateurs: (filtres: Filtres, mesure: Mesure = 'valeur') =>
    poster<ReponseIndicateurs>(`/api/analytique/indicateurs?mesure=${mesure}`, filtres),

  tendance: (filtres: Filtres) =>
    poster<{ semaines: SemaineAgregee[] }>('/api/analytique/tendance', filtres),

  chronologie: (filtres: Filtres) =>
    poster<{ semaines: SemaineAgregee[] }>('/api/analytique/chronologie', filtres),

  repartition: (
    dimension: 'programme' | 'perimetre' | 'categorie' | 'type' | 'statut',
    filtres: Filtres,
    mesure: Mesure = 'valeur',
  ) =>
    poster<{ dimension: string; lignes: LigneRepartition[] }>(
      `/api/analytique/repartition/${dimension}?mesure=${mesure}`,
      filtres,
    ),

  topComposants: (filtres: Filtres, limite = 10, mesure: Mesure = 'valeur') =>
    poster<{ lignes: LigneTopComposant[] }>(
      `/api/analytique/top-composants?limite=${limite}&mesure=${mesure}`,
      filtres,
    ),

  /** Tableau croisé production × semaine et écart × semaine, pour UN périmètre. */
  synthesePerimetre: (filtres: Filtres, mesure: Mesure = 'quantite') =>
    poster<SynthesePerimetre>(`/api/analytique/synthese-perimetre?mesure=${mesure}`, filtres),

  /**
   * Ordres de fabrication derrière UNE cellule d'écart de la vue synthétique.
   *
   * N'envoie pas l'objet de filtres : le contexte doit montrer tous les ordres
   * qui expliquent le chiffre, y compris ceux qu'un filtre global aurait
   * écartés. Le serveur applique en revanche le paramétrage du référentiel,
   * comme la vue synthétique elle-même.
   */
  contexteOf: (cellule: {
    perimetre: string
    composant: string
    annee: number
    semaine: number
  }) => poster<ContexteOf>('/api/analytique/contexte-of', cellule),

  ficheComposant: (childItemId: string, filtres: Filtres) =>
    poster<FicheComposant>(
      `/api/analytique/composant/${encodeURIComponent(childItemId)}`,
      filtres,
    ),

  ficheParent: (parentItemId: string, filtres: Filtres) =>
    poster<FicheParent>(`/api/analytique/parent/${encodeURIComponent(parentItemId)}`, filtres),

  pageGrille: (
    cle: CleGrille,
    corps: { filtres: Filtres; tri?: string | null; sens?: 'asc' | 'desc'; page?: number; taille?: number },
  ) => poster<PageGrille>(`/api/grilles/${cle}`, corps),

  pressePapiers: (
    cle: CleGrille,
    corps: { filtres: Filtres; tri?: string | null; sens?: 'asc' | 'desc' },
    lignesMax = 5000,
  ) =>
    poster<{ tsv: string; lignes: number; colonnes: number }>(
      `/api/grilles/${cle}/presse-papiers?lignes_max=${lignesMax}`,
      corps,
    ),

  etatAssistant: () =>
    appeler<{ actif: boolean; modele: string | null; tours_max: number; execution: string }>(
      '/api/assistant/etat',
    ),

  diagnosticAssistant: () => appeler<DiagnosticAssistant>('/api/assistant/diagnostic'),

  suggestions: () => appeler<{ suggestions: string[] }>('/api/assistant/suggestions'),

  chat: (corps: {
    messages: Array<{ role: 'user' | 'assistant'; contenu: string }>
    filtres: Filtres
    page_active: string
  }) => poster<ReponseAssistant>('/api/assistant/chat', corps),

  // -- Paramétrage : base article et nomenclature ---------------------------
  resumeParametrage: () => appeler<ResumeParametrage>('/api/parametrage/resume'),

  articles: (params: RequeteParametrage) =>
    appeler<PageParametrage<LigneArticle>>(`/api/parametrage/articles?${encoder(params)}`),

  nomenclature: (params: RequeteParametrage) =>
    appeler<PageParametrage<LigneNomenclatureParam>>(
      `/api/parametrage/nomenclature?${encoder(params)}`,
    ),

  basculerExclusion: (corps: { item_ids: string[]; exclu: boolean; motif?: string | null }) =>
    poster<{ lignes: number; exclu: boolean }>('/api/parametrage/articles/exclusion', corps),

  surchargerNomenclature: (corps: {
    lignes: Array<{ parent_itemid: string; child_itemid: string }>
    active: boolean
    coef_bom?: number | null
    motif?: string | null
  }) => poster<{ lignes: number }>('/api/parametrage/nomenclature/surcharge', corps),

  reinitialiserNomenclature: (corps: {
    lignes: Array<{ parent_itemid: string; child_itemid: string }>
  }) => poster<{ lignes: number }>('/api/parametrage/nomenclature/reinitialisation', corps),

  analyserLot: (corps: {
    grille: string
    lignes: LigneGrille[]
    filtres: Filtres
    question: string | null
  }) => poster<ReponseAssistant>('/api/assistant/analyser-lot', corps),
}

/**
 * Télécharge l'export Excel d'une grille.
 *
 * Le nom de fichier est décidé par le serveur (il porte les filtres et
 * l'horodatage) : on le lit dans l'en-tête `Content-Disposition` plutôt que de
 * le reconstruire, au risque de diverger.
 */
export function telechargerExport(
  cle: CleGrille,
  corps: {
    filtres: Filtres
    tri?: string | null
    sens?: 'asc' | 'desc'
    colonnes?: string[]
    lignes_max?: number
  },
): Promise<{ nom: string; octets: number }> {
  return telecharger(`/api/export/${cle}.xlsx`, corps, `backflush_${cle}.xlsx`)
}

/**
 * Télécharge le tableau croisé d'un périmètre.
 *
 * Le classeur est reconstruit côté serveur à partir des mêmes données que
 * l'écran, mais avec des zéros explicites : une case vide obligerait le
 * key-user à neutraliser les vides avant toute somme.
 */
export function telechargerSynthese(
  corps: { filtres: Filtres; mesure: Mesure },
): Promise<{ nom: string; octets: number }> {
  return telecharger(
    '/api/export/synthese-perimetre.xlsx',
    corps,
    'backflush_synthese.xlsx',
  )
}

async function telecharger(
  chemin: string,
  corps: unknown,
  nomDefaut: string,
): Promise<{ nom: string; octets: number }> {
  const reponse = await fetch(chemin, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(corps),
  })
  if (!reponse.ok) {
    const detail = await reponse.json().catch(() => null)
    throw new ErreurApi(
      (detail && typeof detail === 'object' && 'erreur' in detail
        ? String((detail as { erreur: unknown }).erreur)
        : null) ?? "L'export a échoué.",
      reponse.status,
    )
  }

  const blob = await reponse.blob()
  const nom = nomDepuisEntete(reponse.headers.get('Content-Disposition')) ?? nomDefaut

  const url = URL.createObjectURL(blob)
  const lien = document.createElement('a')
  lien.href = url
  lien.download = nom
  document.body.appendChild(lien)
  lien.click()
  lien.remove()
  // Libération différée : révoquer immédiatement annulerait le téléchargement
  // sur certains navigateurs.
  setTimeout(() => URL.revokeObjectURL(url), 10_000)

  return { nom, octets: blob.size }
}

function nomDepuisEntete(entete: string | null): string | null {
  if (!entete) return null
  const encode = /filename\*=UTF-8''([^;]+)/i.exec(entete)
  if (encode?.[1]) return decodeURIComponent(encode[1])
  const simple = /filename="?([^";]+)"?/i.exec(entete)
  return simple?.[1] ?? null
}
