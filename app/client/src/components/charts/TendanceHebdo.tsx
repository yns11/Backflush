/**
 * Tendance hebdomadaire — deux panneaux empilés partageant le même axe des
 * abscisses.
 *
 * Panneau haut  : consommation théorique (texturée, scénario de référence) et
 *                 consommation réelle (pleine, mesure). Même unité, même
 *                 échelle.
 * Panneau bas   : impact financier de la semaine, en barres divergentes autour
 *                 de zéro.
 *
 * Pourquoi deux panneaux plutôt qu'un axe secondaire : des quantités et des
 * euros n'ont pas la même unité. Un double axe laisserait croire à des
 * croisements qui n'existent pas — c'est l'erreur de lecture la plus fréquente
 * des graphiques d'écart. Aligner deux panneaux sur le même axe temporel donne
 * la même comparaison, sans le mensonge visuel.
 */

import { useMemo } from 'react'

import type { SemaineAgregee } from '@/api/types'
import { axeCourt, euro, nombre } from '@/lib/format'
import {
  ECART_BARRE,
  MotifTheorique,
  barreVerticale,
  graduations,
  useInfobulle,
} from './primitives'

const MARGES = { haut: 10, droite: 8, bas: 22, gauche: 54 }
const HAUTEUR_VOLUME = 150
const HAUTEUR_IMPACT = 96
/**
 * Espace entre le panneau des quantités et celui des euros.
 *
 * Il ne s'agit pas d'aération décorative : les deux panneaux portent des unités
 * différentes, et c'est le blanc qui le signale. Trop serrés, ils se lisent
 * comme un seul graphique à double axe — précisément la lecture erronée que
 * l'empilement cherche à éviter. L'étiquette d'unité du panneau bas (« € »)
 * vient de surcroît se loger dans cet interstice.
 */
const INTERSTICE = 40

