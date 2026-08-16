/**
 * Slicer temporel — sélection d'une plage de semaines par brossage.
 *
 * Trois principes :
 *
 * 1. **Montrer ce qu'on exclut.** L'histogramme couvre TOUT l'historique
 *    disponible, pas seulement la sélection : l'utilisateur voit d'un coup
 *    d'œil s'il coupe une période chargée.
 * 2. **Manipulation directe.** Poignées glissables, plage déplaçable en bloc,
 *    et raccourcis pour les horizons usuels d'un pilotage hebdomadaire.
 * 3. **Accessible au clavier.** Chaque poignée est un `slider` ARIA : flèches
 *    pour une semaine, Origine/Fin pour les bornes. Un composant de brossage
 *    utilisable uniquement à la souris exclut une partie des utilisateurs.
 *
 * La barre encode |impact financier| de la semaine, en rampe séquentielle
 * (magnitude continue, une seule teinte) — pas en teintes catégorielles.
 */

import { useCallback, useMemo, useRef, useState } from 'react'

import type { SemaineAgregee } from '@/api/types'
import { date as formatDate, euro } from '@/lib/format'
import { useInfobulle } from './charts/primitives'

const LARGEUR = 1000
const HAUTEUR = 62
const MARGE_BAS = 16

interface Preset {
  cle: string
  libelle: string
  semaines: number | 'ytd' | 'tout'
  aide: string
}

const PRESETS: Preset[] = [
  { cle: '4', libelle: '4 sem.', semaines: 4, aide: 'Le mois écoulé' },
  { cle: '8', libelle: '8 sem.', semaines: 8, aide: 'Deux mois' },
  { cle: '13', libelle: '13 sem.', semaines: 13, aide: 'Un trimestre' },
  { cle: '26', libelle: '26 sem.', semaines: 26, aide: 'Un semestre' },
  { cle: '52', libelle: '52 sem.', semaines: 52, aide: 'Douze mois glissants' },
  { cle: 'ytd', libelle: 'Année', semaines: 'ytd', aide: "Depuis le 1er janvier" },
  { cle: 'tout', libelle: 'Tout', semaines: 'tout', aide: "Tout l'historique disponible" },
]

