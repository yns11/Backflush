/**
 * Navigation entre écrans, sans dépendance à un routeur.
 *
 * L'application compte six écrans et un tiroir de détail : un routeur complet
 * apporterait plus de surface d'API que de valeur. La page active est reflétée
 * dans le chemin de l'URL (FastAPI renvoie `index.html` pour tout chemin
 * inconnu), et le bouton « Précédent » du navigateur fonctionne.
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

import { criteresOfDansUrl } from './filtres'

export const PAGES = {
  synthese: 'Synthèse',
  programmes: 'Programmes',
  perimetres: 'Périmètres',
  references: 'Références',
  detail: 'Détail',
  articles: 'Base article',
  nomenclature: 'Nomenclature',
  assistant: 'Assistant IA',
} as const

export type Page = keyof typeof PAGES

/** Fiche ouverte dans le tiroir latéral, le cas échéant. */
export type Fiche =
  | { genre: 'composant'; id: string }
  | { genre: 'parent'; id: string }
  | null

interface ContexteNavigation {
  page: Page
  aller: (page: Page, options?: OptionsNavigation) => void
  fiche: Fiche
  ouvrirFiche: (fiche: NonNullable<Fiche>) => void
  fermerFiche: () => void
  /**
   * Vue synthétique active sur l'écran « Périmètres ».
   *
   * L'état vit ici et non dans l'écran, parce qu'on y arrive aussi depuis
   * AILLEURS : un clic sur la colonne « Périmètre » d'une grille doit ouvrir
   * directement le tableau croisé. Un état local à l'écran serait réinitialisé
   * à chaque montage, et le clic retomberait sur la grille hebdomadaire.
   */
  synthetique: boolean
  definirSynthetique: (actif: boolean) => void
  /**
   * Granularité de l'écran « Détail » : maille parent, ou maille OF.
   *
   * L'état vit ici, et non dans l'écran, parce qu'un AUTRE composant en dépend :
   * la barre de filtres, qui n'est pas dans l'arbre du détail. Les critères
   * « Numéro OF » et « Statut OF » n'ont de sens que sur la maille OF — ailleurs
   * les colonnes n'existent pas — et la barre les vide et les grise en
   * conséquence. Un état local à l'écran l'obligerait à deviner.
   */
  detailParOf: boolean
  definirDetailParOf: (actif: boolean) => void
}

export interface OptionsNavigation {
  /** Force le mode de la vue « Périmètres » à l'arrivée. */
  synthetique?: boolean
}

const Contexte = createContext<ContexteNavigation | null>(null)

function pageDepuisChemin(chemin: string): Page {
  const segment = chemin.replace(/^\/+|\/+$/g, '').split('/')[0] ?? ''
  return segment in PAGES ? (segment as Page) : 'synthese'
}

export function NavigationProvider({ children }: { children: ReactNode }) {
  const [page, setPage] = useState<Page>(() =>
    typeof window === 'undefined' ? 'synthese' : pageDepuisChemin(window.location.pathname),
  )
  const [fiche, setFiche] = useState<Fiche>(null)
  const [synthetique, setSynthetique] = useState(false)
  // Un lien portant un filtre d'OF vient forcément de la maille OF : y arriver
  // à la maille parent ferait vider ces critères par la barre de filtres, et le
  // lien partagé n'ouvrirait pas ce qu'il décrivait.
  const [detailParOf, setDetailParOf] = useState(criteresOfDansUrl)

  // Prise en charge des boutons Précédent / Suivant du navigateur.
  useEffect(() => {
    const surRetour = () => setPage(pageDepuisChemin(window.location.pathname))
    window.addEventListener('popstate', surRetour)
    return () => window.removeEventListener('popstate', surRetour)
  }, [])

  const aller = useCallback((cible: Page, options?: OptionsNavigation) => {
    setPage(cible)
    setFiche(null)
    if (options?.synthetique !== undefined) setSynthetique(options.synthetique)
    // Les paramètres de filtre sont conservés : changer d'écran ne change pas
    // le périmètre analysé.
    window.history.pushState(null, '', `/${cible}${window.location.search}`)
  }, [])

  const ouvrirFiche = useCallback((valeur: NonNullable<Fiche>) => setFiche(valeur), [])
  const fermerFiche = useCallback(() => setFiche(null), [])

  const valeur = useMemo(
    () => ({
      page, aller, fiche, ouvrirFiche, fermerFiche,
      synthetique, definirSynthetique: setSynthetique,
      detailParOf, definirDetailParOf: setDetailParOf,
    }),
    [page, aller, fiche, ouvrirFiche, fermerFiche, synthetique, detailParOf],
  )

  return <Contexte.Provider value={valeur}>{children}</Contexte.Provider>
}

export function useNavigation(): ContexteNavigation {
  const contexte = useContext(Contexte)
  if (!contexte) throw new Error('useNavigation doit être utilisé dans un NavigationProvider.')
  return contexte
}
