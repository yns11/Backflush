/**
 * Thème clair / sombre.
 *
 * Trois états : `systeme` (aucun attribut posé, la requête média décide),
 * `light` et `dark` (choix explicite, qui prime dans les deux sens).
 */

import { useCallback, useEffect, useState } from 'react'

export type Theme = 'systeme' | 'light' | 'dark'

const CLE = 'backflush.theme'

function lireStockage(): Theme {
  try {
    const valeur = localStorage.getItem(CLE)
    return valeur === 'light' || valeur === 'dark' ? valeur : 'systeme'
  } catch {
    return 'systeme'
  }
}

export function useTheme(): { theme: Theme; basculer: () => void } {
  const [theme, setTheme] = useState<Theme>(lireStockage)

  useEffect(() => {
    const racine = document.documentElement
    if (theme === 'systeme') racine.removeAttribute('data-theme')
    else racine.setAttribute('data-theme', theme)
    try {
      if (theme === 'systeme') localStorage.removeItem(CLE)
      else localStorage.setItem(CLE, theme)
    } catch {
      /* stockage indisponible (navigation privée) : le thème reste en mémoire */
    }
  }, [theme])

  const basculer = useCallback(() => {
    setTheme((precedent) => {
      if (precedent === 'systeme') {
        const sombreSysteme = window.matchMedia('(prefers-color-scheme: dark)').matches
        return sombreSysteme ? 'light' : 'dark'
      }
      return precedent === 'dark' ? 'light' : 'dark'
    })
  }, [])

  return { theme, basculer }
}
