/**
 * Blocs repliables.
 *
 * Un écran d'analyse empile des blocs de nature différente — filtres,
 * indicateurs, graphiques, tables — dont l'utilité varie selon le moment. Une
 * fois la sélection posée, la barre de filtres n'est plus qu'un bandeau qui
 * repousse les données vers le bas ; à l'inverse, un contrôleur qui compare
 * deux périodes veut ses KPI et rien d'autre. Replier est donc une action de
 * cadrage, pas un gadget.
 *
 * Deux partis pris :
 *
 * • **L'état est mémorisé par bloc**, dans le stockage local. Replier un bloc
 *   à chaque visite serait un travail répété ; c'est une habitude de lecture,
 *   pas une sélection analytique — l'URL reste réservée à cette dernière, qui
 *   se partage.
 * • **Le contenu replié n'est pas monté.** Un graphique masqué qui continue de
 *   se dessiner coûte autant qu'un graphique visible, sans rien montrer. Les
 *   requêtes, elles, restent gérées par les composants appelants : replier une
 *   carte n'annule pas le chargement en cours, ce qui rend le dépliage
 *   instantané.
 */

import { useCallback, useEffect, useState, type ReactNode } from 'react'

const PREFIXE = 'backflush.pli.'

function lireEtat(cle: string, defaut: boolean): boolean {
  try {
    const brut = localStorage.getItem(PREFIXE + cle)
    return brut === null ? defaut : brut === '1'
  } catch {
    // Stockage indisponible (navigation privée stricte) : le défaut suffit.
    return defaut
  }
}

/**
 * État de pliage d'un bloc, mémorisé localement.
 *
 * @param cle identifiant stable du bloc — il survit aux rechargements.
 * @param ouvertParDefaut état au tout premier affichage.
 */
export function usePli(cle: string, ouvertParDefaut = true): {
  ouvert: boolean
  basculer: () => void
} {
  const [ouvert, setOuvert] = useState(() => lireEtat(cle, ouvertParDefaut))

  useEffect(() => {
    try {
      localStorage.setItem(PREFIXE + cle, ouvert ? '1' : '0')
    } catch {
      /* sans conséquence */
    }
  }, [cle, ouvert])

  const basculer = useCallback(() => setOuvert((precedent) => !precedent), [])
  return { ouvert, basculer }
}

/** Chevron de pliage. Bouton à part entière : atteignable au clavier. */
export function BoutonPli({
  ouvert,
  basculer,
  libelle,
}: {
  ouvert: boolean
  basculer: () => void
  libelle: string
}) {
  return (
    <button
      type="button"
      className="pli"
      onClick={basculer}
      aria-expanded={ouvert}
      aria-label={`${ouvert ? 'Replier' : 'Déplier'} ${libelle}`}
      title={`${ouvert ? 'Replier' : 'Déplier'} ${libelle}`}
    >
      <span className={`pli__chevron${ouvert ? ' pli__chevron--ouvert' : ''}`} aria-hidden="true">
        ▾
      </span>
    </button>
  )
}

/**
 * Bloc repliable générique : un en-tête toujours visible, un corps escamotable.
 *
 * `resume` est affiché à la place du corps quand le bloc est replié — un bloc
 * fermé qui ne dit rien de ce qu'il cache oblige à le rouvrir pour savoir s'il
 * mérite de l'être.
 */
export function BlocRepliable({
  cle,
  titre,
  resume,
  actions,
  ouvertParDefaut = true,
  className = '',
  children,
}: {
  cle: string
  titre: ReactNode
  resume?: ReactNode
  actions?: ReactNode
  ouvertParDefaut?: boolean
  className?: string
  children: ReactNode
}) {
  const { ouvert, basculer } = usePli(cle, ouvertParDefaut)
  const libelle = typeof titre === 'string' ? titre : 'ce bloc'

  return (
    <section className={`bloc${ouvert ? '' : ' bloc--replie'} ${className}`.trim()}>
      <header className="bloc__entete">
        <BoutonPli ouvert={ouvert} basculer={basculer} libelle={libelle} />
        <span className="bloc__titre" onClick={basculer}>
          {titre}
        </span>
        {!ouvert && resume && <span className="bloc__resume">{resume}</span>}
        {actions && (
          <div className="rang" style={{ marginLeft: 'auto' }}>
            {actions}
          </div>
        )}
      </header>
      {ouvert && <div className="bloc__corps">{children}</div>}
    </section>
  )
}
