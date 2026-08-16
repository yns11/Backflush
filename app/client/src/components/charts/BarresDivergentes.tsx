/**
 * Barres horizontales divergentes autour de zéro.
 *
 * Sert aux classements par impact financier (programmes, catégories, top
 * références). Le codage couleur porte la POLARITÉ de l'écart — bleu pour la
 * non-consommation, rouge pour la surconsommation — et non un jugement
 * bien/mal : les deux sont des anomalies. Les couleurs de statut restent
 * réservées à l'alerte de matérialité.
 *
 * Chaque barre porte son étiquette de valeur : la lecture ne dépend jamais de
 * la seule couleur, ce qui satisfait aussi la règle de relief des teintes à
 * faible contraste.
 */

import type { ReactNode } from 'react'

import { euro } from '@/lib/format'
import { barreHorizontale, graduations, useInfobulle } from './primitives'

export interface ElementBarre {
  cle: string
  libelle: string
  valeur: number
  /**
   * Identifiant métier transporté jusqu'au gestionnaire de clic (référence
   * article, code programme…). Indispensable dès que `cle` est une clé
   * composite : la ré-extraire par découpage de chaîne casserait sur les
   * références contenant elles-mêmes le séparateur.
   */
  reference?: string
  /** Lignes supplémentaires affichées dans l'infobulle. */
  details?: Array<{ libelle: string; valeur: string }>
}

const HAUTEUR_LIGNE = 26
const HAUTEUR_BARRE = 14
const LARGEUR_LIBELLE = 190

export function BarresDivergentes({
  elements,
  largeur = 520,
  onSelection,
  formater = (valeur: number) => euro(valeur, 0, true),
  libelleAxe,
}: {
  elements: ElementBarre[]
  largeur?: number
  onSelection?: (element: ElementBarre) => void
  formater?: (valeur: number) => string
  libelleAxe?: ReactNode
}) {
  const { afficher, masquer, element: infobulle } = useInfobulle()

  if (elements.length === 0) return null

  // Tri par magnitude décroissante de la valeur RÉELLEMENT ENCODÉE. Le serveur
  // classe par impact absolu ; comme les barres montrent l'impact net (signé),
  // conserver son ordre donnerait un classement visuellement incohérent —
  // une petite barre au-dessus d'une grande. Le rang doit toujours se lire.
  const ordonnes = [...elements].sort((a, b) => Math.abs(b.valeur) - Math.abs(a.valeur))

  const hauteur = ordonnes.length * HAUTEUR_LIGNE + 22
  const gaucheTrace = LARGEUR_LIBELLE
  const largeurTrace = Math.max(largeur - gaucheTrace - 70, 60)

  const valeurs = ordonnes.map((item) => item.valeur)
  const ticks = graduations(Math.min(0, ...valeurs), Math.max(0, ...valeurs), 4)
  const min = Math.min(...ticks)
  const max = Math.max(...ticks)
  const etendue = max - min || 1
  const x = (valeur: number) => gaucheTrace + ((valeur - min) / etendue) * largeurTrace
  const zero = x(0)

  return (
    <div style={{ position: 'relative' }}>
      <svg
        width="100%"
        viewBox={`0 0 ${largeur} ${hauteur}`}
        role="img"
        aria-label={typeof libelleAxe === 'string' ? libelleAxe : 'Classement par impact'}
        onMouseLeave={masquer}
      >
        {ticks.map((tick) => (
          <line
            key={tick}
            x1={x(tick)}
            x2={x(tick)}
            y1={0}
            y2={hauteur - 18}
            stroke={tick === 0 ? 'var(--axe)' : 'var(--grille)'}
            strokeWidth={1}
          />
        ))}

        {ordonnes.map((item, index) => {
          const y = index * HAUTEUR_LIGNE + (HAUTEUR_LIGNE - HAUTEUR_BARRE) / 2
          const positif = item.valeur >= 0
          const couleur = positif ? 'var(--pole-non-conso)' : 'var(--pole-surconso)'
          const xValeur = x(item.valeur)
          const xEtiquette = positif ? Math.max(xValeur, zero) + 6 : Math.min(xValeur, zero) - 6

          return (
            <g
              key={item.cle}
              onMouseMove={(evenement) =>
                afficher(evenement, {
                  titre: item.libelle,
                  lignes: [
                    { libelle: 'Impact', valeur: formater(item.valeur), couleur },
                    ...(item.details ?? []),
                  ],
                  note: onSelection ? 'Cliquer pour filtrer' : undefined,
                })
              }
              onClick={() => onSelection?.(item)}
              style={{ cursor: onSelection ? 'pointer' : 'default' }}
            >
              <rect x={0} y={index * HAUTEUR_LIGNE} width={largeur} height={HAUTEUR_LIGNE} fill="transparent" />
              <text
                x={LARGEUR_LIBELLE - 10}
                y={index * HAUTEUR_LIGNE + HAUTEUR_LIGNE / 2 + 4}
                textAnchor="end"
                fontSize={11}
                fill="var(--encre-secondaire)"
              >
                {tronquer(item.libelle, 28)}
              </text>
              <path d={barreHorizontale(y, HAUTEUR_BARRE, xValeur, zero)} fill={couleur} />
              <text
                x={xEtiquette}
                y={index * HAUTEUR_LIGNE + HAUTEUR_LIGNE / 2 + 4}
                textAnchor={positif ? 'start' : 'end'}
                fontSize={10.5}
                fill="var(--encre-secondaire)"
                fontVariant="tabular-nums"
              >
                {formater(item.valeur)}
              </text>
            </g>
          )
        })}

        {ticks.map((tick) => (
          <text
            key={`t-${tick}`}
            x={x(tick)}
            y={hauteur - 5}
            textAnchor="middle"
            fontSize={9.5}
            fill="var(--encre-attenuee)"
          >
            {formater(tick)}
          </text>
        ))}
      </svg>
      {infobulle}
    </div>
  )
}

function tronquer(texte: string, longueur: number): string {
  return texte.length <= longueur ? texte : `${texte.slice(0, longueur - 1)}…`
}
