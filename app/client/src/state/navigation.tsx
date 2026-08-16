/**
 * Navigation entre écrans, sans dépendance à un routeur.
 *
 * L'application compte cinq écrans et un tiroir de détail : un routeur complet
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

export const PAGES = {
  synthese: 'Synthèse',
  programmes: 'Programmes',
  references: 'Références',
  detail: 'Détail',
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
  aller: (page: Page) => void
  fiche: Fiche
  ouvrirFiche: (fiche: NonNullable<Fiche>) => void
  fermerFiche: () => void
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

  // Prise en charge des boutons Précédent / Suivant du navigateur.
  useEffect(() => {
    const surRetour = () => setPage(pageDepuisChemin(window.location.pathname))
    window.addEventListener('popstate', surRetour)
    return () => window.removeEventListener('popstate', surRetour)
  }, [])

  const aller = useCallback((cible: Page) => {
    setPage(cible)
    setFiche(null)
    // Les paramètres de filtre sont conservés : changer d'écran ne change pas
    // le périmètre analysé.
    window.history.pushState(null, '', `/${cible}${window.location.search}`)
  }, [])

  const ouvrirFiche = useCallback((valeur: NonNullable<Fiche>) => setFiche(valeur), [])
  const fermerFiche = useCallback(() => setFiche(null), [])

  const valeur = useMemo(
    () => ({ page, aller, fiche, ouvrirFiche, fermerFiche }),
    [page, aller, fiche, ouvrirFiche, fermerFiche],
  )

  return <Contexte.Provider value={valeur}>{children}</Contexte.Provider>
}

export function useNavigation(): ContexteNavigation {
  const contexte = useContext(Contexte)
  if (!contexte) throw new Error('useNavigation doit être utilisé dans un NavigationProvider.')
  return contexte
}