export function TendanceHebdo({
  semaines,
  largeur = 900,
  onSelectionSemaine,
}: {
  semaines: SemaineAgregee[]
  largeur?: number
  onSelectionSemaine?: (semaine: SemaineAgregee) => void
}) {
  const { afficher, masquer, element } = useInfobulle()

  const hauteur = MARGES.haut + HAUTEUR_VOLUME + INTERSTICE + HAUTEUR_IMPACT + MARGES.bas
  const largeurTrace = Math.max(largeur - MARGES.gauche - MARGES.droite, 80)

  const echelles = useMemo(() => {
    const sommet = Math.max(
      1,
      ...semaines.map((s) => Math.max(Number(s.conso_theorique), Number(s.conso_reelle))),
    )
    // L'échelle suit le domaine des GRADUATIONS, pas la valeur maximale brute :
    // sinon la dernière graduation, arrondie au-dessus, se dessinerait hors du
    // panneau.
    const ticksVolume = graduations(0, sommet, 3)
    const maxVolume = Math.max(sommet, ...ticksVolume)
    const impacts = semaines.map((s) => Number(s.ecart_valorise))
    const ticksImpact = graduations(Math.min(0, ...impacts), Math.max(0, ...impacts), 3)
    const minImpact = Math.min(...ticksImpact)
    const maxImpact = Math.max(...ticksImpact)
    const etendueImpact = maxImpact - minImpact || 1

    const yVolume = (valeur: number) =>
      MARGES.haut + HAUTEUR_VOLUME - (valeur / maxVolume) * HAUTEUR_VOLUME
    const baseImpact = MARGES.haut + HAUTEUR_VOLUME + INTERSTICE
    const yImpact = (valeur: number) =>
      baseImpact + HAUTEUR_IMPACT - ((valeur - minImpact) / etendueImpact) * HAUTEUR_IMPACT

    return {
      maxVolume,
      ticksVolume,
      ticksImpact,
      yVolume,
      yImpact,
      zeroImpact: yImpact(0),
      baseVolume: MARGES.haut + HAUTEUR_VOLUME,
    }
  }, [semaines])

  if (semaines.length === 0) return null

  const pasX = largeurTrace / semaines.length
  const largeurGroupe = Math.max(pasX - 8, 4)
  const largeurBarre = Math.max((largeurGroupe - ECART_BARRE) / 2, 2)
  // Sur un historique long, une étiquette par semaine devient illisible : on
  // n'en garde qu'une sur N, calculée pour laisser ~60 px entre deux libellés.
  const pasEtiquette = Math.max(1, Math.ceil(semaines.length / Math.floor(largeurTrace / 60)))

  return (
    <div style={{ position: 'relative' }}>
      <svg
        width="100%"
        viewBox={`0 0 ${largeur} ${hauteur}`}
        role="img"
        aria-label="Consommation théorique et réelle par semaine, et impact financier hebdomadaire"
        onMouseLeave={masquer}
      >
        <defs>
          <MotifTheorique id="motif-theorique" couleur="var(--serie-1)" />
        </defs>

        {/* --- Panneau des volumes --- */}
        {echelles.ticksVolume.map((tick) => (
          <g key={`v-${tick}`}>
            <line
              x1={MARGES.gauche}
              x2={largeur - MARGES.droite}
              y1={echelles.yVolume(tick)}
              y2={echelles.yVolume(tick)}
              stroke="var(--grille)"
              strokeWidth={1}
            />
            <text
              x={MARGES.gauche - 6}
              y={echelles.yVolume(tick) + 3}
              textAnchor="end"
              fontSize={10}
              fill="var(--encre-attenuee)"
            >
              {axeCourt(tick)}
            </text>
          </g>
        ))}

        {semaines.map((semaine, index) => {
          const xGroupe = MARGES.gauche + index * pasX + (pasX - largeurGroupe) / 2
          const theorique = Number(semaine.conso_theorique)
          const reelle = Number(semaine.conso_reelle)
          const impact = Number(semaine.ecart_valorise)

          const survol = (evenement: { clientX: number; clientY: number }) =>
            afficher(evenement, {
              titre: semaine.semaine_libelle,
              lignes: [
                { libelle: 'Théorique', valeur: nombre(theorique, 0), couleur: 'var(--serie-1)' },
                { libelle: 'Réel', valeur: nombre(reelle, 0), couleur: 'var(--serie-2)' },
                { libelle: 'Écart net', valeur: nombre(Number(semaine.ecart_net), 0) },
                {
                  libelle: 'Impact',
                  valeur: euro(impact, 0, true),
                  couleur: impact >= 0 ? 'var(--pole-non-conso)' : 'var(--pole-surconso)',
                },
                { libelle: 'Lignes en écart', valeur: nombre(Number(semaine.nb_lignes_ecart), 0) },
              ],
              note: onSelectionSemaine ? 'Cliquer pour filtrer sur cette semaine' : undefined,
            })

          return (
            <g
              key={semaine.semaine_debut}
              onMouseMove={survol}
              onClick={() => onSelectionSemaine?.(semaine)}
              style={{ cursor: onSelectionSemaine ? 'pointer' : 'default' }}
            >
              {/* Zone de survol plus large que les marques : la cible reste
                  atteignable même sur une barre de 3 px. */}
              <rect
                x={MARGES.gauche + index * pasX}
                y={MARGES.haut}
                width={pasX}
                height={hauteur - MARGES.haut - MARGES.bas}
                fill="transparent"
              />
              <path
                d={barreVerticale(xGroupe, largeurBarre, echelles.yVolume(theorique), echelles.baseVolume)}
                fill="url(#motif-theorique)"
                stroke="var(--serie-1)"
                strokeWidth={1}
              />
              <path
                d={barreVerticale(
                  xGroupe + largeurBarre + ECART_BARRE,
                  largeurBarre,
                  echelles.yVolume(reelle),
                  echelles.baseVolume,
                )}
                fill="var(--serie-2)"
              />
              <path
                d={barreVerticale(
                  xGroupe,
                  largeurGroupe,
                  echelles.yImpact(impact),
                  echelles.zeroImpact,
                )}
                fill={impact >= 0 ? 'var(--pole-non-conso)' : 'var(--pole-surconso)'}
              />
            </g>
          )
        })}

        {/* --- Panneau de l'impact : graduations et ligne de zéro --- */}
        {echelles.ticksImpact.map((tick) => (
          <g key={`i-${tick}`}>
            <line
              x1={MARGES.gauche}
              x2={largeur - MARGES.droite}
              y1={echelles.yImpact(tick)}
              y2={echelles.yImpact(tick)}
              stroke={tick === 0 ? 'var(--axe)' : 'var(--grille)'}
              strokeWidth={1}
            />
            <text
              x={MARGES.gauche - 6}
              y={echelles.yImpact(tick) + 3}
              textAnchor="end"
              fontSize={10}
              fill="var(--encre-attenuee)"
            >
              {axeCourt(tick)}
            </text>
          </g>
        ))}

        <text
          x={MARGES.gauche - 6}
          y={MARGES.haut - 1}
          textAnchor="end"
          fontSize={9}
          fill="var(--encre-attenuee)"
        >
          qté
        </text>
        <text
          x={MARGES.gauche - 6}
          y={MARGES.haut + HAUTEUR_VOLUME + INTERSTICE - 3}
          textAnchor="end"
          fontSize={9}
          fill="var(--encre-attenuee)"
        >
          €
        </text>

        {/* --- Axe des semaines --- */}
        {semaines.map((semaine, index) =>
          index % pasEtiquette === 0 ? (
            <text
              key={`x-${semaine.semaine_debut}`}
              x={MARGES.gauche + index * pasX + pasX / 2}
              y={hauteur - 6}
              textAnchor="middle"
              fontSize={10}
              fill="var(--encre-attenuee)"
            >
              {semaine.semaine_libelle.replace(/^\d{4}-/, '')}
            </text>
          ) : null,
        )}
      </svg>
      {element}
    </div>
  )
}
