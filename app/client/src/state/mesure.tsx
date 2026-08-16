/**
 * Mesure d'analyse : valeur (€) ou quantité (unités).
 *
 * Ce n'est pas un réglage d'affichage. Les deux mesures produisent des
 * classements DIFFÉRENTS : en euros, une pièce coûteuse écrase le classement ;
 * en unités, c'est une visserie consommée par milliers qui domine. Le tri des
 * graphiques, la concentration et les indicateurs suivent donc la bascule
 * jusque dans les requêtes — sans quoi l'utilisateur lirait un classement en
 * euros habillé d'unités, ce qui serait pire que pas de bascule du tout.
 *
 * Le choix est mémorisé localement : il traduit une habitude de travail (un
 * responsable d'atelier raisonne en pièces, un contrôleur de gestion en euros)
 * et n'a pas à être refait à chaque visite. Il reste hors de l'URL, réservée à
 * la sélection analytique, qui, elle, se partage.
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

import type { Mesure } from '@/api/types'

const CLE_STOCKAGE = 'backflush.mesure'

interface ContexteMesure {
  mesure: Mesure
  definir: (mesure: Mesure) => void
  basculer: () => void
  /** Vrai en mode valeur — évite de répéter la comparaison dans les vues. */
  enValeur: boolean
}

const Contexte = createContext<ContexteMesure | null>(null)

function mesureInitiale(): Mesure {
  try {
    return localStorage.getItem(CLE_STOCKAGE) === 'quantite' ? 'quantite' : 'valeur'
  } catch {
    // Stockage indisponible (navigation privée stricte) : la valeur par défaut
    // suffit, ce réglage n'est pas critique.
    return 'valeur'
  }
}

export function MesureProvider({ children }: { children: ReactNode }) {
  const [mesure, setMesure] = useState<Mesure>(mesureInitiale)

  useEffect(() => {
    try {
      localStorage.setItem(CLE_STOCKAGE, mesure)
    } catch {
      /* sans conséquence */
    }
  }, [mesure])

  const basculer = useCallback(
    () => setMesure((precedente) => (precedente === 'valeur' ? 'quantite' : 'valeur')),
    [],
  )

  const valeur = useMemo<ContexteMesure>(
    () => ({ mesure, definir: setMesure, basculer, enValeur: mesure === 'valeur' }),
    [mesure, basculer],
  )
  return <Contexte.Provider value={valeur}>{children}</Contexte.Provider>
}

export function useMesure(): ContexteMesure {
  const contexte = useContext(Contexte)
  if (!contexte) throw new Error('useMesure doit être utilisé dans MesureProvider.')
  return contexte
}

/** Bouton de bascule, à placer dans l'en-tête des écrans analytiques. */
export function BasculeMesure() {
  const { mesure, definir } = useMesure()
  return (
    <div
      className="bascule"
      role="group"
      aria-label="Mesure d'analyse"
      title="Bascule tous les indicateurs, graphiques et classements entre euros et unités."
    >
      {(['valeur', 'quantite'] as const).map((option) => (
        <button
          key={option}
          type="button"
          className="bascule__option"
          aria-pressed={mesure === option}
          onClick={() => definir(option)}
        >
          {option === 'valeur' ? '€ Valeur' : '# Quantité'}
        </button>
      ))}
    </div>
  )
}
