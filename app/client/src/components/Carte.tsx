/**
 * Carte de contenu.
 *
 * Le titre porte le MESSAGE, pas seulement le sujet : « L'écart se concentre
 * sur trois références » informe, « Top composants » ne fait qu'étiqueter. La
 * propriété `message` accueille cette phrase, calculée à partir des données.
 */

import type { ReactNode } from 'react'

export function Carte({
  titre,
  message,
  actions,
  legende,
  children,
  aide,
}: {
  titre: string
  message?: ReactNode
  actions?: ReactNode
  legende?: ReactNode
  aide?: string
  children: ReactNode
}) {
  return (
    <section className="carte">
      <header className="carte__entete">
        <h2 className="carte__titre" title={aide}>
          {titre}
        </h2>
        {message && <span className="carte__message">{message}</span>}
        {legende}
        {actions && <div className="rang" style={{ marginLeft: 'auto' }}>{actions}</div>}
      </header>
      <div className="carte__corps">{children}</div>
    </section>
  )
}

export function Legende({
  items,
}: {
  items: Array<{ libelle: string; couleur: string; motif?: boolean }>
}) {
  return (
    <div className="legende">
      {items.map((item) => (
        <span key={item.libelle} className="legende__item">
          <span
            className="legende__marque"
            style={
              item.motif
                ? {
                    // La série « théorique » est un scénario de référence, pas une
                    // mesure : elle se distingue par sa texture, ce qui la rend
                    // lisible même sans perception fine des couleurs.
                    background: `repeating-linear-gradient(45deg, ${item.couleur} 0 2px, transparent 2px 4px)`,
                    border: `1px solid ${item.couleur}`,
                  }
                : { background: item.couleur }
            }
          />
          {item.libelle}
        </span>
      ))}
    </div>
  )
}