export function SlicerTemporel({
  semaines,
  dateDebut,
  dateFin,
  onChangement,
}: {
  semaines: SemaineAgregee[]
  dateDebut: string | null
  dateFin: string | null
  onChangement: (debut: string | null, fin: string | null) => void
}) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [glissement, setGlissement] = useState<'debut' | 'fin' | 'plage' | null>(null)
  const ancre = useRef<{ x: number; debut: number; fin: number } | null>(null)
  const { afficher, masquer, element: infobulle } = useInfobulle()

  const cles = useMemo(() => semaines.map((s) => s.semaine_debut), [semaines])

  const indexDebut = useMemo(() => indexPour(cles, dateDebut, 0), [cles, dateDebut])
  const indexFin = useMemo(() => indexPour(cles, dateFin, cles.length - 1), [cles, dateFin])

  const maxImpact = useMemo(
    () => Math.max(1, ...semaines.map((s) => Math.abs(Number(s.ecart_valorise_absolu)))),
    [semaines],
  )

  const pas = semaines.length > 0 ? LARGEUR / semaines.length : LARGEUR
  const largeurBarre = Math.max(pas - 2, 1)

  const appliquer = useCallback(
    (debut: number, fin: number) => {
      if (semaines.length === 0) return
      const bas = Math.max(0, Math.min(debut, fin))
      const haut = Math.min(semaines.length - 1, Math.max(debut, fin))
      onChangement(cles[bas] ?? null, cles[haut] ?? null)
    },
    [cles, semaines.length, onChangement],
  )

  const indexDepuisPointeur = useCallback(
    (clientX: number): number => {
      const rect = svgRef.current?.getBoundingClientRect()
      if (!rect || rect.width === 0 || semaines.length === 0) return 0
      const ratio = (clientX - rect.left) / rect.width
      return Math.max(0, Math.min(semaines.length - 1, Math.floor(ratio * semaines.length)))
    },
    [semaines.length],
  )

  const demarrer = (genre: 'debut' | 'fin' | 'plage') => (evenement: React.PointerEvent) => {
    evenement.preventDefault()
    evenement.currentTarget.setPointerCapture(evenement.pointerId)
    setGlissement(genre)
    ancre.current = { x: evenement.clientX, debut: indexDebut, fin: indexFin }
  }

  const deplacer = (evenement: React.PointerEvent) => {
    if (!glissement || !ancre.current) return
    const index = indexDepuisPointeur(evenement.clientX)
    if (glissement === 'debut') appliquer(index, indexFin)
    else if (glissement === 'fin') appliquer(indexDebut, index)
    else {
      // Déplacement en bloc : la largeur de la fenêtre est préservée, et la
      // plage est retenue aux bornes de l'historique plutôt que rognée.
      const depart = ancre.current
      const decalage = indexDepuisPointeur(evenement.clientX) - indexDepuisPointeur(depart.x)
      const largeurPlage = depart.fin - depart.debut
      let debut = depart.debut + decalage
      debut = Math.max(0, Math.min(debut, semaines.length - 1 - largeurPlage))
      appliquer(debut, debut + largeurPlage)
    }
  }

  const arreter = (evenement: React.PointerEvent) => {
    if (evenement.currentTarget.hasPointerCapture(evenement.pointerId)) {
      evenement.currentTarget.releasePointerCapture(evenement.pointerId)
    }
    setGlissement(null)
    ancre.current = null
  }

  const clavier = (borne: 'debut' | 'fin') => (evenement: React.KeyboardEvent) => {
    const courant = borne === 'debut' ? indexDebut : indexFin
    const deltas: Record<string, number> = {
      ArrowLeft: -1, ArrowRight: 1, ArrowDown: -1, ArrowUp: 1,
      PageDown: -4, PageUp: 4,
    }
    let cible: number | null = null
    if (evenement.key in deltas) cible = courant + (deltas[evenement.key] ?? 0)
    else if (evenement.key === 'Home') cible = 0
    else if (evenement.key === 'End') cible = semaines.length - 1
    if (cible === null) return
    evenement.preventDefault()
    if (borne === 'debut') appliquer(cible, indexFin)
    else appliquer(indexDebut, cible)
  }

  const appliquerPreset = (preset: Preset) => {
    if (semaines.length === 0) return
    const dernier = semaines.length - 1
    if (preset.semaines === 'tout') return appliquer(0, dernier)
    if (preset.semaines === 'ytd') {
      const anneeCourante = semaines[dernier]?.semaine_debut.slice(0, 4)
      const premier = semaines.findIndex((s) => s.semaine_debut.slice(0, 4) === anneeCourante)
      return appliquer(premier < 0 ? 0 : premier, dernier)
    }
    return appliquer(Math.max(0, dernier - preset.semaines + 1), dernier)
  }

  const presetActif = useMemo(() => {
    if (semaines.length === 0) return null
    if (indexDebut === 0 && indexFin === semaines.length - 1) return 'tout'
    if (indexFin !== semaines.length - 1) return null
    const nombreSemaines = indexFin - indexDebut + 1
    return PRESETS.find((p) => p.semaines === nombreSemaines)?.cle ?? null
  }, [indexDebut, indexFin, semaines.length])

  if (semaines.length === 0) {
    return (
      <div className="slicer">
        <div className="attenue">Aucune semaine disponible sur cette sélection.</div>
      </div>
    )
  }

  const xDebut = indexDebut * pas
  const xFin = (indexFin + 1) * pas
  const nbSelectionnees = indexFin - indexDebut + 1

  return (
    <div className="slicer">
      <div className="slicer__entete">
        <span className="slicer__periode">
          {formatDate(cles[indexDebut] ?? null)} → {formatDate(cles[indexFin] ?? null)}
        </span>
        <span className="attenue">
          {nbSelectionnees} semaine{nbSelectionnees > 1 ? 's' : ''} sur {semaines.length}
        </span>
        <div className="slicer__presets" role="group" aria-label="Horizons prédéfinis">
          {PRESETS.map((preset) => (
            <button
              key={preset.cle}
              type="button"
              className="slicer__preset"
              aria-pressed={presetActif === preset.cle}
              title={preset.aide}
              onClick={() => appliquerPreset(preset)}
            >
              {preset.libelle}
            </button>
          ))}
        </div>
      </div>

      <div className="slicer__toile">
        <svg
          ref={svgRef}
          width="100%"
          height={HAUTEUR + MARGE_BAS}
          viewBox={`0 0 ${LARGEUR} ${HAUTEUR + MARGE_BAS}`}
          preserveAspectRatio="none"
          onPointerMove={deplacer}
          onPointerUp={arreter}
          onPointerCancel={arreter}
          onMouseLeave={masquer}
          role="group"
          aria-label="Sélection de la plage de semaines"
        >
          {semaines.map((semaine, index) => {
            const valeur = Math.abs(Number(semaine.ecart_valorise_absolu))
            const hauteurBarre = Math.max(2, (valeur / maxImpact) * (HAUTEUR - 6))
            const dansSelection = index >= indexDebut && index <= indexFin
            return (
              <rect
                key={semaine.semaine_debut}
                x={index * pas + 1}
                y={HAUTEUR - hauteurBarre}
                width={largeurBarre}
                height={hauteurBarre}
                rx={2}
                fill={dansSelection ? 'var(--sequentiel-400)' : 'var(--grille)'}
                onMouseMove={(evenement) =>
                  afficher(evenement, {
                    titre: semaine.semaine_libelle,
                    lignes: [
                      { libelle: 'Impact absolu', valeur: euro(valeur, 0, true) },
                      { libelle: 'Impact net', valeur: euro(Number(semaine.ecart_valorise), 0, true) },
                      { libelle: 'Lignes en écart', valeur: String(semaine.nb_lignes_ecart) },
                    ],
                  })
                }
              />
            )
          })}

          {/* Voiles sur les périodes exclues */}
          <rect x={0} y={0} width={xDebut} height={HAUTEUR} fill="var(--surface)" opacity={0.62} />
          <rect
            x={xFin}
            y={0}
            width={Math.max(LARGEUR - xFin, 0)}
            height={HAUTEUR}
            fill="var(--surface)"
            opacity={0.62}
          />

          {/* Plage sélectionnée, déplaçable en bloc */}
          <rect
            x={xDebut}
            y={0}
            width={Math.max(xFin - xDebut, 1)}
            height={HAUTEUR}
            fill="transparent"
            stroke="var(--serie-1)"
            strokeWidth={1.5}
            style={{ cursor: glissement === 'plage' ? 'grabbing' : 'grab' }}
            onPointerDown={demarrer('plage')}
          />

          {(['debut', 'fin'] as const).map((borne) => {
            const x = borne === 'debut' ? xDebut : xFin
            const index = borne === 'debut' ? indexDebut : indexFin
            return (
              <g
                key={borne}
                className="slicer__poignee"
                onPointerDown={demarrer(borne)}
                onKeyDown={clavier(borne)}
                tabIndex={0}
                role="slider"
                aria-label={borne === 'debut' ? 'Première semaine' : 'Dernière semaine'}
                aria-valuemin={0}
                aria-valuemax={semaines.length - 1}
                aria-valuenow={index}
                aria-valuetext={semaines[index]?.semaine_libelle ?? ''}
              >
                {/* Cible de saisie large, marque fine : confort de pointage sans
                    surcharge visuelle. */}
                <rect x={x - 9} y={0} width={18} height={HAUTEUR} fill="transparent" />
                <rect x={x - 2} y={0} width={4} height={HAUTEUR} rx={2} fill="var(--serie-1)" />
                <rect x={x - 4} y={HAUTEUR / 2 - 9} width={8} height={18} rx={3} fill="var(--serie-1)" />
              </g>
            )
          })}

          <line x1={0} y1={HAUTEUR} x2={LARGEUR} y2={HAUTEUR} stroke="var(--axe)" strokeWidth={1} />
        </svg>

        <div
          className="rang attenue"
          style={{ justifyContent: 'space-between', fontSize: 10.5, marginTop: 2 }}
        >
          <span>{semaines[0]?.semaine_libelle}</span>
          <span>Impact absolu par semaine</span>
          <span>{semaines[semaines.length - 1]?.semaine_libelle}</span>
        </div>
      </div>
      {infobulle}
    </div>
  )
}

/** Index de la semaine correspondant à une date, ou une valeur de repli. */
function indexPour(cles: string[], valeur: string | null, defaut: number): number {
  if (!valeur || cles.length === 0) return Math.max(0, Math.min(defaut, cles.length - 1))
  const exact = cles.indexOf(valeur)
  if (exact >= 0) return exact
  // La date ne tombe pas sur un lundi présent dans l'historique (lien partagé,
  // saisie manuelle) : on retient la semaine la plus proche sans dépasser.
  let candidat = defaut
  for (let index = 0; index < cles.length; index += 1) {
    const cle = cles[index]
    if (cle !== undefined && cle <= valeur) candidat = index
  }
  return Math.max(0, Math.min(candidat, cles.length - 1))
}
