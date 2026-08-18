/**
 * État global des filtres.
 *
 * Un seul objet de filtres pilote TOUS les écrans : c'est ce qui rend le
 * drill-through cohérent — cliquer sur un programme dans la synthèse ouvre le
 * détail avec exactement la même sélection, augmentée de ce programme.
 *
 * Les filtres sont sérialisés dans l'URL : une analyse se partage par simple
 * copier-coller du lien, sans re-décrire les critères à l'oral.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

import type { Filtres, OptionsFiltres } from '@/api/types'

export const FILTRES_VIDES: Filtres = {
  date_debut: null,
  date_fin: null,
  // Absente de CHAMPS_LISTE, donc de l'URL : c'est une restriction posée par le
  // tiroir de contexte, pas un critère que l'utilisateur choisit et partage.
  semaines_debut: [],
  programmes: [],
  perimetres: [],
  categories: [],
  types_ecart: [],
  statuts_ligne: [],
  parents: [],
  composants: [],
  ofs: [],
  statuts_of: [],
  recherche: null,
  seuil_conformite: 0.5,
  seuil_pct: null,
  impact_min: null,
  coef_uniforme_uniquement: false,
  exclure_conforme: false,
}

interface ContexteFiltres {
  filtres: Filtres
  /** Applique une modification partielle. */
  modifier: (patch: Partial<Filtres>) => void
  /** Remplace intégralement la sélection. */
  remplacer: (filtres: Filtres) => void
  /** Revient aux valeurs par défaut du serveur, dates comprises. */
  reinitialiser: () => void
  /** Nombre de critères actifs hors dates — alimente le compteur de la barre. */
  nbCriteresActifs: number
  /** Vrai tant que les valeurs par défaut du serveur n'ont pas été appliquées. */
  initialise: boolean
}

const Contexte = createContext<ContexteFiltres | null>(null)

/** Champs sérialisés dans l'URL, avec leur mode d'encodage. */
const CHAMPS_LISTE = [
  'programmes',
  'perimetres',
  'categories',
  'types_ecart',
  'statuts_ligne',
  'parents',
  'composants',
  // Les deux critères d'OF sont sérialisés comme les autres : un lien partagé
  // depuis la vue « Détail par OF » doit rouvrir la même sélection. La barre de
  // filtres les vide dès qu'on quitte cette vue, de sorte qu'ils ne peuvent
  // rester dans l'URL sans être visibles à l'écran.
  'ofs',
  'statuts_of',
] as const

/**
 * Un lien partagé porte-t-il un critère d'ordre de fabrication ?
 *
 * Sert à l'ouverture : ces deux critères n'ont d'effet que sur la vue « Détail
 * par OF », et la barre de filtres les vide partout ailleurs. Sans cette
 * question posée AVANT que l'écran ne se monte, un lien partagé depuis cette
 * vue arriverait à la maille parent et se ferait immédiatement dépouiller de
 * ce qu'il transportait.
 */
export function criteresOfDansUrl(): boolean {
  if (typeof window === 'undefined') return false
  const params = new URLSearchParams(window.location.search)
  return Boolean(params.get('ofs') || params.get('statuts_of'))
}

export function FiltresProvider({
  children,
  options,
}: {
  children: ReactNode
  options: OptionsFiltres | undefined
}) {
  const [filtres, setFiltres] = useState<Filtres>(() => lireUrl() ?? FILTRES_VIDES)
  const [initialise, setInitialise] = useState(() => lireUrl() !== null)

  // Les défauts (fenêtre glissante, seuil) sont décidés par le serveur : le
  // frontend ne code aucune règle. Ils ne s'appliquent qu'en l'absence de
  // filtres dans l'URL, pour ne pas écraser un lien partagé.
  useEffect(() => {
    if (initialise || !options) return
    setFiltres((precedents) => ({
      ...precedents,
      date_debut: options.defauts.date_debut,
      date_fin: options.defauts.date_fin,
      seuil_conformite: options.defauts.seuil_conformite,
    }))
    setInitialise(true)
  }, [options, initialise])

  useEffect(() => {
    if (initialise) ecrireUrl(filtres)
  }, [filtres, initialise])

  const modifier = useCallback((patch: Partial<Filtres>) => {
    setFiltres((precedents) => ({ ...precedents, ...patch }))
  }, [])

  const remplacer = useCallback((valeur: Filtres) => setFiltres(valeur), [])

  const reinitialiser = useCallback(() => {
    setFiltres({
      ...FILTRES_VIDES,
      date_debut: options?.defauts.date_debut ?? null,
      date_fin: options?.defauts.date_fin ?? null,
      seuil_conformite: options?.defauts.seuil_conformite ?? 0.5,
    })
  }, [options])

  const nbCriteresActifs = useMemo(() => {
    let total = CHAMPS_LISTE.reduce((somme, champ) => somme + filtres[champ].length, 0)
    if (filtres.recherche) total += 1
    if (filtres.impact_min !== null) total += 1
    if (filtres.coef_uniforme_uniquement) total += 1
    if (filtres.exclure_conforme) total += 1
    if (filtres.seuil_pct !== null) total += 1
    return total
  }, [filtres])

  const valeur = useMemo(
    () => ({ filtres, modifier, remplacer, reinitialiser, nbCriteresActifs, initialise }),
    [filtres, modifier, remplacer, reinitialiser, nbCriteresActifs, initialise],
  )

  return <Contexte.Provider value={valeur}>{children}</Contexte.Provider>
}

export function useFiltres(): ContexteFiltres {
  const contexte = useContext(Contexte)
  if (!contexte) throw new Error('useFiltres doit être utilisé dans un FiltresProvider.')
  return contexte
}

/* -------------------------------------------------------------------------
   Sérialisation dans l'URL
   ------------------------------------------------------------------------- */

function lireUrl(): Filtres | null {
  if (typeof window === 'undefined') return null
  const params = new URLSearchParams(window.location.search)
  if ([...params.keys()].length === 0) return null

  const filtres: Filtres = { ...FILTRES_VIDES }
  filtres.date_debut = params.get('du')
  filtres.date_fin = params.get('au')
  for (const champ of CHAMPS_LISTE) {
    const brut = params.get(champ)
    if (brut) {
      // Le tableau est typé plus largement que les littéraux : la validation
      // effective est faite par Pydantic côté serveur, qui rejette une valeur
      // inconnue plutôt que de la laisser fausser une requête.
      ;(filtres[champ] as string[]) = brut.split('|').filter(Boolean)
    }
  }
  filtres.recherche = params.get('q')
  const seuil = params.get('seuil')
  if (seuil !== null && Number.isFinite(Number(seuil))) filtres.seuil_conformite = Number(seuil)
  const seuilPct = params.get('seuilpct')
  if (seuilPct !== null && Number.isFinite(Number(seuilPct))) filtres.seuil_pct = Number(seuilPct)
  const impact = params.get('impact')
  if (impact !== null && Number.isFinite(Number(impact))) filtres.impact_min = Number(impact)
  filtres.coef_uniforme_uniquement = params.get('uniforme') === '1'
  filtres.exclure_conforme = params.get('horsconforme') === '1'
  return filtres
}

function ecrireUrl(filtres: Filtres): void {
  const params = new URLSearchParams()
  if (filtres.date_debut) params.set('du', filtres.date_debut)
  if (filtres.date_fin) params.set('au', filtres.date_fin)
  for (const champ of CHAMPS_LISTE) {
    const valeurs = filtres[champ]
    if (valeurs.length) params.set(champ, valeurs.join('|'))
  }
  if (filtres.recherche) params.set('q', filtres.recherche)
  if (filtres.seuil_conformite !== 0.5) params.set('seuil', String(filtres.seuil_conformite))
  if (filtres.seuil_pct !== null) params.set('seuilpct', String(filtres.seuil_pct))
  if (filtres.impact_min !== null) params.set('impact', String(filtres.impact_min))
  if (filtres.coef_uniforme_uniquement) params.set('uniforme', '1')
  if (filtres.exclure_conforme) params.set('horsconforme', '1')

  const requete = params.toString()
  const cible = `${window.location.pathname}${requete ? `?${requete}` : ''}`
  // replaceState et non pushState : ajuster un filtre ne doit pas créer une
  // entrée d'historique par frappe au clavier.
  window.history.replaceState(null, '', cible)
}
